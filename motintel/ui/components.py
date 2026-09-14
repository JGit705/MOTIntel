"""HTML building blocks for the dashboard.

Kept apart from app.py so the page reads as a layout rather than a wall of
f-strings, and so a card looks the same everywhere it is used. Anything a
building block prints that neither the data nor this code wrote — the model's
words above all — is escaped here.
"""
from __future__ import annotations

import math
from html import escape

# Line icons, drawn rather than pulled from a font so they inherit currentColor
# and stay crisp at any zoom. Only where an icon carries meaning — good, needs
# attention, a check to make, an empty state — never as a tile beside a title.
ICONS = {
    "list": '<path d="M8 6h12M8 12h12M8 18h12M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
    "trend": '<path d="M3 17l6-6 4 4 8-8"/><path d="M21 7v5h-5"/>',
    "scales": '<path d="M12 4v16M7 8h10M5 8l-2.5 6h5zM19 8l-2.5 6h5z"/>',
    "doc": '<path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/>'
           '<path d="M14 3v5h5"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.5 2.5 4.5-5"/>',
    "car": '<path d="M5 17h14M6.5 17v2M17.5 17v2"/>'
           '<path d="M4 17l1.2-5.2A2 2 0 017.2 10h9.6a2 2 0 011.95 1.55L20 17z"/>'
           '<path d="M7.5 14h.01M16.5 14h.01"/>',
    "warn": '<path d="M12 4.5L2.8 20h18.4z"/><path d="M12 10v4M12 17h.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    "cog": '<circle cx="12" cy="12" r="3"/>'
           '<path d="M19.4 15a1.7 1.7 0 00.3 1.9l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 '
           '1.7 0 00-1.9-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1A1.7 1.7 0 '
           '008.9 19a1.7 1.7 0 00-1.9.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 '
           '00.3-1.9 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1A1.7 1.7 0 004.6 8.9a1.7 '
           '1.7 0 00-.3-1.9l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.9.3H9a1.7 '
           '1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.9-.3l.1'
           '-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.9V9a1.7 1.7 0 001.5 1H21a2 '
           '2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z"/>',
}


def icon(name: str, size: int = 17, colour: str = "currentColor",
         width: float = 1.7) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" '
            f'fill="none" stroke="{colour}" stroke-width="{width}" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'{ICONS.get(name, "")}</svg>')


def compact(n: int) -> str:
    """A large count as a reader says it: 1,284,105 is "1.28M"."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if n >= 100_000:
        return f"{n / 1_000:.0f}k"
    return f"{n:,}"


def signed_pts(delta: float) -> str:
    """A difference in percentage points, signed with a true minus sign.

    A hyphen is shorter than a plus and sits lower, so a column reading -3.5
    above +2.4 did not line up and the negative read as a dash."""
    pts = round(delta * 100, 1)
    sign = "+" if pts > 0 else "−" if pts < 0 else "±"
    return f"{sign}{abs(pts):.1f} pp"


def sentence_case(text: str) -> str:
    return text[:1].upper() + text[1:]


def bands_text(band_list: list[str]) -> str:
    """"0-3", "3-6" as a reader would say them: "0–3 and 3–6"."""
    shown = [b.replace("-", "–") for b in band_list]
    if len(shown) > 3:
        return f"{len(shown)} ages from {shown[0]}"
    return (" and ".join([", ".join(shown[:-1]), shown[-1]]) if len(shown) > 1
            else shown[0])


def card_header(title: str, pill: str = "", action: str = "") -> str:
    """Header row only. The card itself is a real Streamlit container — markup
    opened with st.markdown cannot wrap a chart, because Streamlit renders each
    element into a container of its own rather than into the open tag."""
    right = (f'<div class="act">{escape(action)}</div>' if action else
             f'<div class="pill">{escape(pill)}</div>' if pill else "")
    return f'<div class="card-h"><div class="t">{title}</div>{right}</div>'


def section_head(anchor: str, title: str, sub: str = "") -> str:
    """One of the questions the page is organised around, and the target of
    its jump link. A heading role rather than an <h2>, which Streamlit would
    decorate with a hover anchor of its own."""
    return (f'<div id="{anchor}" class="sec-anchor"></div>'
            f'<div class="sec-h"><div class="t" role="heading" aria-level="2">'
            f'{escape(title)}</div>'
            + (f'<div class="s">{sub}</div>' if sub else "") + '</div>')


def status(label: str, colour: str, ico: str = "check",
           big: bool = False) -> str:
    """A verdict in colour, always with an icon and words beside it — the
    colour is never the only thing saying it."""
    return (f'<span class="status{" big" if big else ""}" '
            f'style="color:{colour}">{icon(ico, 18 if big else 15, colour, 2)}'
            f'{escape(label)}</span>')


def figures(items: list[tuple]) -> str:
    """A row of figures — value, label, and an optional line of context. The
    first is the headline and is set larger. A definition list, so a screen
    reader announces each label with the number it describes."""
    cells = ""
    for n, (value, label, *rest) in enumerate(items):
        sub = f'<div class="sub">{rest[0]}</div>' if rest and rest[0] else ""
        cells += (f'<div class="fig{" lead" if n == 0 else ""}">'
                  f'<dt>{escape(label)}</dt><dd>{value}</dd>{sub}</div>')
    return f'<dl class="figures">{cells}</dl>'


def scale(rate: float, bench: float, tone: str, name: str) -> str:
    """This model and the age average on one scale, each marker labelled.

    The comparison the verdict is made from, drawn: the average as a line
    across the track with its value above, this model as a dot with its name
    and value below, so the two never have to be told apart by colour.
    """
    top = math.ceil(max(rate, bench) * 1.25 * 10) / 10
    mine, avg = rate / top * 100, bench / top * 100

    def edge(pct: float) -> str:
        # A label near either end is anchored to that end, not centred over
        # its marker and pushed out of the card.
        return " l" if pct < 12 else " r" if pct > 88 else ""

    return (
        f'<div class="scale" role="img" aria-label="{escape(name)} '
        f'{rate:.1%}, against an average of {bench:.1%}">'
        f'<span class="end">0%</span><div class="lane">'
        f'<div class="mark avg{edge(avg)}" style="left:{avg:.1f}%">'
        f'Average {bench:.1%}</div>'
        f'<div class="track"><i style="width:{mine:.1f}%;background:{tone}"></i>'
        f'<b class="avg-line" style="left:{avg:.1f}%"></b>'
        f'<b class="me-dot" style="left:{mine:.1f}%;background:{tone}"></b>'
        f'</div><div class="mark me{edge(mine)}" style="left:{mine:.1f}%;'
        f'color:{tone}">{escape(name)} {rate:.1%}</div></div>'
        f'<span class="end">{top:.0%}</span></div>')


def takeaways(good: list[tuple[str, str]], watch: list[tuple[str, str]],
              good_colour: str, watch_colour: str) -> str:
    """What looks good beside what needs attention — the page used to be almost
    entirely the second list."""
    def column(title: str, items, ico: str, colour: str) -> str:
        if not items:
            return ""
        rows = "".join(f'<li>{icon(ico, 16, colour, 2)}<div>'
                       f'<div class="t">{escape(t)}</div>'
                       f'<div class="d">{escape(d)}</div></div></li>'
                       for t, d in items)
        return f'<div class="tk"><div class="h">{title}</div><ul>{rows}</ul></div>'

    return (f'<div class="takeaways">'
            f'{column("What looks good", good, "check", good_colour)}'
            f'{column("What needs attention", watch, "warn", watch_colour)}'
            f'</div>')


def reason_head() -> str:
    return ('<div class="reason-head" aria-hidden="true"><span>#</span>'
            '<span>Failure reason</span><span>Frequency</span>'
            '<span>Severity</span><span>Typical repair</span><span></span></div>')


def reason(rank: int, headline: str, detail: str, share: float, severity: str,
           job: str, why: str, facts: list[tuple[str, str]]) -> str:
    """One failure reason: a scannable row that opens onto why it matters.

    Frequency, severity and the size of the repair sit in columns of their own.
    They are three different questions, and a "small job" badge beside a
    Dangerous fault must not read as "nothing to worry about". `severity`,
    `job`, `why` and the fact values carry markup and are composed by the
    caller; the headline and detail are escaped here.
    """
    rows = "".join(f'<div><dt>{escape(k)}</dt><dd>{v}</dd></div>'
                   for k, v in facts)
    return (f'<details class="reason"><summary>'
            f'<span class="rk">{rank}</span>'
            f'<span class="what"><span class="h">{escape(headline)}</span>'
            f'<span class="d">{escape(detail)}</span>'
            f'<span class="more">Why does this matter?</span></span>'
            f'<span class="meta"><span class="freq"><b>{share:.1%}</b> '
            f'of tests</span><span class="sev">{severity}</span>'
            f'<span class="job">{job}</span></span>'
            f'</summary><div class="why"><p>{why}</p><dl>{rows}</dl></div>'
            f'</details>')


def chips(items: list[tuple[str, str, str]]) -> str:
    """Each compared car's headline figure, with its colour key, before the
    detail — and the all-cars average they are all being read against."""
    return ('<div class="chips">' + "".join(
        f'<div class="chip"><span class="dot" style="background:{colour}">'
        f'</span><span class="n">{escape(name)}</span><b>{value}</b></div>'
        for colour, name, value in items) + '</div>')


def ai_insight(ins: dict, levels: dict[str, tuple[str, str]], good: str,
               warn: str) -> str:
    """The model's interpretation, laid out by the page — the model only
    supplies the words, and every one of them is escaped."""
    verdict = ins.get("verdict") or {}
    if ins.get("declined"):
        return f'<div class="ai"><p class="summary">{escape(verdict.get("summary", ""))}</p></div>'
    concern = ins.get("concern") or {}
    colour, soft = levels.get(concern.get("level"), levels["moderate"])
    points = "".join(
        f'<li>{icon("check" if k.get("kind") == "positive" else "warn", 16, good if k.get("kind") == "positive" else warn, 2)}'
        f'<div><div class="t">{escape(k.get("title", ""))}</div>'
        f'<div class="d">{escape(k.get("text", ""))}</div></div></li>'
        for k in ins.get("key_points") or [])
    detail = "".join(
        f'<p><b>{label}.</b> {escape(ins[key])}</p>'
        for label, key in (("Main concern", "main_concern"),
                           ("Main positive", "main_positive"),
                           ("Age and mileage", "age_mileage"))
        if ins.get(key))
    return (
        f'<div class="ai">'
        f'<div class="headline">{escape(verdict.get("headline", ""))}</div>'
        f'<p class="summary">{escape(verdict.get("summary", ""))}</p>'
        f'<div class="concern" style="background:{soft};border-color:{colour}">'
        f'<span class="q">Should I be concerned?</span>'
        f'<span class="a" style="color:{colour}">'
        f'{escape(concern.get("answer", ""))}</span>'
        f'<p>{escape(concern.get("reason", ""))}</p></div>'
        + (f'<div class="sub-h">Key takeaways</div>'
           f'<ul class="ticks">{points}</ul>' if points else "")
        + (f'<div class="sub-h">What to check when viewing one</div>'
           f'<p class="advice">{escape(ins["buying_advice"])}</p>'
           if ins.get("buying_advice") else "")
        + (f'<details class="plain"><summary>Show detailed analysis</summary>'
           f'{detail}</details>' if detail else "")
        + '</div>')


def disclosure(title: str, body: str) -> str:
    """A short, closed explanation. `body` carries markup."""
    return (f'<details class="plain"><summary>{escape(title)}</summary>'
            f'{body}</details>')


def badge(text: str, colour: str, soft: str) -> str:
    return (f'<span class="badge" style="color:{colour};background:{soft}">'
            f'{escape(text)}</span>')


def bar(fraction: float, colour: str = "var(--accent)") -> str:
    pct = max(2.0, min(100.0, fraction * 100))
    return (f'<div class="bar"><i style="width:{pct:.1f}%;background:{colour}">'
            f'</i></div>')


def callout(html: str, colour: str, soft: str, title: str = "") -> str:
    """A page-level alert. One component for every one of them. `html`
    carries markup."""
    head = f'<span class="ttl">{escape(title)}</span>' if title else ""
    return (f'<div class="callout" role="note" style="background:{soft};'
            f'border-color:{colour}">{icon("warn", 17, colour)}'
            f'<div class="txt">{head}{html}</div></div>')


def why(text: str) -> str:
    """What a figure means for a buyer, under the figure. `text` carries
    markup."""
    return f'<div class="why-line">{icon("info", 15, "var(--muted)")}<span>{text}</span></div>'


def table(body: str, head: str = "", cls: str = "", style: str = "") -> str:
    """A ranked table inside its own horizontal scroll box.

    Six columns do not fit a phone, and without the box the table does not
    overflow — it compresses, so "has an excessively worn bush" arrives one
    word per line. The box scrolls sideways; the page never does.
    """
    thead = f"<thead><tr>{head}</tr></thead>" if head else ""
    style = f' style="{style}"' if style else ""
    return (f'<div class="tbl-wrap"><table class="tbl {cls}"{style}>{thead}'
            f'<tbody>{body}</tbody></table></div>')


def note(html: str) -> str:
    """The small print under a panel. Every panel had its own copy of this
    style declaration, which is how three of them drifted apart."""
    return f'<div class="note">{html}</div>'


def defs(items: list[tuple[str, str, str]]) -> str:
    """Terms and what they mean, keyed by colour. Plain rows rather than a box
    per term — inside a card, boxes were cards inside a card."""
    rows = "".join(f'<div><dt><span class="dot" style="background:{colour}">'
                   f'</span>{escape(term)}</dt><dd>{escape(text)}</dd></div>'
                   for term, colour, text in items)
    return f'<dl class="defs">{rows}</dl>'


def empty(title: str, detail: str, ico: str = "cog") -> str:
    """A panel with nothing to show for this selection.

    Distinct from a panel that failed: the dashed border says "nothing here
    for this selection", where a line of grey text in a chart-sized card read
    as something that did not load. `detail` carries markup, so it is composed
    by the caller rather than escaped here.
    """
    return (f'<div class="empty"><div class="ico">'
            f'{icon(ico, 16, "var(--muted)")}</div>'
            f'<div><div class="t">{escape(title)}</div>'
            f'<div class="d">{detail}</div></div></div>')
