"""The serving-side view of the data, and the one place a VehicleProfile is built.

The app and the LLM demo previously assembled a profile each, with their own
thresholds and their own idea of which vehicles existed. That is how the same
car ends up with one failure rate on screen and another in a summary. Both now
call build_profile here, so what the reader sees and what the model is given
come from a single definition.

Everything is read from the pre-aggregated Parquet — never the DuckDB file,
which does not ship.
"""
from __future__ import annotations

import math
from dataclasses import replace
from functools import lru_cache

import polars as pl

from motintel.config import PROCESSED
from motintel.queries import MIN_TESTS_FOR_CONFIDENCE, VehicleProfile

__all__ = ["MIN_TESTS_FOR_CONFIDENCE", "build_profile", "tables",
           "selectable_vehicles", "age_bands", "mileage_curve",
           "mileage_label", "benchmark_for", "peers_for",
           "reliability_ranking", "RANKING_COLUMNS", "defect_labels",
           "is_sparse", "severity_shares", "repair_area_shares",
           "ranking_position", "mileage_step", "findings",
           "defect_short_labels"]

# Everything past this is pooled, so the tail does not become a row of
# single-test noise.
MILEAGE_CAP = 160_000

# Ranking parameters. A mileage cell thinner than this contributes noise rather
# than signal, and is not counted toward a model's coverage either.
MIN_CELL_TESTS = 30
# A model must occupy cells accounting for this much of the band's mileage
# distribution before its rates are worth reweighting. Below it, standardising
# is extrapolation dressed up as arithmetic.
MIN_MILEAGE_COVERAGE = 0.70
# Shrinkage strength, in pseudo-tests at the cell's band-wide rate.
PRIOR_TESTS = 50

RANKING_COLUMNS = {"make": pl.Utf8, "model": pl.Utf8, "n_tests": pl.Int64,
                   "observed": pl.Float64, "standardised": pl.Float64,
                   "margin": pl.Float64, "coverage": pl.Float64}
# Returned wherever a band has nothing rankable, so callers get a frame with
# the right columns instead of having to special-case a None.
_EMPTY_RANKING = pl.DataFrame(schema=RANKING_COLUMNS)
TABLES = ("failure_rates", "top_defects", "age_curve", "benchmark",
          "severity", "vehicle_meta")
# Not in TABLES: the app has to run without it. The enrichment stage needs an
# API key and a day's quota, so a checkout that has run the pipeline but not
# enrich should still work, minus the plain English — the same bargain the
# summary layer makes.
DEFECT_META = "defect_meta"


@lru_cache(maxsize=1)
def tables() -> dict[str, pl.DataFrame]:
    missing = [t for t in TABLES if not (PROCESSED / f"{t}.parquet").exists()]
    if missing:
        raise FileNotFoundError(
            f"missing serving data: {', '.join(missing)}. Run export first.")
    return {t: pl.read_parquet(PROCESSED / f"{t}.parquet") for t in TABLES}


@lru_cache(maxsize=1)
def defect_labels() -> pl.DataFrame | None:
    """The plain-English defect labels, or None if enrich has not been run.

    None is a normal state, not an error. Everything built on these degrades to
    the DVSA wording, which is what the app showed before they existed.
    """
    path = PROCESSED / f"{DEFECT_META}.parquet"
    if not path.exists():
        return None
    return pl.read_parquet(path).select(
        "defect_category", "defect_desc", "plain_english", "repair_area",
        "effort", "forecourt_check")


@lru_cache(maxsize=1)
def defect_short_labels() -> pl.DataFrame | None:
    """Two-part short names for each failure reason, for table rows — "Deep
    tyre cut" / "Structural cords exposed" — or None if short_labels has not
    been run. The page falls back to the plain-English sentence."""
    path = PROCESSED / "defect_short.parquet"
    if not path.exists():
        return None
    return pl.read_parquet(path).select("defect_category", "defect_desc",
                                        "headline", "detail")


def selectable_vehicles() -> pl.DataFrame:
    """Vehicles the page can actually render, most-tested first.

    A vehicle qualifies only if every export it needs has a row for it. Deriving
    the list from a separate model table with a different threshold is what put
    134 dead options in the dropdown.
    """
    t = tables()
    usable = (t["age_curve"].group_by("make", "model")
              .agg(n_tests=pl.col("n_tests").sum()))
    return (usable.join(t["vehicle_meta"].select("make", "model"),
                        on=["make", "model"])
            .sort("n_tests", descending=True))


def age_bands(make: str, model: str) -> list[int]:
    return (tables()["age_curve"]
            .filter((pl.col("make") == make) & (pl.col("model") == model))
            .sort("age_band")["age_band"].to_list())


def mileage_curve(make: str, model: str) -> pl.DataFrame:
    """Failure rate by odometer band, with the long tail pooled.

    Rows with no odometer reading are already excluded upstream: a test with no
    reading has no place on a mileage axis, and a null band crashed the label
    built from it.
    """
    return (tables()["failure_rates"]
            .filter((pl.col("make") == make) & (pl.col("model") == model))
            .drop_nulls("mileage_band")
            .with_columns(band=pl.when(pl.col("mileage_band") >= MILEAGE_CAP)
                          .then(MILEAGE_CAP).otherwise(pl.col("mileage_band")))
            .group_by("band")
            .agg(n_tests=pl.col("n_tests").sum(),
                 failure_rate=(pl.col("failure_rate") * pl.col("n_tests")).sum()
                 / pl.col("n_tests").sum())
            .sort("band"))


def mileage_label(band: int) -> str:
    return f"{int(band) // 1000}k+" if band >= MILEAGE_CAP else f"{int(band) // 1000}k"


def benchmark_for(age_band: int) -> float | None:
    row = tables()["benchmark"].filter(pl.col("age_band") == age_band)
    return float(row["failure_rate"][0]) if not row.is_empty() else None


def peers_for(age_band: int, make: str, model: str, limit: int = 3):
    """Highest-volume other models in the same band. Volume is a proxy for a
    comparable car, not a like-for-like class match, and the app says so."""
    return (tables()["age_curve"]
            .filter((pl.col("age_band") == age_band)
                    & ~((pl.col("make") == make) & (pl.col("model") == model)))
            .sort("n_tests", descending=True).head(limit)
            .select(make="make", model="model", n_tests="n_tests",
                    failure_rate="failure_rate").to_dicts())


@lru_cache(maxsize=16)
def reliability_ranking(age_band: int) -> pl.DataFrame:
    """Models in one age band, ranked so the comparison is a fair one.

    Three corrections stand between a raw group-by and a defensible ranking.
    Each went in after looking at what the raw version actually produced.

    **Mileage.** Sorting models by observed failure rate puts a 12-year-old
    Ferrari 458 (mean odometer 9,100 miles) above a 12-year-old Ford Focus
    (91,700). Mileage is the strongest single driver of failure in this data —
    a Fiesta runs from 9.8% under 20k to 51.3% over 120k — so the raw table
    ranks how little a car is driven at least as much as how well it holds up.
    Each model's per-mileage-band rates are therefore reweighted onto the
    mileage distribution of the whole age band: direct standardisation, the
    same correction behind an age-standardised mortality rate.

    **Cars with no overlap.** Standardising only works where a model has cars
    at the mileages being weighted onto. A model has to appear in cells
    covering at least MIN_MILEAGE_COVERAGE of the band's tests or it is left
    out rather than extrapolated. This is why the supercars vanish: there is no
    such thing as a 90,000-mile Lamborghini here, so the honest answer is that
    it cannot be compared, not a guess at what one would have done.

    **Small cells.** Each cell rate is shrunk toward that cell's band-wide rate
    in proportion to how little data stands behind it, so a 30-test cell that
    happened to pass everything does not take the top of the table.

    The interval is the standard error of a directly standardised rate — the
    weighted sum of the per-cell binomial variances. A Wilson interval would be
    wrong here: it describes a single unweighted proportion, and this is not
    one.

    What none of this removes: an MOT failure rate reflects how a car is looked
    after as much as how it was built, and the expensive marques are looked
    after. The app says so beneath the table.
    """
    cells = (tables()["failure_rates"]
             .filter((pl.col("age_band") == age_band)
                     & pl.col("mileage_band").is_not_null())
             .with_columns(cell=pl.min_horizontal("mileage_band",
                                                  pl.lit(MILEAGE_CAP)))
             .group_by("make", "model", "cell")
             .agg(n=pl.col("n_tests").sum(),
                  failures=(pl.col("failure_rate") * pl.col("n_tests")).sum()))
    if cells.is_empty():
        return _EMPTY_RANKING

    # The reference the whole band is standardised onto: how the band's tests
    # are spread across mileage, and what each of those cells fails at.
    reference = (cells.group_by("cell")
                 .agg(ref_n=pl.col("n").sum(),
                      ref_failures=pl.col("failures").sum())
                 .with_columns(ref_rate=pl.col("ref_failures") / pl.col("ref_n"),
                               weight=pl.col("ref_n") / pl.col("ref_n").sum()))

    usable = (cells.join(reference.select("cell", "ref_rate", "weight"),
                         on="cell")
              .filter(pl.col("n") >= MIN_CELL_TESTS))
    if usable.is_empty():
        return _EMPTY_RANKING

    totals = usable.group_by("make", "model").agg(
        coverage=pl.col("weight").sum(),
        cell_tests=pl.col("n").sum())

    usable = (usable.join(totals, on=["make", "model"])
              .filter((pl.col("coverage") >= MIN_MILEAGE_COVERAGE)
                      & (pl.col("cell_tests") >= MIN_TESTS_FOR_CONFIDENCE))
              .with_columns(
                  # Shrunk toward the cell's band-wide rate. PRIOR_TESTS acts
                  # as that many pseudo-tests at the reference rate, so a
                  # 30-test cell moves most of the way there and a 1,000-test
                  # cell barely moves at all.
                  rate=((pl.col("failures") + PRIOR_TESTS * pl.col("ref_rate"))
                        / (pl.col("n") + PRIOR_TESTS)),
                  # Renormalised over the cells this model actually occupies,
                  # which the coverage floor keeps close to the full reference.
                  w=pl.col("weight") / pl.col("coverage")))
    if usable.is_empty():
        return _EMPTY_RANKING

    # The observed rate and the test count come from age_curve, the same table
    # the gauge reads. Recomputing them over the mileage cells instead lands
    # 0.1 points away from the headline figure for the same car, on the same
    # page — the quiet disagreement this project keeps having to design out.
    # They therefore describe the whole band while the standardised figure
    # describes the cars with odometer readings, and the app labels them so.
    band = (tables()["age_curve"]
            .filter(pl.col("age_band") == age_band)
            .select("make", "model", n_tests="n_tests", observed="failure_rate"))
    return (usable.group_by("make", "model", "coverage")
            .agg(standardised=(pl.col("w") * pl.col("rate")).sum(),
                 variance=(pl.col("w") ** 2 * pl.col("rate")
                           * (1 - pl.col("rate")) / pl.col("n")).sum())
            .with_columns(margin=1.96 * pl.col("variance").sqrt())
            .join(band, on=["make", "model"])
            .sort(["standardised", "n_tests"], descending=[False, True])
            .select("make", "model", "n_tests", "observed", "standardised",
                    "margin", "coverage"))


def severity_shares(make: str, model: str, age_band: int, n_tests: int,
                    failure_rate: float | None) -> dict[str, float]:
    """Shares of failed tests carrying a Dangerous or a Major defect.

    Of failed tests, not of all tests — the question is how bad the failures
    are, given that it failed. One test can carry both, so the two overlap.
    Lived in the app until the summary needed the same figures; two copies is
    how the page and the model come to disagree.
    """
    if not n_tests or failure_rate is None:
        return {}
    rows = tables()["severity"].filter(
        (pl.col("make") == make) & (pl.col("model") == model)
        & (pl.col("age_band") == age_band))
    failed = max(1, round(n_tests * failure_rate))
    shares: dict[str, float] = {}
    for grade in ("Dangerous", "Major"):
        hit = rows.filter(pl.col("deficiency_category") == grade)
        if not hit.is_empty():
            shares[grade] = min(1.0, int(hit["n_tests"][0]) / failed)
    return shares


def repair_area_shares(top_defects: list[dict]) -> list[tuple[str, float]]:
    """Where the ten commonest failure reasons fall, by the part of the car a
    buyer thinks in, largest first.

    A share of those ten reasons by test count, not of tests: summing per-defect
    counts double-counts a test with two defects in the same area, and nothing
    at this grain can undo that. Empty until the enrichment has labelled the
    defects.
    """
    totals: dict[str, int] = {}
    for d in top_defects:
        if d.get("repair_area"):
            totals[d["repair_area"]] = (totals.get(d["repair_area"], 0)
                                        + d["n_tests"])
    whole = sum(totals.values())
    if not whole:
        return []
    return sorted(((area, n / whole) for area, n in totals.items()),
                  key=lambda t: -t[1])


def ranking_position(age_band: int, make: str,
                     model: str) -> tuple[int, int] | None:
    """(position, out of) in the mileage-levelled ranking, 1 the lowest
    failure rate — or None when the model is not fairly rankable at that age."""
    ranked = reliability_ranking(age_band)
    hit = (ranked.with_row_index("i")
           .filter((pl.col("make") == make) & (pl.col("model") == model)))
    return None if hit.is_empty() else (int(hit["i"][0]) + 1, len(ranked))


def mileage_step(by_mileage: list[dict]) -> dict | None:
    """The steepest rise in failure rate between consecutive mileage bands —
    the point at which this model starts costing money — or None when no step
    is sharp enough to be worth pointing at.

    Two points' rise in one band is the floor. Below it the "sharpest step" is
    just the largest of several similar ones, and naming a threshold would
    claim a pattern the curve does not have.
    """
    if len(by_mileage) <= 2:
        return None
    step, i = max(((by_mileage[i]["failure_rate"]
                    - by_mileage[i - 1]["failure_rate"], i)
                   for i in range(1, len(by_mileage))), key=lambda t: t[0])
    if step <= 0.02:
        return None
    return {"after": by_mileage[i]["mileage_band"],
            "rate": by_mileage[i]["failure_rate"], "step": step,
            "top_band": by_mileage[-1]["mileage_band"],
            "top_rate": by_mileage[-1]["failure_rate"]}


# Within this fraction of the all-cars average counts as about average. The
# verdict, the summary and the checklist priorities all read this one value.
AVERAGE_BAND = 0.10


def findings(p: VehicleProfile) -> dict:
    """Layer three — what the statistics mean, decided in code.

    Everything the page states as a judgement comes from here: better or worse
    than average, how far to trust the sample, where the failures concentrate,
    whether mileage has a threshold, where age helps or hurts. The model is
    handed these and asked to interpret them, never to reach them itself — so
    the verdict on the page and the verdict in the AI's words cannot differ.
    """
    out: dict = {}
    if p.failure_rate is None or not p.n_tests:
        return out
    if p.benchmark:
        ratio = p.failure_rate / p.benchmark
        out["classification"] = ("better" if ratio < 1 - AVERAGE_BAND
                                 else "worse" if ratio > 1 + AVERAGE_BAND
                                 else "about")
        out["gap_pp"] = round(round(p.failure_rate * 100, 1)
                              - round(p.benchmark * 100, 1), 1)

    # The 95% margin of error of the rate itself. "Large sample" is otherwise
    # an adjective someone picked; this makes it a statement about how far the
    # figure could move.
    margin = 1.96 * math.sqrt(p.failure_rate * (1 - p.failure_rate)
                              / p.n_tests) * 100
    out["margin_pp"] = margin
    out["sample"] = ("small" if p.is_sparse or margin > 3
                     else "moderate" if margin > 1 else "large")

    if p.repair_areas:
        out["dominant_area"] = p.repair_areas[0]
    step = mileage_step(p.by_mileage)
    if step:
        out["mileage"] = step

    # Age bands compared only where this model has enough tests to say so.
    solid = [a for a in p.age_curve if a["all_cars"] is not None
             and a["n_tests"] >= MIN_TESTS_FOR_CONFIDENCE]
    out["age_better"] = [a["age_band"] for a in solid
                         if round(a["failure_rate"] * 100, 1)
                         < round(a["all_cars"] * 100, 1)]
    out["age_worse"] = [a["age_band"] for a in solid
                        if round(a["failure_rate"] * 100, 1)
                        > round(a["all_cars"] * 100, 1)]

    if p.rank:
        position, total = p.rank
        out["rank_better_than"] = round(100 * (total - position) / total)

    graded = [d for d in p.top_defects if d.get("dangerous_share") is not None]
    if graded:
        out["mostly_dangerous"] = [d["plain_english"] or d["defect"]
                                   for d in graded
                                   if d["dangerous_share"] >= 0.5]
    return out


def build_profile(make: str, model: str, age_band: int) -> VehicleProfile:
    """The single definition of what one vehicle selection contains.

    This object is also the entirety of what the LLM is shown, so anything
    absent here is unreachable by the model.
    """
    t = tables()
    row = (t["age_curve"].filter((pl.col("make") == make)
                                 & (pl.col("model") == model)
                                 & (pl.col("age_band") == age_band)))
    if row.is_empty():
        return VehicleProfile(make=make, model=model, age_years=age_band + 1,
                              age_band=(age_band, age_band + 3), n_tests=0,
                              failure_rate=None)

    defects = (t["top_defects"]
               .filter((pl.col("make") == make) & (pl.col("model") == model)
                       & (pl.col("age_band") == age_band))
               .sort("n_tests", descending=True).head(10))
    # Joined here rather than in the page, so the summary is given the same
    # words the reader sees. These are model-written, but they only exist in
    # the Parquet at all if they passed defect_labels.validate and had the
    # overrides applied — by the time they reach this line they have been
    # checked and read, which is what makes them safe to hand back to a model.
    labels = defect_labels()
    if labels is not None:
        defects = defects.join(labels, on=["defect_category", "defect_desc"],
                               how="left")
    shorts = defect_short_labels()
    if shorts is not None:
        defects = defects.join(shorts, on=["defect_category", "defect_desc"],
                               how="left")
    top = [{"category": r["defect_category"], "defect": r["defect_desc"],
            "n_tests": r["n_tests"], "share_of_tests": r["share_of_tests"],
            "plain_english": r.get("plain_english"),
            "repair_area": r.get("repair_area"), "effort": r.get("effort"),
            "forecourt_check": r.get("forecourt_check"),
            "headline": r.get("headline"), "detail": r.get("detail"),
            # How often this reason was graded Dangerous where it appeared.
            # None on a Parquet exported before the column existed, which the
            # page reads as "not recorded" rather than as zero.
            "dangerous_share": (r["n_dangerous"] / r["n_tests"]
                                if r.get("n_dangerous") is not None
                                and r["n_tests"] else None)}
           for r in defects.to_dicts()]

    miles = [{"mileage_band": mileage_label(r["band"]), "n_tests": r["n_tests"],
              "failure_rate": r["failure_rate"]}
             for r in mileage_curve(make, model).to_dicts()]

    n_tests = int(row["n_tests"][0])
    failure_rate = float(row["failure_rate"][0])

    # This model at every age it was tested, beside all cars at that age —
    # the figures the "as the car ages" chart is drawn from.
    curve = (t["age_curve"]
             .filter((pl.col("make") == make) & (pl.col("model") == model))
             .join(t["benchmark"].select("age_band", all_cars="failure_rate"),
                   on="age_band", how="left")
             .sort("age_band"))
    ages = [{"age_band": f"{r['age_band']}-{r['age_band'] + 3}",
             "n_tests": r["n_tests"], "failure_rate": r["failure_rate"],
             "all_cars": r["all_cars"]} for r in curve.to_dicts()]

    profile = VehicleProfile(
        make=make, model=model, age_years=age_band + 1,
        age_band=(age_band, age_band + 3),
        n_tests=n_tests, failure_rate=failure_rate,
        top_defects=top, by_mileage=miles,
        peers=peers_for(age_band, make, model),
        benchmark=benchmark_for(age_band),
        severity=severity_shares(make, model, age_band, n_tests, failure_rate),
        repair_areas=repair_area_shares(top),
        rank=ranking_position(age_band, make, model),
        age_curve=ages)
    return replace(profile, findings=findings(profile))


def is_sparse(profile: VehicleProfile) -> bool:
    """One threshold, used by the confidence badge, the insight card and the
    note the model is given. They used to be able to disagree."""
    return profile.n_tests < MIN_TESTS_FOR_CONFIDENCE
