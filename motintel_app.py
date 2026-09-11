"""FR5 — the serving layer.  Run with: streamlit run motintel_app.py

Reads only the pre-aggregated Parquet exported by motintel/export.py. There is no
DuckDB file here and no raw data: the app displays numbers computed offline,
plus one on-demand call to the LLM layer.

Everything on the page other than the written summary is computed from the
data — the bullets, the mileage warning and the insight cards included — so
the page stays honest and useful with the LLM layer switched off entirely.
"""
from __future__ import annotations

from html import escape
from pathlib import Path

import plotly.graph_objects as go
import polars as pl
import streamlit as st
from dotenv import load_dotenv

from motintel import llm, serving
from motintel.queries import MIN_TESTS_FOR_CONFIDENCE
from motintel.ui import components as c
from motintel.ui import theme as th

ROOT = Path(__file__).resolve().parent

# Streamlit runs as its own process and inherits nothing from the shell that
# ran the pipeline, so the API key is read here or the summary panel reports
# "no key configured" against a perfectly good .env.
load_dotenv(ROOT / ".env")


st.set_page_config(page_title="MOTIntel", page_icon="🚗", layout="wide",
                   initial_sidebar_state="expanded")


def style_fig(fig: go.Figure, p: dict, height: int) -> go.Figure:
    fig.update_layout(
        template=p["plotly"], height=height,
        margin=dict(l=6, r=6, t=6, b=6),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=p["text"], size=11.5), showlegend=False,
        hoverlabel=dict(font_size=12))
    fig.update_xaxes(gridcolor=p["grid"], zeroline=False)
    fig.update_yaxes(gridcolor=p["grid"], zeroline=False)
    return fig


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# theme + shell
# ---------------------------------------------------------------------------
# ?theme=light selects the theme on a cold load, so a particular view can be
# linked or screenshotted without clicking anything.
if "light_mode" not in st.session_state:
    st.session_state["light_mode"] = st.query_params.get("theme") == "light"
p = th.palette("light" if st.session_state["light_mode"] else "dark")
html(th.css(p))

PAGES = [("Overview", "gauge"), ("Reliability", "check"),
         ("Failure reasons", "list"), ("Mileage analysis", "trend"),
         ("Comparison", "scales")]

with st.sidebar:
    html(f'<div class="mot-brand"><div class="mark">{c.icon("car", 21, "#fff")}'
         f'</div><div><div class="name">MOTIntel</div>'
         f'<div class="tag">Real MOT data. Smarter decisions.</div>'
         f'</div></div>')
    page = st.radio("Sections", [f"{n}" for n, _ in PAGES],
                    label_visibility="collapsed")
    st.divider()
    st.toggle("Light mode", key="light_mode")
    html(f'<div class="foot" style="margin-top:18px">'
         f'{c.icon("doc", 13)} DVSA anonymised MOT data, 2025<br>'
         f'42.7 million tests · 35.4 million after cleaning</div>')

try:
    T = serving.tables()
except FileNotFoundError as e:
    st.error(f"{e}")
    st.stop()

rates, defects = T["failure_rates"], T["top_defects"]
age_curve, benchmark = T["age_curve"], T["benchmark"]
severity, meta = T["severity"], T["vehicle_meta"]

# ---------------------------------------------------------------------------
# selection + derived figures
# ---------------------------------------------------------------------------
# The selectable set, the age bands and the profile all come from
# motintel.serving, so the page and the summary cannot drift apart.
selectable = serving.selectable_vehicles()
makes = sorted(selectable["make"].unique().to_list())
hero_col, ctrl_col = st.columns([3, 2])

with ctrl_col:
    # A real Streamlit container, not a hand-written <div>: markup opened with
    # st.markdown cannot wrap widgets, because Streamlit renders each widget
    # into its own container rather than into the open tag.
    with st.container(border=True):
        a, b = st.columns(2)
        make = a.selectbox("Make", makes)
        model_options = (selectable.filter(pl.col("make") == make)
                         .sort("n_tests", descending=True)["model"].to_list())
        model = b.selectbox("Model", model_options)

        # The age control only offers ages this vehicle was actually tested at.
        # A fixed slider let you land on a band with no data and get an error
        # for your trouble.
        this_model = (age_curve.filter((pl.col("make") == make)
                                       & (pl.col("model") == model))
                      .sort("age_band"))
        bands = serving.age_bands(make, model)
        if not bands:
            st.warning(f"No {make} {model} tests in this dataset.")
            st.stop()
        if len(bands) == 1:
            age_band = bands[0]
            st.caption(f"Only tested at {age_band}–{age_band + 3} years "
                       f"in this data.")
        else:
            age_band = st.select_slider(
                "Vehicle age at test", options=bands,
                value=bands[min(len(bands) - 1, len(bands) // 2)],
                format_func=lambda x: f"{x}–{x + 3} yrs")

# One profile, built by motintel.serving, shared by the page and the model.
# Anything absent from it is unreachable by the summary, and every figure below
# is read off the same object the model is given.
profile = serving.build_profile(make, model, age_band)
sparse = serving.is_sparse(profile)

row = this_model.filter(pl.col("age_band") == age_band)
n_tests = profile.n_tests
failure_rate = profile.failure_rate
bench_row = benchmark.filter(pl.col("age_band") == age_band)
bench = float(bench_row["failure_rate"][0]) if not bench_row.is_empty() else None
peers_same_age = age_curve.filter(pl.col("age_band") == age_band)
percentile = None
if len(peers_same_age) > 5:
    percentile = round(100 * int((peers_same_age["failure_rate"]
                                  > failure_rate).sum()) / len(peers_same_age))

if bench is None:
    tone, soft, verdict = p["accent"], p["accent_soft"], "No benchmark"
elif failure_rate < bench * 0.9:
    tone, soft, verdict = p["good"], p["good_soft"], "Better than average"
elif failure_rate > bench * 1.1:
    tone, soft, verdict = p["bad"], p["bad_soft"], "Worse than average"
else:
    tone, soft, verdict = p["warn"], p["warn_soft"], "About average"

mrow = meta.filter((pl.col("make") == make) & (pl.col("model") == model))
fuels = (mrow["fuels"][0] if not mrow.is_empty() and mrow["fuels"][0]
         else "Not recorded")
yr_from = int(mrow["year_from"][0]) if not mrow.is_empty() else None
yr_to = int(mrow["year_to"][0]) if not mrow.is_empty() else None
avg_age = float(mrow["avg_age"][0]) if not mrow.is_empty() else None

# The profile carries the defect rows under the names the model reads them by;
# the panels want their column names, so map rather than re-query.
DEFECT_SCHEMA = {"defect_category": pl.Utf8, "defect_desc": pl.Utf8,
                 "n_tests": pl.Int64, "share_of_tests": pl.Float64,
                 "plain_english": pl.Utf8, "repair_area": pl.Utf8,
                 "effort": pl.Utf8, "forecourt_check": pl.Utf8}
top = (pl.DataFrame(profile.top_defects)
       .rename({"category": "defect_category", "defect": "defect_desc"})
       if profile.top_defects else pl.DataFrame(schema=DEFECT_SCHEMA))

# Mileage bands, with everything past the cap pooled so the tail does not
# become a row of single-test noise.
# drop_nulls is belt-and-braces: the export no longer emits a null band, but a
# stale Parquet from an older run should degrade rather than crash the page.
by_mileage = serving.mileage_curve(make, model)
mile_labels = [serving.mileage_label(b) for b in by_mileage["band"].to_list()]

# The steepest step between consecutive mileage bands — the point at which
# this model starts costing money.
mile_jump = None
mrows = by_mileage.to_dicts()
if len(mrows) > 2:
    steps = [(mrows[i]["failure_rate"] - mrows[i - 1]["failure_rate"], i)
             for i in range(1, len(mrows))]
    step, idx = max(steps, key=lambda t: t[0])
    if step > 0.02:
        mile_jump = (mile_labels[idx], mrows[idx]["failure_rate"], step,
                     mrows[-1]["failure_rate"], mile_labels[-1])

SEV = {"Dangerous": (p["bad"], p["bad_soft"]), "Major": (p["warn"], p["warn_soft"])}


# ---------------------------------------------------------------------------
# panels
# ---------------------------------------------------------------------------
def panel_hero() -> None:
    facts = [c.fact("fuel", f"<b>{fuels}</b>")]
    if yr_from and yr_to:
        facts.append(c.fact("cal", f"<b>{yr_from} – {yr_to}</b>"))
    if avg_age:
        facts.append(c.fact("clock", f"<b>{avg_age:.0f} years</b> old on average"))
    facts.append(c.fact("doc", f"<b>{int(mrow['n_tests'][0]):,}</b> tests, all ages"
                        if not mrow.is_empty() else "—"))
    # Our own line art rather than manufacturer photography: the shape fills
    # the space the eye expects a car to occupy without borrowing imagery we
    # have no licence to ship.
    silhouette = (f'<div class="silo">{c.icon("car", 300, p["accent"], 0.35)}'
                  f'</div>')
    html(f'<div class="hero"><div class="glow"></div>{silhouette}'
         f'<div class="mk">{make}</div><div class="md">{model}</div>'
         f'<div class="facts">{"".join(facts)}</div></div>')


def panel_probability() -> None:
    with st.container(border=True):
        html(c.card_header("Observed failure rate", "gauge", tone,
                         pill=f"{age_band}–{age_band + 3} years"))
        left, right = st.columns([1, 1])
        with left:
            fig = go.Figure(go.Pie(
                values=[failure_rate, 1 - failure_rate], hole=0.74, sort=False,
                direction="clockwise", rotation=0, textinfo="none",
                marker=dict(colors=[tone, p["track"]], line=dict(width=0)),
                hoverinfo="skip"))
            fig.add_annotation(text=f"<b>{failure_rate:.0%}</b>", x=.5, y=.56,
                               font=dict(size=38, color=p["text"]), showarrow=False)
            fig.add_annotation(text=verdict, x=.5, y=.33,
                               font=dict(size=12, color=tone), showarrow=False)
            st.plotly_chart(style_fig(fig, p, 210), width="stretch",
                            config={"displayModeBar": False})
        with right:
            html(c.stat(f"{n_tests:,}", "MOT tests analysed", "doc", p["accent"])
                 + c.stat(f"{age_band}–{age_band + 3} yrs",
                          "vehicle age at test", "clock", p["accent"])
                 + c.stat(f"{bench:.1%}" if bench else "—",
                          "average for vehicles of this age", "check", p["teal"]))
        if percentile is not None:
            html(f'<div style="font-size:12px;color:var(--muted);margin-top:4px">'
                 f'What actually happened to {n_tests:,} real cars — not a '
                 f'prediction. Fails less often than <b>{percentile}%</b> of '
                 f'models this age.</div>')


def panel_summary() -> None:
    with st.container(border=True):
        html(c.card_header("AI reliability summary", "spark", p["accent_2"],
                         pill="Powered by Gemini"))
        existing = llm.cached_summary(profile)
        if existing:
            # The model's text is escaped, not trusted. It is the one string
            # on this page that neither we nor the data authored, and a
            # prompt-injected response could otherwise inject markup into it.
            html(f'<div class="sum"><p>{escape(existing)}</p></div>')
        elif not llm.credentials_available():
            html('<div class="sum"><p style="color:var(--muted)">The written '
                 'summary needs a Gemini API key in <code>.env</code>. Everything '
                 'else on this page is unaffected.</p></div>')

        # The bullets and the warning are computed from the data, not written by
        # the model, so this card still says something useful with the LLM layer
        # switched off.
        if not top.is_empty():
            items = "".join(
                f'<li><b>{r["defect_category"]}</b> — {r["defect_desc"]} '
                f'({r["share_of_tests"]:.1%})</li>'
                for r in top.head(3).to_dicts())
            html(f'<div class="sum"><div style="font-size:12.5px;color:'
                 f'var(--muted);margin:2px 0 6px">Biggest sources of failure'
                 f'</div><ul>{items}</ul></div>')

        if mile_jump:
            band, rate, step, worst, worst_band = mile_jump
            html(c.callout(
                f'Failure probability climbs sharply after <u>{band} miles</u> '
                f'— up {step * 100:.1f} points in one band, reaching '
                f'{worst:.1%} by {worst_band}.', p["warn"]))

        if not existing and llm.credentials_available():
            if st.button("Write the summary", type="primary"):
                with st.spinner("Reading the defect data ..."):
                    if llm.summarise(profile):
                        st.rerun()
                    else:
                        st.info("The summary could not be written just now — the "
                                "free tier allows a limited number of requests "
                                "per minute. Every figure here is unaffected.")


# DVSA grades a defect Minor, Major or Dangerous, and those words are on the
# next panel across. The enrichment's "effort" is a different thing entirely —
# the size of the job, not how serious the fault is — and calling it
# minor/major beside a severity panel that also says minor/major is how a
# reader concludes that worn brake pads are nothing to worry about.
JOB_SIZE = {"minor": "small job", "moderate": "medium job",
            "major": "big job"}


def _job_pill(effort: str | None) -> str:
    if not effort:
        return ""
    tone = {"minor": ("good", "good_soft"), "moderate": ("warn", "warn_soft"),
            "major": ("bad", "bad_soft")}[effort]
    return c.badge(JOB_SIZE[effort], p[tone[0]], p[tone[1]])


def panel_defects(limit: int = 5) -> None:
    """The failure reasons, in plain English where the enrichment has run.

    The DVSA wording stays underneath rather than being replaced: it is the
    record, the rewrite is an interpretation of it, and a reader who wants to
    check one against the other should not have to leave the page.
    """
    with st.container(border=True):
        shown, total = min(limit, len(top)), len(top)
        html(c.card_header(
            "Top failure reasons", "list", p["accent"],
            action="" if shown >= total else f"Top {shown} of {total}"))
        if top.is_empty():
            html('<div style="color:var(--muted);font-size:13px">No defect '
                 'breakdown for this group.</div>')
            return
        biggest = float(top["share_of_tests"].max())
        shown_rows = top.head(limit).to_dicts()
        # Without the enrichment there is nothing to put in the repair column,
        # and a header over an empty column reads as missing data.
        labelled = any(d.get("plain_english") for d in shown_rows)
        rows = ""
        for i, r in enumerate(shown_rows, 1):
            colour = p["bad"] if i <= 2 else p["warn"] if i <= 4 else p["accent"]
            plain = r.get("plain_english")
            headline = escape(plain) if plain else escape(r["defect_category"])
            under = (f'{escape(r["defect_category"])} — '
                     f'{escape(r["defect_desc"])}' if plain
                     else escape(r["defect_desc"]))
            pill = (f'<td style="white-space:nowrap">'
                    f'{_job_pill(r.get("effort"))}</td>' if labelled else "")
            rows += (
                f'<tr><td class="rk">{i}</td>'
                f'<td style="width:14px"><span class="dot" '
                f'style="background:{colour}"></span></td>'
                f'<td><div class="nm">{headline}</div>'
                f'<div class="sub">{under}</div></td>{pill}'
                f'<td class="num">{r["share_of_tests"]:.1%}</td>'
                f'<td style="width:26%">'
                f'{c.bar(r["share_of_tests"] / biggest, colour)}</td></tr>')
        note = ('Share of all tests in this group, not of failures. One test '
                'can carry several defects.')
        if labelled:
            note += (' The headline is a plain-English reading of the DVSA '
                     'wording beneath it; the job size is how big the repair '
                     'is, which is not the same as how serious the fault is.')
        repair_th = "<th>Repair</th>" if labelled else ""
        html(f'<table class="tbl"><thead><tr><th>#</th><th></th>'
             f'<th>Failure reason</th>{repair_th}<th>Rate</th><th></th>'
             f'</tr></thead><tbody>{rows}</tbody></table>'
             f'<div style="font-size:11.5px;color:var(--faint);margin-top:10px">'
             f'{note}</div>')


def panel_repair_areas() -> None:
    """Where this model's failures cluster.

    A car that mostly fails on suspension is a different purchase from one that
    mostly fails on corrosion, and fifteen DVSA categories hide that: "Body,
    chassis, structure" spans a loose numberplate and a rotten sill.

    Weights are a share of the ten listed reasons, not of tests. Summing the
    per-defect test counts would double-count any test carrying two defects in
    the same area, and there is no way to undo that from this grain.
    """
    with st.container(border=True):
        html(c.card_header("Where the failures are", "cog", p["accent_2"],
                           pill="Top ten reasons"))
        if top.is_empty():
            html('<div style="color:var(--muted);font-size:13px">No defect '
                 'breakdown for this group.</div>')
            return
        rows = [r for r in top.to_dicts() if r.get("repair_area")]
        if not rows:
            html('<div style="color:var(--muted);font-size:13px">Plain-English '
                 'grouping needs the enrichment step — run '
                 '<code>python -m motintel.enrich</code>.</div>')
            return
        by_area: dict[str, int] = {}
        for r in rows:
            by_area[r["repair_area"]] = (by_area.get(r["repair_area"], 0)
                                         + r["n_tests"])
        total_items = sum(by_area.values()) or 1
        ordered = sorted(by_area.items(), key=lambda kv: -kv[1])
        biggest = ordered[0][1]
        body = ""
        for area, n in ordered:
            # Sentence case in Python, not text-transform:capitalize, which
            # renders "Tyres And Wheels".
            body += (f'<tr><td class="nm">'
                     f'{escape(area[:1].upper() + area[1:])}</td>'
                     f'<td class="num">{n / total_items:.0%}</td>'
                     f'<td style="width:58%">'
                     f'{c.bar(n / biggest, p["accent"])}</td></tr>')
        html(f'<table class="tbl"><tbody>{body}</tbody></table>'
             f'<div style="font-size:11.5px;color:var(--faint);margin-top:10px">'
             f'Share of this model\'s ten commonest failure reasons, by the '
             f'part of the car a buyer thinks in. Not a share of tests: one '
             f'test can carry several defects.</div>')


def panel_forecourt() -> None:
    """What to look at when viewing the car.

    The first thing in this app a reader can act on, and it exists only because
    of the enrichment. Where nothing can be checked by eye it says so: most MOT
    failures are not visible from outside a workshop, and a list of invented
    things to look at would be worse than a short one.
    """
    with st.container(border=True):
        html(c.card_header("What to check when viewing one", "check",
                           p["good"], pill=f"{make.title()} {model.title()}"))
        if top.is_empty():
            html('<div style="color:var(--muted);font-size:13px">No defect '
                 'breakdown for this group.</div>')
            return
        rows = [r for r in top.to_dicts() if r.get("plain_english")]
        if not rows:
            html('<div style="color:var(--muted);font-size:13px">Needs the '
                 'enrichment step — run <code>python -m motintel.enrich</code>.'
                 '</div>')
            return
        seen, items = set(), ""
        for r in rows:
            check = r.get("forecourt_check")
            if not check or check in seen:
                continue
            seen.add(check)
            items += (f'<div class="fact" style="align-items:flex-start;'
                      f'margin-bottom:9px">{c.icon("check", 15, p["good"])}'
                      f'<span><b>{escape(check)}</b><br>'
                      f'<span style="color:var(--muted);font-size:11.5px">'
                      f'{escape(r["plain_english"])} — '
                      f'{r["share_of_tests"]:.1%} of tests</span></span></div>')
        if not items:
            html('<div style="color:var(--muted);font-size:13px">None of this '
                 'model\'s commonest failures can be seen from outside a '
                 'workshop. That is the usual answer — brake wear, emissions '
                 'and joint play all need the car on a ramp.</div>')
            return
        html(items)
        html('<div style="font-size:11.5px;color:var(--faint);margin-top:4px">'
             'Only the failures that can actually be seen or tried are listed, '
             'commonest first. Most cannot be, and are left out rather than '
             'turned into a check that would not reveal anything.</div>')


def panel_mileage() -> None:
    with st.container(border=True):
        html(c.card_header("How reliability changes with mileage", "trend",
                         p["teal"], pill="All ages of this model"))
        vals = by_mileage["failure_rate"].to_list()
        # Some vehicles carry no odometer readings at all — every band fell
        # under the export threshold. Annotating the worst of nothing raised
        # ValueError and took the whole page down with it.
        if not vals:
            html('<div style="color:var(--muted);font-size:13px">No odometer '
                 'readings recorded for this model in large enough groups to '
                 'chart. The figures above are unaffected.</div>')
            return
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=mile_labels, y=vals, marker_color=p["accent"],
            customdata=by_mileage["n_tests"].to_list(),
            hovertemplate="%{x} miles<br>%{y:.1%} failed"
                          "<br>%{customdata:,} tests<extra></extra>"))
        fig.add_trace(go.Scatter(
            x=mile_labels, y=vals, mode="lines", hoverinfo="skip",
            line=dict(color=p["accent_2"], width=2)))
        worst = max(range(len(vals)), key=lambda i: vals[i])
        fig.add_annotation(
            x=mile_labels[worst], y=vals[worst], text=f"<b>{mile_labels[worst]} "
            f"miles</b><br>{vals[worst]:.1%} failure rate",
            showarrow=True, arrowhead=0, arrowcolor=p["bad"], ax=-46, ay=-42,
            bgcolor=p["panel"], bordercolor=p["bad"], borderwidth=1,
            borderpad=6, font=dict(size=11, color=p["text"]))
        fig.update_yaxes(tickformat=".0%", nticks=6)
        st.plotly_chart(style_fig(fig, p, 290), width="stretch",
                        config={"displayModeBar": False})


def panel_comparison() -> None:
    with st.container(border=True):
        html(c.card_header(f"How does the {model} compare?", "scales", p["accent"],
                         pill="Lower is better"))
        peers = (peers_same_age.sort("n_tests", descending=True).head(6))
        if not ((peers["make"] == make) & (peers["model"] == model)).any():
            peers = pl.concat([peers, row.select(peers.columns)])
        peers = peers.sort("failure_rate")
        worst = float(peers["failure_rate"].max())
        rows = ""
        for i, r in enumerate(peers.to_dicts(), 1):
            me = r["make"] == make and r["model"] == model
            delta = r["failure_rate"] - failure_rate
            if me:
                tag = c.badge("This car", p["accent"], p["accent_soft"])
            elif i == 1:
                tag = c.badge("Best", p["good"], p["good_soft"])
            else:
                tag = c.badge(f"{delta * 100:+.1f} pts", p["muted"],
                              "var(--panel2)")
            rows += (
                f'<tr class="{"me" if me else ""}"><td class="rk">{i}</td>'
                f'<td class="nm">{r["make"].title()} {r["model"].title()}</td>'
                f'<td class="num">{r["failure_rate"]:.1%}</td><td>{tag}</td>'
                f'<td style="width:30%">'
                f'{c.bar(r["failure_rate"] / worst, p["accent"] if me else p["track"])}'
                f'</td></tr>')
        better = sum(1 for r in peers.to_dicts()
                     if r["failure_rate"] > failure_rate)
        html(f'<table class="tbl"><thead><tr><th>#</th><th>Model</th>'
             f'<th>Failure rate</th><th>vs this car</th><th></th></tr></thead>'
             f'<tbody>{rows}</tbody></table>')
        html(f'<div class="ins" style="margin-top:14px;background:{soft};'
             f'border-color:{tone}">'
             f'<div class="ico" style="background:var(--panel)">'
             f'{c.icon("check", 17, tone)}</div><div><div class="t">'
             f'The {model} ranks better than {better} of the {len(peers) - 1} '
             f'comparable models shown</div><div class="d">Peers are the '
             f'highest-volume models in the same age band — a proxy for '
             f'comparable cars, not a like-for-like class match.</div>'
             f'</div></div>')


def panel_severity() -> None:
    with st.container(border=True):
        html(c.card_header("How serious are the failures?", "warn", p["bad"],
                         pill="Share of failed tests"))
        sev = severity.filter((pl.col("make") == make) & (pl.col("model") == model)
                              & (pl.col("age_band") == age_band))
        if sev.is_empty():
            html('<div style="color:var(--muted);font-size:13px">Not enough '
                 'failure items in this group to break down.</div>')
        else:
            failed = max(1, round(n_tests * failure_rate))
            rows = ""
            for cat in ("Dangerous", "Major"):
                hit = sev.filter(pl.col("deficiency_category") == cat)
                if hit.is_empty():
                    continue
                share = min(1.0, int(hit["n_tests"][0]) / failed)
                col, sf = SEV[cat]
                rows += (f'<tr><td style="width:14px"><span class="dot" '
                         f'style="background:{col}"></span></td>'
                         f'<td class="nm">{cat}</td>'
                         f'<td class="num">{share:.0%}</td>'
                         f'<td style="width:52%">{c.bar(share, col)}</td></tr>')
            html(f'<table class="tbl"><tbody>{rows}</tbody></table>')
        html(f'<div style="margin-top:14px">'
             f'<div class="ins"><div class="ico" style="background:{p["bad_soft"]}">'
             f'{c.icon("warn", 16, p["bad"])}</div><div>'
             f'<div class="t">Dangerous</div><div class="d">A direct and immediate '
             f'risk to road safety, or a serious environmental impact. The car '
             f'fails and must not be driven until it is repaired.</div></div></div>'
             f'<div class="ins"><div class="ico" style="background:{p["warn_soft"]}">'
             f'{c.icon("warn", 16, p["warn"])}</div><div>'
             f'<div class="t">Major</div><div class="d">May affect safety, put '
             f'other road users at risk, or harm the environment. The car fails '
             f'and must be repaired.</div></div></div>'
             f'<div class="ins"><div class="ico" style="background:var(--panel2)">'
             f'{c.icon("check", 16, p["muted"])}</div><div>'
             f'<div class="t">Minor</div><div class="d">Noted on the certificate '
             f'but does not cause a failure, so it never appears above.</div>'
             f'</div></div>'
             f'<div style="font-size:11.5px;color:var(--faint);margin-top:4px">'
             f'Categories as defined by DVSA under the 2018 EU roadworthiness '
             f'directive. One test can carry several defects, so these overlap.'
             f'</div></div>')


def panel_agecurve() -> None:
    with st.container(border=True):
        html(c.card_header("Failure rate as the car ages", "trend", p["accent"],
                         pill="Against all cars"))
        bm = benchmark.sort("age_band")
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=[f"{b}–{b + 3}" for b in bm["age_band"].to_list()],
            y=bm["failure_rate"].to_list(), mode="lines", name="All cars",
            line=dict(color=p["muted"], width=2, dash="dot"),
            hovertemplate="All cars, %{x} yrs<br>%{y:.1%}<extra></extra>"))
        fig.add_trace(go.Scatter(
            x=[f"{b}–{b + 3}" for b in this_model["age_band"].to_list()],
            y=this_model["failure_rate"].to_list(), mode="lines+markers",
            name=f"{make} {model}", line=dict(color=p["accent"], width=3),
            marker=dict(size=7), customdata=this_model["n_tests"].to_list(),
            hovertemplate=f"{make} {model}, %{{x}} yrs<br>%{{y:.1%}}"
                          "<br>%{customdata:,} tests<extra></extra>"))
        fig.update_yaxes(tickformat=".0%")
        fig = style_fig(fig, p, 300)
        fig.update_layout(showlegend=True,
                          legend=dict(orientation="h", y=1.14, x=0,
                                      bgcolor="rgba(0,0,0,0)"))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        html('<div style="font-size:12px;color:var(--muted)">The all-cars average '
             'peaks around 18–21 years and then falls. Not because old cars '
             'improve — the neglected ones have already been scrapped, so what '
             'still takes an MOT at 25 is the maintained minority.</div>')


# How many rows the ranking shows at each end. A scrollable list of two
# thousand trim variants is not something anyone reads; the ends of the
# distribution and your own car are the whole of what the question was.
RANK_HEAD, RANK_TAIL = 12, 5


def _rank_row(r: dict, position: int, mine: bool) -> str:
    lo = max(0.0, r["standardised"] - r["margin"])
    hi = r["standardised"] + r["margin"]
    return (f'<tr class="{"me" if mine else ""}">'
            f'<td class="rk">{position:,}</td>'
            f'<td><div class="nm">{escape(r["make"].title())} '
            f'{escape(r["model"].title())}</div>'
            f'<div class="sub">{lo:.1%}–{hi:.1%} at 95%</div></td>'
            f'<td class="num">{r["standardised"]:.1%}</td>'
            f'<td class="num" style="color:var(--muted);font-weight:500">'
            f'{r["observed"]:.1%}</td>'
            f'<td class="num" style="color:var(--muted);font-weight:500">'
            f'{r["n_tests"]:,}</td></tr>')


def panel_reliability_ranking() -> None:
    """Ranked like-for-like on mileage — see serving.reliability_ranking for
    why a plain sort of the observed column ranks garage queens.

    Built as HTML rather than st.dataframe on purpose. The dataframe widget
    paints to a canvas themed from config.toml, which is pinned to dark, so it
    stayed dark in light mode and no amount of CSS reached it. Every other
    panel here is HTML or Plotly, both of which follow the palette.
    """
    ranked = serving.reliability_ranking(age_band)
    with st.container(border=True):
        html(c.card_header("Most and least reliable at this age", "check",
                           p["good"], pill=f"{age_band}\u2013{age_band + 3} years"))
        if ranked.is_empty():
            html('<div style="color:var(--muted);font-size:13px">No models in '
                 'this age group have cars across enough of the mileage range '
                 'to rank fairly.</div>')
            return

        rows = ranked.to_dicts()
        total = len(rows)
        mine = next((i for i, r in enumerate(rows)
                     if r["make"] == make and r["model"] == model), None)

        if mine is not None:
            r = rows[mine]
            html(f'<div style="font-size:12.5px;color:var(--muted);'
                 f'margin:-2px 0 12px">{escape(make.title())} '
                 f'{escape(model.title())} ranks <b>{mine + 1:,} of '
                 f'{total:,}</b> at this age, at <b>{r["standardised"]:.1%}</b> '
                 f'once mileage is levelled out — against '
                 f'<b>{r["observed"]:.1%}</b> as tested.</div>')
        else:
            html('<div style="font-size:12.5px;color:var(--muted);'
                 'margin:-2px 0 12px">This car is not in the table. Its cars '
                 'in this age group do not span enough of the mileage range '
                 'for a like-for-like comparison, and guessing at the rest '
                 'would be worse than leaving it out.</div>')

        head = list(range(min(RANK_HEAD, total)))
        tail = [i for i in range(max(total - RANK_TAIL, len(head)), total)]
        shown = sorted(set(head) | set(tail) | ({mine} if mine is not None
                                                else set()))
        body, previous = "", None
        for i in shown:
            if previous is not None and i > previous + 1:
                body += (f'<tr><td colspan="5" style="text-align:center;'
                         f'color:var(--faint);font-size:11px;padding:7px">'
                         f'{i - previous - 1:,} more</td></tr>')
            body += _rank_row(rows[i], i + 1, i == mine)
            previous = i

        html(f'<table class="tbl"><thead><tr><th>#</th><th>Vehicle</th>'
             f'<th>Like-for-like</th><th>As tested</th><th>Tests</th></tr>'
             f'</thead><tbody>{body}</tbody></table>'
             f'<div style="font-size:11.5px;color:var(--faint);margin-top:10px">'
             f'Ranked on the like-for-like figure: each model reweighted onto '
             f'the mileage spread of every car its age, because a plain sort '
             f'of the as-tested column puts a barely-driven supercar on top. '
             f'Models without cars across enough of that range are left out '
             f'rather than guessed at. As-tested and the test count cover '
             f'every test in the band; the like-for-like figure covers the '
             f'ones with an odometer reading. None of this separates how a car '
             f'was built from how it was looked after, and the expensive '
             f'marques are looked after.</div>')


def panel_insights() -> None:
    with st.container(border=True):
        html(c.card_header("Key insights", "bulb", p["warn"]))
        out = []
        if bench:
            gap = (failure_rate - bench) * 100
            out.append(c.insight(
                verdict + " reliability",
                f"{failure_rate:.1%} failure rate against {bench:.1%} for all cars "
                f"of this age — {abs(gap):.1f} points "
                f"{'below' if gap < 0 else 'above'} the average.",
                "trend", tone, soft))
        if not top.is_empty():
            cat = top["defect_category"][0]
            share = float(top.filter(pl.col("defect_category") == cat)
                          ["share_of_tests"].sum())
            out.append(c.insight(
                f"{cat} is the most common problem",
                # The count comes from the top-ten table, so it is a share of
                # those ten rather than of every defect type recorded. Saying
                # "distinct defect types" implied the latter.
                f"Accounts for {share:.1%} of all tests in this age group, "
                f"across {int(top.filter(pl.col('defect_category') == cat).height)}"
                f" of the ten most common defects.", "list", p["accent"],
                p["accent_soft"]))
        if mile_jump:
            band, rate, step, worst_rate, worst_band = mile_jump
            out.append(c.insight(
                "Higher risk at high mileage",
                f"Failure rate rises sharply after {band} miles, reaching "
                f"{worst_rate:.1%} by {worst_band}.", "warn", p["warn"],
                p["warn_soft"]))
        if percentile is not None:
            out.append(c.insight(
                f"Ranks better than {percentile}% of models this age",
                f"Compared against {len(peers_same_age):,} models with enough "
                f"tests at {age_band}–{age_band + 3} years.", "car", p["teal"],
                "var(--panel2)"))
        if n_tests < MIN_TESTS_FOR_CONFIDENCE:
            out.append(c.insight(
                "Treat these figures with caution",
                f"Only {n_tests} tests in this group, below the "
                f"{MIN_TESTS_FOR_CONFIDENCE}-test threshold for a reliable rate.",
                "warn", p["bad"], p["bad_soft"]))
        html("".join(out))


def footer() -> None:
    html(f'<div class="foot">{c.icon("doc", 13)} Source: DVSA anonymised MOT '
         f'testing data, 2025 — 42,728,066 tests, 35,368,333 after cleaning. '
         f'Contains public sector information licensed under the Open '
         f'Government Licence v3.0. &nbsp;·&nbsp; An MOT failure rate is not a '
         f'reliability rating: the test covers safety and emissions items at '
         f'one annual point, cannot see repairs made between tests, and '
         f'reflects owner maintenance as much as build quality.</div>')


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------
with hero_col:
    panel_hero()

st.write("")

if page == "Overview":
    l, r = st.columns(2)
    with l:
        panel_probability()
    with r:
        panel_summary()
    st.write("")
    l, r = st.columns(2)
    with l:
        panel_defects(5)
    with r:
        panel_mileage()
    st.write("")
    l, r = st.columns(2)
    with l:
        panel_comparison()
    with r:
        panel_insights()

elif page == "Reliability":
    l, r = st.columns([1, 1])
    with l:
        panel_probability()
    with r:
        panel_insights()
    st.write("")
    panel_agecurve()
    st.write("")
    panel_reliability_ranking()

elif page == "Failure reasons":
    l, r = st.columns([3, 2])
    with l:
        panel_defects(10)
    with r:
        panel_severity()
    st.write("")
    l, r = st.columns(2)
    with l:
        panel_repair_areas()
    with r:
        panel_forecourt()

elif page == "Mileage analysis":
    panel_mileage()
    st.write("")
    l, r = st.columns([2, 3])
    with l:
        panel_insights()
    with r:
        panel_agecurve()

elif page == "Comparison":
    l, r = st.columns([3, 2])
    with l:
        panel_comparison()
    with r:
        panel_probability()

footer()
