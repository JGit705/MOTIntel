"""Phase 3 + the Phase 5 grounding test.

Two vehicles are summarised: one with plenty of data, and one deliberately
sparse. The sparse case is the real test — if the model invents plausible
failure modes for a vehicle it has almost no rows for, the AI layer is not
trustworthy and nothing else in the app matters.

It builds its profile through motintel.serving, the same call the app makes,
so what is tested here is what a reader actually gets.
"""
from __future__ import annotations

import json

from dotenv import load_dotenv

from motintel import llm, serving
from motintel.config import ROOT

load_dotenv(ROOT / ".env")


def show(make: str, model: str, band: int, label: str) -> None:
    print("=" * 70)
    print(f"{label}: {make} {model}, {band}-{band + 3} years old")
    print("=" * 70)
    profile = serving.build_profile(make, model, band)
    print("\n--- DATA BLOCK GIVEN TO THE MODEL (its entire world) ---")
    print(llm.render_data_block(profile))
    print("\n--- INTERPRETATION ---")
    insight = llm.interpret(profile)
    print(json.dumps(insight, indent=2) if insight
          else "[unavailable, or the answer failed its checks — app would "
               "degrade]")
    print()


def sparsest_vehicle() -> tuple[str, str, int] | None:
    """The thinnest group the app will actually let someone select."""
    import polars as pl
    age = serving.tables()["age_curve"]
    sel = serving.selectable_vehicles().select("make", "model")
    thin = (age.join(sel, on=["make", "model"])
            .filter(pl.col("n_tests") < serving.MIN_TESTS_FOR_CONFIDENCE)
            .sort("n_tests"))
    if thin.is_empty():
        return None
    r = thin.row(0, named=True)
    return r["make"], r["model"], r["age_band"]


if __name__ == "__main__":
    show("FORD", "FIESTA", 6, "WELL-COVERED VEHICLE")
    sparse = sparsest_vehicle()
    if sparse:
        show(*sparse[:2], sparse[2], "SPARSE VEHICLE — grounding test")
