"""FR4 — plain-English reliability summaries, grounded in retrieved rows.

The grounding guarantee here is structural, not just prompted. The model is
handed a VehicleProfile built entirely by queries.py and nothing else: no
vehicle knowledge, no web access, no tools. If a fact is not in the rendered
data block, the model has no route to it.

The prompt then does the second half of the job — telling the model to refuse
when the data is thin, which is what the Phase 5 sparse-data test checks.

Served by Google's Gemini API. Nothing about the grounding depends on the
provider: the retrieval is SQL, the constraint is the system prompt, and the
cache is on disk. Swapping the model vendor changes this file and nothing else.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict

from google import genai
from google.genai import errors, types

from motintel.config import PROCESSED
from motintel.queries import MIN_TESTS_FOR_CONFIDENCE, VehicleProfile

log = logging.getLogger(__name__)

# Overridable so a quota-limited free tier can be pointed at a smaller model
# without editing code.
MODEL = os.environ.get("MOTINTEL_MODEL", "gemini-3.6-flash")
CACHE_DIR = PROCESSED / "llm_cache"
REQUEST_TIMEOUT_MS = int(os.environ.get("MOTINTEL_TIMEOUT_MS", "30000"))

# This model reasons before answering and bills for it. Measured on real
# profiles, the default setting spent ~1,700 reasoning tokens to produce ~120
# tokens of summary — two thirds of every request, invisible to the reader.
# At "minimal" the reasoning drops to zero for roughly a quarter of the tokens,
# and it was checked rather than assumed: the sparse-data refusal still fires
# and every figure in a full summary still traces to the data block. The
# default setting also truncated a sparse answer mid-sentence, because the
# reasoning had eaten the output budget.
THINKING_LEVEL = os.environ.get("MOTINTEL_THINKING", "minimal")

# Zero temperature: this is reporting over supplied figures, where sampling
# variety buys nothing but drift away from the numbers.
TEMPERATURE = 0.0
MAX_OUTPUT_TOKENS = 2048

SYSTEM = """You summarise UK MOT test data for used-car buyers.

Use ONLY the data in the DATA block. It is the complete set of facts available
to you. Do not add causes, mechanisms, model history, recalls, manufacturer
reputation, or comparisons that are not present in that block — not even facts
you are confident are true. If you cannot support a statement by pointing at a
specific number in the block, do not make it.

If the data is too thin to support a conclusion, say so plainly and stop. An
honest "there is not enough data on this vehicle" is a correct answer, and is
strongly preferred to a plausible-sounding guess.

Two things you must not imply:
- An MOT failure rate is not a reliability rating. The test covers safety and
  emissions items at one annual point. It cannot see anything repaired between
  tests, and it reflects how owners maintain their cars as much as how the car
  was built.
- Do not give buying advice, valuations, or repair costs. None of that is here.

Write plain British English for a non-expert. No headings, no bullet points,
no preamble — just the paragraph."""


def render_data_block(profile: VehicleProfile) -> str:
    """Render the retrieved rows as the model's entire world."""
    p, lines = profile, []
    age = (f"{p.age_band[0]}-{p.age_band[1]} years old" if p.age_band
           else f"{p.age_years} years old")
    lines.append(f"Vehicle: {p.make} {p.model}, {age}")
    lines.append(f"Tests in this make/model/age group: {p.n_tests:,}")
    if p.failure_rate is not None:
        lines.append(f"Failure rate for this group: {p.failure_rate:.1%}")

    if p.top_defects:
        lines.append("\nMost common failure items (share of tests in group):")
        for d in p.top_defects:
            # The plain-English reading where the enrichment has produced one,
            # so the summary can say "a suspension ball joint is worn" instead
            # of reciting "Suspension: ball joint excessively worn". These are
            # in the profile only if they passed the Phase 2 checks and the
            # overrides, so they are reviewed data by the time they get here.
            plain = d.get("plain_english")
            lines.append(f"  - {d['category']}: {d['defect']} "
                         f"— {d['n_tests']:,} tests ({d['share_of_tests']:.1%})"
                         + (f"\n      in plain English: {plain}" if plain
                            else ""))

    if p.by_mileage:
        lines.append("\nFailure rate by odometer band (all ages of this model):")
        for m in p.by_mileage:
            lines.append(f"  - {m['mileage_band']} miles: "
                         f"{m['failure_rate']:.1%} of {m['n_tests']:,} tests")

    if p.peers:
        lines.append("\nOther high-volume models in the same age band:")
        for q in p.peers:
            lines.append(f"  - {q['make']} {q['model']}: "
                         f"{q['failure_rate']:.1%} of {q['n_tests']:,} tests")

    if p.is_sparse:
        lines.append(f"\nNOTE: only {p.n_tests} tests — below the "
                     f"{MIN_TESTS_FOR_CONFIDENCE}-test threshold for a "
                     f"reliable figure.")
    return "\n".join(lines)


def _cache_key(profile: VehicleProfile) -> str:
    """FR4.5. Keyed on everything that can change the answer — the rendered
    data, the prompt, the model, and the generation settings. Reasoning depth
    and temperature were previously outside the key, so turning the thinking
    level down served yesterday's answer and looked like the setting had done
    nothing."""
    payload = json.dumps({"data": render_data_block(profile), "system": SYSTEM,
                          "model": MODEL, "thinking": THINKING_LEVEL,
                          "temperature": TEMPERATURE,
                          "max_output_tokens": MAX_OUTPUT_TOKENS},
                         sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def cached_summary(profile: VehicleProfile) -> str | None:
    """Return a previously generated summary, or None — never calls the API.

    The app uses this to show an already-paid-for answer immediately, and to
    decide whether asking for a new one needs a deliberate button press.
    """
    if profile.n_tests == 0:
        return None
    path = CACHE_DIR / f"{_cache_key(profile)}.json"
    if path.exists():
        return json.loads(path.read_text())["summary"]
    return None


def summarise(profile: VehicleProfile, *, use_cache: bool = True) -> str | None:
    """Return a grounded summary, or None if the LLM layer is unavailable.

    None is a normal outcome, not an exception: FR4.4 requires the app to keep
    working with this layer down, showing the charts and a notice.
    """
    if profile.n_tests == 0:
        return (f"There are no MOT tests recorded for a "
                f"{profile.age_years}-year-old {profile.make} {profile.model} "
                f"in this dataset, so nothing can be said about it.")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{_cache_key(profile)}.json"
    if use_cache and cached.exists():
        return json.loads(cached.read_text())["summary"]

    data = render_data_block(profile)
    try:
        # A request with no ceiling is not graceful degradation: without this
        # the app spins on a hung connection instead of falling back to the
        # charts. Measured calls land near 10s, so 30s is generous.
        client = genai.Client(
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
        response = client.models.generate_content(
            model=MODEL,
            contents=(f"DATA:\n{data}\n\nWrite a three-sentence "
                      f"plain-English reliability summary."),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                # Generous relative to a three-sentence answer: this model
                # thinks before replying and the reasoning is drawn from the
                # same budget, so a tight cap returns an empty string rather
                # than a short summary.
                max_output_tokens=MAX_OUTPUT_TOKENS,
                temperature=TEMPERATURE,
                thinking_config=types.ThinkingConfig(
                    thinking_level=THINKING_LEVEL),
            ),
        )
    except errors.ClientError as e:
        # 4xx — bad or missing key, or the allowance is gone. Worth telling
        # apart: one is a thing to fix, the other a thing to wait out, and
        # "rejected the request (429)" reads like neither.
        if getattr(e, "code", None) == 429:
            spent = "per day" in str(e).lower() or "perday" in str(e).lower()
            log.warning("Gemini quota exhausted (%s) — serving without a "
                        "summary",
                        "daily allowance" if spent else "rate limited")
        else:
            log.warning("Gemini rejected the request (%s) — serving without a "
                        "summary", getattr(e, "code", "4xx"))
        return None
    except errors.ServerError:
        log.warning("Gemini unavailable — serving without a summary")
        return None
    except errors.APIError as e:
        log.warning("Gemini API error (%s) — serving without a summary", e)
        return None
    except Exception as e:  # network failures surface as plain exceptions
        log.warning("could not reach Gemini (%s) — serving without a summary", e)
        return None

    usage = response.usage_metadata
    summary = (response.text or "").strip()
    # Why it stopped, before what it said. A summary cut off at the token
    # ceiling is still a non-empty string, so without this the page shows half
    # a sentence and caches it — the reasoning budget truncating a sparse
    # answer is exactly how the thinking level came to be set to minimal.
    reason = getattr((response.candidates or [None])[0], "finish_reason", None)
    if reason is not None and reason != types.FinishReason.STOP:
        log.warning("Gemini stopped early (%s) — serving without a summary",
                    reason)
        return None
    if not summary:
        # A safety filter or an empty candidate list, not an exception.
        log.warning("Gemini returned no text — serving without a summary")
        return None
    cached.write_text(json.dumps({
        "vehicle": asdict(profile) | {"top_defects": [], "by_mileage": [],
                                      "peers": []},
        "data_block": data,
        "summary": summary,
        "model": MODEL,
        "usage": {
            "input_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
            # Reasoning tokens are billed and drawn from the same output
            # budget, so they belong in the record even though they are
            # never shown.
            "thinking_tokens": getattr(usage, "thoughts_token_count", None),
        },
    }, indent=2))
    return summary


def credentials_available() -> bool:
    """Used by the app to show an honest 'AI summary unavailable' notice
    instead of a spinner that goes nowhere. google-genai reads either name."""
    return bool(os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY"))
