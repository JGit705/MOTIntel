"""Phase 4 of AI_PLAN — measuring the AI layer instead of asserting it.

"Grounded by design" is an architecture claim. This turns it into a number:
how many figures across how many summaries could not be traced back to the data
the model was given, how often a vehicle too thin to describe was declined, and
how often one that was well covered was declined anyway.

That last one matters more than it looks. A model that declines everything
scores perfectly on grounding and is worthless, so refusing when it should not
is counted as its own failure. A single number that can be maximised by
answering nothing is not a measurement.

**This spends no quota by default.** The free tier allows twenty requests a
day, and a forty-profile evaluation that generated as it went would cost two
days of it and could not run twice. So it reads what llm.py has already cached
and reports how much of the set that covered. To fill gaps, give it a budget:

    MOTINTEL_EVAL_BUDGET=10 python -m tests.test_llm_eval

Run from the repository root:  python -m tests.test_llm_eval
"""
from __future__ import annotations

import json
import os
import sys

import polars as pl
from dotenv import load_dotenv

from motintel import grounding, llm, serving
from motintel.config import PROCESSED, ROOT
from motintel.queries import MIN_TESTS_FOR_CONFIDENCE

load_dotenv(ROOT / ".env")

REPORT = PROCESSED / "llm_eval.json"
BUDGET = int(os.environ.get("MOTINTEL_EVAL_BUDGET", "0"))

# Fixed sizes so the number the README quotes always refers to the same set.
N_DENSE, N_MID, N_SPARSE = 15, 15, 10
# Above this a vehicle is well enough covered that declining to describe it is
# a fault rather than caution.
DENSE_TESTS = 2_000


def eval_profiles() -> list[tuple[str, str, int, str]]:
    """A fixed, deterministic spread of vehicles: well covered, middling, and
    too thin to describe.

    Deterministic matters more than random here. The set has to be the same on
    every run or the cache never covers it and the measurement is not
    comparable with the last one.
    """
    age = serving.tables()["age_curve"]
    selectable = serving.selectable_vehicles().select("make", "model")
    rows = (age.join(selectable, on=["make", "model"])
            .sort(["n_tests", "make", "model", "age_band"],
                  descending=[True, False, False, False]))

    dense = rows.head(N_DENSE)
    thin = rows.filter(pl.col("n_tests") < MIN_TESTS_FOR_CONFIDENCE)
    covered = rows.filter(pl.col("n_tests") >= MIN_TESTS_FOR_CONFIDENCE)
    middle = covered.slice(max(0, len(covered) // 2 - N_MID // 2), N_MID)
    sparse = thin.sort(["n_tests", "make", "model", "age_band"]).head(N_SPARSE)

    out = []
    for band, frame in (("dense", dense), ("mid", middle),
                        ("sparse", sparse)):
        for r in frame.iter_rows(named=True):
            out.append((r["make"], r["model"], r["age_band"], band))
    return out


def band_for(n_tests: int) -> str:
    """Which part of the spread a vehicle sits in.

    Read off the test count rather than carried alongside it, so a summary
    found in the cache can be classified without rebuilding its profile.
    """
    if n_tests < MIN_TESTS_FOR_CONFIDENCE:
        return "sparse"
    return "dense" if n_tests >= DENSE_TESTS else "mid"


def cached_cases() -> list[dict]:
    """Every summary llm.py has already paid for.

    Each cache entry stores the data block the model was actually given, so
    grounding is checked against what it saw rather than against a block
    re-rendered later — which is both more faithful and free. These are the
    measurement; the fixed set below is what the measurement is aiming to
    cover.
    """
    if not llm.CACHE_DIR.exists():
        return []
    cases, quarantined = [], 0
    for path in sorted(llm.CACHE_DIR.glob("*.json")):
        try:
            entry = json.loads(path.read_text())
            vehicle = entry["vehicle"]
            # Measure what a reader can actually be shown. An entry llm.py now
            # refuses to serve is reported rather than scored, so a cache full
            # of half-finished answers cannot look like a clean result.
            if not llm._usable(entry["summary"]):
                quarantined += 1
                continue
            cases.append({
                "vehicle": f'{vehicle["make"]} {vehicle["model"]}',
                "age_years": vehicle.get("age_years"),
                "n_tests": vehicle["n_tests"],
                "band": band_for(vehicle["n_tests"]),
                "summary": entry["summary"],
                "data_block": entry["data_block"],
                "source": "cache"})
        except (KeyError, json.JSONDecodeError):
            # An entry written by an older layout is not a test failure.
            continue
    if quarantined:
        print(f"  {quarantined} cache entr(ies) are cut off mid-thought and "
              f"no longer served; they regenerate when next asked for")
    return cases


def assess_text(summary: str, data_block: str, n_tests: int,
                band: str) -> list[str]:
    """Everything wrong with one summary, given the block it was written from."""
    problems = []
    invented = grounding.ungrounded(summary, data_block)
    if invented:
        problems.append(f"figures not in the data block: {invented[:4]}")
    claims = grounding.out_of_scope_claims(summary)
    if claims:
        problems.append(f"volunteered {', '.join(claims)}")
    if grounding.truncated(summary):
        problems.append(f"stops mid-thought: ...{summary.rstrip()[-45:]!r}")
    declined = grounding.refused(summary)
    if band == "sparse" and not declined:
        problems.append(f"described a {n_tests}-test vehicle instead of "
                        f"declining")
    if band == "dense" and declined:
        # Refusing where the data is ample is the failure a grounding-only
        # score would reward.
        problems.append(f"declined a {n_tests:,}-test vehicle")
    return problems


# Fixed cases for the rules themselves, so they are covered whatever happens
# to be in the cache. Each one is a mistake the harness made before it was
# fixed, or one it must never start making.
BLOCK = ("Tests in this make/model/age group: 265,905\n"
         "Failure rate for this group: 42.0%\n"
         "  - Suspension: ball joint excessively worn — 26,375 tests (9.9%)\n"
         "  - 0k-20k miles: 9.8% of 48,606 tests\n"
         "  - 120k-500k miles: 51.3% of 141,005 tests")

GROUNDED = [
    ("42% of 265,905 tests failed.", "a rate quoted to fewer decimals"),
    ("42.0% failed, 9.9% on ball joints.", "quoted exactly"),
    ("Over 265,000 tests were recorded.", "coarsened to the nearest thousand"),
    ("Under 20,000 miles the rate is 9.8%.", "the 20k band, spelled out"),
    ("By 120,000 miles it reaches 51.3%.", "the 120k band, spelled out"),
    ("Around 10% of tests under 20,000 miles fail.",
     "9.8% rounded to a whole number"),
    ("Roughly 51% by the highest mileage band.", "51.3% to no decimals"),
]
UNGROUNDED = [
    ("The failure rate is 37%.", "a rate that is simply not there"),
    ("About 42.3% of tests failed.", "a near miss on a real figure"),
    ("Some 300,000 tests were recorded.", "rounded past what the data says"),
]
REFUSALS = [
    ("There is not enough data on this vehicle to say anything.", True),
    ("Only 30 tests were recorded, too few to draw a conclusion.", True),
    ("With 12 tests, no meaningful conclusion can be drawn.", True),
    ("Tread depth below the 1.6mm limit and insufficient washer liquid.",
     False),
    ("26.5% failed, mostly non-working lamps and worn ball joints.", False),
]


def check_rules() -> list[str]:
    """The grounding rules, on fixed text.

    The harness reads whatever is cached, so a rule with no example in the
    cache is a rule nobody is testing — removing the rounding tolerance broke
    nothing until these were added.
    """
    fails = []
    for text, why in GROUNDED:
        found = grounding.ungrounded(text, BLOCK)
        if found:
            fails.append(f"{why}: called {found} invented — {text!r}")
    for text, why in UNGROUNDED:
        if not grounding.ungrounded(text, BLOCK):
            fails.append(f"{why}: accepted — {text!r}")
    for text, expected in REFUSALS:
        if grounding.refused(text) is not expected:
            fails.append(f"refusal detection: expected {expected} for "
                         f"{text!r}")
    if not grounding.truncated("It failed 26.5% of the time."):
        pass
    else:
        fails.append("a finished sentence was called truncated")
    if not grounding.truncated("Across all age groups, failure rates"):
        fails.append("a sentence stopping mid-thought was not noticed")
    for text, wanted in [("It is an expensive repair.", "repair expense"),
                         ("This model was subject to a recall.", "a recall"),
                         ("A practical hatchback.", "a body type"),
                         ("Repairs cost about £200.", "a price"),
                         ("A ball joint is worn on 9.9% of tests.", None)]:
        claims = grounding.out_of_scope_claims(text)
        if wanted and wanted not in claims:
            fails.append(f"out-of-scope: missed {wanted} in {text!r}")
        if wanted is None and claims:
            fails.append(f"out-of-scope: false alarm {claims} on {text!r}")
    return fails


def run() -> int:
    rule_failures = check_rules()
    print(f"grounding rules: "
          f"{'OK' if not rule_failures else f'{len(rule_failures)} failed'}")
    for f in rule_failures:
        print(f"  {f}")

    if not (PROCESSED / "age_curve.parquet").exists():
        print("  skipped: no serving data in this checkout")
        return 1 if rule_failures else 0

    cases = cached_cases()
    print(f"{len(cases)} summary/summaries already cached")

    # Top up towards the fixed set, but only with a budget the caller asked
    # for. Forty generations would be two days of the free tier's allowance.
    wanted = eval_profiles()
    spent = 0
    if BUDGET:
        for make, model, age_band, band in wanted:
            if spent >= BUDGET:
                break
            profile = serving.build_profile(make, model, age_band)
            if llm.cached_summary(profile) is not None:
                continue
            summary = llm.summarise(profile)
            spent += 1
            if summary is None:
                print(f"  generation stopped at {make} {model} — API "
                      f"unavailable")
                break
            cases.append({"vehicle": f"{make} {model}",
                          "age_years": age_band + 1,
                          "n_tests": profile.n_tests,
                          "band": band_for(profile.n_tests),
                          "summary": summary,
                          "data_block": llm.render_data_block(profile),
                          "source": "generated"})
        print(f"generated {spent} of a {BUDGET}-request budget")
    else:
        print("generation budget 0 — reading the cache only "
              "(MOTINTEL_EVAL_BUDGET=10 to top up)")

    if not cases:
        print("\n  nothing cached, so nothing measured. Browse a few vehicles "
              "in the app, or set MOTINTEL_EVAL_BUDGET.")
        return 1 if rule_failures else 0

    failures = list(rule_failures)
    for case in cases:
        case["problems"] = assess_text(case["summary"], case["data_block"],
                                       case["n_tests"], case["band"])
        case["figures"] = len(grounding.numbers_in(case["summary"]))
        failures += [f'{case["vehicle"]}: {p}' for p in case["problems"]]

    def counted(band: str, needle: str) -> tuple[int, int]:
        rows = [c for c in cases if c["band"] == band]
        good = sum(1 for c in rows
                   if not any(needle in p for p in c["problems"]))
        return good, len(rows)

    figures = sum(c["figures"] for c in cases)
    ungrounded = sum(1 for c in cases
                     for p in c["problems"] if p.startswith("figures"))
    cut_off = sum(1 for c in cases
                  for p in c["problems"] if p.startswith("stops mid-thought"))
    scope = sum(1 for c in cases
                for p in c["problems"] if p.startswith("volunteered"))
    declined, sparse_total = counted("sparse", "instead of declining")
    answered, dense_total = counted("dense", "declined a")

    covered = sum(1 for make, model, age_band, _ in wanted
                  if llm.cached_summary(
                      serving.build_profile(make, model, age_band)) is not None)
    report = {
        "summaries_measured": len(cases),
        "figures_checked": figures,
        "summaries_with_an_unsupported_figure": ungrounded,
        "summaries_volunteering_out_of_scope_claims": scope,
        "summaries_cut_off": cut_off,
        "sparse_declined": declined, "sparse_total": sparse_total,
        "dense_answered": answered, "dense_total": dense_total,
        "fixed_set_size": len(wanted), "fixed_set_covered": covered,
        "requests_spent": spent,
        "cases": [{k: v for k, v in c.items() if k != "data_block"}
                  for c in cases],
    }
    REPORT.write_text(json.dumps(report, indent=2))

    print(f"\n  {figures} figures across {len(cases)} summaries; "
          f"{ungrounded} summary/summaries carry one that is not in the data "
          f"block it was given")
    print(f"  out-of-scope claims (prices, recalls, body types, buying "
          f"advice): {scope}")
    print(f"  summaries cut off mid-thought: {cut_off}")
    print(f"  sparse vehicles declined: {declined}/{sparse_total}")
    print(f"  well-covered vehicles answered rather than declined: "
          f"{answered}/{dense_total}")
    print(f"  fixed comparison set covered: {covered}/{len(wanted)}")
    print(f"  report -> {REPORT.name}")

    print(f"\nfailures: {len(failures)}")
    for f in failures[:15]:
        print(f"  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    print("LLM evaluation")
    sys.exit(run())
