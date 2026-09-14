"""FR5 — the serving layer.  Run with: streamlit run motintel_app.py

Reads only the pre-aggregated Parquet exported by motintel/export.py. There is no
DuckDB file here and no raw data: the app displays numbers computed offline,
plus one on-demand call to the AI interpretation layer.

The page answers the questions a buyer asks, in the order they ask them: is it
reliable, what usually goes wrong, does age or mileage matter, how does it
compare, and what should I do. Every figure and every verdict on it is computed
from the data. The AI layer interprets those findings and produces none of
them, so the page stays honest and useful with it switched off entirely.
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from html import escape
from pathlib import Path

import plotly.graph_objects as go
import polars as pl
import streamlit as st
from dotenv import load_dotenv

from motintel import llm, serving
from motintel.config import PROCESSED
from motintel.queries import MIN_TESTS_FOR_CONFIDENCE
from motintel.ui import charts
from motintel.ui import components as c
from motintel.ui import names
from motintel.ui import theme as th

ROOT = Path(__file__).resolve().parent

# Streamlit runs as its own process and inherits nothing from the shell that
# ran the pipeline, so the API key is read here or the AI panel reports "no key
# configured" against a perfectly good .env.
load_dotenv(ROOT / ".env")


st.set_page_config(page_title="MOTIntel", page_icon="🚗", layout="wide")


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


# Counts the cards on one render so each gets a unique key. The script runs
# top to bottom on every rerun, so this starts at zero each time.
_card_n = 0


@contextmanager
def shell(title: str = "", pill: str = "", action: str = "",
          boxed: bool = True):
    """The card a panel sits in — or nothing at all, inside an expander.

    An expander is already a bordered surface carrying the panel's title, so
    boxing the panel again would draw a border inside a border and print the
    heading twice. `title` carries markup and is escaped by the caller.
    """
    if boxed:
        # Keyed so the stylesheet can find it. Streamlit renders a container's
        # key as an `st-key-…` class, which is the only stable hook it offers.
        global _card_n
        _card_n += 1
        with st.container(border=True, key=f"motcard{_card_n}"):
            if title or pill or action:
                html(c.card_header(title, pill, action))
            yield
    else:
        if pill or action:
            html(f'<div class="panel-sub">{escape(pill or action)}</div>')
        yield


# ---------------------------------------------------------------------------
# theme + data
# ---------------------------------------------------------------------------
# ?theme=light selects the theme on a cold load, so a particular view can be
# linked or screenshotted without clicking anything.
if "light_mode" not in st.session_state:
    st.session_state["light_mode"] = st.query_params.get("theme") == "light"
p = th.palette("light" if st.session_state["light_mode"] else "dark")
html(th.css(p))

try:
    T = serving.tables()
except FileNotFoundError as e:
    st.error(f"{e}")
    st.stop()

age_curve, benchmark, meta = T["age_curve"], T["benchmark"], T["vehicle_meta"]

# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------
# The selectable set, the age bands and the profile all come from
# motintel.serving, so the page and the AI layer cannot drift apart.
selectable = serving.selectable_vehicles()
# Every car the page can show, most-tested first. One list rather than a make
# and then a model: 5,998 cars sit under 122 makes, Mercedes-Benz alone has 425
# entries once trims are counted, and a reader after a Golf GTI had to know to
# open Volkswagen before there was anywhere to type "gti".
cars: list[tuple[str, str]] = list(zip(selectable["make"].to_list(),
                                       selectable["model"].to_list()))
car_set = set(cars)

# How many other cars can sit beside this one. Four columns is as many as the
# comparison table can hold on a laptop before a row stops reading as a row.
MAX_COMPARE = 3


def car_label(car: tuple[str, str]) -> str:
    return names.display_name(*car)


def car_from_url(make_key: str, model_key: str) -> tuple[str, str] | None:
    """The car a link names, if it names one there is. A make on its own —
    the shape links took before the model was kept in them too — opens that
    make's most-tested model."""
    mk, md = st.query_params.get(make_key), st.query_params.get(model_key)
    if (mk, md) in car_set:
        return mk, md
    if mk and not md:
        return next((car for car in cars if car[0] == mk), None)
    return None


def sync_url(make_key: str, model_key: str, car: tuple[str, str] | None,
             default: tuple[str, str] | None) -> None:
    """Keep the link in step with a choice, and leave it out at the default
    so a plain visit keeps a plain URL."""
    if car is None or car == default:
        st.query_params.pop(make_key, None)
        st.query_params.pop(model_key, None)
    else:
        st.query_params[make_key], st.query_params[model_key] = car


def cars_from_url() -> list[tuple[str, str]]:
    """The comparison cars a link names — `vs=MAKE|MODEL`, once per car — or
    the single vs_make/vs_model pair links carried before there could be more
    than one. Anything naming no car in the data is dropped, not guessed at."""
    asked = [tuple(v.split("|", 1)) for v in st.query_params.get_all("vs")
             if "|" in v]
    legacy = car_from_url("vs_make", "vs_model")
    found = [car for car in asked if car in car_set] + ([legacy] if legacy
                                                        else [])
    return list(dict.fromkeys(found))[:MAX_COMPARE]


def pick_car() -> None:
    """The search box goes somewhere; it does not record where you are. A pick
    becomes the page's car and the field clears for the next search — the car
    itself is named in the page title, where it can be read."""
    chosen = st.session_state.get("search")
    if chosen:
        st.session_state["car"] = chosen
        st.session_state["search"] = None


# Opens on the car a link names, or else on the most-tested car in the data.
# Synced to the URL by hand: bind="query-params" would write the display name
# into the link, and the links already shared say make= and model=.
if "car" not in st.session_state:
    st.session_state["car"] = car_from_url("make", "model") or cars[0]
make, model = st.session_state["car"]
sync_url("make", "model", (make, model), cars[0])

brand, mode, switch = st.columns([5, 3, 2], vertical_alignment="center")
with brand:
    html(f'<div class="mot-brand"><div class="mark">{c.icon("car", 21, "#fff")}'
         f'</div><div><div class="name">MOTIntel</div>'
         f'<div class="tag">Real MOT data. Smarter decisions.</div>'
         f'</div></div>')
with mode:
    # Buyer view puts the answers first and keeps the working out out of the
    # way; data view shows it — the DVSA wording under each failure reason,
    # the sampling margin, the severity definitions, the like-for-like
    # percentile. The figures and verdicts are the same in both.
    view = st.segmented_control("View", ["Buyer view", "Data view"],
                                default="Buyer view", key="view",
                                label_visibility="collapsed")
    data_view = view == "Data view"
with switch:
    st.toggle("Light mode", key="light_mode")

this_model = (age_curve.filter((pl.col("make") == make)
                               & (pl.col("model") == model))
              .sort("age_band"))
bands = serving.age_bands(make, model)

# Seeded once from the link. A car cannot sit beside itself, so picking the
# page's own car in the search box takes it out of the comparison.
if "vs" not in st.session_state:
    st.session_state["vs"] = cars_from_url()
if (make, model) in st.session_state["vs"]:
    st.session_state["vs"] = [car for car in st.session_state["vs"]
                              if car != (make, model)]

# Search, age and comparison in one control area: three settings of one
# question, not three unrelated boxes above the page.
with st.container(key="controls"):
    find, age_col, compare = st.columns([5, 3, 4], gap="medium")
    with find:
        # Only cars in the data can be offered — the list is the data — and
        # most-tested first, so the top of it before anything is typed is the
        # cars people actually own.
        st.selectbox("Search make, model or trim", cars, index=None,
                     key="search", format_func=car_label, on_change=pick_car,
                     placeholder="e.g. Ford Fiesta, Golf GTI, MX-5")
        st.caption(f"{len(cars):,} vehicles available")
    with age_col:
        if not bands:
            st.warning(f"No {names.display_name(make, model)} tests in this "
                       f"dataset.")
            st.stop()
        if len(bands) == 1:
            age_band = bands[0]
            st.query_params.pop("age", None)
            st.caption(f"Only tested at {age_band}–{age_band + 3} years "
                       f"in this data.")
        else:
            # Opens on the age this model was most often tested at, rather
            # than the middle of its range, so the first figure shown rests on
            # the most data there is for it.
            tested = dict(zip(this_model["age_band"].to_list(),
                              this_model["n_tests"].to_list()))
            default_age = max(bands, key=lambda b: tested.get(b, 0))
            # Synced to the URL by hand rather than with bind="query-params",
            # which serialises through format_func: the link would have to say
            # ?age=6–9 yrs, en dash and all, and ?age=6 was silently thrown
            # away. Seeded only when the held value is not an age this car was
            # tested at — first load, or a new model without that band.
            if st.session_state.get("age") not in bands:
                asked = st.query_params.get("age", "")
                st.session_state["age"] = (int(asked) if asked.isdigit()
                                           and int(asked) in bands
                                           else default_age)
            age_band = st.select_slider(
                "Age at test", options=bands,
                format_func=lambda x: f"{x}–{x + 3} years", key="age",
                help="The vehicle's age when the MOT was recorded.")
            if age_band == default_age:
                st.query_params.pop("age", None)
            else:
                st.query_params["age"] = str(age_band)
    with compare:
        # Always at the page car's age — panel_compare says why never across
        # ages.
        vs = st.multiselect(
            "Compare with", cars, key="vs", format_func=car_label,
            max_selections=MAX_COMPARE,
            placeholder=f"Add up to {MAX_COMPARE} cars")

st.query_params.pop("vs_make", None)
st.query_params.pop("vs_model", None)
if vs:
    st.query_params["vs"] = [f"{mk}|{md}" for mk, md in vs]
else:
    st.query_params.pop("vs", None)

# ---------------------------------------------------------------------------
# figures and findings
# ---------------------------------------------------------------------------
# One profile, built by motintel.serving, shared by the page and the AI layer.
# Every figure below is read off it, and every verdict off its findings — the
# layer of the data that says what the figures mean, decided in code.
profile = serving.build_profile(make, model, age_band)
f = profile.findings
sparse = serving.is_sparse(profile)
n_tests, failure_rate, bench = (profile.n_tests, profile.failure_rate,
                                profile.benchmark)
top = profile.top_defects
area_shares = profile.repair_areas
sev_shares = profile.severity
step = f.get("mileage")
ages = f"{age_band}–{age_band + 3}"

# As a reader expects to see them — `MX-5`, not `Mx-5`. Display only: every
# filter still matches on the DVSA strings.
car_name = names.display_name(make, model)
model_name = names.display_model(model, make)

VERDICT = {"better": (p["good"], p["good_soft"], "Better than average", "check"),
           "about": (p["warn"], p["warn_soft"], "About average", "warn"),
           "worse": (p["bad"], p["bad_soft"], "Worse than average", "warn")}
tone, soft, verdict, verdict_icon = VERDICT.get(
    f.get("classification"),
    (p["accent"], p["accent_soft"], "No benchmark", "info"))
SAMPLE = {"large": "Large sample", "moderate": "Moderate sample",
          "small": "Small sample — treat with caution"}
SEV = {"Dangerous": (p["bad"], p["bad_soft"]), "Major": (p["warn"], p["warn_soft"])}
# The colours of the cars being compared, after this model's blue.
SERIES = [p["teal"], p["violet"], p["fuchsia"]]
JOB_SIZE = {"minor": "Small job", "moderate": "Medium job", "major": "Big job"}

mrow = meta.filter((pl.col("make") == make) & (pl.col("model") == model))
fuels = (mrow["fuels"][0] if not mrow.is_empty() and mrow["fuels"][0]
         else None)
yr_from = int(mrow["year_from"][0]) if not mrow.is_empty() else None
yr_to = int(mrow["year_to"][0]) if not mrow.is_empty() else None
avg_age = float(mrow["avg_age"][0]) if not mrow.is_empty() else None
all_tests = int(mrow["n_tests"][0]) if not mrow.is_empty() else None

by_mileage = serving.mileage_curve(make, model)

peers_same_age = age_curve.filter(pl.col("age_band") == age_band)
percentile = None
if len(peers_same_age) > 5:
    percentile = round(100 * int((peers_same_age["failure_rate"]
                                  > failure_rate).sum()) / len(peers_same_age))
row = this_model.filter(pl.col("age_band") == age_band)
peers = peers_same_age.sort("n_tests", descending=True).head(6)
if not ((peers["make"] == make) & (peers["model"] == model)).any():
    peers = pl.concat([peers, row.select(peers.columns)])
peers = peers.sort("failure_rate")
ranked_rows = serving.reliability_ranking(age_band).to_dicts()
# Zero-based, for indexing the ranked rows; the profile's position is 1-based.
rank_mine = profile.rank[0] - 1 if profile.rank else None
labelled = serving.defect_labels() is not None

# What moving the age slider changed. The slider was a way to see a different
# number, and the reader had to remember the last one to know what the move
# meant; the page remembers it for them. Kept until the age moves again, so
# ticking a checklist box further down does not wipe it.
current = {"car": (make, model), "band": age_band, "rate": failure_rate,
           "rank": profile.rank}
seen = st.session_state.get("_seen")
if seen and seen["car"] != (make, model):
    st.session_state.pop("_age_change", None)
elif seen and seen["band"] != age_band:
    st.session_state["_age_change"] = (seen, current)
st.session_state["_seen"] = current
age_change = st.session_state.get("_age_change")
if age_change and age_change[1]["band"] != age_band:
    age_change = None


def severity_badge(share: float | None) -> str:
    """How often DVSA graded this reason Dangerous. A share, not a label: the
    grade belongs to each defect line, not to the wording."""
    if share is None:
        return '<span class="badge" style="color:var(--muted)">Not recorded</span>'
    if share >= 0.95:
        return c.badge("Dangerous", p["bad"], p["bad_soft"])
    if share <= 0.05:
        return c.badge("Major", p["warn"], p["warn_soft"])
    return c.badge(f"Dangerous in {share:.0%}", p["bad"], p["bad_soft"])


def job_badge(effort: str | None) -> str:
    if not effort:
        return ""
    return c.badge(JOB_SIZE[effort], p["muted"], "var(--panel2)")


def mileage_range(band: int) -> str:
    return (f"{band // 1000}k+" if band >= serving.MILEAGE_CAP
            else f"{band // 1000}–{(band + 20_000) // 1000}k")


# ---------------------------------------------------------------------------
# page furniture
# ---------------------------------------------------------------------------
def panel_hero() -> None:
    """What this car is, and what the data holds on it."""
    first = []
    if yr_from and yr_to:
        first.append(f"First registered <b>{yr_from}–{yr_to}</b>")
    if fuels:
        parts = [x.strip() for x in fuels.split("/")]
        joined = (", ".join(parts[:-1]) + " & " + parts[-1] if len(parts) > 1
                  else parts[0])
        first.append(f"<b>{escape(joined)}</b>")
    second = []
    if all_tests:
        second.append(f"<b>{c.compact(all_tests)}</b> MOT tests analysed")
    if avg_age:
        second.append(f"Average vehicle age <b>{avg_age:.0f} years</b>")

    def line(items: list[str]) -> str:
        # Each item in its own span: the line is a flex row, and bare text
        # nodes as flex items lose the space after a <b>.
        return ('<div class="meta">'
                + "<i>·</i>".join(f"<span>{m}</span>" for m in items)
                + '</div>') if items else ""

    html(f'<div class="hero"><div class="title-row">'
         f'<div class="title" role="heading" aria-level="1">'
         f'<span class="mk">{escape(names.display_make(make))}</span> '
         f'{escape(model_name)}</div>'
         f'<span class="tag">MOT reliability analysis</span></div>'
         f'{line(first)}{line(second)}</div>')


SECTIONS = [("overview", "Overview"), ("failures", "Failures"),
            ("age-mileage", "Age & mileage"), ("compare", "Compare"),
            ("checks", "Checks"), ("method", "Method")]


def panel_summarybar() -> None:
    """The model, its rate and its verdict, and a link to each question.
    Sticky on a wide screen, so the answer stays in view down a long page."""
    links = "".join(f'<a href="#{anchor}">{label}</a>'
                    for anchor, label in SECTIONS)
    with st.container(key="summarybar"):
        html(f'<div class="sbar"><div class="who"><b>{escape(car_name)}</b>'
             f'<span class="r">{failure_rate:.1%} failure rate at {ages} '
             f'years</span>{c.status(verdict, tone, verdict_icon)}</div>'
             f'<nav aria-label="Sections">{links}</nav></div>')


def section(anchor: str, title: str, sub: str = "") -> None:
    html(c.section_head(anchor, title, sub))


# ---------------------------------------------------------------------------
# 1. is it reliable?
# ---------------------------------------------------------------------------
def takeaways() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """What looks good and what needs attention, from the findings alone.

    The page used to be almost entirely a list of things that go wrong. A buyer
    weighing a car needs both sides, and needs them without waiting on the AI
    layer, so these are written from the findings in code.
    """
    good, watch = [], []
    cls, gap = f.get("classification"), f.get("gap_pp")
    if cls == "better":
        good.append(("Below-average failure rate",
                     f"{abs(gap):.1f} percentage points under the average "
                     f"for {ages} year vehicles."))
    elif cls == "worse":
        watch.append(("Above-average failure rate",
                      f"{gap:.1f} percentage points over the average for "
                      f"{ages} year vehicles."))
    elif cls == "about":
        watch.append(("Only about average",
                      f"Within 10% of the average failure rate for {ages} "
                      f"year vehicles."))
    if f.get("age_better"):
        good.append(("Better than average at some ages" if f.get("age_worse")
                     else "Better than average at every age",
                     f"Below the all-cars failure rate at "
                     f"{c.bands_text(f['age_better'])} years."))
    if f.get("age_worse"):
        watch.append(("Worse than average at other ages" if f.get("age_better")
                      else "Worse than average at every age",
                      f"Above the all-cars failure rate at "
                      f"{c.bands_text(f['age_worse'])} years."))
    if f.get("sample") == "large":
        good.append(("Large supporting sample",
                     f"{n_tests:,} tests: the rate is accurate to within "
                     f"{f['margin_pp']:.1f} percentage points."))
    elif f.get("sample") == "small":
        watch.append(("Small sample",
                      f"Only {n_tests:,} tests: the rate could move by "
                      f"{f['margin_pp']:.1f} percentage points either way."))
    better_than = f.get("rank_better_than")
    if better_than is not None and better_than >= 60:
        good.append(("Strong once mileage is levelled out",
                     f"Its mileage-levelled failure rate is lower than "
                     f"{better_than}% of models this age."))
    elif better_than is not None and better_than <= 40:
        watch.append(("Weaker once mileage is levelled out",
                      f"Its mileage-levelled failure rate is lower than only "
                      f"{better_than}% of models this age."))
    if f.get("dominant_area") and f["dominant_area"][1] >= 0.3:
        area, share = f["dominant_area"]
        watch.append((c.sentence_case(area),
                      f"{share:.0%} of the ten commonest failure reasons."))
    if step:
        watch.append(("Mileage matters",
                      f"Observed failure rates rise sharply from the "
                      f"{step['after']} miles band, reaching "
                      f"{step['top_rate']:.1%} at {step['top_band']}."))
    if f.get("mostly_dangerous"):
        count = len(f["mostly_dangerous"])
        watch.append(("Dangerous-graded failures",
                      f"{count} of the ten commonest reasons "
                      f"{'is' if count == 1 else 'are'} usually graded "
                      f"Dangerous."))
    return good[:4], watch[:4]


def panel_reliability() -> None:
    with shell():
        if bench is not None:
            gap = f.get("gap_pp", 0.0)
            said = (f"{abs(gap):.1f} percentage points "
                    f"{'below' if gap < 0 else 'above'} the {bench:.1%} "
                    f"average for {ages} year vehicles."
                    if abs(gap) >= 0.05
                    else f"Level with the {bench:.1%} average for {ages} "
                         f"year vehicles.")
        else:
            said = "There is no all-cars figure for this age to compare with."
        html(f'<div class="verdict-row">'
             f'{c.status(verdict, tone, verdict_icon, big=True)}'
             f'<p>{said}</p></div>')

        if age_change:
            before, after = age_change
            move = (f'Moved from <b>{before["band"]}–{before["band"] + 3}</b> '
                    f'to <b>{ages} years</b>: failure rate '
                    f'<b>{before["rate"]:.1%} → {after["rate"]:.1%}</b> '
                    f'({c.signed_pts(after["rate"] - before["rate"])})')
            if before["rank"] and after["rank"]:
                move += (f' · mileage-levelled rank <b>{before["rank"][0]:,} of '
                         f'{before["rank"][1]:,} → {after["rank"][0]:,} of '
                         f'{after["rank"][1]:,}</b>')
            html(f'<div class="change">{move}</div>')

        sample = SAMPLE.get(f.get("sample"), "")
        if data_view and "margin_pp" in f:
            sample += f" · ±{f['margin_pp']:.1f} pp at 95%"
        figures = [(f"{failure_rate:.1%}", "Observed failure rate",
                    f"{ages} year vehicles")]
        if bench is not None:
            figures += [(f"{bench:.1%}", "Average for cars this age",
                         "All makes and models"),
                        (c.signed_pts(failure_rate - bench),
                         "Against that average", "Negative is better")]
        figures.append((f"{n_tests:,}", "MOT tests in this age band", sample))
        html(c.figures(figures))
        if bench is not None:
            html(c.scale(failure_rate, bench, tone, model_name))

        good, watch = takeaways()
        html(c.takeaways(good, watch, p["good"], p["warn"]))

        extra = (f' It fails less often than <b>{percentile}%</b> of the '
                 f'{len(peers_same_age):,} models tested at this age, counting '
                 f'each model once however many were sold.'
                 if data_view and percentile is not None else "")
        html(c.note(f'Observed MOT data, not a prediction. Based on '
                    f'{n_tests:,} tests from this age band. A single MOT can '
                    f'contain multiple defects.{extra}'))


# ---------------------------------------------------------------------------
# 2. what usually goes wrong?
# ---------------------------------------------------------------------------
def toggle_reasons() -> None:
    st.session_state["all_reasons"] = not st.session_state.get("all_reasons")


def panel_failures() -> None:
    """The failure reasons, then where on the car they fall and how serious
    they are.

    A reason is a row that scans — a short name, how often, how serious, how
    big the repair — and opens onto why it matters. The DVSA wording is kept
    inside: it is the record, the rewrite is a reading of it, and a reader who
    wants to check one against the other should not have to leave the page.

    Choosing a part of the car filters the reasons to it and moves it to the
    top of the checklist, so a buyer following one thread through the page
    does not have to hold it in their head.
    """
    chosen = None
    if area_shares:
        share_of = dict(area_shares)
        options = [area for area, _ in area_shares]
        if st.session_state.get("area") not in options:
            st.session_state["area"] = None
        chosen = st.pills(
            "Show one part of the car", options, key="area",
            selection_mode="single",
            format_func=lambda a: f"{c.sentence_case(a)} · {share_of[a]:.0%}")

    rows = [(i, d) for i, d in enumerate(top, 1)]
    if chosen:
        rows = [(i, d) for i, d in rows if d.get("repair_area") == chosen]
    elif not st.session_state.get("all_reasons"):
        rows = rows[:3]
    action = (f"{c.sentence_case(chosen)} only" if chosen
              else f"Top {len(rows)} of {len(top)}" if len(rows) < len(top)
              else f"All {len(top)}")
    with shell("Top failure reasons", action=action if top else ""):
        if not top:
            html(c.empty("No breakdown for this group",
                         "Too few recorded failure items at this make, model "
                         "and age to break down. The rates above are "
                         "unaffected.", "list"))
            return
        body = ""
        for i, d in rows:
            plain = (d.get("plain_english") or "").rstrip(".")
            dvsa = f'{d["category"]} — {d["defect"]}'
            headline = d.get("headline") or plain or d["category"]
            detail = (d.get("detail") if d.get("headline")
                      else dvsa if plain else d["defect"])
            if data_view and d.get("headline"):
                detail = f"{detail} · DVSA: {dvsa}"
            share = d.get("dangerous_share")
            meaning = ("DVSA grades it Dangerous: a car with it must not be "
                       "driven until it is repaired." if share is not None
                       and share >= 0.95
                       else "DVSA grades it Major: the car fails and must be "
                            "repaired." if share is not None and share <= 0.05
                       else f"DVSA graded it Dangerous in {share:.0%} of these "
                            f"tests, and Major in the rest." if share is not None
                       else "")
            why = (f'{escape(plain + "." if plain else dvsa)} It was recorded '
                   f'in {d["share_of_tests"]:.1%} of all MOT tests for this '
                   f'model at {ages} years. {meaning}')
            facts = [("DVSA record", escape(dvsa))]
            if d.get("repair_area"):
                facts.append(("Part of the car",
                              escape(c.sentence_case(d["repair_area"]))))
            if d.get("effort"):
                facts.append(("Typical repair",
                              f'{JOB_SIZE[d["effort"]]} — how big the repair '
                              f'is, not how serious the fault is'))
            if d.get("forecourt_check"):
                facts.append(("What to check", escape(d["forecourt_check"])))
            body += c.reason(i, headline, detail, d["share_of_tests"],
                             severity_badge(share), job_badge(d.get("effort")),
                             why, facts)
        html(c.reason_head() + body)
        if not chosen and len(top) > 3:
            st.button(f"Show all {len(top)}"
                      if not st.session_state.get("all_reasons")
                      else "Show the top 3", key="more",
                      on_click=toggle_reasons)
        html(c.note("Frequency is the share of all tests in this group, not of "
                    "failures. A single MOT can contain multiple defects, so "
                    "these overlap and do not add up to the failure rate. "
                    "Severity is DVSA's grade; the typical repair is how big "
                    "the job is, which is a different thing."))

    st.write("")
    left, right = st.columns(2)
    with left:
        with shell("Where the failures are", pill="Ten commonest reasons"):
            if not area_shares:
                html(c.empty("These failures are not grouped yet",
                             "This model's failure reasons have not yet been "
                             "read into parts of the car. Every figure on this "
                             "page is unaffected."))
            else:
                biggest = area_shares[0][1]
                body = ""
                for area, share in area_shares:
                    fill = p["accent"] if area == (chosen or area_shares[0][0]) \
                        else p["bar_peer"]
                    body += (f'<tr><td class="nm">'
                             f'{escape(c.sentence_case(area))}</td>'
                             f'<td class="num">{share:.0%}</td>'
                             f'<td style="width:52%">'
                             f'{c.bar(share / biggest, fill)}</td></tr>')
                area, share = area_shares[0]
                html(c.table(body) + c.why(
                    f"<b>{share:.0%}</b> of this model's ten commonest failure "
                    f"reasons involve <b>{escape(area)}</b> — more than any "
                    f"other part of the car."))
    with right:
        with shell("How serious are the failures?",
                   pill="Share of failed tests"):
            if not sev_shares:
                html(c.empty("Not enough failure items to break down",
                             "This make, model and age has too few recorded "
                             "defects to split by severity.", "warn"))
            else:
                body = ""
                for grade, share in sev_shares.items():
                    colour, _ = SEV[grade]
                    body += (f'<tr><td style="width:14px"><span class="dot" '
                             f'style="background:{colour}"></span></td>'
                             f'<td class="nm">{grade}</td>'
                             f'<td class="num">{share:.0%}</td>'
                             f'<td style="width:48%">{c.bar(share, colour)}'
                             f'</td></tr>')
                html(c.table(body))
                if "Dangerous" in sev_shares:
                    html(c.why(
                        f"<b>{sev_shares['Dangerous']:.0%}</b> of failed tests "
                        f"had at least one Dangerous defect: those cars must "
                        f"not be driven until repaired."))
            if data_view:
                html(c.defs([
                    ("Dangerous", p["bad"],
                     "A direct and immediate risk to road safety, or a serious "
                     "environmental impact. The car must not be driven until "
                     "it is repaired."),
                    ("Major", p["warn"],
                     "May affect safety, put other road users at risk, or harm "
                     "the environment. The car fails and must be repaired."),
                    ("Minor", p["muted"],
                     "Noted on the certificate but does not cause a failure, "
                     "so it never appears here."),
                ]))
            html(c.note("A single MOT test can carry several defects, so these "
                        "shares overlap and do not add to 100%."))


# ---------------------------------------------------------------------------
# 3. does age or mileage matter?
# ---------------------------------------------------------------------------
def age_curve_fig(extra: list[tuple[str, str, str]] | None = None) -> go.Figure:
    """The all-cars average by age with this model on it, the selected age
    band shaded — and each car being compared, in colours that say nothing
    about better or worse.

    Points sit mid-band on a numeric age axis, so the shaded band contains its
    own point and the ticks read as ages. Hovering a point gives this model,
    all cars, and the difference between them.
    """
    bm = benchmark.sort("age_band")
    all_cars = dict(zip(bm["age_band"].to_list(), bm["failure_rate"].to_list()))
    fig = go.Figure()
    fig.add_vrect(x0=age_band, x1=age_band + 3, fillcolor=p["accent_soft"],
                  line_width=0, layer="below")
    fig.add_trace(go.Scatter(
        x=[b + 1.5 for b in bm["age_band"].to_list()],
        y=bm["failure_rate"].to_list(), mode="lines", name="All cars",
        line=dict(color=p["muted"], width=2, dash="dot"), hoverinfo="skip"))
    highest = max(bm["age_band"].to_list())
    for mk, md, colour in [(make, model, p["accent"]), *(extra or [])]:
        curve = (age_curve.filter((pl.col("make") == mk)
                                  & (pl.col("model") == md))
                 .sort("age_band"))
        label = names.display_name(mk, md)
        band_list = curve["age_band"].to_list()
        rates = curve["failure_rate"].to_list()
        custom = [[n, all_cars.get(b),
                   c.signed_pts(r - all_cars[b]) if b in all_cars else "—"]
                  for b, r, n in zip(band_list, rates,
                                     curve["n_tests"].to_list())]
        fig.add_trace(go.Scatter(
            x=[b + 1.5 for b in band_list], y=rates,
            text=[f"{b}–{b + 3}" for b in band_list], customdata=custom,
            mode="lines+markers", name=label,
            line=dict(color=colour, width=3), marker=dict(size=7),
            hovertemplate=(f"<b>%{{text}} years</b><br>{label}: %{{y:.1%}}"
                           "<br>All cars: %{customdata[1]:.1%}"
                           "<br>Difference: %{customdata[2]}"
                           "<br>%{customdata[0]:,} tests<extra></extra>")))
    fig.update_yaxes(tickformat=".0%")
    fig.update_xaxes(tickmode="linear", tick0=0, dtick=3, tickangle=0,
                     range=[0, highest + 3],
                     title=dict(text="Age at test (years)",
                                font=dict(size=12, color=p["muted"])))
    fig = charts.style_fig(fig, p, 300)
    fig.update_layout(showlegend=True,
                      legend=dict(orientation="h", y=1.14, x=0,
                                  bgcolor="rgba(0,0,0,0)"))
    return fig


def mileage_fig() -> go.Figure:
    """Observed failure rate by mileage band, the band where it jumps picked
    out in amber — the reason a mileage warning appears on the page at all."""
    band_list = by_mileage["band"].to_list()
    labels = [mileage_range(b) for b in band_list]
    jump = step["after"] if step else None
    colours = [p["warn"] if serving.mileage_label(b) == jump else p["accent"]
               for b in band_list]
    fig = go.Figure(go.Bar(
        x=labels, y=by_mileage["failure_rate"].to_list(), marker_color=colours,
        customdata=by_mileage["n_tests"].to_list(),
        hovertemplate="%{x} miles<br>%{y:.1%} failed"
                      "<br>%{customdata:,} tests<extra></extra>"))
    fig.update_yaxes(tickformat=".0%", nticks=6)
    fig.update_xaxes(title=dict(text="Mileage at test",
                                font=dict(size=12, color=p["muted"])))
    return charts.style_fig(fig, p, 300)


def panel_age_mileage() -> None:
    if step:
        html(c.callout(
            f"Observed failure rates increase sharply from the "
            f"{step['after']} miles band, reaching {step['top_rate']:.1%} in "
            f"the {step['top_band']} band. <a href=\"#mileage\">View mileage "
            f"data</a>", p["warn"], p["warn_soft"],
            title="Mileage is a major factor"))
        st.write("")
    left, right = st.columns(2)
    with left:
        with shell("Failure rate as the model ages", pill="Against all cars"):
            st.plotly_chart(age_curve_fig(), width="stretch",
                            config={"displayModeBar": False})
            better, worse = f.get("age_better", []), f.get("age_worse", [])
            if better and worse:
                said = (f"Below the all-cars failure rate at "
                        f"{c.bands_text(better)} years, and above it at "
                        f"{c.bands_text(worse)} years.")
            elif better:
                said = ("Below the all-cars failure rate at every age with "
                        "enough tests to say so.")
            elif worse:
                said = ("Above the all-cars failure rate at every age with "
                        "enough tests to say so.")
            else:
                said = "Not enough tests at any age to compare with all cars."
            html(c.why(said))
            if data_view:
                html(c.note("The all-cars average peaks around 18–21 years and "
                            "then falls. Not because old cars improve — the "
                            "neglected ones have already been scrapped, so "
                            "what still takes an MOT at 25 is the maintained "
                            "minority."))
    with right:
        html('<div id="mileage" class="sec-anchor"></div>')
        with shell("Observed failure rate by mileage",
                   pill="All ages of this model"):
            if by_mileage.is_empty():
                html(c.empty("No odometer readings to chart",
                             "This model has no mileage band with enough tests "
                             "in it to plot.", "trend"))
                return
            st.plotly_chart(mileage_fig(), width="stretch",
                            config={"displayModeBar": False})
            rows = by_mileage.to_dicts()
            first, last = rows[0], rows[-1]
            html(c.why(
                f"High-mileage examples need more scrutiny: "
                f"<b>{last['failure_rate']:.1%}</b> of tests in the "
                f"{mileage_range(last['band'])} miles band failed, against "
                f"<b>{first['failure_rate']:.1%}</b> in the "
                f"{mileage_range(first['band'])} band."))


# ---------------------------------------------------------------------------
# 4. how does it compare?
# ---------------------------------------------------------------------------
def shared_bands(mk: str, md: str) -> tuple[int, int]:
    """In how many of the age bands both cars were properly tested at this
    model had the lower failure rate — the whole basis for saying one does
    better than the other, rather than a single age that happens to be
    selected."""
    solid = pl.col("n_tests") >= MIN_TESTS_FOR_CONFIDENCE
    mine = dict(this_model.filter(solid).select("age_band", "failure_rate")
                .iter_rows())
    theirs = dict(age_curve.filter((pl.col("make") == mk)
                                   & (pl.col("model") == md) & solid)
                  .select("age_band", "failure_rate").iter_rows())
    both = [b for b in theirs if b in mine]
    lower = sum(1 for b in both
                if round(mine[b] * 100, 1) < round(theirs[b] * 100, 1))
    return lower, len(both)


def panel_compare() -> None:
    """This model beside up to three others, at the same age.

    The question a buyer is usually asking is "this one or that one?", and
    answering it meant noting figures down, changing car, and reading the page
    a second time. Only ever at the same age: a four-year-old car against a
    twelve-year-old one mostly measures the eight years between them.
    """
    if not vs:
        panel_peers()
        return
    rivals = [(mk, md, colour, serving.build_profile(mk, md, age_band))
              for (mk, md), colour in zip(vs, SERIES)]
    title = (f"{escape(car_name)} vs "
             + ", ".join(escape(names.display_name(mk, md))
                         for mk, md, _, _ in rivals))
    with shell(title, pill=f"{ages} years"):
        here = ([(make, model, p["accent"], profile)]
                + [r for r in rivals if r[3].n_tests])

        def called(r) -> str:
            return escape(names.display_name(r[0], r[1]))

        chip_items = [(r[2], names.display_name(r[0], r[1]),
                       f"{r[3].failure_rate:.1%}") for r in here]
        if bench is not None:
            chip_items.append(("var(--muted)", "All cars", f"{bench:.1%}"))
        html(c.chips(chip_items))

        if len(here) > 1:
            # Ordered on the figures as printed, so a reader subtracting
            # 17.4% from 23.4% gets the 6.0 points the sentence says.
            def shown(r):
                return round(r[3].failure_rate * 100, 1)

            ordered = sorted(here, key=shown)
            lead_gap = shown(ordered[1]) - shown(ordered[0])
            if len(here) == 2 and lead_gap < 0.5:
                lead = (f"At {ages} years the two fail at much the same rate.")
            elif len(here) == 2:
                lead = (f"At {ages} years the <b>{called(ordered[0])}</b> "
                        f"failed <b>{lead_gap:.1f} percentage points</b> less "
                        f"often than the {called(ordered[1])}.")
            else:
                named = [f"the {called(r)}" for r in ordered]
                lead = (f"At {ages} years, lowest failure rate first: "
                        f"<b>{named[0]}</b>, then "
                        + ", ".join(named[1:-1])
                        + (" and " if len(named) > 2 else "")
                        + named[-1] + ".")
            # Across every age both were tested at, not only the selected one.
            for r in here[1:]:
                lower, both = shared_bands(r[0], r[1])
                if both:
                    lead += (f" Across the {both} age bands both were tested "
                             f"at, the {escape(model_name)} had the lower rate "
                             f"in {lower}" + (" — all of them." if lower == both
                                              else "."))
                if serving.is_sparse(r[3]):
                    lead += (f" Only {r[3].n_tests:,} {called(r)} tests at "
                             f"this age, so treat its figures with caution.")
            html(f'<div class="h2h-lead">{lead}</div>')

            lowest = ordered[0] if lead_gap >= 0.5 else None
            best = c.badge("Lowest" if len(here) > 2 else "Lower",
                           p["good"], p["good_soft"])

            def commonest(r) -> str:
                if not r[3].top_defects:
                    return "—"
                d = r[3].top_defects[0]
                what = (d.get("headline")
                        or (d.get("plain_english") or "").rstrip(".")
                        or f'{d["category"]} — {d["defect"]}')
                return (f'{escape(what)}<div class="sub">'
                        f'{d["share_of_tests"]:.1%} of tests</div>')

            def climbs(r) -> str:
                jump = r[3].findings.get("mileage")
                return f"{jump['after']} miles" if jump else "No sharp step"

            measures = [
                ("Failure rate", lambda r: f"{r[3].failure_rate:.1%}"
                 + (best if lowest is not None and r is lowest else "")),
                ("Against the average for this age",
                 lambda r: c.signed_pts(r[3].failure_rate - bench)
                 if bench is not None else "—"),
                ("MOT tests at this age", lambda r: f"{r[3].n_tests:,}"),
                ("Failures with a Dangerous defect",
                 lambda r: f"{r[3].severity['Dangerous']:.0%}"
                 if "Dangerous" in r[3].severity else "—"),
                ("Rank with mileage levelled out",
                 lambda r: f"{r[3].rank[0]:,} of {r[3].rank[1]:,}"
                 if r[3].rank else "Not ranked"),
                ("Commonest failure", commonest),
                ("Failure rate climbs sharply after", climbs),
            ]
            body = "".join(
                f'<tr><td class="lbl">{label}</td>'
                + "".join(f'<td class="val"><span class="dot k" '
                          f'style="background:{r[2]}"></span>{value(r)}</td>'
                          for r in here)
                + '</tr>' for label, value in measures)
            head = '<th></th>' + "".join(
                f'<th><span class="dot" style="background:{r[2]}"></span> '
                f'{called(r)}</th>' for r in here)
            html(c.table(body, head, cls="h2h",
                         style=f"--cols:{min(len(here), 2)}")
                 + c.note("Failure rates are as tested, so whichever car has "
                          "been driven further looks worse; the rank levels "
                          "mileage out. None of it separates how a car was "
                          "built from how it was looked after."))

        # A missing age is not a zero. Said as plainly as the figures are.
        for mk, md, _, other in rivals:
            if other.n_tests:
                continue
            tested = ", ".join(f"{b}–{b + 3}" for b in serving.age_bands(mk, md))
            name = names.display_name(mk, md)
            html(c.empty("Not enough comparable data",
                         f"The {escape(name)} has no tests in the selected "
                         f"{ages} year band, so a direct comparison is "
                         f"unavailable here. It was tested at {tested} years.",
                         "scales"))
        st.plotly_chart(age_curve_fig([(mk, md, colour)
                                       for mk, md, colour, _ in rivals]),
                        width="stretch", config={"displayModeBar": False})


def panel_peers() -> None:
    """With nothing chosen to compare, the highest-volume models of the same
    age stand in — a proxy for comparable cars, and said to be one."""
    with shell(f"How does the {escape(model_name)} compare?",
               pill="Lower is better"):
        worst = float(peers["failure_rate"].max())
        body = ""
        for i, r in enumerate(peers.to_dicts(), 1):
            me = r["make"] == make and r["model"] == model
            if me:
                tag = c.badge("This model", p["accent"], p["accent_soft"])
            elif i == 1:
                tag = c.badge("Lowest", p["good"], p["good_soft"])
            else:
                tag = c.badge(c.signed_pts(r["failure_rate"] - failure_rate),
                              p["muted"], "var(--panel2)")
            fill = p["accent"] if me else p["bar_peer"]
            body += (f'<tr class="{"me" if me else ""}"><td class="rk">{i}</td>'
                     f'<td class="nm">'
                     f'{escape(names.display_name(r["make"], r["model"]))}</td>'
                     f'<td class="num">{r["failure_rate"]:.1%}</td><td>{tag}</td>'
                     f'<td style="width:30%">'
                     f'{c.bar(r["failure_rate"] / worst, fill)}</td></tr>')
        html(c.table(body, '<th>#</th><th>Model</th><th>Failure rate</th>'
                           '<th>vs this model</th><th></th>'))
        if profile.rank:
            position, total = profile.rank
            html(c.why(
                f"Once mileage is levelled out, the {escape(model_name)} ranks "
                f"<b>{position:,} of {total:,}</b> models at this age — a lower "
                f"failure rate than {f.get('rank_better_than', 0)}% of them."))
        html(c.note("Peers are the highest-volume models in the same age band — "
                    "a proxy for comparable cars, not a like-for-like class "
                    "match. Add up to three cars in <b>Compare with</b> above "
                    "to put them side by side."))


# ---------------------------------------------------------------------------
# 5. what should I do?
# ---------------------------------------------------------------------------
def panel_insight() -> None:
    """The AI layer's interpretation of the findings above.

    Laid out by the page from structured fields, so the model supplies words
    and never layout, and never a figure the page shows. Every answer has been
    checked against the data it was given before it reaches this panel.
    """
    with shell("AI buying insight",
               pill="AI interpretation based on MOTIntel data"):
        insight = llm.cached_insight(profile)
        if insight:
            html(c.ai_insight(insight,
                              {"low": (p["good"], p["good_soft"]),
                               "moderate": (p["warn"], p["warn_soft"]),
                               "high": (p["bad"], p["bad_soft"])},
                              p["good"], p["warn"]))
        elif not llm.credentials_available():
            html('<div class="lead-line" style="margin:0">The AI insight needs '
                 'a Gemini API key in <code>.env</code>. Everything else on '
                 'this page is unaffected.</div>')
        else:
            html('<div class="lead-line" style="margin:0 0 14px">An '
                 'interpretation of the figures above: whether a buyer should '
                 'be concerned, the patterns that matter most, and what to '
                 'prioritise when viewing one. It takes around ten seconds and '
                 'uses one request from a limited free quota.</div>')
            if st.button("Get AI buying insight", type="primary",
                         key="ask-ai"):
                with st.spinner("Reading this model's figures ..."):
                    if llm.interpret(profile):
                        st.rerun()
                    else:
                        st.info("No insight could be produced just now. The "
                                "free tier allows a limited number of "
                                "requests, and an answer that fails its checks "
                                "is never shown. Everything else on this page "
                                "is unaffected.")
        html(c.disclosure(
            "How this insight was generated",
            "<p>The AI uses only the MOT statistics and findings calculated "
            "for this page. It has no access to external sources and does not "
            "independently verify the underlying data.</p><p>Every figure in "
            "its answer is checked against the data it was given, as is "
            "anything outside that data — a price, a recall, advice to buy or "
            "avoid. An answer that fails is not shown.</p>"))


def checklist_groups() -> list[tuple[str, str, list[str]]]:
    """This model's checklist: its own checkable failure reasons, grouped by
    part of the car and ordered by how much of its failures each part carries.

    Priority is that share and nothing else — the largest part is high
    priority, any other carrying a fifth or more is medium — so the label can
    be explained in one sentence. Mileage joins the list when the data shows
    a threshold, because it is the one thing every buyer can check.
    """
    if not labelled:
        return []
    groups = []
    order = [area for area, _ in area_shares]
    chosen = st.session_state.get("area")
    if chosen in order:
        order.remove(chosen)
        order.insert(0, chosen)
    share_of = dict(area_shares)
    for area in order:
        checks = list(dict.fromkeys(d["forecourt_check"] for d in top
                                    if d.get("repair_area") == area
                                    and d.get("forecourt_check")))
        if not checks:
            continue
        priority = ("High priority" if area == area_shares[0][0]
                    else "Medium priority" if share_of[area] >= 0.2 else "")
        groups.append((c.sentence_case(area), priority, checks))
    if step:
        groups.append(("Mileage", "High priority" if step["step"] >= 0.05
                       else "", [f"Check the recorded mileage: observed "
                                 f"failure rates rise sharply from the "
                                 f"{step['after']} miles band."]))
    return groups


def panel_checklist() -> None:
    with shell("What to check before buying", pill=car_name):
        groups = checklist_groups()
        if not groups:
            html(c.empty("Nothing here can be checked by eye",
                         "None of this model's commonest failures can be seen "
                         "or tried from outside a workshop — brake wear, "
                         "emissions and joint play all need the car on a "
                         "ramp.", "check"))
            return
        keys = [f"chk::{make}::{model}::{g}::{i}"
                for g, (_, _, checks) in enumerate(groups)
                for i in range(len(checks))]
        done = sum(1 for k in keys if st.session_state.get(k))
        for g, (area, priority, checks) in enumerate(groups):
            badge = (c.badge(priority, p["bad"] if priority.startswith("High")
                             else p["warn"],
                             p["bad_soft"] if priority.startswith("High")
                             else p["warn_soft"]) if priority else "")
            html(f'<div class="chk-group{" first" if g == 0 else ""}">'
                 f'{escape(area)}{badge}</div>')
            for i, check in enumerate(checks):
                st.checkbox(check, key=f"chk::{make}::{model}::{g}::{i}")
        html(f'<div class="progress">{done} of {len(keys)} checked</div>')
        text = (f"What to check before buying — {car_name}\n"
                f"Built by MOTIntel from DVSA MOT data for {ages} year "
                f"vehicles.\n\n"
                + "\n\n".join(
                    f"{area}{' (' + priority + ')' if priority else ''}\n"
                    + "\n".join(f"[ ] {check}" for check in checks)
                    for area, priority, checks in groups) + "\n")
        st.download_button("Save checklist", text,
                           file_name=f"{make}-{model}-checklist.txt"
                           .lower().replace(" ", "-"),
                           mime="text/plain", key="save-checklist")
        html(c.note(f"Built from this model's commonest failure reasons at "
                    f"{ages} years. Priority follows the share of those "
                    f"failures each part of the car carries. Most MOT failures "
                    f"cannot be seen without a ramp, so only checks that can "
                    f"be done by eye or by trying the car are listed."))


def panel_verdict() -> None:
    """The page's own conclusion, written from the findings in code — the one
    thing a reader who skips to the bottom takes away."""
    label = {"better": "Better-than-average MOT record",
             "about": "About-average MOT record",
             "worse": "Worse-than-average MOT record"}.get(
        f.get("classification"), "No benchmark to judge against")
    words = {"better": "a better-than-average", "about": "an about-average",
             "worse": "a worse-than-average"}.get(f.get("classification"))
    factors = []
    if f.get("dominant_area"):
        factors.append(f["dominant_area"][0])
    if step:
        factors.append("mileage")
    said = (f"The {escape(model_name)} has {words} MOT failure rate for "
            f"{ages} year vehicles" if words
            else f"The {escape(model_name)} cannot be judged against an "
                 f"average at this age")
    if factors:
        said += (f", with {escape(' and '.join(factors))} the biggest factors "
                 f"to watch.")
    else:
        said += "."
    if sparse:
        said += (f" Based on only {n_tests:,} tests, so treat it with "
                 f"caution.")
    with shell("MOTIntel verdict"):
        html(f'<div class="final">{c.status(label, tone, verdict_icon, big=True)}'
             f'<p>{said}</p></div>')


# ---------------------------------------------------------------------------
# detail
# ---------------------------------------------------------------------------
# How many rows the ranking shows at each end. A scrollable list of two
# thousand trim variants is not something anyone reads; the ends of the
# distribution and this model are the whole of what the question was.
RANK_HEAD, RANK_TAIL = 12, 5


def _rank_row(r: dict, position: int, mine: bool) -> str:
    lo = max(0.0, r["standardised"] - r["margin"])
    hi = r["standardised"] + r["margin"]
    return (f'<tr class="{"me" if mine else ""}">'
            f'<td class="rk">{position:,}</td>'
            f'<td><div class="nm">'
            f'{escape(names.display_name(r["make"], r["model"]))}</div>'
            f'<div class="sub">{lo:.1%}–{hi:.1%} at 95%</div></td>'
            f'<td class="num">{r["standardised"]:.1%}</td>'
            f'<td class="num" style="color:var(--muted);font-weight:400">'
            f'{r["observed"]:.1%}</td>'
            f'<td class="num" style="color:var(--muted);font-weight:400">'
            f'{r["n_tests"]:,}</td></tr>')


def panel_reliability_ranking() -> None:
    """Ranked like-for-like on mileage — see serving.reliability_ranking for
    why a plain sort of the observed column ranks garage queens.

    Built as HTML rather than st.dataframe on purpose. The dataframe widget
    paints to a canvas themed from config.toml, which is pinned to dark, so it
    stayed dark in light mode and no amount of CSS reached it.
    """
    html(f'<div class="panel-sub">{ages} years</div>')
    if not ranked_rows:
        html(c.empty("Nothing here can be ranked fairly",
                     "No model in this age group has cars across enough of "
                     "the mileage range to level out, and a ranking built "
                     "without that would sort by how far the cars had been "
                     "driven.", "check"))
        return
    rows, mine, total = ranked_rows, rank_mine, len(ranked_rows)
    if mine is not None:
        r = rows[mine]
        html(f'<div class="lead-line">The {escape(model_name)} ranks '
             f'<b>{mine + 1:,} of {total:,}</b> at this age, at '
             f'<b>{r["standardised"]:.1%}</b> once mileage is levelled out — '
             f'against <b>{r["observed"]:.1%}</b> as tested.</div>')
    else:
        html('<div class="lead-line">This model is not in the table. Its cars '
             'in this age group do not span enough of the mileage range for a '
             'like-for-like comparison, and guessing at the rest would be '
             'worse than leaving it out.</div>')
    head = list(range(min(RANK_HEAD, total)))
    tail = [i for i in range(max(total - RANK_TAIL, len(head)), total)]
    shown = sorted(set(head) | set(tail) | ({mine} if mine is not None
                                            else set()))
    body, previous = "", None
    for i in shown:
        if previous is not None and i > previous + 1:
            body += (f'<tr class="gap"><td colspan="5">'
                     f'{i - previous - 1:,} more</td></tr>')
        body += _rank_row(rows[i], i + 1, i == mine)
        previous = i
    html(c.table(body, '<th>#</th><th>Vehicle</th><th>Like-for-like</th>'
                       '<th>As tested</th><th>Tests</th>')
         + c.note("Ranked on the like-for-like figure: each model reweighted "
                  "onto the mileage spread of every car its age, because a "
                  "plain sort of the as-tested column puts a barely-driven "
                  "supercar on top. Models without cars across enough of that "
                  "range are left out rather than guessed at. None of this "
                  "separates how a car was built from how it was looked "
                  "after."))


def exported(name: str) -> str:
    path = PROCESSED / name
    return (dt.date.fromtimestamp(path.stat().st_mtime).strftime("%-d %B %Y")
            if path.exists() else "not produced")


def panel_method() -> None:
    """Where every figure on the page comes from, and what the AI can and
    cannot see."""
    tested_ages = (f"{bands[0]}–{bands[-1] + 3} years ({len(bands)} age "
                   f"bands)" if bands else "—")
    items = [
        ("Data source", "DVSA anonymised MOT testing data for tests recorded "
                        "in 2025, published under the Open Government Licence "
                        "v3.0."),
        ("Tests analysed", "42,728,066 car tests in the extract; 35,368,333 "
                           "after cleaning"
                           + (f"; {all_tests:,} for this model across all "
                              f"ages." if all_tests else ".")),
        ("Coverage", f"First registered {yr_from}–{yr_to}, the 2nd to 98th "
                     f"percentile of this model's first-use dates."
         if yr_from and yr_to else "Not recorded."),
        ("Age range", f"Tested at {tested_ages}. Ages are grouped in "
                      f"three-year bands."),
        ("Failure rate", "The share of MOT tests in a group that ended in a "
                         "fail. Observed, not predicted: it describes what "
                         "happened to these cars, not what will happen to one "
                         "car."),
        ("Sample size", "Large, moderate or small by the 95% margin of error "
                        "on the failure rate: within 1 percentage point, "
                        "within 3, or wider. Under "
                        f"{MIN_TESTS_FOR_CONFIDENCE} tests is always small."),
        ("Severity", "DVSA grades each defect Dangerous, Major or Minor under "
                     "the 2018 roadworthiness rules. A single MOT test can "
                     "carry several defects, so category and severity shares "
                     "overlap and may not add to 100%."),
        ("Like-for-like rank", "Each model's failure rates by mileage band, "
                               "reweighted onto the mileage spread of every "
                               "car its age, so a lightly driven model is not "
                               "ranked on its mileage."),
        ("AI interpretation", f"Gemini ({escape(llm.MODEL)}), given only the "
                              f"figures and findings on this page, with no "
                              f"web access or other sources. It interprets; it "
                              f"does not calculate anything shown. Answers "
                              f"with a figure not in its data, or a claim "
                              f"outside it, are discarded."),
        ("Data updated", f"Serving data exported {exported('age_curve.parquet')}"
                         f"; plain-English defect labels "
                         f"{exported('defect_meta.parquet')}."),
    ]
    html('<dl class="method">' + "".join(
        f'<div><dt>{k}</dt><dd>{v}</dd></div>' for k, v in items) + '</dl>')


def footer() -> None:
    html(f'<div class="foot">{c.icon("doc", 13)} Source: DVSA anonymised MOT '
         f'testing data, 2025 — 42,728,066 tests, 35,368,333 after cleaning. '
         f'Data updated {exported("age_curve.parquet")}. Contains public '
         f'sector information licensed under the Open Government Licence '
         f'v3.0. &nbsp;·&nbsp; An MOT failure rate is not a reliability '
         f'rating: the test covers safety and emissions items at one annual '
         f'point, cannot see repairs made between tests, and reflects owner '
         f'maintenance as much as build quality.</div>')


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------
# Organised around the questions a buyer asks, in the order they ask them, so
# the page answers "is it reliable, what goes wrong, does mileage matter, how
# does it compare, what should I check" in that order and in about fifteen
# seconds. It used to be data, charts, a warning, a summary, and eight closed
# sections of more data — the same panels, with no question to hang them on.
panel_hero()
panel_summarybar()

section("overview", "Is it reliable?",
        f"The observed MOT failure rate for {ages} year vehicles, against all "
        f"cars of the same age.")
panel_reliability()
# Never behind a click. A rate built on too few tests has to carry its warning
# wherever it is read.
if sparse:
    html(c.callout(
        f"Only {n_tests:,} tests in this group, below the "
        f"{MIN_TESTS_FOR_CONFIDENCE}-test threshold for a reliable rate.",
        p["bad"], p["bad_soft"], title="Treat these figures with caution"))

section("failures", "What usually goes wrong?",
        "The commonest reasons this model failed its MOT at this age — how "
        "often, how serious, and where on the car.")
panel_failures()

section("age-mileage", "Does age or mileage matter?",
        "How the observed failure rate changes as the model gets older and "
        "covers more miles.")
panel_age_mileage()

section("compare", "How does it compare?",
        "Against other models of the same age. Add cars to compare in the "
        "control area above.")
panel_compare()

section("checks", "What should I do?",
        "An interpretation of everything above, and a checklist built from "
        "this model's own failures.")
left, right = st.columns([1, 1])
with left:
    panel_insight()
with right:
    panel_checklist()
st.write("")
panel_verdict()

section("method", "Detail and methodology")
with st.expander("Every model ranked at this age", key="sec-ranking"):
    panel_reliability_ranking()
with st.expander("Data & methodology", key="sec-method"):
    panel_method()

footer()
