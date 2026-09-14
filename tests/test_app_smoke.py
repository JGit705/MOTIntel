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

from motintel.ui import names

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


def pick(at: AppTest, make: str, model: str) -> bool:
    """Choose a car through the search box, the way a reader would. False if
    the box does not offer it — which is a failure in its own right, since
    the box is the only way to reach a car."""
    box = at.selectbox(key="search")
    if names.display_name(make, model) not in box.options:
        return False
    box.set_value((make, model)).run()
    return at.session_state["car"] == (make, model)


# The app is one page now, and every panel renders on it: Streamlit runs an
# expander's body whether or not it is open, so a collapsed section is still
# executed. Each vehicle therefore exercises all twelve panels rather than the
# fifth of them that happened to be on the page it was dealt — the harness got
# stricter by the app getting simpler, and there are no page blocks to deal
# out any more.


def check_ranking(at: AppTest) -> list[tuple[str, str, str]]:
    """The ranking panel's own edge cases, kept explicit rather than left to
    the sample: a car that is in the table, one that is excluded from it for
    thin mileage coverage, and the oldest band, where the table is shortest."""
    failures = []
    for make, model, band, note in [("FORD", "FIESTA", 12, "ranked"),
                                    ("AUDI", "R8", 12, "excluded, no coverage"),
                                    ("FORD", "FIESTA", 27, "oldest band")]:
        try:
            if not pick(at, make, model):
                failures.append((make, model, f"{note}: not selectable"))
                continue
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
    labelled = serving.defect_labels() is not None

    for make, model in [("FORD", "FIESTA"), ("MAZDA", "MX-5")]:
        for band in bands_for(make, model):
            try:
                if not pick(at, make, model):
                    break
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
    # honest rather than render an empty card. A panel's title is its
    # expander's label now rather than a markdown card header, so both streams
    # are read — checking only the markdown would have looked like the section
    # had vanished.
    page = " ".join([m.value for m in at.markdown]
                    + [e.label for e in at.get("expander")])
    # With the enrichment, the panels built on it have to be there.
    if labelled:
        for phrase in ("Where the failures are", "What to check before buying"):
            if phrase not in page:
                failures.append(("MAZDA", "MX-5",
                                 f"page is missing {phrase!r}"))
    # Whichever way it has gone, a reader is never handed a developer's
    # command. The page used to tell them to run the enrichment themselves.
    if "python -m motintel.enrich" in page:
        failures.append(("MAZDA", "MX-5", "page shows a developer command"))
    return failures


# The questions the page is organised around, each a section heading with a
# jump link pointing at it. One going missing is a question the page no longer
# answers, and a jump link to nowhere.
QUESTIONS = ["Is it reliable?", "What usually goes wrong?",
             "Does age or mileage matter?", "How does it compare?",
             "What should I do?"]
# The only detail left behind a click.
EXPANDERS = ["Every model ranked at this age", "Data & methodology"]
# Words the page has stopped using. "Failure probability" reads as a
# prediction for one car; "this car" claims the data is about the car being
# viewed rather than about its model; the command belongs to a developer.
BANNED = ["probability", "This car ", "this car ", "python -m motintel"]


def check_sections(at: AppTest) -> list[tuple[str, str, str]]:
    """Every question once, every remaining expander once, and none of the
    retired wording anywhere on the page."""
    page = " ".join(m.value for m in at.markdown)
    labels = [e.label for e in at.get("expander")]
    failures = []
    for question in QUESTIONS:
        hits = page.count(f">{question}<")
        if hits != 1:
            failures.append(("", "", f"question {question!r} appears {hits} "
                                     f"times"))
    for wanted in EXPANDERS:
        hits = sum(1 for got in labels if wanted in got)
        if hits != 1:
            failures.append(("", "", f"expander {wanted!r} appears {hits} "
                                     f"times"))
    for anchor in ("overview", "failures", "age-mileage", "compare", "checks",
                   "method"):
        if f'id="{anchor}"' not in page or f'href="#{anchor}"' not in page:
            failures.append(("", "", f"jump link #{anchor} has no target"))
    for phrase in BANNED:
        if phrase in page:
            failures.append(("", "", f"page still says {phrase!r}"))
    return failures


def check_compare(at: AppTest) -> list[tuple[str, str, str]]:
    """The comparison panel: one car beside it, three at once, a car never
    tested at the page car's age, and the panel going away again when the
    comparison is cleared."""
    failures = []
    for (make, model), others, band, note in [
            (("FORD", "FIESTA"), [("VAUXHALL", "CORSA")], 12, "one car"),
            (("FORD", "FIESTA"), [("VAUXHALL", "CORSA"),
                                  ("VOLKSWAGEN", "GOLF"), ("TOYOTA", "YARIS")],
             12, "three cars"),
            (("FORD", "FIESTA"), [("TESLA", "MODEL 3 LONG RANGE AWD")], 12,
             "car not tested at this age")]:
        try:
            if not pick(at, make, model):
                failures.append((make, model, f"{note}: not selectable"))
                continue
            if band in bands_for(make, model) and at.select_slider:
                at.select_slider[0].set_value(band).run()
            box = at.multiselect(key="vs")
            absent = [o for o in others
                      if names.display_name(*o) not in box.options]
            if absent:
                failures.append((make, model, f"{note}: {absent} not offered"))
                continue
            box.set_value(others).run()
            if at.exception:
                failures.append((make, model, f"{note}: "
                                 f"{at.exception[0].message.strip().splitlines()[-1]}"))
                continue
            header = (f"{names.display_name(make, model)} vs "
                      + ", ".join(names.display_name(*o) for o in others))
            if not any(header in m.value for m in at.markdown):
                failures.append((make, model, f"{note}: no comparison panel"))
        except Exception as e:
            failures.append((make, model, f"{note}: {type(e).__name__}: {e}"))

    try:
        at.multiselect(key="vs").set_value([]).run()
        if any(" vs " in m.value and "card-h" in m.value for m in at.markdown):
            failures.append(("", "", "comparison still shown once cleared"))
    except Exception as e:
        failures.append(("", "", f"clearing the comparison: {e}"))
    return failures


def check_links() -> list[tuple[str, str, str]]:
    """A shared link opens on what it names; links in the shapes they took
    before — a make on its own, a single vs_make/vs_model pair — still open
    somewhere sensible; and a link naming nothing real falls back to the
    default rather than failing."""
    failures = []
    for params, car, age, vs in [
            ({"make": "TOYOTA", "model": "YARIS", "age": "6",
              "vs": "FORD|FIESTA"},
             ("TOYOTA", "YARIS"), 6, [("FORD", "FIESTA")]),
            ({"make": "TOYOTA", "vs_make": "FORD", "vs_model": "FOCUS"},
             ("TOYOTA", "YARIS"), None, [("FORD", "FOCUS")]),
            ({"make": "NOPE", "model": "NOPE", "age": "x", "vs": "NOPE|NOPE"},
             ("FORD", "FIESTA"), None, [])]:
        try:
            at = AppTest.from_file(str(APP), default_timeout=90)
            for key, value in params.items():
                at.query_params[key] = value
            at.run()
            got = (at.session_state["car"],
                   at.select_slider[0].value if age is not None else None,
                   at.multiselect(key="vs").value)
            if at.exception or got != (car, age, vs):
                failures.append(("", "", f"link {params}: opened on {got}"))
        except Exception as e:
            failures.append(("", "", f"link {params}: {type(e).__name__}: {e}"))
    return failures


def run() -> int:
    cases = vehicles()
    print(f"running the real app against {len(cases)} vehicles, "
          f"every panel on each")
    failures = []
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.run()
    if at.exception:
        print(f"  cold start raised: {at.exception[0].message}")
        return 1

    for make, model in cases:
        try:
            if not pick(at, make, model):
                failures.append((make, model, "missing from the car search"))
                continue
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

    failures += check_sections(at)
    failures += check_ranking(at)
    failures += check_defect_panels(at)
    failures += check_compare(at)
    failures += check_links()

    print(f"\nfailures: {len(failures)}")
    for f in failures[:15]:
        print(f"  {f[0]} {f[1]}: {f[2]}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
