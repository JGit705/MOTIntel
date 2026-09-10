"""FR5 — the serving layer.

Reads only the pre-aggregated Parquet exported by src/export.py. There is no
DuckDB file here and no raw data: the app's whole job is to display numbers
that were computed offline, plus one on-demand call to the LLM layer.

Laid out buyer-first — the headline, the plain-English summary and the two
charts that answer "what breaks" sit above the fold; the analytical depth is
below it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import plotly.graph_objects as go
import polars as pl
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app"))

# Streamlit runs as its own process and inherits nothing from the shell that
# ran the pipeline, so the API key has to be read here or the summary panel
# reports "no key configured" against a perfectly good .env.
load_dotenv(ROOT / ".env")

import llm  # noqa: E402
import theme as th  # noqa: E402
from queries import MIN_TESTS_FOR_CONFIDENCE, VehicleProfile  # noqa: E402

DATA = ROOT / "data" / "processed"

st.set_page_config(page_title="MOTIntel", page_icon="🚗", layout="wide")


@st.cache_data
def load(name: str) -> pl.DataFrame:
    return pl.read_parquet(DATA / name)


def profile_of(make, model, age, n, rate, defects, mileage) -> VehicleProfile:
    return VehicleProfile(make=make, model=model, age_years=age, n_tests=n,
                          failure_rate=rate, top_defects=defects,
                          by_mileage=mileage)


def card(label: str, value: str, note: str = "", delta: str = "",
         delta_colour: str = "") -> str:
    d = (f'<div class="delta" style="color:{delta_colour}">{delta}</div>'
         if delta else "")
    n = f'<div class="note">{note}</div>' if note else ""
    return (f'<div class="mot-card"><div class="lab">{label}</div>'
            f'<div class="val">{value}</div>{d}{n}</div>')


def style_fig(fig: go.Figure, p: dict, height: int = 300) -> go.Figure:
    fig.update_layout(
        template=p["plotly"], height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=p["text"], size=12),
        showlegend=False, hoverlabel=dict(font_size=12),
    )
    fig.update_xaxes(gridcolor=p["grid"], zeroline=False)
    fig.update_yaxes(gridcolor=p["grid"], zeroline=False)
    return fig


# --------------------------------------------------------------------------
# theme
# --------------------------------------------------------------------------
# The toggle is read before the CSS is written, because the stylesheet has to
# be chosen before anything is drawn. A keyed widget's state is already
# updated when the script re-runs, so reading it up here is correct and needs
# no manual rerun — driving it with st.rerun() instead fights Streamlit's own
# widget state and the switch silently refuses to move.
# ?theme=light selects the theme on a cold load, so a particular view can be
# linked or screenshotted without clicking anything. The switch still wins
# once someone touches it, since its state is what is read here.
if "light_mode" not in st.session_state:
    st.session_state["light_mode"] = st.query_params.get("theme") == "light"
light = st.session_state["light_mode"]
p = th.palette("light" if light else "dark")
st.markdown(th.css(p), unsafe_allow_html=True)

st.toggle("Light mode", key="light_mode")
st.markdown(
        '<div class="mot-head">'
        '<div class="mot-mark">🚗</div>'
        '<div><div class="mot-title">MOTIntel</div>'
        '<div class="mot-sub">UK vehicle reliability, from 42.7 million real '
        'MOT tests</div></div>'
        '<div class="mot-pill">DVSA open data · 2025</div>'
        '</div>', unsafe_allow_html=True)

try:
    models = load("models.parquet")
    rates = load("failure_rates.parquet")
    defects = load("top_defects.parquet")
    age_curve = load("age_curve.parquet")
    benchmark = load("benchmark.parquet")
    severity = load("severity.parquet")
except FileNotFoundError:
    st.error("Serving data not found. Run `python src/export.py` first.")
    st.stop()

# --- FR5.1 selection -------------------------------------------------------
c1, c2, c3 = st.columns([2, 3, 3])
make = c1.selectbox("Make", sorted(models["make"].unique().to_list()))
model_options = (models.filter(pl.col("make") == make)
                 .sort("n_tests", descending=True)["model"].to_list())
model = c2.selectbox("Model", model_options)

# The age control only offers ages this vehicle was actually tested at. A
# fixed 3-25 slider let you land on a band with no data and get an error
# message for your trouble — the control was promising something the dataset
# could not answer.
this_model = (age_curve.filter((pl.col("make") == make)
                               & (pl.col("model") == model))
              .sort("age_band"))
bands = this_model["age_band"].to_list()

if not bands:
    st.warning(f"No {make} {model} tests in this dataset.")
    st.stop()
if len(bands) == 1:
    age_band = bands[0]
    c3.markdown(f'<div style="margin-top:26px" class="mot-id">'
                f'<span class="chip">Only tested at {age_band}–{age_band + 3} '
                f'years in this data</span></div>', unsafe_allow_html=True)
else:
    age_band = c3.select_slider(
        "Vehicle age at test", options=bands, value=bands[len(bands) // 2],
        format_func=lambda b: f"{b}–{b + 3} yrs")

cells = rates.filter((pl.col("make") == make) & (pl.col("model") == model)
                     & (pl.col("age_band") == age_band))
this_age = this_model.filter(pl.col("age_band") == age_band)

n_tests = int(this_age["n_tests"][0])
failure_rate = float(this_age["failure_rate"][0])

# --- benchmark context -----------------------------------------------------
bench_row = benchmark.filter(pl.col("age_band") == age_band)
bench = float(bench_row["failure_rate"][0]) if not bench_row.is_empty() else None

peers_same_age = age_curve.filter(pl.col("age_band") == age_band)
percentile = None
if len(peers_same_age) > 5:
    better = int((peers_same_age["failure_rate"] > failure_rate).sum())
    percentile = round(100 * better / len(peers_same_age))

if bench is None:
    tone, delta = p["muted"], ""
elif failure_rate < bench * 0.9:
    tone = p["good"]
    delta = f"↓ {abs(failure_rate - bench) * 100:.1f} pts better than average"
elif failure_rate > bench * 1.1:
    tone = p["bad"]
    delta = f"↑ {abs(failure_rate - bench) * 100:.1f} pts worse than average"
else:
    tone, delta = p["warn"], "≈ about average for this age"

sparse = n_tests < MIN_TESTS_FOR_CONFIDENCE
verdict = ("Fails less often than average" if tone == p["good"] else
           "Fails more often than average" if tone == p["bad"] else
           "About average for its age")
st.markdown(
    f'<div class="mot-id"><span class="name">{make} {model}</span>'
    f'<span class="chip">{age_band}–{age_band + 3} years old</span>'
    f'<span class="chip">{n_tests:,} MOT tests</span>'
    f'<span class="verdict" style="color:{tone}">{verdict}</span></div>',
    unsafe_allow_html=True)

m1, m2, m3 = st.columns(3)
m1.markdown(card(
    "Failure probability", f"{failure_rate:.0%}", delta=delta,
    delta_colour=tone,
    note=(f"All {age_band}–{age_band + 3} year old cars average "
          f"{bench:.1%}" if bench else "")), unsafe_allow_html=True)
m2.markdown(card(
    "Tests in this group", f"{n_tests:,}",
    note=f"{make} {model}, {age_band}–{age_band + 3} years old"),
    unsafe_allow_html=True)
m3.markdown(card(
    "Confidence", "Low" if sparse else "Good",
    note=(f"Below {MIN_TESTS_FOR_CONFIDENCE} tests — treat with caution"
          if sparse else
          (f"Better than {percentile}% of models this age"
           if percentile is not None else f"Based on {n_tests:,} tests"))),
    unsafe_allow_html=True)

# --- FR5.6 the AI summary --------------------------------------------------
top = (defects.filter((pl.col("make") == make) & (pl.col("model") == model)
                      & (pl.col("age_band") == age_band))
       .sort("n_tests", descending=True).head(10))
by_mileage = (cells.sort("mileage_band")
              .select(mileage_band=pl.col("mileage_band"),
                      miles=pl.format("{}k", (pl.col("mileage_band") // 1000)
                                      .cast(pl.Utf8).str.pad_start(3, "0")),
                      n_tests=pl.col("n_tests"),
                      failure_rate=pl.col("failure_rate")))

profile = profile_of(
    make, model, age_band + 1, n_tests, failure_rate,
    top.rename({"defect_category": "category",
                "defect_desc": "defect"}).to_dicts(),
    [{"mileage_band": r["miles"], "n_tests": r["n_tests"],
      "failure_rate": r["failure_rate"]} for r in by_mileage.to_dicts()])

# An already-generated summary costs nothing to show, so it appears straight
# away. A new one costs an API call against a limited daily quota, so it only
# happens when asked for — otherwise idly changing the dropdowns would spend
# the day's allowance without anyone reading a word of it.
existing = llm.cached_summary(profile)
cite = ("Written by Gemini from the figures on this page and nothing else — "
        "no vehicle knowledge of its own, no web access. If a fact is not in "
        "the retrieved data, it has no route to it.")
eyebrow = (f'<div class="eyebrow">✦ What this means &nbsp;·&nbsp; '
           f'{make} {model}, {age_band}–{age_band + 3} years old</div>')

if not llm.credentials_available():
    st.markdown(
        f'<div class="mot-hero empty">{eyebrow}<div class="body">'
        f'The plain-English summary needs a Gemini API key in .env. '
        f'Every figure on this page is unaffected.</div></div>',
        unsafe_allow_html=True)
elif existing:
    st.markdown(
        f'<div class="mot-hero">{eyebrow}<div class="body">{existing}</div>'
        f'<div class="cite">{cite} Shown from cache — no API call made.'
        f'</div></div>', unsafe_allow_html=True)
else:
    st.markdown(
        f'<div class="mot-hero empty">{eyebrow}<div class="body">'
        f'Turn the figures below into a few plain sentences: what actually '
        f'fails on this vehicle, how it changes with mileage, and how it '
        f'compares with its rivals.</div></div>', unsafe_allow_html=True)
    b1, b2 = st.columns([1, 4])
    with b1:
        write_it = st.button("Write the summary", type="primary")
    with b2:
        st.markdown('<div style="margin-top:9px;font-size:12.5px;opacity:.7">'
                    'One API call, about 600 tokens. Summaries you have '
                    'already generated reappear instantly and cost nothing.'
                    '</div>', unsafe_allow_html=True)
    if write_it:
        with st.spinner("Reading the defect data ..."):
            summary = llm.summarise(profile)
        if summary:
            st.markdown(f'<div class="mot-hero">{eyebrow}'
                        f'<div class="body">{summary}</div>'
                        f'<div class="cite">{cite}</div></div>',
                        unsafe_allow_html=True)
        else:
            # FR4.4 — the layer is down, the page still works.
            st.info("The summary could not be written just now — the free "
                    "tier allows a limited number of requests per minute. "
                    "Every figure on this page is unaffected; try again "
                    "shortly.")

# --- where this model is at its best ---------------------------------------
best_age = this_model.sort("failure_rate").head(1)
model_miles = (rates.filter((pl.col("make") == make) & (pl.col("model") == model))
               .group_by("mileage_band")
               .agg(n_tests=pl.col("n_tests").sum(),
                    failure_rate=(pl.col("failure_rate") * pl.col("n_tests"))
                    .sum() / pl.col("n_tests").sum())
               .filter(pl.col("n_tests") >= 50).sort("failure_rate"))

# The steepest step between consecutive age bands — the point at which this
# model starts costing money, which is more actionable than "newer is better".
cliff = None
if len(this_model) > 1:
    rows = this_model.to_dicts()
    jumps = [(rows[i]["failure_rate"] - rows[i - 1]["failure_rate"], rows[i])
             for i in range(1, len(rows))]
    step, row = max(jumps, key=lambda t: t[0])
    if step > 0.02:
        cliff = (row["age_band"], step)

s1, s2, s3 = st.columns(3)
if not best_age.is_empty():
    b = int(best_age["age_band"][0])
    s1.markdown(card(
        "Best age, on this data", f"{b}–{b + 3} yrs",
        note=(f"{float(best_age['failure_rate'][0]):.1%} failure rate over "
              f"{int(best_age['n_tests'][0]):,} tests. Younger is almost "
              f"always better — the useful question is where it stops being.")),
        unsafe_allow_html=True)
if not model_miles.is_empty():
    mb = int(model_miles["mileage_band"][0])
    s2.markdown(card(
        "Best mileage", f"{mb // 1000}–{mb // 1000 + 20}k",
        note=(f"{float(model_miles['failure_rate'][0]):.1%} failure rate over "
              f"{int(model_miles['n_tests'][0]):,} tests at this odometer "
              f"reading.")), unsafe_allow_html=True)
if cliff:
    s3.markdown(card(
        "Where it turns", f"{cliff[0]} yrs",
        delta=f"↑ {cliff[1] * 100:.1f} pts in one band",
        delta_colour=p["warn"],
        note="The sharpest rise between consecutive age bands for this model."),
        unsafe_allow_html=True)
else:
    s3.markdown(card("Where it turns", "—",
                     note="No sharp step between age bands for this model."),
                unsafe_allow_html=True)

# --- FR5.3 / FR5.4 charts --------------------------------------------------
left, right = st.columns(2)
with left:
    st.markdown('<div class="mot-sechead">What fails most</div>'
                '<div class="mot-secsub">Share of tests in this group with '
                'each defect</div>', unsafe_allow_html=True)
    if top.is_empty():
        st.caption("No defect breakdown for this group.")
    else:
        t = top.sort("share_of_tests")
        labels = [f"{c}: {d}" for c, d in zip(t["defect_category"].to_list(),
                                              t["defect_desc"].to_list())]
        labels = [l if len(l) <= 46 else l[:45] + "…" for l in labels]
        fig = go.Figure(go.Bar(
            x=t["share_of_tests"].to_list(), y=labels, orientation="h",
            marker_color=p["accent"],
            hovertemplate="%{y}<br>%{x:.1%} of tests<extra></extra>"))
        # The panel is narrow and the long defect labels eat the width, so
        # the tick count is capped — left to itself Plotly rotates the
        # percentages vertically and they collide.
        fig.update_xaxes(tickformat=".1%", nticks=4, tickangle=0)
        st.plotly_chart(style_fig(fig, p, 330), width="stretch")

with right:
    st.markdown('<div class="mot-sechead">Failure rate by mileage</div>'
                '<div class="mot-secsub">Every age of this model, by odometer '
                'reading at test</div>', unsafe_allow_html=True)
    fig = go.Figure(go.Bar(
        x=by_mileage["miles"].to_list(),
        y=by_mileage["failure_rate"].to_list(),
        marker_color=p["teal"],
        customdata=by_mileage["n_tests"].to_list(),
        hovertemplate="%{x} miles<br>%{y:.1%} failed"
                      "<br>%{customdata:,} tests<extra></extra>"))
    fig.update_yaxes(tickformat=".0%", nticks=6)
    st.plotly_chart(style_fig(fig, p, 330), width="stretch")

# --- age curve, with the survivorship peak ---------------------------------
st.markdown('<div class="mot-sechead">Failure rate as the car ages</div>'
            '<div class="mot-secsub">This model against the average for all '
            'cars. The average peaks around 18–21 years and then falls — not '
            'because old cars get better, but because the neglected ones have '
            'already been scrapped.</div>', unsafe_allow_html=True)
mine = (age_curve.filter((pl.col("make") == make) & (pl.col("model") == model))
        .sort("age_band"))
bm = benchmark.sort("age_band")
fig = go.Figure()
fig.add_trace(go.Scatter(
    x=[f"{b}–{b + 3}" for b in bm["age_band"].to_list()],
    y=bm["failure_rate"].to_list(), name="All cars",
    mode="lines", line=dict(color=p["muted"], width=2, dash="dot"),
    hovertemplate="All cars, %{x} yrs<br>%{y:.1%}<extra></extra>"))
if not mine.is_empty():
    fig.add_trace(go.Scatter(
        x=[f"{b}–{b + 3}" for b in mine["age_band"].to_list()],
        y=mine["failure_rate"].to_list(), name=f"{make} {model}",
        mode="lines+markers", line=dict(color=p["accent"], width=3),
        marker=dict(size=7),
        customdata=mine["n_tests"].to_list(),
        hovertemplate=f"{make} {model}, %{{x}} yrs<br>%{{y:.1%}}"
                      "<br>%{customdata:,} tests<extra></extra>"))
fig.update_yaxes(tickformat=".0%", title=None)
fig = style_fig(fig, p, 330)
fig.update_layout(showlegend=True,
                  legend=dict(orientation="h", y=1.12, x=0,
                              bgcolor="rgba(0,0,0,0)"))
st.plotly_chart(fig, width="stretch")

# --- severity + peers ------------------------------------------------------
sev_col, peer_col = st.columns([1, 2])
with sev_col:
    st.markdown('<div class="mot-sechead">How serious are the failures?</div>'
                '<div class="mot-secsub">Share of failed tests carrying at '
                'least one defect of each severity</div>',
                unsafe_allow_html=True)
    sev = severity.filter((pl.col("make") == make) & (pl.col("model") == model)
                          & (pl.col("age_band") == age_band))
    if sev.is_empty():
        st.caption("Not enough failure items in this group to break down.")
    else:
        failed_tests = max(1, round(n_tests * failure_rate))
        order = {"Dangerous": 0, "Major": 1}
        sev = sev.sort(pl.col("deficiency_category").replace_strict(order,
                                                                   default=9))
        colours = {"Dangerous": p["bad"], "Major": p["warn"]}
        fig = go.Figure(go.Bar(
            x=[min(1.0, v / failed_tests) for v in sev["n_tests"].to_list()],
            y=sev["deficiency_category"].to_list(), orientation="h",
            marker_color=[colours.get(c, p["accent"])
                          for c in sev["deficiency_category"].to_list()],
            customdata=sev["n_tests"].to_list(),
            hovertemplate="%{y}<br>%{x:.0%} of failed tests"
                          "<br>%{customdata:,} tests<extra></extra>"))
        fig.update_xaxes(tickformat=".0%", range=[0, 1], nticks=5,
                         tickangle=0)
        st.plotly_chart(style_fig(fig, p, 190), width="stretch")

    # Nobody should have to guess what separates the two. These are DVSA's
    # own definitions, not a paraphrase.
    st.markdown(
        f'<div class="mot-key">'
        f'<div class="row"><div class="dot" style="background:{p["bad"]}">'
        f'</div><div><b>Dangerous</b> — <span>a direct and immediate risk to '
        f'road safety, or a serious environmental impact. The car fails, and '
        f'must not be driven until it is repaired.</span></div></div>'
        f'<div class="row"><div class="dot" style="background:{p["warn"]}">'
        f'</div><div><b>Major</b> — <span>may affect safety, put other road '
        f'users at risk, or harm the environment. The car fails and must be '
        f'repaired.</span></div></div>'
        f'<div class="row"><div class="dot" style="background:{p["muted"]}">'
        f'</div><div><b>Minor</b> — <span>noted on the certificate but does '
        f'not cause a failure, so it never appears above.</span></div></div>'
        f'</div>'
        f'<div class="mot-secsub" style="margin-top:10px">Categories as '
        f'defined by DVSA under the 2018 EU roadworthiness directive. One '
        f'test can carry several defects, so the two bars overlap.</div>',
        unsafe_allow_html=True)

with peer_col:
    st.markdown('<div class="mot-sechead">Compared with other models of the '
                'same age</div><div class="mot-secsub">The highest-volume '
                'models in this age band, with this one highlighted</div>',
                unsafe_allow_html=True)
    peers = (peers_same_age.sort("n_tests", descending=True).head(8)
             .sort("failure_rate"))
    is_me = [(mk == make and md == model) for mk, md
             in zip(peers["make"].to_list(), peers["model"].to_list())]
    if not any(is_me) and not this_age.is_empty():
        peers = (pl.concat([peers, this_age.select(peers.columns)])
                 .sort("failure_rate"))
        is_me = [(mk == make and md == model) for mk, md
                 in zip(peers["make"].to_list(), peers["model"].to_list())]
    fig = go.Figure(go.Bar(
        x=peers["failure_rate"].to_list(),
        y=[f"{mk} {md}" for mk, md in zip(peers["make"].to_list(),
                                          peers["model"].to_list())],
        orientation="h",
        marker_color=[p["accent"] if me else p["border"] for me in is_me],
        customdata=peers["n_tests"].to_list(),
        hovertemplate="%{y}<br>%{x:.1%} failed"
                      "<br>%{customdata:,} tests<extra></extra>"))
    fig.update_xaxes(tickformat=".0%")
    st.plotly_chart(style_fig(fig, p, 300), width="stretch")

# --- FR5.7 provenance ------------------------------------------------------
st.markdown(
    '<div class="mot-panel" style="margin-top:10px"><div class="cite">'
    'Source: DVSA anonymised MOT testing data, 2025 (42.7 million tests). '
    'Contains public sector information licensed under the Open Government '
    'Licence v3.0. An MOT failure rate is not a reliability rating: the test '
    'covers safety and emissions items at one annual point, cannot see repairs '
    'made between tests, and reflects owner maintenance as much as build '
    'quality.</div></div>', unsafe_allow_html=True)
