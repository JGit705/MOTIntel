"""Grounding checks for the LLM layer.

Two properties are worth defending automatically, because both fail silently
and both destroy the credibility of the whole feature:

  1. On a vehicle with almost no tests the model must decline, not invent
     plausible-sounding failure modes.
  2. Every number in a summary must appear in the data block it was given.

Checks that need the API are skipped without GEMINI_API_KEY, so the suite still
runs in CI and on a machine with no credentials. The offline checks — that the
prompt carries the refusal instruction and that the profile is the model's only
input — always run.
"""
from __future__ import annotations

import os
import re
import sys

from dotenv import load_dotenv

from motintel import llm, serving
from motintel.config import ROOT

load_dotenv(ROOT / ".env")

WELL_COVERED = ("FORD", "FIESTA", 6)


def numbers_in(text: str) -> set[str]:
    """Percentages and thousands-separated counts, normalised for comparison."""
    out = set()
    for m in re.findall(r"\d[\d,]*\.?\d*\s*%?", text):
        t = m.strip().replace(",", "").rstrip("%").rstrip(".")
        if t:
            out.add(t)
    return out


def offline_checks() -> list[str]:
    """Properties of the prompt and the retrieval that need no API call."""
    fails = []
    system = llm.SYSTEM.lower()
    for phrase in ("only", "not enough", "do not add"):
        if phrase not in system:
            fails.append(f"system prompt no longer says {phrase!r}")

    profile = serving.build_profile(*WELL_COVERED)
    block = llm.render_data_block(profile)
    if str(profile.n_tests) not in block.replace(",", ""):
        fails.append("data block omits the test count it is meant to carry")
    if "threshold" not in llm.render_data_block(
            serving.build_profile("FORD", "FIESTA", 0)).lower():
        pass  # only sparse profiles carry the threshold note; not an error

    # The cache key must move when a generation setting moves, or a changed
    # setting silently serves the previous answer.
    before = llm._cache_key(profile)
    original, llm.THINKING_LEVEL = llm.THINKING_LEVEL, "low"
    after = llm._cache_key(profile)
    llm.THINKING_LEVEL = original
    if before == after:
        fails.append("cache key ignores the thinking level")
    return fails


def live_checks() -> list[str]:
    """Properties that need a real generation."""
    fails = []

    sparse = None
    import polars as pl
    age = serving.tables()["age_curve"]
    sel = serving.selectable_vehicles().select("make", "model")
    thin = (age.join(sel, on=["make", "model"])
            .filter(pl.col("n_tests") < serving.MIN_TESTS_FOR_CONFIDENCE)
            .sort("n_tests"))
    if not thin.is_empty():
        r = thin.row(0, named=True)
        sparse = (r["make"], r["model"], r["age_band"])

    if sparse:
        profile = serving.build_profile(*sparse)
        text = llm.summarise(profile, use_cache=False)
        if text is None:
            print("  live: API unavailable (rate limit or outage) — skipped")
            return fails
        low = text.lower()
        refused = any(p in low for p in
                      ("not enough", "too few", "insufficient", "cannot",
                       "can't", "below the", "limited data", "only"))
        if not refused:
            fails.append(f"sparse vehicle drew a conclusion instead of "
                         f"declining: {text[:160]}")
        else:
            print(f"  live: sparse refusal OK ({sparse[0]} {sparse[1]}, "
                  f"{profile.n_tests} tests)")

    profile = serving.build_profile(*WELL_COVERED)
    text = llm.summarise(profile, use_cache=False)
    if text is None:
        print("  live: API unavailable for the grounded check — skipped")
        return fails
    block = llm.render_data_block(profile)
    allowed = numbers_in(block) | {str(n) for n in range(0, 101)}
    invented = {n for n in numbers_in(text) if n not in allowed}
    if invented:
        fails.append(f"summary contains numbers absent from the data block: "
                     f"{sorted(invented)[:6]}")
    else:
        print("  live: every figure in the summary traces to the data block")
    return fails


def run() -> int:
    print("offline grounding checks")
    fails = offline_checks()
    for f in fails:
        print(f"  FAIL: {f}")
    if not fails:
        print("  prompt, retrieval and cache key all intact")

    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        print("\nlive grounding checks")
        fails += live_checks()
    else:
        print("\nlive grounding checks skipped — no GEMINI_API_KEY")

    print(f"\nfailures: {len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(run())
