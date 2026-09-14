"""What it means for a summary to be grounded, in one place.

Both the grounding test and the evaluation harness read
this, so there is one definition of "traces to the data" rather than two that
drift — the same seam as defect_labels.py, for the same reason.

Three properties are worth defending, because each fails silently and each on
its own destroys the credibility of the layer:

  1. Every figure in a summary appears in the data block it was given.
  2. A vehicle with too few tests is declined rather than described.
  3. Nothing outside the dataset is volunteered: no prices, no recalls, no body
     types, no buying advice, no claim that an MOT failure rate is a
     reliability rating.

Property 2 needs 1 and 3 measured alongside it, and needs its own opposite
measured too. A model that declines everything scores perfectly on grounding
and is useless, so the harness also checks that well-covered vehicles are NOT
declined. A single number that can be maximised by refusing to answer is not a
measurement.
"""
from __future__ import annotations

import re

# A figure with optional thousands separators and decimals, and the "20k"
# shorthand the mileage axis is labelled with.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?k?", re.IGNORECASE)

# Declining to answer, which has to be told apart from describing a fault.
#
# A bare word list does not work here, and the first version of this proved it:
# "insufficient" matched "insufficient washer liquid" and "below the" matched
# "tread depth below the 1.6mm limit", so four well-covered vehicles were
# recorded as refusals. The vocabulary of not knowing and the vocabulary of
# broken cars overlap almost completely.
#
# So a refusal has to be about the evidence rather than about the car: an
# inability word within a short distance of a word for data.
_INABILITY = (r"not enough|too few|insufficient|cannot|can't|unable|"
              r"not possible|no meaningful|nothing can be|too small|"
              r"limited|not reliable enough|below the .{0,20}threshold")
_EVIDENCE = (r"data|tests?|sample|records?|evidence|figures?|"
             r"information|basis|conclusion|say|said|tell|draw")
REFUSAL = re.compile(
    rf"(?:{_INABILITY})(?:\W+\w+){{0,6}}\W+(?:{_EVIDENCE})\b"
    rf"|(?:{_EVIDENCE})\b(?:\W+\w+){{0,6}}\W+(?:{_INABILITY})",
    re.IGNORECASE)

# Things the dataset does not contain. Matched as phrases rather than single
# words so a summary can still say a brake pad is worn without being accused
# of quoting a price.
OUT_OF_SCOPE = {
    "a price": re.compile(r"[£$€]\s?\d|\b\d+\s?(?:pounds|gbp)\b"
                          r"|\bcosts?\s+(?:about|around|roughly|approximately|"
                          r"up\s+to)?\s*[£$€\d]", re.IGNORECASE),
    "repair expense": re.compile(r"\b(?:expensive|cheap|costly|pricey)\b"
                                 r"|\bcost of (?:the )?repair", re.IGNORECASE),
    "a recall": re.compile(r"\brecalls?\b", re.IGNORECASE),
    "a body type": re.compile(r"\b(?:hatchback|saloon|estate car|"
                              r"convertible|coupe|coupé|\bSUV\b|"
                              r"people carrier)\b", re.IGNORECASE),
    "a reliability rating": re.compile(
        r"\breliability (?:rating|score|ranking|index)\b"
        r"|\b(?:most|least) reliable\b", re.IGNORECASE),
    "buying advice": re.compile(
        r"\b(?:you should (?:buy|avoid)|we recommend|i recommend|worth buying|"
        r"a good buy|avoid this)\b", re.IGNORECASE),
    "manufacturer reputation": re.compile(
        r"\b(?:known for|notorious for|reputation for|renowned for|"
        r"well[- ]known problem)\b", re.IGNORECASE),
}


def numbers_in(text: str) -> list[str]:
    """Every figure in a piece of text, as written."""
    return [m.group(0) for m in _NUMBER.finditer(text)]


def _value(written: str) -> float:
    """The numeric value of a written figure, expanding the "20k" shorthand.

    The mileage axis is labelled 20k/40k/…/160k+ and a summary spells that out
    as "20,000". Without this the expansion reads as an invented figure, which
    is how the first run of the harness accused four summaries of making up the
    mileage bands they had been handed."""
    written = written.replace(",", "")
    if written[-1:].lower() == "k":
        return float(written[:-1]) * 1000
    return float(written)


def _decimals(written: str) -> int:
    written = written.rstrip("kK")
    return len(written.split(".")[1]) if "." in written else 0


def _is_rounding_of(written: str, source: float) -> bool:
    """Whether a figure is the same one, said less precisely.

    Two ways a summary legitimately restates a number it was given:

    "42%" for a block's 42.0% — the same value to the precision quoted. And
    "over 265,000" for 265,905 — deliberately coarsened to a round number,
    which is good writing rather than invention.

    A relative tolerance would be the obvious way to allow the second and is
    the wrong tool: 1% of 42.0 admits 42.3, a different figure. So the
    coarsening rule is tied to the trailing zeros actually written — a number
    given to the nearest thousand may sit within a thousand of its source, and
    a number given exactly may not move at all.
    """
    value = _value(written)
    if round(source, _decimals(written)) == value:
        return True
    bare = written.replace(",", "").rstrip("kK")
    trailing = len(re.search(r"0*$", bare).group(0))
    if written[-1:].lower() == "k":
        trailing += 3
    if trailing >= 2 and "." not in bare:
        return 0 <= source - value < 10 ** trailing
    return False


def ungrounded(summary: str, data_block: str) -> list[str]:
    """Figures in the summary that are not in the data block it was given."""
    sources = [_value(n) for n in numbers_in(data_block)]
    return [n for n in numbers_in(summary)
            if not any(_is_rounding_of(n, s) for s in sources)]


def refused(summary: str) -> bool:
    return REFUSAL.search(summary) is not None


def truncated(summary: str) -> bool:
    """Whether the answer stops mid-thought.

    Worth checking from the output side as well as from finish_reason: a
    summary cut off at the token ceiling is a non-empty string, and one of the
    cached answers ends "Across all age groups of this model, failure rates".
    """
    return not summary.rstrip().endswith((".", "!", "?", '."', ".)"))


def out_of_scope_claims(summary: str) -> list[str]:
    """Which out-of-scope things a summary volunteered, if any."""
    return [name for name, pattern in OUT_OF_SCOPE.items()
            if pattern.search(summary)]
