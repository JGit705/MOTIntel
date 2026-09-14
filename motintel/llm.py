"""FR4 — the AI interpretation layer, grounded in retrieved rows.

The architecture keeps four layers apart, and this is the last of them:

  1. raw MOT data          — DuckDB, offline
  2. calculated statistics — the exported Parquet and serving.build_profile
  3. derived findings      — serving.findings: better or worse than average,
                             sample size, the mileage threshold, and so on
  4. interpretation        — here

The model never produces a number the page shows. Every figure on the page
comes from layers 2 and 3; the model is handed those and asked what they mean
to a buyer. Its answer is structured JSON, which the page lays out, and it is
checked before it is cached or shown: a figure that is not in the data it was
given, a claim outside the dataset, or a refusal in the wrong place, and the
answer is discarded.

The grounding guarantee is structural as well as prompted. The model is handed
a rendered data block and nothing else: no vehicle knowledge, no web access, no
tools. If a fact is not in that block, the model has no route to it.

Served by Google's Gemini API. Nothing about the grounding depends on the
provider: the retrieval is SQL, the constraint is the system prompt plus the
checks, and the cache is on disk. Swapping vendor changes this file only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict

from google import genai
from google.genai import errors, types

from motintel import grounding
from motintel.config import PROCESSED
from motintel.grounding import truncated
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

# Zero temperature: this is interpretation of supplied figures, where sampling
# variety buys nothing but drift away from them.
TEMPERATURE = 0.0
MAX_OUTPUT_TOKENS = 2048

# Part of the cache key. Bumped whenever the shape of the answer changes, so an
# answer in an old shape is never served to a page expecting the new one.
FORMAT = "insight-v1"

SYSTEM = """You are the interpretation layer of MOTIntel, a tool for people
considering buying a used car. The application has already calculated every
figure and several findings; they are in the DATA block, which is the complete
set of facts available to you.

Your job is not to repeat those figures — the page already shows them. Identify
the two to four patterns in the data that matter most to someone considering
this model, and say what they mean in plain British English: how its failure
rate compares with other cars of the same age, which failure reasons dominate
and how serious they are, how age and mileage change the picture, and what is
worth checking when viewing one.

Rules:
- Use ONLY the DATA block. Do not add causes, mechanisms, model history,
  recalls, manufacturer reputation, or comparisons that are not present in it —
  not even facts you are confident are true.
- Use figures sparingly. Any figure you use must be copied exactly from the
  DATA block. Never work out figures of your own: no differences, totals,
  ratios or averages.
- Where the application's findings answer a question, use them rather than
  drawing your own conclusion from the raw rows.
- Say "this model", never "this car": the data describes many cars of this
  model, not the one being viewed.
- An MOT failure rate is not a reliability rating. It covers safety and
  emissions items at one annual test, and reflects how cars were maintained as
  much as how they were built. Do not call a model more or less reliable.
- Do not tell the reader to buy or avoid a car, and do not mention prices,
  valuations or repair costs. Say what to check directly ("Check the tyres for
  cuts"), not as a recommendation.

If the DATA block carries a NOTE that the test count is below the threshold,
set "declined" to true, write one sentence in verdict.summary saying there is
not enough data on this vehicle to draw a reliable conclusion, and leave every
other text field empty and key_points empty.

Otherwise set "declined" to false and fill in:
- verdict.headline: at most ten words, e.g. "Better than average, but mileage
  matters".
- verdict.summary: one or two sentences giving the overall interpretation.
- concern.level: low, moderate or high — how concerned a buyer should be, judged
  from the findings.
- concern.answer: two to four words answering "Should I be concerned?", e.g.
  "Not particularly".
- concern.reason: one sentence explaining that answer.
- key_points: two to four, each with kind "positive" or "warning", a title of
  at most six words, and one sentence of text.
- main_concern and main_positive: one sentence each.
- buying_advice: one sentence on what to prioritise when viewing one.
- age_mileage: one sentence on how age and mileage change the picture."""

_TEXT = types.Schema(type=types.Type.STRING)
INSIGHT_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    required=["declined", "verdict", "concern", "key_points", "main_concern",
              "main_positive", "buying_advice", "age_mileage"],
    properties={
        "declined": types.Schema(type=types.Type.BOOLEAN),
        "verdict": types.Schema(
            type=types.Type.OBJECT, required=["headline", "summary"],
            properties={"headline": _TEXT, "summary": _TEXT}),
        "concern": types.Schema(
            type=types.Type.OBJECT, required=["level", "answer", "reason"],
            properties={
                "level": types.Schema(type=types.Type.STRING,
                                      enum=["low", "moderate", "high"]),
                "answer": _TEXT, "reason": _TEXT}),
        "key_points": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT, required=["kind", "title", "text"],
                properties={
                    "kind": types.Schema(type=types.Type.STRING,
                                         enum=["positive", "warning"]),
                    "title": _TEXT, "text": _TEXT})),
        "main_concern": _TEXT,
        "main_positive": _TEXT,
        "buying_advice": _TEXT,
        "age_mileage": _TEXT,
    },
)

# The enrichment's word for the size of a repair, in the words the page uses.
# "minor" and "major" are DVSA severity grades too, and the block carries both.
_JOB_SIZE = {"minor": "small", "moderate": "medium", "major": "big"}
_CLASSIFICATION = {"better": "better than average", "about": "about average",
                   "worse": "worse than average"}


def render_data_block(profile: VehicleProfile) -> str:
    """Render the retrieved rows and the derived findings as the model's
    entire world."""
    p, lines = profile, []
    age = (f"{p.age_band[0]}-{p.age_band[1]} years old" if p.age_band
           else f"{p.age_years} years old")
    lines.append(f"Vehicle: {p.make} {p.model}, {age}")
    lines.append(f"Tests in this make/model/age group: {p.n_tests:,}")
    if p.failure_rate is not None:
        lines.append(f"Failure rate for this group: {p.failure_rate:.1%}")
        if p.benchmark is not None:
            lines.append(f"Average failure rate for all cars of this age: "
                         f"{p.benchmark:.1%}")
            # Worked out here, from the figures as printed, so the model never
            # has to subtract — a difference it computed itself would be a
            # figure absent from the block.
            gap = round(p.failure_rate * 100, 1) - round(p.benchmark * 100, 1)
            lines.append("Difference from that average: " + (
                f"{abs(gap):.1f} percentage points "
                f"{'lower' if gap < 0 else 'higher'}" if abs(gap) >= 0.05
                else "none"))

    f = p.findings
    if f:
        lines.append("\nFindings calculated by the application:")
        if "classification" in f:
            lines.append(f"  - Against all cars of this age: "
                         f"{_CLASSIFICATION[f['classification']]}")
        if "sample" in f:
            lines.append(f"  - Sample size: {f['sample']} — the failure rate "
                         f"is accurate to within {f['margin_pp']:.1f} "
                         f"percentage points, 95 times in 100")
        if "dominant_area" in f:
            area, share = f["dominant_area"]
            lines.append(f"  - Largest part of the car among the failure "
                         f"reasons: {area}, {share:.0%} of them")
        if "mileage" in f:
            m = f["mileage"]
            lines.append(f"  - Mileage: the failure rate rises most sharply "
                         f"in the {m['after']} miles band, and reaches "
                         f"{m['top_rate']:.1%} at {m['top_band']} miles")
        if f.get("age_better") or f.get("age_worse"):
            lines.append("  - Ages where this model's failure rate is below "
                         "all cars: " + (", ".join(f["age_better"]) + " years"
                                         if f["age_better"] else "none"))
            lines.append("  - Ages where it is above all cars: " + (
                ", ".join(f["age_worse"]) + " years" if f["age_worse"]
                else "none"))
        if "rank_better_than" in f:
            lines.append(f"  - Once mileage is levelled out, its failure rate "
                         f"is lower than {f['rank_better_than']}% of the "
                         f"models ranked at this age")
        if f.get("mostly_dangerous"):
            lines.append("  - Failure reasons graded Dangerous in most of the "
                         "tests they appear in: "
                         + "; ".join(f["mostly_dangerous"]))

    if p.top_defects:
        lines.append("\nFailure reasons, most common first (share of all tests "
                     "in the group; one test can carry several):")
        for i, d in enumerate(p.top_defects, 1):
            lines.append(f"  {i}. {d['category']}: {d['defect']} "
                         f"— {d['n_tests']:,} tests ({d['share_of_tests']:.1%})")
            # The plain-English reading where the enrichment has produced one.
            # These are in the profile only if they passed the Phase 2 checks
            # and the overrides, so they are reviewed data by the time they
            # get here.
            if d.get("plain_english"):
                lines.append(f"      in plain English: {d['plain_english']}")
            if d.get("repair_area"):
                job = _JOB_SIZE.get(d.get("effort") or "")
                lines.append(f"      part of the car: {d['repair_area']}"
                             + (f"; size of repair job: {job}" if job else ""))
            if d.get("dangerous_share") is not None:
                lines.append(f"      graded Dangerous in "
                             f"{d['dangerous_share']:.0%} of the tests with "
                             f"this reason")

    if p.repair_areas:
        lines.append("\nWhere those failure reasons fall, by part of the car "
                     "(share of those reasons, by test count):")
        for area, share in p.repair_areas:
            lines.append(f"  - {area}: {share:.0%}")

    if p.severity:
        lines.append("\nHow serious the failures are (share of failed tests "
                     "with at least one defect of that grade; one test can "
                     "carry both):")
        for grade in ("Dangerous", "Major"):
            if grade in p.severity:
                lines.append(f"  - {grade}: {p.severity[grade]:.0%}")
        lines.append("  Dangerous means a direct and immediate risk to road "
                     "safety: the car must not be driven until it is repaired.")
        lines.append("  Major means it may affect safety or harm the "
                     "environment: the car fails and must be repaired.")

    if any(d.get("plain_english") for d in p.top_defects):
        seen, checks = set(), []
        for i, d in enumerate(p.top_defects, 1):
            check = d.get("forecourt_check")
            if check and check not in seen:
                seen.add(check)
                checks.append(f"  - {check} (reason {i}, "
                              f"{d['share_of_tests']:.1%} of tests)")
        if checks:
            lines.append("\nThings a buyer can check when viewing one, from "
                         "the failure reasons above:")
            lines.extend(checks)
        else:
            lines.append("\nNone of the failure reasons above can be checked "
                         "by eye when viewing one.")

    if p.age_curve:
        lines.append("\nFailure rate by age for this model, against all cars "
                     "of the same age:")
        for a in p.age_curve:
            others = (f"{a['all_cars']:.1%}" if a["all_cars"] is not None
                      else "no figure")
            lines.append(f"  - {a['age_band']} years: {a['failure_rate']:.1%} "
                         f"for this model ({a['n_tests']:,} tests), {others} "
                         f"for all cars")

    if p.by_mileage:
        lines.append("\nFailure rate by odometer band (all ages of this model):")
        for m in p.by_mileage:
            lines.append(f"  - {m['mileage_band']} miles: "
                         f"{m['failure_rate']:.1%} of {m['n_tests']:,} tests")

    if p.rank:
        position, total = p.rank
        lines.append(f"\nPosition once mileage is levelled out, among {total:,} "
                     f"models of this age (1 is the lowest failure rate): "
                     f"{position:,}")

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


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


def insight_text(insight: dict) -> str:
    """Every sentence the model wrote, as plain text.

    What the grounding checks read, and what the cache stores as the
    "summary" the evaluation harness has always measured — so a figure hidden
    in a key point's title is caught as surely as one in a paragraph.
    """
    verdict = insight.get("verdict") or {}
    concern = insight.get("concern") or {}
    parts = [verdict.get("headline"), verdict.get("summary"),
             concern.get("answer"), concern.get("reason")]
    for point in insight.get("key_points") or []:
        parts += [point.get("title"), point.get("text")]
    parts += [insight.get(k) for k in ("main_concern", "main_positive",
                                       "buying_advice", "age_mileage")]
    return "\n".join(_sentence(s) for s in parts if s and s.strip())


def problems(insight: dict, profile: VehicleProfile, block: str) -> list[str]:
    """Everything that stops an answer being shown.

    Run on every answer before it is cached. The prompt asks for all of this;
    these are what make it true of what actually reaches the page.
    """
    faults = []
    text = insight_text(insight)
    if not text:
        faults.append("no text")
    invented = grounding.ungrounded(text, block)
    if invented:
        faults.append(f"figures not in the data block: {invented[:4]}")
    claims = grounding.out_of_scope_claims(text)
    if claims:
        faults.append(f"volunteered {', '.join(claims)}")
    if profile.is_sparse and not insight.get("declined"):
        faults.append(f"described a {profile.n_tests}-test vehicle instead of "
                      f"declining")
    if not profile.is_sparse and insight.get("declined"):
        # Refusing where the data is ample is the failure a grounding-only
        # check would reward.
        faults.append(f"declined a {profile.n_tests:,}-test vehicle")
    if not insight.get("declined") and not (insight.get("verdict") or {}).get(
            "headline"):
        faults.append("no headline")
    return faults


def _cache_key(profile: VehicleProfile) -> str:
    """FR4.5. Keyed on everything that can change the answer — the rendered
    data, the prompt, the answer's shape, the model, and the generation
    settings. Reasoning depth and temperature were previously outside the key,
    so turning the thinking level down served yesterday's answer and looked
    like the setting had done nothing."""
    payload = json.dumps({"data": render_data_block(profile), "system": SYSTEM,
                          "format": FORMAT, "model": MODEL,
                          "thinking": THINKING_LEVEL,
                          "temperature": TEMPERATURE,
                          "max_output_tokens": MAX_OUTPUT_TOKENS},
                         sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _usable(summary: str | None) -> bool:
    """Whether a cached answer's text is fit to show.

    An answer cut off at the token ceiling is a non-empty string, so before
    finish_reason was checked, truncated summaries were written to disk and
    served from it indefinitely. Still read by the evaluation harness, over
    every entry the cache holds.
    """
    return bool(summary) and not truncated(summary)


def cached_insight(profile: VehicleProfile) -> dict | None:
    """A previously generated interpretation, or None — never calls the API.

    The app uses this to show an already-paid-for answer immediately, and to
    decide whether asking for a new one needs a deliberate button press.
    """
    if profile.n_tests == 0:
        return None
    path = CACHE_DIR / f"{_cache_key(profile)}.json"
    if path.exists():
        return json.loads(path.read_text()).get("insight")
    return None


def interpret(profile: VehicleProfile, *,
              use_cache: bool = True) -> dict | None:
    """Return a checked interpretation, or None if there is none to show.

    None is a normal outcome, not an exception: FR4.4 requires the app to keep
    working with this layer down — and an answer that fails its checks is
    treated exactly like an outage.
    """
    if profile.n_tests == 0:
        return {"declined": True, "verdict": {
            "headline": "", "summary": (
                f"There are no MOT tests recorded for a "
                f"{profile.age_years}-year-old {profile.make} "
                f"{profile.model} in this dataset, so nothing can be said "
                f"about it.")}}

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{_cache_key(profile)}.json"
    if use_cache and cached.exists():
        previous = json.loads(cached.read_text()).get("insight")
        if previous:
            return previous

    data = render_data_block(profile)
    try:
        # A request with no ceiling is not graceful degradation: without this
        # the app spins on a hung connection instead of falling back to the
        # charts. Measured calls land near 10s, so 30s is generous.
        client = genai.Client(
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
        response = client.models.generate_content(
            model=MODEL,
            contents=f"DATA:\n{data}\n\nReturn the interpretation as JSON.",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                # Generous relative to the answer: this model thinks before
                # replying and the reasoning is drawn from the same budget, so
                # a tight cap returns nothing rather than a short answer.
                max_output_tokens=MAX_OUTPUT_TOKENS,
                temperature=TEMPERATURE,
                response_mime_type="application/json",
                response_schema=INSIGHT_SCHEMA,
                thinking_config=types.ThinkingConfig(
                    thinking_level=THINKING_LEVEL),
            ),
        )
    except errors.ClientError as e:
        # 4xx — bad or missing key, or the allowance is gone. Worth telling
        # apart: one is a thing to fix, the other a thing to wait out.
        if getattr(e, "code", None) == 429:
            spent = "per day" in str(e).lower() or "perday" in str(e).lower()
            log.warning("Gemini quota exhausted (%s) — serving without an "
                        "interpretation",
                        "daily allowance" if spent else "rate limited")
        else:
            log.warning("Gemini rejected the request (%s) — serving without "
                        "an interpretation", getattr(e, "code", "4xx"))
        return None
    except errors.ServerError:
        log.warning("Gemini unavailable — serving without an interpretation")
        return None
    except errors.APIError as e:
        log.warning("Gemini API error (%s) — serving without an "
                    "interpretation", e)
        return None
    except Exception as e:  # network failures surface as plain exceptions
        log.warning("could not reach Gemini (%s) — serving without an "
                    "interpretation", e)
        return None

    usage = response.usage_metadata
    # Why it stopped, before what it said. A reply cut off at the token
    # ceiling is partial JSON, and read without this it looks merely malformed.
    reason = getattr((response.candidates or [None])[0], "finish_reason", None)
    if reason is not None and reason != types.FinishReason.STOP:
        log.warning("Gemini stopped early (%s) — serving without an "
                    "interpretation", reason)
        return None
    try:
        insight = json.loads(response.text or "")
    except json.JSONDecodeError:
        log.warning("Gemini returned malformed JSON — serving without an "
                    "interpretation")
        return None

    faults = problems(insight, profile, data)
    if faults:
        # Not cached: a rejected answer must not become a permanent one.
        log.warning("Gemini's interpretation failed its checks (%s) — not "
                    "shown", "; ".join(faults))
        return None

    cached.write_text(json.dumps({
        "vehicle": asdict(profile) | {"top_defects": [], "by_mileage": [],
                                      "peers": [], "age_curve": [],
                                      "repair_areas": []},
        "data_block": data,
        "insight": insight,
        "summary": insight_text(insight),
        "format": FORMAT,
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
    return insight


def credentials_available() -> bool:
    """Used by the app to show an honest 'AI unavailable' notice instead of a
    spinner that goes nowhere. google-genai reads either name."""
    return bool(os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY"))
