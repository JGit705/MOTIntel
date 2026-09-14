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
from motintel.defect_labels import EFFORTS, REPAIR_AREAS, apply_overrides
from motintel.defect_labels import validate as validate_labels

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
# The free tier answers a fair number of requests with "high demand, try again
# later". Two immediate retries lost a whole run to one 503, so attempts back
# off: 2s, 4s, 8s, 16s, jittered so a retry does not land in the same instant
# as everything else that was rejected alongside it.
ATTEMPTS = 5
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 30.0

# A reply that failed validation gets one more go, not five. Temperature is
# zero, so asking again sends byte-for-byte the same request; the odds it comes
# back different are low and each attempt costs a request out of twenty.
# Transient failures are worth the full ATTEMPTS because nothing about the
# request was wrong.
VALIDATION_ATTEMPTS = 2

# Attempts, not successes. A rejected request appears to count against the
# allowance — a run that cached eight batches had spent far more than eight of
# the day's twenty — so the run is planned against a budget rather than left to
# discover the ceiling by hitting it on the last batch. Raise it for a paid key.
REQUEST_BUDGET = int(os.environ.get("MOTINTEL_REQUEST_BUDGET", "20"))

# REPAIR_AREAS and EFFORTS come from defect_labels, which is also what the
# validation and the Phase 4 regression suite read. Defining them here as well
# is how the prompt and the checks come to allow different things.

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


class Truncated(EnrichmentError):
    """The reply ran out of output budget mid-JSON.

    Its own type because it is the one failure that retrying cannot fix: at
    temperature zero the next attempt truncates in the same place, so five
    attempts spend five requests to learn what the first one said. The caller
    stops and says which knob to turn.
    """


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
    # Why the model stopped, before what it said. A truncated reply is partial
    # JSON, and read without this it looks like a malformed one and gets asked
    # for again four more times.
    reason = getattr(
        (response.candidates or [None])[0], "finish_reason", None)
    if reason == types.FinishReason.MAX_TOKENS:
        raise Truncated(
            f"the reply hit the {MAX_OUTPUT_TOKENS}-token output ceiling "
            f"part-way through a batch of {len(batch)}. Lower BATCH_SIZE or "
            f"raise MAX_OUTPUT_TOKENS — retrying cannot help.")
    if reason not in (None, types.FinishReason.STOP):
        raise EnrichmentError(f"the model stopped early: {reason}")
    if not (response.text or "").strip():
        raise EnrichmentError("empty response")
    return json.loads(response.text)


@dataclass
class Budget:
    """How many requests this run may still spend.

    Counts attempts rather than successes, because a rejected request appears
    to count against the allowance too. Exists so the run can say up front that
    it does not have the budget to finish, instead of labelling nine batches
    and then dying on the tenth with a wall of quota JSON.
    """
    remaining: int

    def spend(self) -> None:
        if self.remaining <= 0:
            raise OutOfBudget(
                f"the run's request budget is spent. Raise it with "
                f"MOTINTEL_REQUEST_BUDGET if this key is not on the free "
                f"tier's {REQUEST_BUDGET}/day.")
        self.remaining -= 1


class OutOfBudget(EnrichmentError):
    """Stopped by our own accounting rather than by the API. Never retried."""


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
    # Truncation repeats at temperature zero and our own budget stop is not
    # the API's opinion — neither is worth another request.
    if isinstance(e, (Truncated, OutOfBudget)):
        return False
    return isinstance(e, (EnrichmentError, json.JSONDecodeError))


def _daily_quota_spent(e: Exception) -> bool:
    """Whether a 429 is the day's allowance rather than a per-minute limit.

    Read off the quotaId Google returns — `...RequestsPerDayPerProject...` —
    with the prose form allowed for too, since the wording of the message is
    not a stable interface and the distinction decides whether the run waits
    or stops.
    """
    text = str(e).lower()
    return "perday" in text or "per day" in text


def _backoff(attempt: int) -> float:
    return min(BACKOFF_CAP_S,
               BACKOFF_BASE_S * 2 ** (attempt - 1)) * (0.5 + random.random())


def _brief(e: Exception) -> str:
    text = str(e).replace("\n", " ")
    return text[:110] + "…" if len(text) > 110 else text


def _batch(batch: list[Pair], client, budget: Budget) -> list[dict]:
    """One batch, from cache if it is there. Validation happens before the
    write, so the cache can only ever hold batches that passed."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(batch)}.json"
    if path.exists():
        return json.loads(path.read_text())["rows"]

    last: Exception | None = None
    allowed = ATTEMPTS
    for attempt in range(1, ATTEMPTS + 1):
        if attempt > allowed:
            break
        try:
            budget.spend()
            rows = _check_batch(batch, _ask(batch, client()))
        # Broad on purpose: the transport raises its own exceptions, and an
        # unrecognised one must reach _retryable to be judged rather than
        # escape the loop. Anything it declines is re-raised immediately.
        except Exception as e:
            if not _retryable(e):
                raise EnrichmentError(
                    f"batch starting at id {batch[0].id}: {e}") from e
            last = e
            # A reply we rejected is a different kind of failure from a server
            # that was busy: the request was fine, so sending it again asks for
            # the same thing. One more go, then stop.
            if not isinstance(e, (errors.APIError, httpx.HTTPError)):
                allowed = min(allowed, VALIDATION_ATTEMPTS)
            log.warning("batch at id %d, attempt %d/%d: %s",
                        batch[0].id, attempt, allowed, _brief(e))
            if attempt < allowed:
                time.sleep(_backoff(attempt))
            continue
        path.write_text(json.dumps(
            {"model": MODEL, "thinking": THINKING_LEVEL,
             "items": _render(batch), "rows": rows}, indent=2))
        return rows
    raise EnrichmentError(
        f"batch starting at id {batch[0].id} did not survive {allowed} "
        f"attempts: {last}")


def _lazy_client():
    """Build the client on first use, not on entry.

    google-genai raises at construction when there is no key, so building it up
    front made a fully-cached run — which sends nothing — fail without one.
    Once the labelling is done that is the common case: rebuilding the Parquet,
    changing how the table is assembled, or re-running the stage as part of the
    pipeline should all cost nothing and need no credentials.
    """
    held = []

    def client():
        if not held:
            held.append(genai.Client(
                http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS)))
        return held[0]
    return client


def batches_needed(pairs: list[Pair]) -> int:
    return -(-len(pairs) // BATCH_SIZE)


def enrich(pairs: list[Pair] | None = None,
           budget: Budget | None = None) -> pl.DataFrame:
    """Label every pair and return the table, cache-first throughout.

    Idempotent, and idempotent without credentials: a second run with the same
    pairs and settings sends nothing and needs no key.
    """
    pairs = pairs if pairs is not None else defect_pairs()
    budget = budget if budget is not None else Budget(REQUEST_BUDGET)
    client = _lazy_client()
    by_id = {p.id: p for p in pairs}
    out = []
    for start in range(0, len(pairs), BATCH_SIZE):
        batch = pairs[start:start + BATCH_SIZE]
        cached = (CACHE_DIR / f"{_cache_key(batch)}.json").exists()
        print(f"  {start + 1:>4}-{start + len(batch):<4} of {len(pairs)}"
              f"{'  (cached)' if cached else ''}", flush=True)
        for r in _batch(batch, client, budget):
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


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    argv = sys.argv[1:] if argv is None else argv
    pairs = defect_pairs()

    # --missing-only: keep every shipped label whose defect is still in the
    # source, drop the ones that have left it, and ask only for the new ones.
    #
    # The cache is per batch, and batches are cut from the whole list in order
    # of frequency, so one defect entering or leaving the top ten shifts every
    # batch after it and re-prices the lot. Making the export's tie-break
    # deterministic did exactly that: 24 descriptions in, 32 out, and a full
    # run would have been eleven requests to re-label 483 defects that already
    # had reviewed labels. These go through the same overrides and the same
    # validation as a full run, against the full source.
    kept = None
    todo_pairs = pairs
    if "--missing-only" in argv and OUTPUT.exists():
        source = pl.DataFrame({"defect_category": [p.category for p in pairs],
                               "defect_desc": [p.description for p in pairs],
                               "n_tests": [p.n_tests for p in pairs]})
        kept = (pl.read_parquet(OUTPUT).drop("n_tests")
                .join(source, on=["defect_category", "defect_desc"]))
        labelled = set(zip(kept["defect_category"], kept["defect_desc"]))
        todo_pairs = [p for p in pairs
                      if (p.category, p.description) not in labelled]
        print(f"keeping {kept.height} existing labels; "
              f"{len(todo_pairs)} defect descriptions still to label")

    todo = [start for start in range(0, len(todo_pairs), BATCH_SIZE)
            if not (CACHE_DIR
                    / f"{_cache_key(todo_pairs[start:start + BATCH_SIZE])}"
                    ".json").exists()]
    budget = Budget(REQUEST_BUDGET)
    print(f"labelling {len(todo_pairs)} defect descriptions in batches of "
          f"{BATCH_SIZE}")
    print(f"{batches_needed(todo_pairs) - len(todo)} of "
          f"{batches_needed(todo_pairs)} batches already cached; {len(todo)} "
          f"to fetch, budget {budget.remaining}")
    if len(todo) > budget.remaining:
        # Better to say so now than to label most of them and stop.
        print(f"\nnot enough budget to finish: {len(todo)} batches needed, "
              f"{budget.remaining} requests allowed. Raise "
              f"MOTINTEL_REQUEST_BUDGET on a paid key, or run again after the "
              f"free tier's daily reset — cached batches are kept.")
        return 1
    try:
        table = enrich(todo_pairs, budget) if todo_pairs else None
        if kept is not None:
            table = (kept if table is None
                     else pl.concat([kept.select(table.columns), table]))
    except EnrichmentError as e:
        done = len(list(CACHE_DIR.glob("*.json"))) if CACHE_DIR.exists() else 0
        if isinstance(e, Truncated):
            print(f"\nstopped: {e}")
            print(f"{done} batches cached and reusable.")
            print("nothing written — a partly-labelled mapping must not ship")
            return 1
        if _daily_quota_spent(e):
            # The wall of quota JSON says all of this, and buries it.
            print("\nstopped: the free tier's daily request allowance is "
                  "spent.")
            print(f"{done} of {batches_needed(pairs)} batches are cached; "
                  f"re-running tomorrow picks up where this left off.")
        else:
            print(f"\nstopped: {e}")
            print(f"{done} batches cached and reusable.")
        print("nothing written — a partly-labelled mapping must not ship")
        return 1
    # The corrections from the human read go on before anything is checked or
    # written, so what is validated is what ships.
    try:
        table = apply_overrides(table)
    except ValueError as e:
        print(f"\nstopped: {e}")
        return 1

    problems = validate_labels(table, [(p.category, p.description)
                                       for p in pairs])
    if problems:
        print(f"\n{len(problems)} problem(s) with the labelling:")
        for problem in problems[:20]:
            print(f"  {problem}")
        if len(problems) > 20:
            print(f"  ...and {len(problems) - 20} more")
        print("nothing written — see AI_PLAN phase 2")
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
