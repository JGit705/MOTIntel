"""Short names for the failure reasons — "Deep tyre cut" / "Structural cords
exposed" — for the rows of the failure table.

The enrichment gives every DVSA defect a plain-English sentence, which is the
right thing to read but the wrong thing to scan: a ten-row table of full
sentences is a wall of text, and the table is where a buyer's eye goes first.
Shortening a sentence without changing what it claims is a bounded semantic
job of exactly the kind the enrichment stage already does, so it runs the same
way: once, offline, batched, cached, validated, shipped as data.

It works from the plain-English sentences, which have already been through the
Phase 2 checks and the human read, so a short label can only restate a claim
that has been reviewed — and the validation below rejects one that adds a
number the sentence did not contain.

Run with:  python -m motintel.short_labels
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import time

import polars as pl
from google.genai import errors, types

from motintel.config import PROCESSED
from motintel.enrich import (ATTEMPTS, MODEL, REQUEST_BUDGET, THINKING_LEVEL,
                             VALIDATION_ATTEMPTS, Budget, EnrichmentError,
                             Truncated, _backoff, _brief, _daily_quota_spent,
                             _lazy_client, _retryable)

log = logging.getLogger(__name__)

SOURCE = PROCESSED / "defect_meta.parquet"
OUTPUT = PROCESSED / "defect_short.parquet"
CACHE_DIR = PROCESSED / "short_cache"

# Three requests for the 515 reasons. Each label is a handful of tokens, so a
# batch this size stays far inside the output ceiling, and three requests is
# small enough to fit beside a day's other use of the free tier.
BATCH_SIZE = 175
TEMPERATURE = 0.0
MAX_OUTPUT_TOKENS = 8192
MAX_HEADLINE_WORDS, MAX_DETAIL_WORDS = 5, 7

SYSTEM = """You write short labels for UK MOT failure reasons, for a table a
used-car buyer scans.

Each item gives the DVSA category, the DVSA description, and a plain-English
sentence that has already been checked. For each item return:

- headline: two to four words naming the fault, in sentence case — for example
  "Deep tyre cut" or "Headlight not working".
- detail: two to six words adding the specific finding, in sentence case — for
  example "Structural cords exposed" or "On dipped beam".

Say nothing the plain-English sentence does not say, and never name a part more
precisely than it does. No numbers unless the sentence contains them. No full
stops, no prices.

Return one object per item, echoing its id. Return every id you are given."""

SCHEMA = types.Schema(
    type=types.Type.ARRAY,
    items=types.Schema(
        type=types.Type.OBJECT, required=["id", "headline", "detail"],
        properties={"id": types.Schema(type=types.Type.INTEGER),
                    "headline": types.Schema(type=types.Type.STRING),
                    "detail": types.Schema(type=types.Type.STRING)}))


def items() -> list[dict]:
    """Every labelled reason, commonest first, so a partial run still covers
    the reasons people actually meet."""
    if not SOURCE.exists():
        raise EnrichmentError(f"{SOURCE.name} not found. Run the enrichment "
                              f"first.")
    rows = (pl.read_parquet(SOURCE)
            .sort(["n_tests", "defect_category", "defect_desc"],
                  descending=[True, False, False]))
    return [{"id": i, **r} for i, r in enumerate(rows.to_dicts())]


def _render(batch: list[dict]) -> str:
    return "\n".join(f'{r["id"]}. [{r["defect_category"]}] {r["defect_desc"]}'
                     f' | plain English: {r["plain_english"]}' for r in batch)


def _cache_key(batch: list[dict]) -> str:
    payload = json.dumps({"items": _render(batch), "system": SYSTEM,
                          "model": MODEL, "thinking": THINKING_LEVEL,
                          "temperature": TEMPERATURE}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def validate(batch: list[dict], rows: list[dict]) -> list[str]:
    """What stops a batch of labels shipping. Exposed for the offline tests."""
    faults = []
    wanted = {r["id"] for r in batch}
    got = [r.get("id") for r in rows]
    if sorted(got) != sorted(wanted) or len(got) != len(set(got)):
        faults.append(f"ids do not match: missing {sorted(wanted - set(got))}, "
                      f"unexpected {sorted(set(got) - wanted)}")
    source = {r["id"]: r for r in batch}
    for r in rows:
        item = source.get(r.get("id"))
        if item is None:
            continue
        headline = (r.get("headline") or "").strip().rstrip(".")
        detail = (r.get("detail") or "").strip().rstrip(".")
        if not 1 <= len(headline.split()) <= MAX_HEADLINE_WORDS:
            faults.append(f'id {r["id"]}: headline "{headline}" is not 1-'
                          f'{MAX_HEADLINE_WORDS} words')
        if not 1 <= len(detail.split()) <= MAX_DETAIL_WORDS:
            faults.append(f'id {r["id"]}: detail "{detail}" is not 1-'
                          f'{MAX_DETAIL_WORDS} words')
        said = f'{item["plain_english"]} {item["defect_desc"]}'
        for number in re.findall(r"\d+(?:\.\d+)?", f"{headline} {detail}"):
            if number not in said:
                faults.append(f'id {r["id"]}: {number} is not in the source')
        if "£" in headline + detail:
            faults.append(f'id {r["id"]}: priced the repair')
    return faults


def _ask(batch: list[dict], client) -> list[dict]:
    response = client.models.generate_content(
        model=MODEL,
        contents=f"Label these {len(batch)} items:\n\n{_render(batch)}",
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM, temperature=TEMPERATURE,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            response_mime_type="application/json", response_schema=SCHEMA,
            thinking_config=types.ThinkingConfig(
                thinking_level=THINKING_LEVEL)))
    reason = getattr((response.candidates or [None])[0], "finish_reason", None)
    if reason == types.FinishReason.MAX_TOKENS:
        raise Truncated(f"the reply hit the {MAX_OUTPUT_TOKENS}-token ceiling "
                        f"in a batch of {len(batch)}. Lower BATCH_SIZE.")
    if reason not in (None, types.FinishReason.STOP):
        raise EnrichmentError(f"the model stopped early: {reason}")
    rows = json.loads(response.text or "[]")
    faults = validate(batch, rows)
    if faults:
        raise EnrichmentError("; ".join(faults[:5]))
    return rows


def _batch(batch: list[dict], client, budget: Budget) -> list[dict]:
    """One batch, from cache if it is there. Validation happens before the
    write, so the cache can only ever hold batches that passed."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(batch)}.json"
    if path.exists():
        return json.loads(path.read_text())["rows"]
    last, allowed = None, ATTEMPTS
    for attempt in range(1, ATTEMPTS + 1):
        if attempt > allowed:
            break
        try:
            budget.spend()
            rows = _ask(batch, client())
        except Exception as e:
            if not _retryable(e):
                raise EnrichmentError(f"batch at id {batch[0]['id']}: {e}") from e
            last = e
            # A reply we rejected gets one more go; a busy server gets them all.
            if not isinstance(e, errors.APIError):
                allowed = min(allowed, VALIDATION_ATTEMPTS)
            log.warning("batch at id %d, attempt %d/%d: %s", batch[0]["id"],
                        attempt, allowed, _brief(e))
            if attempt < allowed:
                time.sleep(_backoff(attempt))
            continue
        path.write_text(json.dumps({"model": MODEL, "rows": rows}, indent=2))
        return rows
    raise EnrichmentError(f"batch at id {batch[0]['id']} did not survive "
                          f"{allowed} attempts: {last}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    todo = items()
    batches = [todo[i:i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    uncached = [b for b in batches
                if not (CACHE_DIR / f"{_cache_key(b)}.json").exists()]
    budget = Budget(REQUEST_BUDGET)
    print(f"short labels for {len(todo)} failure reasons in {len(batches)} "
          f"batches; {len(uncached)} to fetch, budget {budget.remaining}")
    if len(uncached) > budget.remaining:
        print("not enough request budget to finish — cached batches are kept")
        return 1
    client, out = _lazy_client(), []
    try:
        for batch in batches:
            by_id = {r["id"]: r for r in batch}
            for r in _batch(batch, client, budget):
                item = by_id[r["id"]]
                out.append({"defect_category": item["defect_category"],
                            "defect_desc": item["defect_desc"],
                            "headline": r["headline"].strip().rstrip("."),
                            "detail": r["detail"].strip().rstrip(".")})
    except EnrichmentError as e:
        if _daily_quota_spent(e):
            print("stopped: the free tier's daily allowance is spent; re-run "
                  "tomorrow and cached batches are reused")
        else:
            print(f"stopped: {e}")
        print("nothing written — a partly-labelled table must not ship")
        return 1
    table = pl.DataFrame(out)
    table.write_parquet(OUTPUT, compression="zstd")
    print(f"{len(table):,} short labels -> {OUTPUT.name}")
    print(table.head(8))
    return 0


if __name__ == "__main__":
    sys.exit(main())
