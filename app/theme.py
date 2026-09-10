"""Palette and CSS for the MOTIntel shell.

Colour carries meaning rather than decoration: a failure rate is painted
against the all-cars average for that age, so green/amber/red says "better
than typical / about typical / worse than typical". Severity dots reuse the
same three, which is why the key beside the defect table is worth its space.
"""
from __future__ import annotations

DARK = {
    "bg": "#0a1120", "rail": "#0d1729", "panel": "#111c31",
    "panel_2": "#16223a", "border": "#1e2d49", "border_soft": "#182742",
    "text": "#e8eefb", "muted": "#8598ba", "faint": "#5e7093",
    "accent": "#3b82f6", "accent_2": "#60a5fa",
    "accent_soft": "rgba(59,130,246,.14)",
    "good": "#22c55e", "good_soft": "rgba(34,197,94,.13)",
    "warn": "#f59e0b", "warn_soft": "rgba(245,158,11,.13)",
    "bad": "#ef4444", "bad_soft": "rgba(239,68,68,.13)",
    "teal": "#2dd4bf", "track": "#1b2a45",
    "grid": "rgba(255,255,255,.055)",
    "hero": "linear-gradient(115deg,#16233c 0%,#0f1c31 45%,#122votes 100%)",
    "plotly": "plotly_dark",
    "shadow": "0 18px 40px -28px rgba(0,0,0,.9)",
}
DARK["hero"] = "linear-gradient(115deg,#17253f 0%,#101d33 48%,#0e1a2e 100%)"

LIGHT = {
    "bg": "#f2f5fa", "rail": "#ffffff", "panel": "#ffffff",
    "panel_2": "#f5f8fd", "border": "#e0e7f1", "border_soft": "#eaeff7",
    "text": "#0e1a2c", "muted": "#5a6b86", "faint": "#8496ae",
    "accent": "#2563eb", "accent_2": "#3b82f6",
    "accent_soft": "rgba(37,99,235,.09)",
    "good": "#15803d", "good_soft": "rgba(21,128,61,.10)",
    "warn": "#b45309", "warn_soft": "rgba(180,83,9,.10)",
    "bad": "#b91c1c", "bad_soft": "rgba(185,28,28,.10)",
    "teal": "#0f766e", "track": "#e6ecf6",
    "grid": "rgba(14,26,44,.08)",
    "hero": "linear-gradient(115deg,#e9effb 0%,#f3f6fc 48%,#eef3fb 100%)",
    "plotly": "plotly_white",
    "shadow": "0 14px 32px -26px rgba(15,30,60,.5)",
}


def palette(mode: str) -> dict:
    return DARK if mode == "dark" else LIGHT


def css(p: dict) -> str:
    return f"""
<style>
  :root {{
    --bg:{p['bg']}; --rail:{p['rail']}; --panel:{p['panel']};
    --panel2:{p['panel_2']}; --border:{p['border']};
    --border-soft:{p['border_soft']}; --text:{p['text']};
    --muted:{p['muted']}; --faint:{p['faint']}; --accent:{p['accent']};
    --accent2:{p['accent_2']}; --accent-soft:{p['accent_soft']};
    --good:{p['good']}; --good-soft:{p['good_soft']};
    --warn:{p['warn']}; --warn-soft:{p['warn_soft']};
    --bad:{p['bad']}; --bad-soft:{p['bad_soft']};
    --track:{p['track']}; --shadow:{p['shadow']};
  }}

  .stApp {{ background: var(--bg); color: var(--text); }}
  [data-testid="stToolbar"] {{ display: none !important; }}
  /* config.toml paints the header with the configured theme's background, so
     in the other theme it sat across the top as a dark band. */
  [data-testid="stHeader"] {{ background: transparent !important; }}
  footer, #MainMenu {{ visibility: hidden; }}
  .block-container {{ padding: 1.4rem 1.8rem 2.4rem; max-width: 1720px; }}
  h1,h2,h3,h4,p,span,label,li,div {{ color: var(--text); }}

  /* ---------- left rail ---------- */
  [data-testid="stSidebar"] {{
    background: var(--rail); border-right: 1px solid var(--border);
  }}
  [data-testid="stSidebar"] .block-container {{ padding-top: 1.2rem; }}
  .mot-brand {{ display:flex; align-items:center; gap:11px; padding: 4px 4px 18px; }}
  .mot-brand .mark {{
    width:38px; height:38px; border-radius:11px; flex:none;
    background: var(--accent); display:grid; place-items:center;
  }}
  .mot-brand .name {{ font-size:19px; font-weight:750; letter-spacing:-.4px; }}
  .mot-brand .tag {{ font-size:11.5px; color:var(--muted); margin-top:1px; }}

  /* The radio is rendered as navigation. Streamlit exposes the option as
     data-testid="stRadioOption" with a data-selected attribute — the input is
     visually hidden, so :checked never matches and the active state has to key
     off the attribute. The 16px circle is the first child of the inner div. */
  [data-testid="stSidebar"] [role="radiogroup"] {{ gap:3px; }}
  [data-testid="stRadioOption"] {{
    padding: 9px 12px; border-radius:10px; width:100%;
    transition: background .13s ease;
  }}
  [data-testid="stRadioOption"]:hover {{ background: var(--panel2); }}
  [data-testid="stRadioOption"] > div > div > div:first-child {{
    display:none !important;
  }}
  [data-testid="stRadioOption"][data-selected="true"] {{
    background: var(--accent); box-shadow: var(--shadow);
  }}
  [data-testid="stRadioOption"][data-selected="true"] * {{
    color:#fff !important; font-weight:640 !important;
  }}
  [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] p {{
    font-size:13.5px; font-weight:520; margin:0;
  }}

  /* ---------- cards ---------- */
  .card {{
    background: var(--panel); border:1px solid var(--border);
    border-radius:16px; padding:18px 20px; height:100%;
    box-shadow: var(--shadow);
  }}
  .card-h {{ display:flex; align-items:center; gap:10px; margin-bottom:14px; }}
  .card-h .ico {{
    width:30px; height:30px; border-radius:9px; flex:none;
    background: var(--accent-soft); display:grid; place-items:center;
  }}
  .card-h .t {{ font-size:16.5px; font-weight:680; letter-spacing:-.2px; }}
  .card-h .act {{
    margin-left:auto; font-size:12px; color:var(--accent); font-weight:600;
  }}
  .card-h .pill {{
    margin-left:auto; font-size:11px; font-weight:600; color:var(--muted);
    border:1px solid var(--border); border-radius:999px; padding:4px 10px;
  }}

  /* Streamlit's own bordered container, dressed as one of our cards so the
     control panel matches the rest of the grid. */
  [data-testid="stVerticalBlockBorderWrapper"] {{
    background: var(--panel); border:1px solid var(--border) !important;
    border-radius:16px; box-shadow: var(--shadow);
  }}
  [data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] {{
    background: transparent; border:0 !important; box-shadow:none;
  }}

  /* ---------- vehicle hero ---------- */
  .hero {{
    background: {p['hero']}; border:1px solid var(--border);
    border-radius:16px; padding:22px 26px; height:100%;
    position:relative; overflow:hidden;
  }}
  .hero .mk {{ font-size:12.5px; letter-spacing:.16em; color:var(--muted);
               font-weight:640; }}
  .hero .md {{ font-size:46px; font-weight:780; letter-spacing:-1.6px;
               line-height:1.06; margin:2px 0 14px; }}
  .hero .facts {{ display:flex; gap:10px; flex-wrap:wrap; }}
  .hero .fact {{
    display:flex; align-items:center; gap:7px; font-size:12.5px;
    color:var(--muted); background:var(--panel); border:1px solid var(--border);
    border-radius:9px; padding:7px 11px;
  }}
  .hero .fact b {{ color:var(--text); font-weight:600; }}
  .hero .silo {{
    position:absolute; right:-26px; bottom:-96px; opacity:.10;
    pointer-events:none; line-height:0;
  }}
  .hero .glow {{
    position:absolute; right:-90px; top:-70px; width:320px; height:320px;
    border-radius:50%; background:var(--accent-soft); filter:blur(46px);
    pointer-events:none;
  }}

  /* ---------- stat rows ---------- */
  .stat {{ display:flex; align-items:center; gap:12px; padding:9px 0; }}
  .stat .ico {{
    width:36px; height:36px; border-radius:11px; flex:none;
    background:var(--panel2); border:1px solid var(--border-soft);
    display:grid; place-items:center;
  }}
  .stat .v {{ font-size:20px; font-weight:700; letter-spacing:-.4px;
              line-height:1.15; }}
  .stat .l {{ font-size:12px; color:var(--muted); margin-top:1px; }}

  /* ---------- ranked table ---------- */
  .tbl {{ width:100%; border-collapse:collapse; }}
  .tbl th {{
    text-align:left; font-size:11.5px; font-weight:600; color:var(--muted);
    padding:9px 10px; background:var(--panel2);
    border-top:1px solid var(--border-soft);
    border-bottom:1px solid var(--border-soft);
  }}
  .tbl th:first-child {{ border-radius:8px 0 0 8px; }}
  .tbl th:last-child {{ border-radius:0 8px 8px 0; }}
  .tbl td {{ padding:11px 10px; border-bottom:1px solid var(--border-soft);
             font-size:13.5px; vertical-align:middle; }}
  .tbl tr:last-child td {{ border-bottom:0; }}
  .tbl .rk {{ color:var(--faint); font-size:12.5px; width:26px; }}
  .tbl .nm {{ font-weight:590; }}
  .tbl .sub {{ font-size:11.5px; color:var(--muted); margin-top:2px; }}
  .tbl .num {{ font-variant-numeric: tabular-nums; font-weight:640;
               white-space:nowrap; }}
  .tbl tr.me td {{ background:var(--accent-soft); }}
  .dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; }}
  .bar {{ height:7px; border-radius:99px; background:var(--track);
          min-width:70px; }}
  .bar > i {{ display:block; height:100%; border-radius:99px;
              background:var(--accent); }}
  .badge {{
    font-size:11px; font-weight:680; border-radius:7px; padding:3px 8px;
    white-space:nowrap;
  }}

  /* ---------- summary ---------- */
  .sum p {{ font-size:14.5px; line-height:1.66; margin:0 0 10px; max-width:64ch; }}
  .sum ul {{ margin:0; padding-left:18px; }}
  .sum li {{ font-size:13.5px; line-height:1.85; }}
  .callout {{
    display:flex; gap:11px; align-items:flex-start; margin-top:14px;
    background:var(--warn-soft); border:1px solid var(--warn);
    border-radius:12px; padding:13px 15px;
  }}
  .callout .txt {{ font-size:13px; line-height:1.6; color:var(--text); }}
  .callout u {{ text-decoration-color: var(--warn);
                text-underline-offset:3px; font-weight:650; }}

  /* ---------- insights ---------- */
  .ins {{ display:flex; gap:12px; align-items:flex-start;
          background:var(--panel2); border:1px solid var(--border-soft);
          border-radius:12px; padding:13px 15px; margin-bottom:10px; }}
  .ins .ico {{ width:34px; height:34px; border-radius:10px; flex:none;
               display:grid; place-items:center; }}
  .ins .t {{ font-size:13.5px; font-weight:660; }}
  .ins .d {{ font-size:12.5px; color:var(--muted); margin-top:3px;
             line-height:1.55; }}

  .foot {{ font-size:11.5px; color:var(--faint); line-height:1.7;
           padding:14px 4px 0; border-top:1px solid var(--border-soft);
           margin-top:6px; }}

  /* ---------- widgets ----------
     config.toml bakes one set of widget colours into the build, so in the
     theme that is not the configured one they leak through. These follow the
     CSS variables instead. Selectors are blunt on purpose: Streamlit's
     emotion class names change between versions. */
  [data-testid="stSelectbox"] div, [role="listbox"], [role="option"] {{
    background-color: var(--panel) !important; color: var(--text) !important;
  }}
  [data-testid="stSelectbox"] > div > div {{
    border:1px solid var(--border) !important; border-radius:10px !important;
  }}
  [data-testid="stSelectbox"] input, [data-testid="stSelectbox"] svg {{
    color: var(--text) !important; fill: var(--text) !important;
  }}
  [role="option"]:hover {{ background-color: var(--panel2) !important; }}
  .stSlider label, .stSelectbox label {{
    color: var(--muted) !important; font-size:12px !important;
    font-weight:560 !important;
  }}
  .stButton > button {{
    background: var(--accent); color:#fff; border:0; border-radius:10px;
    padding:.5rem 1.05rem; font-weight:640; font-size:13.5px;
  }}
  .stButton > button:hover {{ filter:brightness(1.09); color:#fff; }}

  /* Streamlit styles Plotly's SVG text with its own configured colour, which
     beats the colour set on the figure and made every axis unreadable in the
     non-configured theme. */
  .js-plotly-plot text {{ fill: var(--text) !important; }}
</style>
"""
