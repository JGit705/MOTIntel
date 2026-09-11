"""The offline half of the enrichment stage.

Everything here runs without an API key and without spending quota, which
matters more than usual on this stage: the free tier allows twenty requests a
day, so a test suite that called the model would cost most of a run.

What is checked is the machinery around the call — which pairs go in, what
invalidates the cache, which replies are rejected, and which failures are worth
retrying. The quality of the labels themselves is Phase 2's job, and needs a
human.

Run from the repository root:  python -m tests.test_enrich
"""
from __future__ import annotations

import json
import sys

import httpx
from google.genai import errors

from motintel import enrich
from motintel.enrich import EnrichmentError, Pair


def _client_error(code: int, message: str) -> errors.ClientError:
    return errors.ClientError(code, {"error": {"code": code,
                                               "message": message}})


def _rows(pairs: list[Pair], **overrides) -> list[dict]:
    rows = [{"id": p.id, "plain_english": f"Something is wrong with {p.id}.",
             "repair_area": "other", "effort": "moderate",
             "forecourt_check": None} for p in pairs]
    if overrides:
        rows[0].update(overrides)
    return rows


def check_pairs(fail) -> None:
    pairs = enrich.defect_pairs()
    if not pairs:
        return fail("defect_pairs returned nothing")
    ids = [p.id for p in pairs]
    if ids != list(range(len(pairs))):
        fail("ids are not contiguous from zero")
    seen = {(p.category, p.description) for p in pairs}
    if len(seen) != len(pairs):
        fail("the same category/description pair appears twice")
    counts = [p.n_tests for p in pairs]
    if counts != sorted(counts, reverse=True):
        fail("pairs are not commonest-first, so a truncated run would cover "
             "the rare defects rather than the ones people meet")
    # A cold run has to fit inside one day of the free tier's allowance.
    batches = -(-len(pairs) // enrich.BATCH_SIZE)
    if batches > 15:
        fail(f"{len(pairs)} pairs at {enrich.BATCH_SIZE} per batch is "
             f"{batches} requests, too close to the 20/day free-tier limit "
             f"to leave room for a retry")


def check_cache_key(fail) -> None:
    pairs = enrich.defect_pairs()[:4]
    key = enrich._cache_key(pairs)
    if enrich._cache_key(pairs) != key:
        return fail("cache key is not stable for the same batch")
    if enrich._cache_key(pairs[:3]) == key:
        fail("cache key ignores which pairs are in the batch")

    # Every generation setting has to be inside the key. llm.py shipped with
    # the thinking level outside it, so turning the level down served
    # yesterday's answer and looked like the setting had done nothing.
    for name, value in [("MODEL", "some-other-model"),
                        ("THINKING_LEVEL", "high"), ("TEMPERATURE", 0.7),
                        ("MAX_OUTPUT_TOKENS", 111), ("SYSTEM", "different"),
                        ("REPAIR_AREAS", ("a", "b")), ("EFFORTS", ("x",))]:
        original = getattr(enrich, name)
        setattr(enrich, name, value)
        try:
            if enrich._cache_key(pairs) == key:
                fail(f"cache key does not cover {name}")
        finally:
            setattr(enrich, name, original)


def check_validation(fail) -> None:
    pairs = enrich.defect_pairs()[:3]

    try:
        enrich._check_batch(pairs, _rows(pairs))
    except EnrichmentError as e:
        return fail(f"a well-formed batch was rejected: {e}")

    bad = {
        "a missing id": _rows(pairs)[:-1],
        "an unexpected id": _rows(pairs) + [{"id": 999, "plain_english": "x",
                                             "repair_area": "other",
                                             "effort": "minor",
                                             "forecourt_check": None}],
        "a duplicated id": _rows(pairs) + [dict(_rows(pairs)[0])],
        "a price in the description": _rows(
            pairs, plain_english="A bulb has blown, about £15 to replace."),
        "a price in the forecourt check": _rows(
            pairs, forecourt_check="Ask if the £200 repair was done."),
        "an empty description": _rows(pairs, plain_english="   "),
    }
    for what, rows in bad.items():
        try:
            enrich._check_batch(pairs, rows)
        except EnrichmentError:
            continue
        fail(f"validation let through {what}")


def check_retry_policy(fail) -> None:
    daily = ("Quota exceeded ... quotaId: "
             "GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    cases = [
        ("a 503 from high demand", errors.ServerError(503, {}), True),
        ("a per-minute 429", _client_error(429, "rate limit, retry in 12s"),
         True),
        ("the daily allowance", _client_error(429, daily), False),
        ("a bad request", _client_error(400, "invalid argument"), False),
        ("a bad key", _client_error(401, "unauthorised"), False),
        ("a read timeout", httpx.ReadTimeout("timed out"), True),
        ("a connection failure", httpx.ConnectError("no route"), True),
        ("a malformed reply", json.JSONDecodeError("bad", "{", 0), True),
        ("a rejected batch", EnrichmentError("ids do not match"), True),
        ("a programming error", TypeError("not callable"), False),
    ]
    for what, error, expected in cases:
        if enrich._retryable(error) is not expected:
            fail(f"{what}: expected retryable={expected}")

    if not enrich._daily_quota_spent(_client_error(429, daily)):
        fail("the daily allowance was not recognised as spent")
    if enrich._daily_quota_spent(_client_error(429, "retry in 12s")):
        fail("a per-minute limit was mistaken for the daily allowance")


def check_backoff(fail) -> None:
    # Jittered, so the assertion is on the band rather than the value.
    for attempt in range(1, enrich.ATTEMPTS + 1):
        waits = [enrich._backoff(attempt) for _ in range(200)]
        ceiling = min(enrich.BACKOFF_CAP_S,
                      enrich.BACKOFF_BASE_S * 2 ** (attempt - 1))
        if not all(0.5 * ceiling <= w <= 1.5 * ceiling for w in waits):
            fail(f"attempt {attempt}: backoff left its jitter band")
    if enrich._backoff(1) >= enrich._backoff(enrich.ATTEMPTS) * 1.5:
        fail("backoff does not grow with the attempt number")


def run() -> int:
    failures: list[str] = []
    # The first two read the exported Parquet, which is gitignored, so they sit
    # out a checkout that has no data rather than failing it. Skipping loudly
    # beats a green tick over checks that never ran.
    needs_data = {"pairs", "cache key", "validation"}
    have_data = enrich.SOURCE.exists()
    checks = [("pairs", check_pairs), ("cache key", check_cache_key),
              ("validation", check_validation),
              ("retry policy", check_retry_policy), ("backoff", check_backoff)]
    for name, check in checks:
        if name in needs_data and not have_data:
            print(f"  {name}: skipped, no {enrich.SOURCE.name} in this "
                  f"checkout")
            continue
        found: list[str] = []
        check(found.append)
        print(f"  {name}: {'OK' if not found else f'{len(found)} failed'}")
        failures += [f"{name}: {f}" for f in found]

    print(f"\nfailures: {len(failures)}")
    for f in failures:
        print(f"  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    print("enrichment stage, offline checks")
    sys.exit(run())
