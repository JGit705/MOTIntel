"""HTML building blocks for the dashboard.

Kept apart from app.py so the page reads as a layout rather than a wall of
f-strings, and so a card looks the same everywhere it is used.
"""
from __future__ import annotations

from html import escape

# Line icons, drawn rather than pulled from a font so they inherit currentColor
# and stay crisp at any zoom.
ICONS = {
    "gauge": '<path d="M12 13a3 3 0 013-3"/><path d="M4.5 18a8.5 8.5 0 1115 0"/>'
             '<path d="M12 13l4-3"/>',
    "spark": '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>',
    "list": '<path d="M8 6h12M8 12h12M8 18h12M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
    "trend": '<path d="M3 17l6-6 4 4 8-8"/><path d="M21 7v5h-5"/>',
    "scales": '<path d="M12 4v16M7 8h10M5 8l-2.5 6h5zM19 8l-2.5 6h5z"/>',
    "bulb": '<path d="M9.5 18h5M10 21h4"/>'
            '<path d="M12 3a6 6 0 00-3.5 10.9c.6.5.9 1.2.9 1.9V16h5.2v-.2c0-.7'
            '.3-1.4.9-1.9A6 6 0 0012 3z"/>',
    "doc": '<path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/>'
           '<path d="M14 3v5h5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.5 2.5 4.5-5"/>',
    "car": '<path d="M5 17h14M6.5 17v2M17.5 17v2"/>'
           '<path d="M4 17l1.2-5.2A2 2 0 017.2 10h9.6a2 2 0 011.95 1.55L20 17z"/>'
           '<path d="M7.5 14h.01M16.5 14h.01"/>',
    "warn": '<path d="M12 4.5L2.8 20h18.4z"/><path d="M12 10v4M12 17h.01"/>',
    "fuel": '<path d="M4 20V6a2 2 0 012-2h5a2 2 0 012 2v14"/><path d="M3 20h11"/>'
            '<path d="M13 10h3a2 2 0 012 2v4a1.5 1.5 0 003 0V8l-2.5-2.5"/>',
    "cal": '<rect x="3.5" y="5" width="17" height="15" rx="2"/>'
           '<path d="M3.5 10h17M8 3.5v3M16 3.5v3"/>',
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
            f'stroke-linecap="round" stroke-linejoin="round">'
            f'{ICONS.get(name, "")}</svg>')


def card_header(title: str, ico: str = "gauge", accent: str = "var(--accent)",
                action: str = "", pill: str = "") -> str:
    """Header row only. The card itself is a real Streamlit container — markup
    opened with st.markdown cannot wrap a chart, because Streamlit renders each
    element into a container of its own rather than into the open tag."""
    right = (f'<div class="act">{escape(action)}</div>' if action else
             f'<div class="pill">{pill}</div>' if pill else "")
    return (f'<div class="card-h">'
            f'<div class="ico">{icon(ico, 16, accent)}</div>'
            f'<div class="t">{title}</div>{right}</div>')


def stat(value: str, label: str, ico: str = "doc",
         colour: str = "var(--accent)") -> str:
    return (f'<div class="stat"><div class="ico">{icon(ico, 16, colour)}</div>'
            f'<div><div class="v">{value}</div>'
            f'<div class="l">{label}</div></div></div>')


def badge(text: str, colour: str, soft: str) -> str:
    return (f'<span class="badge" style="color:{colour};background:{soft}">'
            f'{escape(text)}</span>')


def bar(fraction: float, colour: str = "var(--accent)") -> str:
    pct = max(2.0, min(100.0, fraction * 100))
    return (f'<div class="bar"><i style="width:{pct:.1f}%;background:{colour}">'
            f'</i></div>')


def insight(title: str, detail: str, ico: str, colour: str, soft: str) -> str:
    return (f'<div class="ins">'
            f'<div class="ico" style="background:{soft}">'
            f'{icon(ico, 17, colour)}</div>'
            f'<div><div class="t">{escape(title)}</div>'
            f'<div class="d">{escape(detail)}</div></div></div>')


def callout(html: str, colour: str = "var(--warn)") -> str:
    return (f'<div class="callout">{icon("warn", 17, colour)}'
            f'<div class="txt">{html}</div></div>')


def fact(ico: str, html: str) -> str:
    return f'<div class="fact">{icon(ico, 14)}<span>{html}</span></div>'
