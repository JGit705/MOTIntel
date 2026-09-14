"""Palette and CSS for the MOTIntel shell.

Colour carries meaning rather than decoration, and never alone — every coloured
verdict has an icon and words beside it:

  green  better than the benchmark
  amber  needs attention
  red    Dangerous, or worse than the benchmark
  blue   neutral information, and the model being looked at

One type scale — 12, 13, 14, 16, 20 and 40px — in the three weights Source Sans
actually ships. The page had grown to twenty-eight size and weight pairs, 590
and 680 among them, which the browser can only fake from the nearest real cut.

The surfaces the browser draws for us — selection, caret, scrollbars, focus
rings — are themed here too. They ship with defaults that belong to no design
system, and in the theme that config.toml was not built for they arrive in the
wrong one entirely.
"""
from __future__ import annotations

DARK = {
    "bg": "#0a1120", "rail": "#0d1729", "panel": "#111c31",
    "panel_2": "#16223a", "border": "#1e2d49", "border_soft": "#182742",
    "text": "#e8eefb", "muted": "#8598ba", "faint": "#7586a7",
    "accent": "#3b82f6", "accent_2": "#60a5fa",
    "accent_soft": "rgba(59,130,246,.14)",
    "good": "#22c55e", "good_soft": "rgba(34,197,94,.13)",
    "warn": "#f59e0b", "warn_soft": "rgba(245,158,11,.13)",
    "bad": "#f15b5b", "bad_soft": "rgba(239,68,68,.13)",
    "teal": "#2dd4bf", "track": "#1b2a45",
    "grid": "rgba(255,255,255,.055)",
    "plotly": "plotly_dark",
    "shadow": "0 18px 40px -28px rgba(0,0,0,.9)",
    # Browser-drawn surfaces.
    "code": "#93c5fd",
    "selection": "rgba(59,130,246,.34)",
    "thumb": "#2a3c5e", "thumb_hover": "#3a5079",
    # A peer bar in the comparison table. The track colour was used here and
    # painted every rival's bar in the colour of the empty groove behind it,
    # so the column read as missing data for every row but your own.
    "bar_peer": "#5977aa",
    # Further cars in a comparison. Blue and teal come first; these follow.
    # None is green, amber or red, which already mean better, needs attention
    # and worse on this page.
    "violet": "#a78bfa", "fuchsia": "#e879f9",
}

LIGHT = {
    "bg": "#f2f5fa", "rail": "#ffffff", "panel": "#ffffff",
    "panel_2": "#f5f8fd", "border": "#e0e7f1", "border_soft": "#eaeff7",
    "text": "#0e1a2c", "muted": "#4a5a73", "faint": "#5b6f8a",
    "accent": "#2563eb", "accent_2": "#1d4ed8",
    "accent_soft": "rgba(37,99,235,.09)",
    "good": "#147c3b", "good_soft": "rgba(21,128,61,.10)",
    "warn": "#ad5009", "warn_soft": "rgba(180,83,9,.10)",
    "bad": "#b91c1c", "bad_soft": "rgba(185,28,28,.10)",
    "teal": "#0f766e", "track": "#e6ecf6",
    "grid": "rgba(14,26,44,.08)",
    "plotly": "plotly_white",
    "shadow": "0 14px 32px -26px rgba(15,30,60,.5)",
    "code": "#1d4ed8",
    "selection": "rgba(37,99,235,.18)",
    "thumb": "#c6d2e4", "thumb_hover": "#a8b8d1",
    "bar_peer": "#6b86b1",
    "violet": "#6d28d9", "fuchsia": "#a21caf",
}


def palette(mode: str) -> dict:
    return DARK if mode == "dark" else LIGHT


def css(p: dict) -> str:
    # A magnifier for the search field, drawn in the muted text colour. A data
    # URI cannot read a CSS variable, so the colour is written in per theme.
    magnifier = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
                 "width='16' height='16' viewBox='0 0 24 24' fill='none' "
                 f"stroke='{p['muted'].replace('#', '%23')}' stroke-width='2' "
                 "stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E"
                 "%3Cpath d='M20 20l-3.5-3.5'/%3E%3C/svg%3E")
    return f"""
<style>
  :root {{
    --bg:{p['bg']}; --panel:{p['panel']};
    --panel2:{p['panel_2']}; --border:{p['border']};
    --border-soft:{p['border_soft']}; --text:{p['text']};
    --muted:{p['muted']}; --faint:{p['faint']}; --accent:{p['accent']};
    --accent2:{p['accent_2']}; --accent-soft:{p['accent_soft']};
    --good:{p['good']}; --good-soft:{p['good_soft']};
    --warn:{p['warn']}; --warn-soft:{p['warn_soft']};
    --bad:{p['bad']}; --bad-soft:{p['bad_soft']};
    --track:{p['track']}; --shadow:{p['shadow']};
    --code:{p['code']}; --thumb:{p['thumb']};
    --thumb-hover:{p['thumb_hover']};

    /* The type scale. Nothing on the page sets a size outside it. */
    --fs-xs:12px; --fs-sm:13px; --fs-base:14px; --fs-md:16px;
    --fs-lg:20px; --fs-xl:40px;
    --radius:14px;
  }}

  .stApp {{ background: var(--bg); color: var(--text); }}

  /* Streamlit's deploy and menu chrome, which a reader of this page has no
     use for. */
  [data-testid="stToolbarActions"], [data-testid="stAppDeployButton"],
  [data-testid="stMainMenu"] {{ display: none !important; }}
  [data-testid="stToolbar"] {{ background: transparent !important; }}
  /* config.toml paints the header with the configured theme's background, so
     in the other theme it sat across the top as a dark band. It is a fixed
     full-width strip over the top of the page, so it also lets clicks through
     to what is beneath — all but the running indicator, the page's only sign
     that a change is still loading. */
  [data-testid="stHeader"] {{ background: transparent !important;
                              pointer-events: none; }}
  [data-testid="stStatusWidget"] {{ pointer-events: auto; }}
  footer {{ visibility: hidden; }}
  /* 1200px. The page is one column of reading and tables, and at full width
     the bars ran a metre long and the small print 250 characters a line. */
  .block-container {{ padding: 1.6rem 2rem 2.6rem; max-width: 1200px; }}
  h1,h2,h3,h4,p,span,label,li,div {{ color: var(--text); }}

  /* ---------- browser surfaces ----------
     Selection, caret and scrollbars are drawn by the browser from its own
     defaults, which in the theme config.toml was not built for arrive in the
     opposite one. Cheap to set and conspicuous when missing. */
  ::selection {{ background: {p['selection']}; color: var(--text); }}
  .stApp {{ caret-color: var(--accent); scrollbar-color: var(--thumb) transparent; }}
  * {{ scrollbar-width: thin; }}
  ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
  ::-webkit-scrollbar-track {{ background: transparent; }}
  ::-webkit-scrollbar-thumb {{
    background: var(--thumb); border-radius: 99px;
    border: 2px solid transparent; background-clip: content-box;
  }}
  ::-webkit-scrollbar-thumb:hover {{ background: var(--thumb-hover);
                                     background-clip: content-box; }}
  ::-webkit-scrollbar-corner {{ background: transparent; }}

  /* Streamlit ships no visible focus ring on its primary button, so the
     keyboard path through the page was invisible. One ring, one colour,
     everywhere something can take focus. */
  :is(button, [role="radio"], [role="option"], summary, a,
      input, select, textarea, [tabindex]):focus-visible {{
    outline: 2px solid var(--accent) !important;
    outline-offset: 2px !important; border-radius: 8px;
  }}

  /* Figures are read down a column as often as across a row. */
  .figures dd, .scale, .tbl .num, .tbl .rk, .tbl td.val, .reason .freq,
  .chip b, .sbar .who {{ font-variant-numeric: tabular-nums; }}

  /* Streamlit's inline code is painted from the configured theme and arrived
     as green on near-black in light mode, at a size nothing else here uses. */
  code {{
    color: var(--code) !important; background: var(--accent-soft) !important;
    font-size: .88em !important; padding: 2px 6px; border-radius: 6px;
  }}
  a {{ color: var(--accent2); text-underline-offset: 3px; }}

  /* ---------- brand and controls ---------- */
  .mot-brand {{ display:flex; align-items:center; gap:12px; }}
  .mot-brand .mark {{
    width:36px; height:36px; border-radius:10px; flex:none;
    background: var(--accent); display:grid; place-items:center;
  }}
  .mot-brand .name {{ font-size:var(--fs-lg); font-weight:700;
                      letter-spacing:-.02em; line-height:1.1; }}
  .mot-brand .tag {{ font-size:var(--fs-xs); color:var(--muted); margin-top:2px; }}

  /* Search, age and comparison grouped into one control area, so they read
     as the three settings of one question rather than three unrelated boxes
     floating above the page. */
  [class*="st-key-controls"] {{
    background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius); box-shadow: var(--shadow);
    padding: 14px 20px 6px;
  }}
  [class*="st-key-controls"] [data-testid="stCaptionContainer"] p {{
    font-size: var(--fs-xs) !important; color: var(--muted) !important;
    margin-top: -6px;
  }}
  /* The search field reads as search before anything is typed. Streamlit
     1.63 draws the box as a react-aria combobox, so the hook is its group
     wrapper — the older baseweb selector matches nothing. */
  [class*="st-key-search"] .react-aria-ComboBox [role="group"] {{
    padding-left: 32px !important;
    background-image: url("{magnifier}") !important;
    background-repeat: no-repeat !important;
    background-position: 11px 50% !important;
  }}

  /* ---------- vehicle title ----------
     A page title on the page, not a card: the name, then what the data holds
     on the car in two short lines. */
  .hero {{ padding: 20px 2px 4px; }}
  .hero .title-row {{ display:flex; align-items:center; flex-wrap:wrap;
                      gap:8px 14px; }}
  .hero .title {{ font-size:var(--fs-xl); font-weight:700; line-height:1.1;
                  letter-spacing:-.03em; text-wrap:balance; }}
  .hero .title .mk {{ color:var(--muted); font-weight:400; }}
  .hero .tag {{ font-size:var(--fs-xs); font-weight:600; color:var(--accent2);
                border:1px solid var(--border); border-radius:999px;
                padding:3px 10px; white-space:nowrap; }}
  .hero .meta {{ margin-top:10px; font-size:var(--fs-base); color:var(--muted);
                 display:flex; flex-wrap:wrap; row-gap:4px; line-height:1.5; }}
  .hero .meta + .meta {{ margin-top:2px; }}
  .hero .meta b {{ color:var(--text); font-weight:600; }}
  .hero .meta i {{ font-style:normal; color:var(--faint); margin:0 10px; }}

  /* ---------- summary bar and jump links ----------
     The model, its rate and its verdict, with a link to each question —
     sticky on a wide screen so both stay in view down a long page. */
  [class*="st-key-summarybar"] {{
    background: var(--bg); padding: 10px 0;
    border-bottom: 1px solid var(--border-soft);
  }}
  @media (min-width: 900px) {{
    [class*="st-key-summarybar"] {{ position: sticky; top: 0; z-index: 90; }}
  }}
  .sbar {{ display:flex; align-items:center; justify-content:space-between;
           flex-wrap:wrap; gap:8px 24px; }}
  .sbar .who {{ display:flex; align-items:center; flex-wrap:wrap; gap:4px 12px;
                font-size:var(--fs-base); }}
  .sbar .who b {{ font-weight:700; }}
  .sbar .who .r {{ color:var(--muted); }}
  .sbar nav {{ display:flex; flex-wrap:wrap; gap:2px; }}
  .sbar nav a {{ font-size:var(--fs-sm); font-weight:600; color:var(--muted);
                 text-decoration:none; padding:6px 10px; border-radius:8px; }}
  .sbar nav a:hover {{ background:var(--panel2); color:var(--text); }}
  /* Room above a jump target for the sticky bar, which would otherwise sit
     on top of the heading the link was followed to. */
  .sec-anchor {{ scroll-margin-top: 72px; }}

  /* ---------- the questions ---------- */
  .sec-h {{ margin: 36px 0 14px; }}
  .sec-h .t {{ font-size:var(--fs-lg); font-weight:700; letter-spacing:-.02em;
               line-height:1.25; }}
  .sec-h .s {{ font-size:var(--fs-sm); color:var(--muted); margin-top:4px;
               max-width:80ch; }}

  /* ---------- cards ---------- */
  /* Wraps rather than squeezes: in a half-width card at a laptop's narrower
     widths, "AI buying insight" beside its pill broke one word to a line. */
  .card-h {{ display:flex; align-items:center; flex-wrap:wrap; gap:8px 12px;
             margin-bottom:16px; }}
  .card-h .t {{ font-size:var(--fs-md); font-weight:600; letter-spacing:-.01em;
                line-height:1.3; flex:1 1 auto; min-width:14ch; }}
  /* A count, not a link. In the accent it promised a click it never had. */
  .card-h .act {{
    margin-left:auto; font-size:var(--fs-xs); color:var(--muted); font-weight:600;
  }}
  .card-h .pill {{
    margin-left:auto; font-size:var(--fs-xs); font-weight:600; color:var(--muted);
    border:1px solid var(--border); border-radius:999px; padding:3px 10px;
    white-space:nowrap;
  }}

  /* Streamlit's own bordered container, dressed as one of our cards.
     Matched on the key the app gives it — `st.container(key=…)` renders as an
     `st-key-…` class — because the data-testid this used to select does not
     exist in Streamlit 1.63 and the whole rule had stopped applying. */
  [class*="st-key-motcard"] {{
    background: var(--panel); border:1px solid var(--border) !important;
    border-radius:var(--radius) !important; box-shadow: var(--shadow);
    padding:20px 24px;
  }}
  /* Two cards in a row end level. The column itself already stretches, but
     Streamlit's layout wrapper inside it is flex:0, so the shorter card kept
     its content height and left a ragged edge. Each step of the chain has to
     be told to fill. */
  [data-testid="stColumn"] > div {{ height: 100%; }}
  [data-testid="stColumn"] [data-testid="stLayoutWrapper"] {{
    flex: 1 1 auto; height: 100%;
  }}
  [data-testid="stColumn"] [class*="st-key-motcard"] {{ height: 100%; }}

  /* ---------- is it reliable? ---------- */
  .status {{ display:inline-flex; align-items:center; gap:6px;
             font-size:var(--fs-sm); font-weight:700; white-space:nowrap; }}
  .status svg {{ flex:none; }}
  .status.big {{ font-size:var(--fs-md); }}
  .verdict-row {{ display:flex; align-items:baseline; flex-wrap:wrap;
                  gap:4px 14px; margin-bottom:18px; }}
  .verdict-row p {{ margin:0; font-size:var(--fs-base); color:var(--muted); }}
  .change {{ font-size:var(--fs-sm); color:var(--muted); background:var(--panel2);
             border:1px solid var(--border-soft); border-radius:10px;
             padding:8px 12px; margin:-6px 0 18px; }}
  .change b {{ color:var(--text); font-weight:600; }}

  .figures {{
    display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr));
    align-items:end; gap:16px 24px; margin:0;
  }}
  .figures .fig {{ display:flex; flex-direction:column; }}
  .figures dd {{ order:1; margin:0; font-size:var(--fs-lg); font-weight:700;
                 letter-spacing:-.01em; line-height:1.2; }}
  .figures .lead dd {{ font-size:var(--fs-xl); letter-spacing:-.03em;
                       line-height:1; }}
  .figures dt {{ order:2; font-size:var(--fs-sm); font-weight:600;
                 margin-top:6px; line-height:1.3; }}
  .figures .sub {{ order:3; font-size:var(--fs-xs); color:var(--muted);
                   margin-top:2px; line-height:1.4; }}

  .scale {{ display:grid; grid-template-columns:auto 1fr auto; align-items:center;
            gap:12px; margin:46px 0 40px; }}
  .scale .end {{ font-size:var(--fs-xs); color:var(--faint); }}
  .scale .lane {{ position:relative; }}
  .scale .mark {{ position:absolute; white-space:nowrap; font-size:var(--fs-xs);
                  font-weight:700; transform:translateX(-50%); }}
  .scale .mark.l {{ transform:translateX(-6px); }}
  .scale .mark.r {{ transform:translateX(calc(-100% + 6px)); }}
  .scale .mark.avg {{ bottom:calc(100% + 12px); color:var(--text); }}
  .scale .mark.me {{ top:calc(100% + 12px); }}
  .scale .track {{ position:relative; height:12px; border-radius:99px;
                   background:var(--track); }}
  .scale .track i {{ display:block; height:100%; border-radius:99px; }}
  .scale .avg-line {{ position:absolute; top:-8px; bottom:-8px; width:3px;
                      margin-left:-1.5px; border-radius:2px;
                      background:var(--text); }}
  .scale .me-dot {{ position:absolute; top:50%; width:18px; height:18px;
                    margin:-9px 0 0 -9px; border-radius:50%;
                    border:3px solid var(--panel); }}

  .takeaways {{
    display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr));
    gap:16px 32px; padding-top:18px; border-top:1px solid var(--border-soft);
  }}
  .takeaways .h {{ font-size:var(--fs-sm); font-weight:700; margin-bottom:10px; }}
  .takeaways ul, .ticks {{ list-style:none; margin:0; padding:0; display:grid;
                           gap:10px; }}
  .takeaways li, .ticks li {{ display:flex; gap:10px; align-items:flex-start;
                              margin:0; }}
  .takeaways li svg, .ticks li svg {{ flex:none; margin-top:2px; }}
  .takeaways .t, .ticks .t {{ font-size:var(--fs-base); font-weight:600;
                              line-height:1.4; }}
  .takeaways .d, .ticks .d {{ font-size:var(--fs-sm); color:var(--muted);
                              line-height:1.5; }}

  /* ---------- what goes wrong? ----------
     Each failure reason a row that scans — name, frequency, severity, repair
     size — and opens onto why it matters. */
  .reason-head, .reason summary {{
    display:grid; align-items:center; gap:12px;
    grid-template-columns: 28px minmax(0, 1fr) 96px 150px 110px 16px;
  }}
  .reason-head {{ font-size:var(--fs-xs); font-weight:600; color:var(--muted);
                  padding:8px 12px; background:var(--panel2); border-radius:8px;
                  margin-bottom:4px; }}
  .reason {{ border-bottom:1px solid var(--border-soft); }}
  .reason:last-child {{ border-bottom:0; }}
  .reason summary {{ list-style:none; cursor:pointer; padding:12px;
                     border-radius:10px; transition: background .15s ease-out; }}
  .reason summary::-webkit-details-marker {{ display:none; }}
  .reason summary:hover {{ background:var(--panel2); }}
  .reason summary::after {{
    content:""; width:7px; height:7px; justify-self:center;
    border-right:2px solid var(--muted); border-bottom:2px solid var(--muted);
    transform: translateY(-2px) rotate(45deg); transition: transform .15s ease-out;
  }}
  .reason[open] summary::after {{ transform: translateY(2px) rotate(-135deg); }}
  .reason .meta {{ display:contents; }}
  .reason .rk {{ color:var(--faint); font-size:var(--fs-sm); }}
  .reason .what .h {{ display:block; font-size:var(--fs-base); font-weight:600;
                      line-height:1.35; }}
  .reason .what .d {{ display:block; font-size:var(--fs-sm); color:var(--muted);
                      margin-top:2px; line-height:1.4; }}
  .reason .what .more {{ display:block; font-size:var(--fs-xs); font-weight:600;
                         color:var(--accent2); margin-top:4px; }}
  .reason[open] .what .more {{ display:none; }}
  .reason .freq {{ font-size:var(--fs-xs); color:var(--muted); }}
  .reason .freq b {{ color:var(--text); font-size:var(--fs-base); font-weight:600; }}
  .reason .why {{ padding:0 12px 18px 52px; }}
  .reason .why p {{ font-size:var(--fs-base); line-height:1.6; margin:0 0 12px;
                    max-width:75ch; }}
  .reason .why dl {{ display:grid; margin:0; gap:10px 24px;
                     grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); }}
  .reason .why dt {{ font-size:var(--fs-xs); font-weight:600; color:var(--muted); }}
  .reason .why dd {{ margin:2px 0 0; font-size:var(--fs-sm); line-height:1.5; }}

  .why-line {{ display:flex; gap:8px; align-items:flex-start; margin-top:14px;
               font-size:var(--fs-sm); line-height:1.55; color:var(--text); }}
  .why-line svg {{ flex:none; margin-top:2px; }}

  /* The show-all and save buttons are secondary actions, not the page's one
     primary button. */
  [class*="st-key-more"] button, [data-testid="stDownloadButton"] button {{
    background: transparent !important; color: var(--accent2) !important;
    border: 1px solid var(--border) !important; font-weight:600 !important;
  }}
  [class*="st-key-more"] button:hover,
  [data-testid="stDownloadButton"] button:hover {{
    background: var(--panel2) !important; filter:none !important;
  }}
  [class*="st-key-more"] button p,
  [data-testid="stDownloadButton"] button p {{ color: var(--accent2) !important; }}

  /* ---------- tables ----------
     Six columns do not fit a phone. Left to itself the table did not
     overflow, it compressed, so it scrolls inside its own box instead and the
     page never scrolls sideways. */
  .tbl-wrap {{ width:100%; overflow-x:auto; overscroll-behavior-x:contain; }}
  .tbl {{ width:100%; border-collapse:collapse; min-width:430px; }}
  .tbl th {{
    text-align:left; font-size:var(--fs-xs); font-weight:600; color:var(--muted);
    padding:8px 12px; background:var(--panel2);
    border-top:1px solid var(--border-soft);
    border-bottom:1px solid var(--border-soft); white-space:nowrap;
  }}
  .tbl th:first-child {{ border-radius:8px 0 0 8px; }}
  .tbl th:last-child {{ border-radius:0 8px 8px 0; }}
  .tbl td {{ padding:12px; border-bottom:1px solid var(--border-soft);
             font-size:var(--fs-base); line-height:1.4; vertical-align:middle; }}
  .tbl tr:last-child td {{ border-bottom:0; }}
  .tbl .rk {{ color:var(--faint); font-size:var(--fs-sm); width:28px; }}
  .tbl .nm {{ font-weight:600; }}
  .tbl .sub {{ font-size:var(--fs-xs); color:var(--muted); margin-top:2px;
               font-weight:400; }}
  .tbl .num {{ font-weight:600; white-space:nowrap; }}
  .tbl tr.me td {{ background:var(--accent-soft); }}
  .tbl tr.gap td {{ text-align:center; color:var(--faint); font-size:var(--fs-xs);
                    padding:8px; }}
  /* Head to head: a label column, then one column per car. The values are
     sentences as often as figures, so these cells wrap where .num does not. */
  .tbl td.lbl {{ color:var(--muted); font-size:var(--fs-sm); width:26%; }}
  .tbl td.val {{ font-weight:600; }}
  .tbl td.val .badge {{ margin-left:8px; }}
  /* A car's colour key beside each value. The column heading does that job
     until the table stacks on a phone, where the cars wrap into rows. */
  .tbl td.val .k {{ display:none; }}
  .h2h-lead {{ font-size:var(--fs-md); line-height:1.5; margin:-4px 0 12px;
               max-width:72ch; }}
  .h2h-lead b {{ font-weight:700; }}
  .dot {{ width:9px; height:9px; border-radius:50%; display:inline-block;
          flex:none; }}
  .bar {{ height:8px; border-radius:99px; background:var(--track);
          min-width:70px; }}
  .bar > i {{ display:block; height:100%; border-radius:99px;
              background:var(--accent); }}
  .badge {{
    font-size:var(--fs-xs); font-weight:600; border-radius:6px; padding:2px 8px;
    white-space:nowrap;
  }}
  .chips {{ display:flex; flex-wrap:wrap; gap:8px; margin:0 0 14px; }}
  .chip {{ display:flex; align-items:center; gap:8px; padding:8px 12px;
           border:1px solid var(--border); background:var(--panel2);
           border-radius:10px; font-size:var(--fs-sm); }}
  .chip b {{ font-size:var(--fs-md); font-weight:700; }}

  /* ---------- prose inside panels ---------- */
  .lead-line {{ font-size:var(--fs-base); color:var(--muted); line-height:1.6;
                margin:-4px 0 12px; max-width:75ch; }}
  .lead-line b {{ color:var(--text); font-weight:600; }}
  .note {{ font-size:var(--fs-xs); color:var(--faint); line-height:1.6;
           margin-top:14px; max-width:90ch; }}
  .note b {{ color:var(--muted); font-weight:600; }}

  .callout {{
    display:flex; gap:12px; align-items:flex-start; margin-top:16px;
    border:1px solid; border-radius:12px; padding:12px 16px;
  }}
  .callout svg {{ flex:none; margin-top:2px; }}
  .callout .txt {{ font-size:var(--fs-base); line-height:1.55; }}
  .callout .ttl {{ display:block; font-weight:700; margin-bottom:2px; }}
  .callout .txt b {{ font-weight:600; }}
  .callout a {{ color:inherit; font-weight:600; }}

  /* Terms keyed by colour, one row each. */
  .defs {{ margin:18px 0 0; display:grid; gap:10px; }}
  .defs > div {{ display:grid; grid-template-columns:110px 1fr; gap:4px 16px; }}
  .defs dt {{ font-size:var(--fs-sm); font-weight:600; display:flex;
              align-items:center; gap:8px; height:20px; }}
  .defs dd {{ margin:0; font-size:var(--fs-sm); color:var(--muted);
              line-height:1.55; }}

  /* ---------- what should I do? ---------- */
  .ai .headline {{ font-size:var(--fs-lg); font-weight:700; line-height:1.3;
                   letter-spacing:-.01em; text-wrap:balance; }}
  .ai .summary {{ font-size:var(--fs-md); line-height:1.6; margin:6px 0 16px;
                  max-width:70ch; }}
  .ai .concern {{ display:grid; grid-template-columns:auto 1fr; align-items:baseline;
                  gap:2px 12px; padding:12px 16px; border:1px solid;
                  border-radius:12px; margin-bottom:6px; }}
  .ai .concern .q {{ font-size:var(--fs-sm); font-weight:600; color:var(--muted); }}
  .ai .concern .a {{ font-size:var(--fs-md); font-weight:700; }}
  .ai .concern p {{ grid-column:1 / -1; margin:2px 0 0; font-size:var(--fs-sm);
                    line-height:1.5; }}
  .ai .sub-h {{ font-size:var(--fs-sm); font-weight:700; margin:18px 0 10px; }}
  .ai .advice {{ font-size:var(--fs-base); line-height:1.6; margin:0; }}
  details.plain {{ margin-top:14px; }}
  details.plain summary {{ cursor:pointer; font-size:var(--fs-sm); font-weight:600;
                           color:var(--accent2); width:fit-content; }}
  details.plain[open] summary {{ margin-bottom:8px; }}
  details.plain p {{ font-size:var(--fs-sm); color:var(--muted); line-height:1.6;
                     margin:0 0 8px; max-width:75ch; }}
  details.plain p b {{ color:var(--text); font-weight:600; }}

  .chk-group {{ display:flex; align-items:center; flex-wrap:wrap; gap:8px;
                margin:16px 0 2px; font-size:var(--fs-sm); font-weight:700; }}
  .chk-group.first {{ margin-top:0; }}
  [class*="st-key-motcard"] [data-testid="stCheckbox"] label p {{
    font-size: var(--fs-base) !important; line-height:1.45;
  }}
  .progress {{ font-size:var(--fs-sm); color:var(--muted); margin:14px 0 8px; }}
  .final p {{ font-size:var(--fs-md); line-height:1.6; margin:8px 0 0;
              max-width:75ch; }}

  /* ---------- absent data ---------- */
  .empty {{ display:flex; gap:12px; align-items:flex-start;
            border:1px dashed var(--border); border-radius:12px;
            padding:16px; background:var(--panel2); margin-bottom:14px; }}
  .empty .ico {{ width:32px; height:32px; border-radius:8px; flex:none;
                 background:var(--panel); border:1px solid var(--border-soft);
                 display:grid; place-items:center; }}
  .empty .t {{ font-size:var(--fs-base); font-weight:600; }}
  .empty .d {{ font-size:var(--fs-sm); color:var(--muted); margin-top:4px;
               line-height:1.6; }}

  /* ---------- disclosure sections ----------
     The expander IS the card: its summary row is the card header, its body is
     the card body. A bordered container inside one would be a border inside a
     border, which is why the panels render bare in here. */
  [data-testid="stExpander"] {{
    border:1px solid var(--border) !important; border-radius:var(--radius);
    background: var(--panel); box-shadow: var(--shadow);
    overflow:hidden; margin-bottom:8px;
  }}
  [data-testid="stExpander"] details {{
    border:0 !important; background: var(--panel) !important;
  }}
  /* The summary keeps its own background in every state. Streamlit paints an
     open expander's header from config.toml, which is pinned to the dark
     theme, so in light mode the bar came out navy with the title lost inside
     it. */
  [data-testid="stExpander"] summary,
  [data-testid="stExpander"] details[open] > summary {{
    padding:14px 24px; background: var(--panel) !important;
    transition: background .15s ease-out;
  }}
  [data-testid="stExpander"] summary:hover,
  [data-testid="stExpander"] details[open] > summary:hover {{
    background: var(--panel2) !important;
  }}
  [data-testid="stExpander"] summary p {{
    font-size:var(--fs-md) !important; font-weight:600 !important;
    letter-spacing:-.01em; color:var(--text);
  }}
  [data-testid="stExpander"] summary svg {{ fill: var(--muted); }}
  [data-testid="stExpander"] [data-testid="stExpanderDetails"] {{
    padding: 4px 24px 20px;
  }}
  [data-testid="stExpander"] summary p em {{
    font-style: normal; font-weight: 400; font-size: var(--fs-base);
    color: var(--muted); margin-left: 8px; letter-spacing: 0;
  }}
  .panel-sub {{
    font-size:var(--fs-xs); font-weight:600; color:var(--muted); margin:0 0 12px;
  }}
  .method {{ display:grid; margin:0; gap:12px 32px;
             grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); }}
  .method dt {{ font-size:var(--fs-xs); font-weight:600; color:var(--muted); }}
  .method dd {{ margin:2px 0 0; font-size:var(--fs-sm); line-height:1.6; }}

  .foot {{ font-size:var(--fs-xs); color:var(--faint); line-height:1.7;
           padding:16px 2px 0; border-top:1px solid var(--border-soft);
           margin-top:20px; max-width:110ch; }}

  /* ---------- widgets ----------
     config.toml bakes one set of widget colours into the build, so in the
     theme that is not the configured one they leak through. These follow the
     CSS variables instead. Selectors are blunt on purpose: Streamlit's
     emotion class names change between versions. */
  [data-testid="stSelectbox"] div, [data-testid="stMultiSelect"] div,
  [role="listbox"], [role="option"] {{
    background-color: var(--panel) !important; color: var(--text) !important;
  }}
  [data-testid="stSelectbox"] > div > div,
  [data-testid="stMultiSelect"] > div > div {{
    border:1px solid var(--border) !important; border-radius:10px !important;
  }}
  [data-testid="stSelectbox"] input, [data-testid="stSelectbox"] svg {{
    color: var(--text) !important; fill: var(--text) !important;
  }}
  /* An empty box shows its placeholder, which config.toml paints 60% white —
     in light mode a blank, disabled-looking control. */
  [data-testid="stSelectbox"] input::placeholder,
  [data-testid="stMultiSelect"] input::placeholder {{
    color: var(--muted) !important; opacity: 1;
  }}
  /* A chosen car in the comparison box, with a dot in the colour that car has
     in the comparison table and chart, so the key is learned where the choice
     is made. */
  [data-testid="stMultiSelect"] [data-tag] {{
    background-color: var(--panel2) !important; color: var(--text) !important;
    border: 1px solid var(--border) !important; border-radius: 6px !important;
    padding-left: 8px !important; gap: 6px;
  }}
  [data-testid="stMultiSelect"] [data-tag]::before {{
    content: ""; width: 8px; height: 8px; border-radius: 50%; flex: none;
    background: {p['teal']};
  }}
  [data-testid="stMultiSelect"] [data-tag-index="1"]::before {{
    background: {p['violet']};
  }}
  [data-testid="stMultiSelect"] [data-tag-index="2"]::before {{
    background: {p['fuchsia']};
  }}
  [data-testid="stMultiSelect"] [data-tag] span {{
    color: var(--text) !important; background-color: transparent !important;
  }}
  [data-testid="stMultiSelect"] [data-tag] button,
  [data-testid="stMultiSelect"] [data-tag] svg {{
    color: var(--muted) !important; fill: var(--muted) !important;
    background-color: transparent !important;
  }}
  [role="option"]:hover {{ background-color: var(--panel2) !important; }}

  /* The view switch and the part-of-the-car filter. Streamlit paints both
     from config.toml's dark background, so in light mode every unselected
     option was a solid black lozenge with its label lost inside it. */
  [data-testid="stButtonGroup"] button[data-variant] {{
    background: var(--panel) !important; color: var(--text) !important;
    border-color: var(--border) !important;
  }}
  [data-testid="stButtonGroup"] button[data-variant]:hover {{
    background: var(--panel2) !important;
  }}
  [data-testid="stButtonGroup"] button[data-variant][aria-checked="true"] {{
    background: var(--accent-soft) !important;
    border-color: var(--accent) !important;
  }}
  [data-testid="stButtonGroup"] button[data-variant] p {{
    color: var(--text) !important; font-size: var(--fs-sm) !important;
    font-weight: 600 !important;
  }}
  [data-testid="stButtonGroup"] button[data-variant][aria-checked="true"] p {{
    color: var(--accent2) !important;
  }}

  /* Checklist boxes. Painted from the dark theme, an empty box in light mode
     was a filled dark square — every unticked item looked ticked, which is
     the one thing a checklist must not get wrong. Scoped to the cards, so the
     light-mode switch, which is the same component, keeps its own track. */
  [class*="st-key-motcard"] [data-testid="stCheckbox"] label > div:not([data-testid]) {{
    background: var(--panel) !important;
    border: 1.5px solid var(--muted) !important;
  }}
  [class*="st-key-motcard"] [data-testid="stCheckbox"] label[data-selected="true"] > div:not([data-testid]) {{
    background: var(--accent) !important; border-color: var(--accent) !important;
  }}
  [class*="st-key-motcard"] [data-testid="stCheckbox"] label > div:not([data-testid]) svg {{
    color: #fff !important; fill: #fff !important; stroke: #fff !important;
  }}
  .stSlider label, .stSelectbox label, .stMultiSelect label,
  [data-testid="stWidgetLabel"] p {{
    color: var(--muted) !important; font-size:var(--fs-sm) !important;
    font-weight:600 !important;
  }}
  .stButton > button {{
    background: var(--accent); color:#fff; border:0; border-radius:10px;
    padding:.5rem 1rem; font-weight:600; font-size:var(--fs-base);
    transition: filter .15s ease-out;
  }}
  .stButton > button p {{ color:#fff; }}
  .stButton > button:hover {{ filter:brightness(1.09); color:#fff; }}
  .stButton > button:active {{ filter:brightness(.94); }}
  .stButton > button:disabled {{ opacity:.55; cursor:not-allowed; }}

  /* Streamlit styles Plotly's SVG text with its own configured colour, which
     beats the colour set on the figure and made every axis unreadable in the
     non-configured theme. */
  .js-plotly-plot text {{ fill: var(--text) !important; }}

  /* ---------- narrow screens ---------- */
  @media (max-width: 640px) {{
    .block-container {{ padding: 1rem 1rem 2rem; }}
    [class*="st-key-motcard"] {{ padding:16px; }}
    [class*="st-key-controls"] {{ padding:12px 14px 4px; }}
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] details[open] > summary {{ padding:14px 16px; }}
    [data-testid="stExpander"] [data-testid="stExpanderDetails"] {{
      padding: 4px 16px 16px;
    }}
    .hero .title, .figures .lead dd {{ font-size:32px; }}
    /* Two figures a row: one a row made the snapshot a screen long before the
       reader reached the scale it summarises. */
    .figures {{ grid-template-columns: 1fr 1fr; }}
    .defs > div {{ grid-template-columns:1fr; }}
    .sbar nav {{ display:none; }}

    /* A failure reason's frequency, severity and repair size drop beneath
       its name rather than squeezing it into a column of single words. */
    .reason-head {{ display:none; }}
    .reason summary {{ grid-template-columns: 22px minmax(0, 1fr) 16px; }}
    .reason summary::after {{ grid-column:3; grid-row:1; }}
    .reason .meta {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px;
                     grid-column:2 / 4; grid-row:2; }}
    .reason .why {{ padding:0 8px 16px 34px; }}

    /* Head to head stacks: each measure's label runs across the top of its
       row and the cars sit side by side beneath it. */
    .tbl.h2h {{ min-width:0; }}
    .tbl.h2h tr {{ display:grid;
                   grid-template-columns:repeat(var(--cols, 2), 1fr); }}
    .tbl.h2h thead th:first-child {{ display:none; }}
    .tbl.h2h thead th {{ white-space:normal; }}
    .tbl.h2h thead th:nth-child(2) {{ border-radius:8px 0 0 8px; }}
    .tbl.h2h td.lbl {{ grid-column:1 / -1; width:auto; border-bottom:0;
                       padding:12px 12px 0; }}
    .tbl.h2h td.val {{ padding-top:4px; }}
    .tbl.h2h td.val .k {{ display:inline-block; margin-right:6px;
                          vertical-align:1px; }}
  }}

  @media (prefers-reduced-motion: reduce) {{
    *, *::before, *::after {{
      transition-duration: .01ms !important;
      animation-duration: .01ms !important;
      scroll-behavior: auto !important;
    }}
  }}
</style>
"""
