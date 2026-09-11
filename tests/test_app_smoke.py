"""Smoke-test the real app over a sample of vehicles.

Run from the repository root:  python -m tests.test_app_smoke

The previous harness re-implemented the app's derivations and therefore only
tested the lines it happened to copy — it computed the mileage values but not
the labels built from them, which is exactly where the null-band crash lived.
This runs app.py itself, so there is nothing to forget to copy.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import polars as pl
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "motintel_app.py"
DATA = ROOT / "data" / "processed"


def vehicles() -> list[tuple[str, str]]:
    age = pl.read_parquet(DATA / "age_curve.parquet")
    rates = pl.read_parquet(DATA / "failure_rates.parquet")
    meta = pl.read_parquet(DATA / "vehicle_meta.parquet")
    sel = (age.group_by("make", "model").agg(n=pl.col("n_tests").sum())
           .join(meta.select("make", "model"), on=["make", "model"]))

    # Deliberately weighted towards the shapes that have broken before rather
    # than a flat random sample: vehicles with no mileage rows at all, and
    # vehicles whose defect codes do not resolve.
    no_miles = (sel.join(rates.select("make", "model").unique(),
                         on=["make", "model"], how="anti")
                .select("make", "model").head(15).rows())
    biggest = sel.sort("n", descending=True).select("make", "model").head(15).rows()
    smallest = sel.sort("n").select("make", "model").head(15).rows()
    rng = random.Random(11)
    rest = rng.sample(sel.select("make", "model").rows(), 110)
    # Vehicles that carried the phantom null mileage band, kept as explicit
    # regressions rather than left to a random draw.
    regressions = [("BMW", "520"), ("NISSAN", "ALMERA"), ("FIAT", "500L"),
                   ("PEUGEOT", "EXPERT"), ("BMW", "525")]
    seen, out = set(), []
    for v in [*regressions, *no_miles, *biggest, *smallest, *rest]:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def bands_for(make: str, model: str) -> list[int]:
    age = pl.read_parquet(DATA / "age_curve.parquet")
    return (age.filter((pl.col("make") == make) & (pl.col("model") == model))
            .sort("age_band")["age_band"].to_list())


# Every page, not just the one the app opens on. The panels below the fold
# were never being rendered: the harness drove the controls and read the
# Overview, so a crash in any other section went unseen. Vehicles are dealt out
# across the pages in blocks, which costs one extra rerun per block rather than
# one per vehicle.
PAGES = ["Overview", "Reliability", "Failure reasons", "Mileage analysis",
         "Comparison"]


def check_ranking(at: AppTest) -> list[tuple[str, str, str]]:
    """The ranking panel's own edge cases, kept explicit rather than left to
    the sample: a car that is in the table, one that is excluded from it for
    thin mileage coverage, and the oldest band, where the table is shortest."""
    failures = []
    at.radio[0].set_value("Reliability").run()
    for make, model, band, note in [("FORD", "FIESTA", 12, "ranked"),
                                    ("AUDI", "R8", 12, "excluded, no coverage"),
                                    ("FORD", "FIESTA", 27, "oldest band")]:
        try:
            at.selectbox[0].set_value(make).run()
            if model not in at.selectbox[1].options:
                failures.append((make, model, f"{note}: not selectable"))
                continue
            at.selectbox[1].set_value(model).run()
            if band in bands_for(make, model) and at.select_slider:
                at.select_slider[0].set_value(band).run()
            if at.exception:
                failures.append((make, model, f"{note}: "
                                 f"{at.exception[0].message.strip().splitlines()[-1]}"))
        except Exception as e:
            failures.append((make, model, f"{note}: {type(e).__name__}: {e}"))
    return failures


def check_defect_panels(at: AppTest) -> list[tuple[str, str, str]]:
    """The panels built on the enrichment, including the branch where it has
    nothing to offer.

    A vehicle whose commonest failures are all invisible from outside a
    workshop is the normal case, not an edge one, and saying so is the point —
    so it is driven explicitly rather than left to the sample.
    """
    from motintel import serving
    failures = []
    at.radio[0].set_value("Failure reasons").run()
    labelled = serving.defect_labels() is not None

    for make, model in [("FORD", "FIESTA"), ("MAZDA", "MX-5")]:
        for band in bands_for(make, model):
            try:
                at.selectbox[0].set_value(make).run()
                if model not in at.selectbox[1].options:
                    break
                at.selectbox[1].set_value(model).run()
                if at.select_slider:
                    at.select_slider[0].set_value(band).run()
                if at.exception:
                    failures.append((make, model, f"band {band}: "
                                     f"{at.exception[0].message.strip().splitlines()[-1]}"))
                    break
            except Exception as e:
                failures.append((make, model, f"band {band}: "
                                 f"{type(e).__name__}: {e}"))
                break

    # Whichever way the enrichment has gone, the page has to say something
    # honest rather than render an empty card.
    page = " ".join(m.value for m in at.markdown)
    if labelled:
        wanted = ("Where the failures are", "What to check when viewing")
    else:
        wanted = ("Where the failures are", "python -m motintel.enrich")
    for phrase in wanted:
        if phrase not in page:
            failures.append(("MAZDA", "MX-5", f"page is missing {phrase!r}"))
    return failures


def run() -> int:
    cases = vehicles()
    print(f"running the real app against {len(cases)} vehicles "
          f"across {len(PAGES)} pages")
    failures = []
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.run()
    if at.exception:
        print(f"  cold start raised: {at.exception[0].message}")
        return 1

    block = max(1, len(cases) // len(PAGES))
    for i, (make, model) in enumerate(cases):
        page = PAGES[min(i // block, len(PAGES) - 1)]
        if at.radio[0].value != page:
            at.radio[0].set_value(page).run()
        try:
            at.selectbox[0].set_value(make).run()
            opts = at.selectbox[1].options
            if model not in opts:
                failures.append((make, model, "model missing from dropdown"))
                continue
            at.selectbox[1].set_value(model).run()
            if at.exception:
                failures.append((make, model, at.exception[0].message.strip()
                                 .splitlines()[-1]))
                continue
            # Exercise every age band the control offers, not just the
            # default. The bands come from the data rather than from
            # widget.options, because a select_slider reports its options
            # already run through format_func — set_value needs the
            # underlying value.
            bands = bands_for(make, model)
            # A vehicle tested at only one age renders a caption, not a
            # slider, so there is no widget to drive.
            if len(bands) < 2 or not at.select_slider:
                continue
            for opt in bands:
                at.select_slider[0].set_value(opt).run()
                if at.exception:
                    failures.append((make, model, f"band {opt}: "
                                     f"{at.exception[0].message.strip().splitlines()[-1]}"))
                    break
        except Exception as e:  # harness-level problem, still a failure
            failures.append((make, model, f"{type(e).__name__}: {e}"))

    failures += check_ranking(at)
    failures += check_defect_panels(at)

    print(f"\nfailures: {len(failures)}")
    for f in failures[:15]:
        print(f"  {f[0]} {f[1]}: {f[2]}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
