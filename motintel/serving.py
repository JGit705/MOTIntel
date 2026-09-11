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

from functools import lru_cache

import polars as pl

from motintel.config import PROCESSED
from motintel.queries import MIN_TESTS_FOR_CONFIDENCE, VehicleProfile

__all__ = ["MIN_TESTS_FOR_CONFIDENCE", "build_profile", "tables",
           "selectable_vehicles", "age_bands", "mileage_curve",
           "mileage_label", "benchmark_for", "peers_for",
           "reliability_ranking", "RANKING_COLUMNS", "is_sparse"]

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


@lru_cache(maxsize=1)
def tables() -> dict[str, pl.DataFrame]:
    missing = [t for t in TABLES if not (PROCESSED / f"{t}.parquet").exists()]
    if missing:
        raise FileNotFoundError(
            f"missing serving data: {', '.join(missing)}. Run export first.")
    return {t: pl.read_parquet(PROCESSED / f"{t}.parquet") for t in TABLES}


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

    top = (t["top_defects"]
           .filter((pl.col("make") == make) & (pl.col("model") == model)
                   & (pl.col("age_band") == age_band))
           .sort("n_tests", descending=True).head(10)
           .select(category="defect_category", defect="defect_desc",
                   n_tests="n_tests", share_of_tests="share_of_tests")
           .to_dicts())

    miles = [{"mileage_band": mileage_label(r["band"]), "n_tests": r["n_tests"],
              "failure_rate": r["failure_rate"]}
             for r in mileage_curve(make, model).to_dicts()]

    return VehicleProfile(
        make=make, model=model, age_years=age_band + 1,
        age_band=(age_band, age_band + 3),
        n_tests=int(row["n_tests"][0]),
        failure_rate=float(row["failure_rate"][0]),
        top_defects=top, by_mileage=miles,
        peers=peers_for(age_band, make, model))


def is_sparse(profile: VehicleProfile) -> bool:
    """One threshold, used by the confidence badge, the insight card and the
    note the model is given. They used to be able to disagree."""
    return profile.n_tests < MIN_TESTS_FOR_CONFIDENCE
