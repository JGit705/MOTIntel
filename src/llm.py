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

from config import PROCESSED
from queries import MIN_TESTS_FOR_CONFIDENCE, VehicleProfile

log = logging.getLogger(__name__)

# Overridable so a quota-limited free tier can be pointed at a smaller model
# without editing code.
MODEL = os.environ.get("MOTINTEL_MODEL", "gemini-3.6-flash")
CACHE_DIR = PROCESSED / "llm_cache"

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


def _render(profile: VehicleProfile) -> str:
    """Render the retrieved rows as the model's entire world."""
    p, lines = profile, []
    lines.append(f"Vehicle: {p.make} {p.model}, {p.age_years} years old")
    lines.append(f"Tests in this make/model/age group: {p.n_tests:,}")
    if p.failure_rate is not None:
        lines.append(f"Failure rate for this group: {p.failure_rate:.1%}")

    if p.top_defects:
        lines.append("\nMost common failure items (share of tests in group):")
        for d in p.top_defects:
            lines.append(f"  - {d['category']}: {d['defect']} "
                         f"— {d['n_tests']:,} tests ({d['share_of_tests']:.1%})")

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
    """FR4.5. Keyed on the rendered data and the prompt, so a changed prompt or
    a rebuilt database produces a fresh answer rather than a stale hit."""
    payload = json.dumps({"data": _render(profile), "system": SYSTEM,
                          "model": MODEL}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


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

    data = _render(profile)
    try:
        client = genai.Client()
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
                max_output_tokens=2048,
                # Zero temperature: this is a reporting task over supplied
                # figures, and sampling variety buys nothing but drift away
                # from the numbers.
                temperature=0.0,
            ),
        )
    except errors.ClientError as e:
        # 4xx — bad or missing key, or the free tier's quota is spent.
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
