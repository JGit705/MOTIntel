"""Phase 1 of AI_PLAN — the LLM as a pipeline stage, not a page feature.

The MOT extracts describe a failure as a category plus a sentence fragment:
"Lamps, reflectors and electrical equipment" / "not working". There are 510
such pairs behind every defect the app can show, and a buyer cannot act on any
of them. They cannot tell a bulb from a rotten subframe, cannot see that two
entries are the same underlying fault, and cannot tell what to look at when
they go and view the car.

510 short strings needing semantic interpretation is what a language model is
for. A regex over them is brittle — "fractured or broken" means something very
different under Suspension than under Visibility — and writing the mapping by
hand is a week of work.

So this runs **once, offline**, and its output ships as data:

  * the reader pays no latency and no tokens, because nothing here runs at
    page load;
  * the output can be read before it is shipped, which is the actual defence
    against a wrong label — Phase 2 of the plan;
  * a bad batch is re-runnable on its own, because the cache is per batch.

It reads the exported serving layer rather than DuckDB, so it needs neither the
raw downloads nor the 6 GB database. The set of pairs in top_defects.parquet is
exactly the set the app can display, which makes it the right input.

Run with:  python -m motintel.enrich
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass

import httpx
import polars as pl
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from motintel.config import PROCESSED, ROOT

# Its own process, inheriting nothing from the shell that ran the pipeline —
# the same reason the app reads the file rather than trusting the environment.
load_dotenv(ROOT / ".env")

log = logging.getLogger(__name__)

SOURCE = PROCESSED / "top_defects.parquet"
OUTPUT = PROCESSED / "defect_meta.parquet"
CACHE_DIR = PROCESSED / "enrich_cache"

# Shared with llm.py by intent rather than by import: this stage is offline and
# batched, so it wants its own token ceiling, and coupling the two would make a
# change for one silently re-price the other.
MODEL = os.environ.get("MOTINTEL_MODEL", "gemini-3.6-flash")
# A 25-item batch under load ran past 60s and the read timed out mid-run.
REQUEST_TIMEOUT_MS = int(os.environ.get("MOTINTEL_TIMEOUT_MS", "180000"))
THINKING_LEVEL = os.environ.get("MOTINTEL_THINKING", "minimal")
TEMPERATURE = 0.0
MAX_OUTPUT_TOKENS = 8192

# Sized by the quota, not by latency. The free tier allows 20 generate_content
# requests per DAY on this model — measured, not read off a docs page: a run at
# 25 per batch needs 21 requests and died on the twenty-first, and waiting two
# minutes did not clear it. At 50 the 510 pairs are 11 requests, which leaves
# room for a retry or two and still fits a cold run inside one day's quota.
# The reply stays well inside MAX_OUTPUT_TOKENS at this size; a batch that
# comes back short of ids is rejected and asked again.
BATCH_SIZE = 50
# A run is ~21 requests, and the free tier answers a fair number of them with
# "high demand, try again later". Two immediate retries lost the whole run to
# one 503, so attempts back off: 2s, 4s, 8s, 16s, jittered so a retry does not
# land in the same instant as everything else that was rejected with it.
ATTEMPTS = 5
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 30.0

# Closed sets. The model picks from these or the batch is rejected — an
# open-ended label is how you end up with "brakes", "braking" and "brake
# system" as three groups.
REPAIR_AREAS = ("brakes", "suspension", "steering", "tyres and wheels",
                "lights and electrics", "corrosion and structure",
                "emissions and exhaust", "visibility",
                "seatbelts and restraints", "other")
# Effort, not cost. This dataset carries no pricing, and a number in pounds
# would be the exact invention the whole project is built to avoid.
EFFORTS = ("minor", "moderate", "major")

SYSTEM = f"""You are labelling UK MOT failure descriptions so a used-car buyer
can understand them.

Each item gives a DVSA test category and a description. The description is a
sentence FRAGMENT, and its subject is the thing the category names — under
"Lamps, reflectors and electrical equipment", "not working" means a lamp or an
electrical item is not working.

You are NOT told which specific part failed, and the data does not record it.
Never name a part more precisely than the category allows. "A light is not
working" is correct; "the headlight bulb has blown" invents a fact.

For each item return:

- plain_english: one sentence saying what is actually wrong, in plain British
  English a non-expert would understand. No jargon, no repair instructions.
- repair_area: exactly one of {list(REPAIR_AREAS)}. Group by the part of the
  car a buyer thinks in, not by the DVSA category. Use "other" only when none
  of the rest fits.
- effort: exactly one of {list(EFFORTS)} — the scale of the job, not its price.
  minor: a consumable or an adjustment. moderate: a component replacement.
  major: structural, or several components, or a job needing the car stripped.
  Judge by the worst reasonable reading of the description: an MOT records this
  as a failure, so it is never trivial.
- forecourt_check: one short imperative sentence telling the buyer what to look
  at or try when viewing the car — or null when nothing about it can be seen or
  tried from outside a workshop. null is the right answer often; do not invent
  a check that would not actually reveal the fault.

Never mention prices, mileages, years, vehicle makes or vehicle models. None of
those are in front of you.

Return one object per item, echoing its id. Return every id you are given."""

RESPONSE_SCHEMA = types.Schema(
    type=types.Type.ARRAY,
    items=types.Schema(
        type=types.Type.OBJECT,
        required=["id", "plain_english", "repair_area", "effort",
                  "forecourt_check"],
        properties={
            "id": types.Schema(type=types.Type.INTEGER),
            "plain_english": types.Schema(type=types.Type.STRING),
            "repair_area": types.Schema(type=types.Type.STRING,
                                        enum=list(REPAIR_AREAS)),
            "effort": types.Schema(type=types.Type.STRING,
                                   enum=list(EFFORTS)),
            "forecourt_check": types.Schema(type=types.Type.STRING,
                                            nullable=True),
        },
    ),
)


class EnrichmentError(RuntimeError):
    """Raised when a batch cannot be produced or does not survive validation.

    Deliberately fatal. Unlike llm.py, where a missing summary degrades to the
    charts, a half-built mapping must never reach the export: the app would
    show plain English for the common defects and DVSA legalese for the rest,
    with no way to tell which was which.
    """


@dataclass(frozen=True)
class Pair:
    """One thing to label: a DVSA category and description, with the number of
    tests nationally that recorded it. The count is passed to the model only as
    ordering context — it must not appear in any output."""
    id: int
    category: str
    description: str
    n_tests: int


def defect_pairs() -> list[Pair]:
    """Every distinct (category, description) the app can display, commonest
    first, so a truncated run still covers the defects people actually meet."""
    if not SOURCE.exists():
        raise EnrichmentError(f"{SOURCE.name} not found. Run the export first.")
    rows = (pl.read_parquet(SOURCE)
            .group_by("defect_category", "defect_desc")
            .agg(n_tests=pl.col("n_tests").sum())
            .sort(["n_tests", "defect_category", "defect_desc"],
                  descending=[True, False, False]))
    return [Pair(i, r["defect_category"], r["defect_desc"], r["n_tests"])
            for i, r in enumerate(rows.to_dicts())]


def _render(batch: list[Pair]) -> str:
    return "\n".join(f'{p.id}. [{p.category}] {p.description}' for p in batch)


def _cache_key(batch: list[Pair]) -> str:
    """Keyed on everything that can change the answer, the generation settings
    included. llm.py learned this the hard way: with the thinking level outside
    the key, turning it down served yesterday's answer and looked like the
    setting had done nothing."""
    payload = json.dumps({"items": _render(batch), "system": SYSTEM,
                          "model": MODEL, "thinking": THINKING_LEVEL,
                          "temperature": TEMPERATURE,
                          "max_output_tokens": MAX_OUTPUT_TOKENS,
                          "areas": REPAIR_AREAS, "efforts": EFFORTS},
                         sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _check_batch(batch: list[Pair], rows: list[dict]) -> list[dict]:
    """Structural validation, run before a batch is allowed into the cache.

    The response schema already constrains the shape and the two closed sets,
    so what is left is what a schema cannot see: whether every id came back,
    exactly once, and whether the free text stayed inside its brief. The wider
    checks — determinism, the golden set, the human read — are Phase 2.
    """
    wanted = {p.id for p in batch}
    got = [r["id"] for r in rows]
    if sorted(got) != sorted(wanted):
        missing, extra = wanted - set(got), set(got) - wanted
        raise EnrichmentError(f"ids do not match: missing {sorted(missing)}, "
                              f"unexpected {sorted(extra)}")
    if len(got) != len(set(got)):
        raise EnrichmentError("an id came back more than once")

    for r in rows:
        text = f'{r["plain_english"]} {r["forecourt_check"] or ""}'
        if "£" in text or "$" in text:
            raise EnrichmentError(f'id {r["id"]}: priced the repair')
        if not r["plain_english"].strip():
            raise EnrichmentError(f'id {r["id"]}: empty description')
    return rows


def _ask(batch: list[Pair], client: genai.Client) -> list[dict]:
    response = client.models.generate_content(
        model=MODEL,
        contents=f"Label these {len(batch)} items:\n\n{_render(batch)}",
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            temperature=TEMPERATURE,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
            thinking_config=types.ThinkingConfig(
                thinking_level=THINKING_LEVEL),
        ),
    )
    if not (response.text or "").strip():
        raise EnrichmentError("empty response")
    return json.loads(response.text)


def _retryable(e: Exception) -> bool:
    """Whether asking again could plausibly work.

    A 503 ("high demand") and a 429 (quota) both clear on their own, and so
    does a reply that arrived malformed. A 400 or a 401 will not: the key is
    wrong, or the request is, and four more attempts spread over half a minute
    only make the failure slower to read.
    """
    if isinstance(e, errors.ServerError):
        return True
    if isinstance(e, errors.ClientError):
        # A 429 is retryable when it is a per-minute rate limit and pointless
        # when it is the daily allowance: four more attempts spread over half
        # a minute cannot bring back a quota that resets tomorrow.
        return getattr(e, "code", None) == 429 and not _daily_quota_spent(e)
    # A read timeout is not an APIError and so escaped the retry loop entirely
    # the first time, taking a run that had already cached eight batches down
    # with a traceback.
    if isinstance(e, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    return isinstance(e, (EnrichmentError, json.JSONDecodeError))


def _daily_quota_spent(e: Exception) -> bool:
    return "PerDay" in str(e)


def _backoff(attempt: int) -> float:
    return min(BACKOFF_CAP_S,
               BACKOFF_BASE_S * 2 ** (attempt - 1)) * (0.5 + random.random())


def _brief(e: Exception) -> str:
    text = str(e).replace("\n", " ")
    return text[:110] + "…" if len(text) > 110 else text


def _batch(batch: list[Pair], client: genai.Client) -> list[dict]:
    """One batch, from cache if it is there. Validation happens before the
    write, so the cache can only ever hold batches that passed."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(batch)}.json"
    if path.exists():
        return json.loads(path.read_text())["rows"]

    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            rows = _check_batch(batch, _ask(batch, client))
        # Broad on purpose: the transport raises its own exceptions, and an
        # unrecognised one must reach _retryable to be judged rather than
        # escape the loop. Anything it declines is re-raised immediately.
        except Exception as e:
            if not _retryable(e):
                raise EnrichmentError(
                    f"batch starting at id {batch[0].id}: {e}") from e
            last = e
            log.warning("batch at id %d, attempt %d/%d: %s",
                        batch[0].id, attempt, ATTEMPTS, _brief(e))
            if attempt < ATTEMPTS:
                time.sleep(_backoff(attempt))
            continue
        path.write_text(json.dumps(
            {"model": MODEL, "thinking": THINKING_LEVEL,
             "items": _render(batch), "rows": rows}, indent=2))
        return rows
    raise EnrichmentError(
        f"batch starting at id {batch[0].id} did not survive {ATTEMPTS} "
        f"attempts: {last}")


def enrich(pairs: list[Pair] | None = None) -> pl.DataFrame:
    """Label every pair and return the table, cache-first throughout.

    Idempotent: a second run with the same pairs and settings makes no API
    calls at all.
    """
    pairs = pairs if pairs is not None else defect_pairs()
    client = genai.Client(
        http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
    by_id = {p.id: p for p in pairs}
    out = []
    for start in range(0, len(pairs), BATCH_SIZE):
        batch = pairs[start:start + BATCH_SIZE]
        cached = (CACHE_DIR / f"{_cache_key(batch)}.json").exists()
        print(f"  {start + 1:>4}-{start + len(batch):<4} of {len(pairs)}"
              f"{'  (cached)' if cached else ''}", flush=True)
        for r in _batch(batch, client):
            p = by_id[r["id"]]
            out.append({"defect_category": p.category,
                        "defect_desc": p.description,
                        "plain_english": r["plain_english"].strip(),
                        "repair_area": r["repair_area"],
                        "effort": r["effort"],
                        "forecourt_check": (r["forecourt_check"] or "").strip()
                        or None,
                        "n_tests": p.n_tests})
    return pl.DataFrame(out)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    pairs = defect_pairs()
    print(f"labelling {len(pairs)} defect descriptions in batches of "
          f"{BATCH_SIZE}")
    try:
        table = enrich(pairs)
    except EnrichmentError as e:
        done = len(list(CACHE_DIR.glob("*.json"))) if CACHE_DIR.exists() else 0
        if _daily_quota_spent(e):
            # The wall of quota JSON says all of this, and buries it.
            print("\nstopped: the free tier's daily request allowance is "
                  "spent.")
            print(f"{done} of {-(-len(pairs) // BATCH_SIZE)} batches are "
                  f"cached; re-running tomorrow picks up where this left off.")
        else:
            print(f"\nstopped: {e}")
            print(f"{done} batches cached and reusable.")
        print("nothing written — a partly-labelled mapping must not ship")
        return 1
    table.write_parquet(OUTPUT, compression="zstd")
    print(f"\n{len(table):,} labelled -> {OUTPUT.name} "
          f"({OUTPUT.stat().st_size / 1024:.0f} KB)")
    print(table.group_by("repair_area")
          .agg(pl.len().alias("defects"), pl.col("n_tests").sum())
          .sort("n_tests", descending=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
