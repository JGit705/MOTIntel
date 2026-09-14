"""Grounding checks for the AI interpretation layer.

Three properties are worth defending automatically, because each fails silently
and each destroys the credibility of the whole feature:

  1. On a vehicle with almost no tests the model must decline, not invent
     plausible-sounding failure modes.
  2. Every number in an answer must appear in the data block it was given.
  3. An answer that breaks either rule never reaches the page — which is what
     llm.problems() is for, so its checks are tested here with answers built to
     fail them.

Checks that need the API are skipped without GEMINI_API_KEY, and checks that
need the exported serving data are skipped without it, so the suite still runs
in CI, where neither exists. The prompt checks always run.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

from dotenv import load_dotenv

from motintel import grounding, llm, serving
from motintel.config import PROCESSED, ROOT

load_dotenv(ROOT / ".env")

WELL_COVERED = ("FORD", "FIESTA", 6)
HAVE_DATA = (PROCESSED / "age_curve.parquet").exists()


def _insight(**changes) -> dict:
    """A well-formed, grounded answer, with any field replaced."""
    base = {"declined": False,
            "verdict": {"headline": "Better than average, but mileage matters",
                        "summary": "This model fails less often than other "
                                   "cars of its age."},
            "concern": {"level": "low", "answer": "Not particularly",
                        "reason": "Its failure rate is below the average."},
            "key_points": [{"kind": "positive", "title": "Below the average",
                            "text": "It fails less often than all cars of its "
                                    "age."}],
            "main_concern": "Failures rise with mileage.",
            "main_positive": "A large sample stands behind the figures.",
            "buying_advice": "Check the tyres and lights when viewing one.",
            "age_mileage": "The failure rate climbs as mileage rises."}
    base.update(changes)
    return base


def offline_checks() -> list[str]:
    """Properties of the prompt, the retrieval and the answer checks that need
    no API call."""
    fails = []
    system = llm.SYSTEM.lower()
    for phrase in ("only", "not enough", "do not add", "this model"):
        if phrase not in system:
            fails.append(f"system prompt no longer says {phrase!r}")

    # Everything below reads a real profile, and the serving data is gitignored.
    if not HAVE_DATA:
        print("  skipped the retrieval and answer checks: no serving data in "
              "this checkout")
        return fails

    profile = serving.build_profile(*WELL_COVERED)
    block = llm.render_data_block(profile)
    if str(profile.n_tests) not in block.replace(",", ""):
        fails.append("data block omits the test count it is meant to carry")
    # The cache key must move when a generation setting moves, or a changed
    # setting silently serves the previous answer.
    before = llm._cache_key(profile)
    original, llm.THINKING_LEVEL = llm.THINKING_LEVEL, "low"
    after = llm._cache_key(profile)
    llm.THINKING_LEVEL = original
    if before == after:
        fails.append("cache key ignores the thinking level")

    # The model interprets findings the application calculated, so the block
    # has to carry them — and everything the page's sections show.
    wanted = ["Findings calculated by the application", "Sample size",
              "Average failure rate for all cars", "How serious the failures",
              "Failure rate by age", "Failure rate by odometer band"]
    if serving.defect_labels() is not None:
        wanted += ["Where those failure reasons fall", "in plain English"]
    for needle in wanted:
        if needle not in block:
            fails.append(f"data block no longer carries {needle!r}")

    # problems() is the gate between the model and the page. Each answer here
    # is built to break exactly one rule.
    rate = f"{profile.failure_rate:.1%}"
    grounded = _insight(verdict={"headline": "Better than average",
                                 "summary": f"It fails {rate} of tests."})
    if llm.problems(grounded, profile, block):
        fails.append(f"a grounded answer was rejected: "
                     f"{llm.problems(grounded, profile, block)}")
    cases = {
        "an invented figure": _insight(main_concern="Failures reach 97.3% at "
                                                    "high mileage."),
        "buying advice": _insight(buying_advice="You should buy one."),
        "a price": _insight(main_concern="Repairs cost about £400."),
        "declining a well-covered vehicle": _insight(declined=True),
    }
    for why, bad in cases.items():
        if not llm.problems(bad, profile, block):
            fails.append(f"problems() let through {why}")
    sparse_profile = replace(profile, n_tests=12)
    if not llm.problems(_insight(), sparse_profile, block):
        fails.append("problems() let a sparse vehicle be described")

    text = llm.insight_text(_insight())
    if grounding.truncated(text):
        fails.append("insight_text() does not end on a full sentence")
    if "Not particularly" not in text or "Check the tyres" not in text:
        fails.append("insight_text() drops fields the checks must read")
    return fails


def live_checks() -> list[str]:
    """Properties that need a real generation."""
    fails = []

    import polars as pl
    age = serving.tables()["age_curve"]
    sel = serving.selectable_vehicles().select("make", "model")
    thin = (age.join(sel, on=["make", "model"])
            .filter(pl.col("n_tests") < serving.MIN_TESTS_FOR_CONFIDENCE)
            .sort("n_tests"))
    if not thin.is_empty():
        r = thin.row(0, named=True)
        profile = serving.build_profile(r["make"], r["model"], r["age_band"])
        insight = llm.interpret(profile, use_cache=False)
        if insight is None:
            print("  live: no answer for the sparse vehicle (outage, quota, or "
                  "it failed its checks) — see the log")
        elif not (insight.get("declined")
                  and grounding.refused(llm.insight_text(insight))):
            fails.append(f"sparse vehicle drew a conclusion: "
                         f"{llm.insight_text(insight)[:160]}")
        else:
            print(f"  live: sparse refusal OK ({r['make']} {r['model']}, "
                  f"{profile.n_tests} tests)")

    profile = serving.build_profile(*WELL_COVERED)
    insight = llm.interpret(profile, use_cache=False)
    if insight is None:
        print("  live: no answer for the well-covered vehicle (outage, quota, "
              "or it failed its checks) — see the log")
        return fails
    # interpret() already ran problems(); checked again here so a regression in
    # that gate cannot pass silently.
    faults = llm.problems(insight, profile, llm.render_data_block(profile))
    if faults:
        fails.append(f"a checked answer has problems: {faults}")
    else:
        print(f"  live: \"{insight['verdict']['headline']}\" — every figure "
              f"traces to the data block")
    return fails


def run() -> int:
    print("offline grounding checks")
    fails = offline_checks()
    for f in fails:
        print(f"  FAIL: {f}")
    if not fails:
        print("  prompt, retrieval, cache key and answer checks all intact"
              if HAVE_DATA else "  prompt checks intact")

    if not HAVE_DATA:
        print("\nlive grounding checks skipped — no serving data")
    elif os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        print("\nlive grounding checks")
        live = live_checks()
        for f in live:
            print(f"  FAIL: {f}")
        fails += live
    else:
        print("\nlive grounding checks skipped — no GEMINI_API_KEY")

    print(f"\nfailures: {len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(run())
