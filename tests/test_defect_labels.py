"""Phase 2 checks — the contract a labelled defect table has to satisfy.

Runs offline. Every case builds a table that passes, breaks one thing, and
asserts the break is reported: a check that has never been seen to fail is not
evidence of anything.

Run from the repository root:  python -m tests.test_defect_labels
"""
from __future__ import annotations

import sys

import polars as pl

from motintel import defect_labels as dl
from motintel import enrich


def good_table() -> tuple[pl.DataFrame, list[tuple[str, str]]]:
    """A labelling that passes every check, built over the real defect pairs
    so the anchors have something to match."""
    pairs = [(p.category, p.description) for p in enrich.defect_pairs()]
    rows = []
    for category, description in pairs:
        area, effort = "other", "moderate"
        for a_cat, a_prefix, a_areas, a_effort in dl.ANCHORS:
            if category == a_cat and description.startswith(a_prefix):
                area, effort = sorted(a_areas)[0], a_effort
                break
        rows.append({"defect_category": category, "defect_desc": description,
                     "plain_english": "A part of the car is worn or damaged.",
                     "repair_area": area, "effort": effort,
                     "forecourt_check": None, "n_tests": 100})
    return pl.DataFrame(rows), pairs


def check_clean_table_passes(fail, table, pairs) -> None:
    problems = dl.validate(table, pairs)
    if problems:
        fail(f"a clean table was rejected: {problems[:3]}")


def check_coverage(fail, table, pairs) -> None:
    if not dl.validate(table.head(len(table) - 1), pairs):
        fail("an unlabelled defect was not reported")
    doubled = pl.concat([table, table.head(1)])
    if not dl.validate(doubled, pairs):
        fail("a defect labelled twice was not reported")
    invented = pl.concat([table, table.head(1).with_columns(
        defect_desc=pl.lit("a defect nobody recorded"))])
    if not dl.validate(invented, pairs):
        fail("an invented defect was not reported")


def check_closed_sets(fail, table, pairs) -> None:
    for column, bad in (("repair_area", "brake system"), ("effort", "medium")):
        broken = table.with_columns(
            pl.when(pl.int_range(pl.len()) == 400).then(pl.lit(bad))
            .otherwise(pl.col(column)).alias(column))
        if not any(column in p for p in dl.validate(broken, pairs)):
            fail(f"{column} outside its closed set was not reported")


def check_shape(fail, table, pairs) -> None:
    cases = {
        "a bulleted list": "- A part is worn.",
        "markdown": "A **part** is worn or damaged badly.",
        "a preamble": "Here is what is wrong with this part of the car.",
        "two lines": "A part is worn.\nAnd another thing.",
        "a fragment": "Worn.",
        "an essay": "A part of the car is worn or damaged. " * 8,
    }
    for what, text in cases.items():
        broken = table.with_columns(
            pl.when(pl.int_range(pl.len()) == 7).then(pl.lit(text))
            .otherwise(pl.col("plain_english")).alias("plain_english"))
        if not dl.validate(broken, pairs):
            fail(f"{what} was accepted as a plain sentence")


def check_inventions(fail, table, pairs) -> None:
    cases = {
        "a price": "A bulb has blown and costs about £15 to replace.",
        "pounds in words": "A worn part, roughly forty pounds to put right.",
        "a mileage": "A part that usually wears out by 80k miles.",
        "a year": "A known problem on cars built after 2015 for this part.",
        "an ungrounded figure": "One of the 4 brake discs is worn away.",
    }
    for what, text in cases.items():
        broken = table.with_columns(
            pl.when(pl.int_range(pl.len()) == 7).then(pl.lit(text))
            .otherwise(pl.col("plain_english")).alias("plain_english"))
        if not dl.validate(broken, pairs):
            fail(f"{what} was accepted: {text!r}")

    # The forecourt check is free text too, and gets the same treatment.
    priced = table.with_columns(
        pl.when(pl.int_range(pl.len()) == 7)
        .then(pl.lit("Ask whether the £200 repair was done."))
        .otherwise(pl.col("forecourt_check")).alias("forecourt_check"))
    if not dl.validate(priced, pairs):
        fail("a price in the forecourt check was accepted")

    # A figure that IS in the description must be allowed through.
    grounded = table.with_columns(
        pl.when(pl.col("defect_desc").str.contains("1.6mm"))
        .then(pl.lit("The tyre tread is below the 1.6mm legal minimum."))
        .otherwise(pl.col("plain_english")).alias("plain_english"))
    if dl.validate(grounded, pairs):
        fail("a figure taken from the description was rejected")


def check_anchors(fail, table, pairs) -> None:
    lamps = ((pl.col("defect_category")
              == "Lamps, reflectors and electrical equipment")
             & (pl.col("defect_desc") == "not working"))
    for column, wrong in (("effort", "major"), ("repair_area", "brakes")):
        broken = table.with_columns(
            pl.when(lamps).then(pl.lit(wrong)).otherwise(pl.col(column))
            .alias(column))
        if not dl.anchor_problems(broken):
            fail(f"an anchor disagreeing on {column} was not reported")

    # An anchor whose defect has vanished must complain rather than pass.
    # Every row the anchor matches has to go, not just the exact wording: the
    # prefix also catches "not working on dipped beam" and "on main beam".
    without = table.filter(
        ~((pl.col("defect_category")
           == "Lamps, reflectors and electrical equipment")
          & pl.col("defect_desc").str.starts_with("not working")))
    if not any("matches no defect" in p for p in dl.anchor_problems(without)):
        fail("an anchor matching nothing passed silently")


def check_overrides(fail, table, pairs) -> None:
    category, description = pairs[0]
    key = dl.override_key(category, description)
    overrides = {key: {"effort": "major",
                       "plain_english": "A corrected description here."}}
    out = dl.apply_overrides(table, overrides)
    row = out.filter((pl.col("defect_category") == category)
                     & (pl.col("defect_desc") == description)).row(
                         0, named=True)
    if row["effort"] != "major":
        fail("an override did not replace the field it names")
    if row["plain_english"] != "A corrected description here.":
        fail("an override did not replace plain_english")
    if row["repair_area"] != table.row(0, named=True)["repair_area"]:
        fail("an override changed a field it did not name")
    if len(out) != len(table):
        fail("applying overrides changed the number of rows")

    untouched = dl.apply_overrides(table, {})
    if untouched.equals(table) is False:
        fail("applying no overrides changed the table")

    try:
        dl.apply_overrides(table, {"Nothing||matches this": {"effort": "minor"}})
    except ValueError:
        pass
    else:
        fail("an override matching no defect was silently ignored")


def check_stability(fail, table, pairs) -> None:
    if dl.stability_problems(table, table):
        fail("a table was reported as unstable against itself")
    drifted = table.with_columns(
        pl.when(pl.int_range(pl.len()) == 3).then(pl.lit("brakes"))
        .otherwise(pl.col("repair_area")).alias("repair_area"))
    if not dl.stability_problems(table, drifted):
        fail("a repair_area that moved between runs was not reported")


def check_review_page(fail, table, pairs) -> None:
    from motintel import review
    page = review.build(table.sort("n_tests", descending=True), [])
    for wanted in ("<table", "Plain English", dl.override_key(*pairs[0])):
        if wanted not in page:
            fail(f"the review page is missing {wanted!r}")
    if "<script" in page.lower():
        fail("the review page grew a script")
    flagged = review.build(table, ["something is wrong"])
    if "something is wrong" not in flagged:
        fail("the review page does not show failing checks")


def check_shipped_labelling(fail, table, pairs) -> None:
    """The labelling that is actually on disk, not a synthetic stand-in.

    enrich gates its own write on these checks, but nothing else did: a Parquet
    edited by hand, or one produced before an anchor was added, would ship
    unnoticed. This is the regression the plan asks for — a prompt change that
    quietly reclassifies brake faults fails here rather than in the app.
    """
    from motintel import serving
    shipped = serving.defect_labels()
    if shipped is None:
        print("        (no defect_meta.parquet — the shipped labelling is "
              "not being checked)")
        return
    shipped = shipped.with_columns(n_tests=pl.lit(0, dtype=pl.Int64))
    for problem in dl.validate(shipped, pairs)[:10]:
        fail(problem)


def run() -> int:
    if not enrich.SOURCE.exists():
        print(f"  skipped: no {enrich.SOURCE.name} in this checkout")
        return 0
    table, pairs = good_table()
    failures: list[str] = []
    checks = [("clean table", check_clean_table_passes),
              ("coverage", check_coverage),
              ("closed sets", check_closed_sets), ("shape", check_shape),
              ("inventions", check_inventions), ("anchors", check_anchors),
              ("overrides", check_overrides), ("stability", check_stability),
              ("review page", check_review_page),
              ("shipped labelling", check_shipped_labelling)]
    for name, check in checks:
        found: list[str] = []
        check(found.append, table, pairs)
        print(f"  {name}: {'OK' if not found else f'{len(found)} failed'}")
        failures += [f"{name}: {f}" for f in found]

    print(f"\nfailures: {len(failures)}")
    for f in failures:
        print(f"  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    print("defect label contract")
    sys.exit(run())
