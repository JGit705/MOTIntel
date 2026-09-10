"""Palette and CSS for the dashboard, in both light and dark.

Colour carries meaning rather than decoration: a failure rate is painted
against the all-cars average for that age, so green/amber/red says "better
than typical / about typical / worse than typical" rather than simply looking
pleasant. That is the difference between a chart that decorates a number and
one that helps somebody judge it.
"""
from __future__ import annotations

DARK = {
    "bg": "#0b1220", "panel": "#131c2e", "panel_2": "#182236",
    "border": "#22304a", "text": "#e6edf7", "muted": "#8ea0bd",
    "accent": "#3b82f6", "accent_soft": "rgba(59,130,246,.14)",
    "good": "#22c55e", "good_soft": "rgba(34,197,94,.14)",
    "warn": "#f59e0b", "warn_soft": "rgba(245,158,11,.14)",
    "bad": "#ef4444", "bad_soft": "rgba(239,68,68,.14)",
    "teal": "#14b8a6", "grid": "rgba(255,255,255,.07)",
    "header": "linear-gradient(100deg,#132038 0%,#0e1830 55%,#101c33 100%)",
    "plotly": "plotly_dark",
}

LIGHT = {
    "bg": "#f5f7fb", "panel": "#ffffff", "panel_2": "#f0f3f9",
    "border": "#dfe5ef", "text": "#0f1b2d", "muted": "#5b6b85",
    "accent": "#2563eb", "accent_soft": "rgba(37,99,235,.10)",
    "good": "#15803d", "good_soft": "rgba(21,128,61,.10)",
    "warn": "#b45309", "warn_soft": "rgba(180,83,9,.10)",
    "bad": "#b91c1c", "bad_soft": "rgba(185,28,28,.10)",
    "teal": "#0f766e", "grid": "rgba(15,27,45,.09)",
    "header": "linear-gradient(100deg,#e8eefb 0%,#eef2fa 55%,#e9effc 100%)",
    "plotly": "plotly_white",
}


def palette(mode: str) -> dict:
    return DARK if mode == "dark" else LIGHT


def css(p: dict) -> str:
    return f"""
<style>
  :root {{
    --bg:{p['bg']}; --panel:{p['panel']}; --panel2:{p['panel_2']};
    --border:{p['border']}; --text:{p['text']}; --muted:{p['muted']};
    --accent:{p['accent']}; --good:{p['good']}; --warn:{p['warn']};
    --bad:{p['bad']}; --teal:{p['teal']};
  }}

  .stApp {{ background: var(--bg); color: var(--text); }}
  [data-testid="stHeader"] {{ background: transparent; }}
  .block-container {{ padding-top: 1.6rem; max-width: 1500px; }}

  /* Streamlit's floating toolbar sits in the top-right corner and keeps its
     hit area even when its buttons are not visible, so it silently swallowed
     every click on the theme switch beneath it. There is nothing on it worth
     keeping for a local app. */
  [data-testid="stToolbar"] {{ display: none !important; }}

  h1,h2,h3,h4,p,span,label,li {{ color: var(--text); }}

  /* ---- masthead ---- */
  .mot-head {{
    background: {p['header']};
    border: 1px solid var(--border); border-radius: 16px;
    padding: 18px 22px; margin-bottom: 18px;
    display: flex; align-items: center; gap: 16px;
  }}
  .mot-mark {{
    width: 42px; height: 42px; border-radius: 12px; flex: none;
    background: var(--accent); display: grid; place-items: center;
    font-size: 22px;
  }}
  .mot-title {{ font-size: 26px; font-weight: 700; letter-spacing: -.4px; }}
  .mot-sub {{ color: var(--muted); font-size: 13px; margin-top: 2px; }}
  .mot-pill {{
    margin-left: auto; font-size: 12px; color: var(--muted);
    border: 1px solid var(--border); border-radius: 999px;
    padding: 6px 12px; background: var(--panel);
  }}

  /* ---- metric cards ---- */
  .mot-card {{
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 14px; padding: 16px 18px; height: 100%;
  }}
  .mot-card .lab {{ color: var(--muted); font-size: 13px; font-weight: 500; }}
  .mot-card .val {{ font-size: 40px; font-weight: 700; line-height: 1.15;
                    letter-spacing: -1px; margin-top: 2px; }}
  .mot-card .note {{ font-size: 12.5px; color: var(--muted); margin-top: 4px; }}
  .mot-card .delta {{ font-size: 13px; font-weight: 600; margin-top: 4px; }}

  /* ---- panels ---- */
  .mot-panel {{
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 14px; padding: 16px 18px; margin-bottom: 6px;
  }}
  .mot-panel h4 {{ margin: 0 0 4px 0; font-size: 17px; font-weight: 650; }}
  .mot-panel .body {{ font-size: 15px; line-height: 1.62; }}
  .mot-panel .cite {{ font-size: 12px; color: var(--muted); margin-top: 10px; }}

  .mot-sechead {{ font-size: 17px; font-weight: 650; margin: 6px 0 2px 2px; }}
  .mot-secsub  {{ font-size: 12.5px; color: var(--muted); margin: 0 0 8px 2px; }}

  /* ---- widgets ----
     config.toml bakes a single set of widget colours into the build, so in
     the theme that is not the configured one they leak through — the model
     dropdown stayed dark on a light page. These override the widget internals
     from the CSS variables instead, which follow the toggle. The selectors are
     blunt on purpose: Streamlit's emotion class names change between
     versions, so structure and data-testid are the only stable handles. */
  [data-testid="stSelectbox"] div,
  [role="listbox"], [role="option"] {{
    background-color: var(--panel) !important;
    color: var(--text) !important;
  }}
  [data-testid="stSelectbox"] > div > div {{
    border: 1px solid var(--border) !important; border-radius: 10px !important;
  }}
  [data-testid="stSelectbox"] input,
  [data-testid="stSelectbox"] svg {{ color: var(--text) !important;
                                     fill: var(--text) !important; }}
  [role="option"]:hover {{ background-color: var(--panel2) !important; }}

  /* Streamlit styles Plotly's SVG text with its own configured text colour,
     which wins over the colour set on the figure — in light mode every axis
     and label rendered in the dark theme's near-white and was unreadable. */
  .js-plotly-plot text {{ fill: var(--text) !important; }}
  .stSlider label, .stSelectbox label {{
    color: var(--muted) !important; font-size: 13px !important;
    font-weight: 500 !important;
  }}
  .stButton > button {{
    background: var(--accent); color: #fff; border: 0; border-radius: 10px;
    padding: .55rem 1.1rem; font-weight: 600;
  }}
  .stButton > button:hover {{ filter: brightness(1.08); color: #fff; }}
  [data-testid="stDataFrame"] {{ border-radius: 12px; overflow: hidden; }}
  footer, #MainMenu {{ visibility: hidden; }}

  /* the theme switch sits beside the masthead, so line it up with it */
  [data-testid="stCheckbox"] {{ margin-top: 24px; }}
</style>
"""
