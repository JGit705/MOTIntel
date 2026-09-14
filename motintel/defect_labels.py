"""What a valid set of defect labels looks like.

Kept apart from enrich.py deliberately. That module knows how to get labels out
of a model; this one knows what makes them acceptable, and the two should be
able to disagree. The checks here run before anything is written, they run
again as a test, and Phase 4's regression suite imports the same anchors — so
there is one definition of "correct" rather than three that drift.

The defence against a wrong label is not the prompt. It is this file plus a
human reading the rows, and the overrides that reading produces.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import polars as pl

OVERRIDES_PATH = Path(__file__).resolve().parent / "defect_overrides.json"

# Closed sets. An open-ended label is how you end up with "brakes", "braking"
# and "brake system" as three groups.
REPAIR_AREAS = ("brakes", "suspension", "steering", "tyres and wheels",
                "lights and electrics", "corrosion and structure",
                "emissions and exhaust", "visibility",
                "seatbelts and restraints", "other")
# The size of the job, never its price: this dataset carries no repair costs.
# Note this is not severity — brake pads under 1.5 mm are a "minor" job and a
# dangerous fault. Severity comes from severity.parquet, which is data.
EFFORTS = ("minor", "moderate", "major")

# One sentence, not a paragraph and not a word. The bounds are loose enough
# that a legitimate answer never trips them and tight enough to catch a model
# that has started writing an essay or returning a fragment.
MIN_SENTENCE, MAX_SENTENCE = 15, 220

# Markup, list syntax and the openings of a model that has ignored "one
# sentence, no preamble".
BANNED_SHAPES = re.compile(
    r"(^\s*[-*•\d]+[.)]?\s)|(\*\*)|(^#)|(\n)|(^(here|this|the following)\b)",
    re.IGNORECASE)

# Anything that would be an invention: a price, or a claim about how far or how
# long. None of it is in front of the model.
BANNED_CLAIMS = re.compile(
    r"[£$€]|\bpounds?\b|\bgbp\b|\bmiles?\b|\bmileage\b|\bmot\s+history\b"
    r"|\b(19|20)\d{2}\b", re.IGNORECASE)

# Numbers, so a label's figures can be traced back to the text it was given.
NUMBER = re.compile(r"\d+(?:\.\d+)?")

# Anchors a correct labelling must agree with, chosen to span the effort range
# and matched on the start of the description so a reworded lookup still hits.
#
# `areas` is a set rather than one value because some of these genuinely sit in
# two places at once: corrosion of a suspension mounting is both corrosion and
# suspension, and a buyer would accept either. Anchoring an ambiguous case to a
# single answer tests the anchor, not the labels.
ANCHORS: tuple[tuple[str, str, frozenset[str], str], ...] = (
    ("Lamps, reflectors and electrical equipment", "not working",
     frozenset({"lights and electrics"}), "minor"),
    ("Tyres", "tread depth below requirements",
     frozenset({"tyres and wheels"}), "minor"),
    ("Brakes", "less than 1.5 mm thick", frozenset({"brakes"}), "minor"),
    ("Visibility", "does not clear the windscreen effectively",
     frozenset({"visibility"}), "minor"),
    ("Suspension", "ball joint excessively worn",
     frozenset({"suspension"}), "moderate"),
    ("Noise, emissions and leaks", "emissions exceed manufacturer",
     frozenset({"emissions and exhaust"}), "moderate"),
    ("Suspension", "prescribed area excessively corroded",
     frozenset({"corrosion and structure", "suspension"}), "major"),
    ("Body, chassis, structure", "corroded to the extent that the rigidity",
     frozenset({"corrosion and structure"}), "major"),
    ("Seat belts and supplementary restraint systems", "prescribed area "
     "strength or continuity significantly reduced",
     frozenset({"corrosion and structure", "seatbelts and restraints"}),
     "major"),
)

COLUMNS = ("defect_category", "defect_desc", "plain_english", "repair_area",
           "effort", "forecourt_check", "n_tests")


def load_overrides() -> dict[str, dict]:
    """Corrections from the human read, keyed "category||description".

    Committed alongside the code, applied after generation and before the
    write, so the shipped mapping is reproducible and every departure from what
    the model said is recorded rather than quietly edited into a Parquet file
    nobody can diff.
    """
    if not OVERRIDES_PATH.exists():
        return {}
    raw = json.loads(OVERRIDES_PATH.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def override_key(category: str, description: str) -> str:
    return f"{category}||{description}"


def apply_overrides(table: pl.DataFrame,
                    overrides: dict[str, dict] | None = None) -> pl.DataFrame:
    """Replace the named fields on the rows an override addresses.

    An override that matches nothing is a problem, not a no-op: it means the
    wording it was written against has changed, and the correction it carried
    has silently stopped being applied.
    """
    overrides = load_overrides() if overrides is None else overrides
    if not overrides:
        return table
    keys = pl.format("{}||{}", pl.col("defect_category"), pl.col("defect_desc"))
    table = table.with_columns(_key=keys)
    present = set(table["_key"].to_list())
    unmatched = sorted(set(overrides) - present)
    if unmatched:
        raise ValueError(
            f"{len(unmatched)} override(s) match no defect, so their "
            f"correction is not being applied: {unmatched[:3]}")

    for field in ("plain_english", "repair_area", "effort", "forecourt_check"):
        mapping = {k: v[field] for k, v in overrides.items() if field in v}
        if mapping:
            table = table.with_columns(
                pl.col("_key").replace_strict(mapping, default=None)
                .alias("_new"))
            table = table.with_columns(
                pl.coalesce("_new", field).alias(field)).drop("_new")
    return table.drop("_key")


def _sentence_problems(what: str, text: str) -> list[str]:
    problems = []
    if BANNED_SHAPES.search(text):
        problems.append(f"{what} is not a plain single sentence: {text!r}")
    if not MIN_SENTENCE <= len(text) <= MAX_SENTENCE:
        problems.append(f"{what} is {len(text)} characters, outside "
                        f"{MIN_SENTENCE}-{MAX_SENTENCE}: {text!r}")
    return problems


def validate(table: pl.DataFrame,
             pairs: list[tuple[str, str]] | None = None) -> list[str]:
    """Every check a labelled table must pass, as a list of problems.

    Returns rather than raises so one run reports everything wrong with a
    labelling instead of the first thing.
    """
    problems: list[str] = []

    missing_cols = [c for c in COLUMNS if c not in table.columns]
    if missing_cols:
        return [f"missing columns: {missing_cols}"]

    # 1. Coverage. Every pair labelled exactly once, nothing invented.
    got = list(zip(table["defect_category"], table["defect_desc"]))
    if len(got) != len(set(got)):
        seen, twice = set(), set()
        for g in got:
            (twice if g in seen else seen).add(g)
        problems.append(f"{len(twice)} defect(s) labelled more than once: "
                        f"{sorted(twice)[:3]}")
    if pairs is not None:
        wanted = set(pairs)
        for label, extra in (("unlabelled", wanted - set(got)),
                             ("invented", set(got) - wanted)):
            if extra:
                problems.append(f"{len(extra)} {label} defect(s): "
                                f"{sorted(extra)[:3]}")

    # 2. Closed sets.
    for column, allowed in (("repair_area", REPAIR_AREAS),
                            ("effort", EFFORTS)):
        stray = sorted(set(table[column].to_list()) - set(allowed))
        if stray:
            problems.append(f"{column} outside its closed set: {stray}")

    # 3-4. Shape, and nothing invented in the free text.
    for row in table.iter_rows(named=True):
        where = f'[{row["defect_category"]}] {row["defect_desc"][:40]}'
        problems += [f"{where}: {p}" for p in
                     _sentence_problems("plain_english",
                                        row["plain_english"] or "")]
        if row["forecourt_check"]:
            problems += [f"{where}: {p}" for p in
                         _sentence_problems("forecourt_check",
                                            row["forecourt_check"])]

        text = f'{row["plain_english"]} {row["forecourt_check"] or ""}'
        claim = BANNED_CLAIMS.search(text)
        if claim:
            problems.append(f"{where}: claims something it was not given "
                            f"({claim.group(0)!r})")
        # Every figure has to trace back to the text it came from — the same
        # rule the summary layer is held to, applied to the labels.
        source = set(NUMBER.findall(
            f'{row["defect_category"]} {row["defect_desc"]}'))
        for number in set(NUMBER.findall(text)) - source:
            problems.append(f"{where}: the figure {number!r} is not in the "
                            f"description it was given")

    # 5. Anchors.
    problems += anchor_problems(table)
    return problems


def anchor_problems(table: pl.DataFrame) -> list[str]:
    """Where a labelling disagrees with something we are sure of.

    Also reports an anchor that matches nothing: an anchor quietly passing
    because the defect it names is no longer in the data is worse than one
    that fails, because it reads as coverage that is not there.
    """
    problems = []
    for category, prefix, areas, effort in ANCHORS:
        rows = table.filter((pl.col("defect_category") == category)
                            & pl.col("defect_desc").str.starts_with(prefix))
        if rows.is_empty():
            problems.append(f"anchor [{category}] {prefix!r} matches no "
                            f"defect, so it is not checking anything")
            continue
        for row in rows.iter_rows(named=True):
            where = f'[{category}] {row["defect_desc"][:40]}'
            if row["repair_area"] not in areas:
                problems.append(f"{where}: repair_area is "
                                f"{row['repair_area']!r}, expected one of "
                                f"{sorted(areas)}")
            if row["effort"] != effort:
                problems.append(f"{where}: effort is {row['effort']!r}, "
                                f"expected {effort!r}")
    return problems


def stability_problems(first: pl.DataFrame, second: pl.DataFrame) -> list[str]:
    """Where two labelling runs disagree on the fields that are meant to be
    decidable.

    Temperature is zero, so the categorical fields should come back identical.
    Drift here means the mapping is a coin toss and the prompt needs work; the
    free text is allowed to vary in wording, so it is not compared.
    """
    key = ["defect_category", "defect_desc"]
    joined = first.select(*key, "repair_area", "effort").join(
        second.select(*key, "repair_area", "effort"), on=key, how="inner",
        suffix="_again")
    problems = []
    for column in ("repair_area", "effort"):
        moved = joined.filter(pl.col(column) != pl.col(f"{column}_again"))
        for row in moved.head(5).iter_rows(named=True):
            problems.append(
                f'[{row["defect_category"]}] {row["defect_desc"][:40]}: '
                f'{column} moved {row[column]!r} -> {row[f"{column}_again"]!r}')
        if len(moved) > 5:
            problems.append(f"...and {len(moved) - 5} more {column} changes")
    return problems
