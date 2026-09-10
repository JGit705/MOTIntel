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
           "mileage_label", "benchmark_for", "peers_for", "is_sparse"]

# Everything past this is pooled, so the tail does not become a row of
# single-test noise.
MILEAGE_CAP = 160_000
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
