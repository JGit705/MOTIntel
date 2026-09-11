"""Phase 2 of AI_PLAN — the page the labels are read on before they ship.

The machine checks in defect_labels.py catch what a schema and a regex can see:
a missing row, a label outside its closed set, a figure that was not in the
description, a disagreement with an anchor. They cannot tell you that "a light
is not working" is a fair reading of `[Lamps…] not working` while something
else is not. That needs a person, and this is what the person reads.

Rows are ordered commonest-first and carry a running share of every recorded
failure, so it is visible where reading stops paying: the top eighty or so
account for the overwhelming majority of what anyone will actually meet, and
the tail is one-off codes.

Run with:  python -m motintel.review
"""
from __future__ import annotations

import sys
import webbrowser
from html import escape

import polars as pl

from motintel.config import PROCESSED
from motintel.defect_labels import (COLUMNS, EFFORTS, REPAIR_AREAS,
                                    load_overrides, override_key, validate)

SOURCE = PROCESSED / "defect_meta.parquet"
OUTPUT = PROCESSED / "defect_review.html"

EFFORT_COLOUR = {"minor": "#15803d", "moderate": "#b45309",
                 "major": "#b91c1c"}

CSS = """
  :root { color-scheme: light; }
  body { margin:0; background:#f2f5fa; color:#0e1a2c;
         font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  main { max-width:1180px; margin:0 auto; padding:28px 20px 80px; }
  h1 { font-size:21px; margin:0 0 4px; }
  .sub { color:#5a6b86; font-size:13px; margin:0 0 22px; }
  .cards { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:22px; }
  .card { background:#fff; border:1px solid #e0e7f1; border-radius:10px;
          padding:10px 14px; }
  .card b { display:block; font-size:19px; }
  .card span { font-size:11.5px; color:#5a6b86; }
  .warn { background:#fff5f5; border-color:#f3c7c7; }
  .warn ul { margin:6px 0 0; padding-left:18px; font-size:12.5px; }
  table { width:100%; border-collapse:collapse; background:#fff;
          border:1px solid #e0e7f1; border-radius:10px; overflow:hidden; }
  th { text-align:left; font-size:11.5px; text-transform:uppercase;
       letter-spacing:.03em; color:#5a6b86; background:#f5f8fd;
       padding:9px 10px; position:sticky; top:0; }
  td { padding:10px; border-top:1px solid #eaeff7; vertical-align:top;
       font-size:13px; }
  tr:hover td { background:#f8fafd; }
  .rk { color:#8496ae; font-size:12px; white-space:nowrap; }
  .src { color:#5a6b86; font-size:12px; }
  .src b { color:#0e1a2c; font-weight:600; display:block; font-size:12.5px; }
  .pe { font-weight:500; }
  .chk { color:#5a6b86; font-size:12.5px; font-style:italic; }
  .none { color:#a7b3c6; font-style:italic; }
  .pill { display:inline-block; font-size:11px; font-weight:650;
          border-radius:6px; padding:2px 7px; white-space:nowrap; }
  .area { background:#eef3fb; color:#2d4a7a; }
  code { font-size:11px; background:#f1f5fa; padding:1px 4px;
         border-radius:4px; color:#5a6b86; word-break:break-all; }
  .cov { font-variant-numeric:tabular-nums; }
"""


def _rows_html(table: pl.DataFrame, overrides: dict) -> str:
    total = int(table["n_tests"].sum()) or 1
    running, out = 0, []
    for i, row in enumerate(table.iter_rows(named=True), 1):
        running += row["n_tests"]
        key = override_key(row["defect_category"], row["defect_desc"])
        check = row["forecourt_check"]
        edited = " · edited" if key in overrides else ""
        out.append(
            f'<tr>'
            f'<td class="rk">{i}<div class="cov">{running / total:.0%}</div>'
            f'</td>'
            f'<td class="src"><b>{escape(row["defect_category"])}</b>'
            f'{escape(row["defect_desc"])}'
            f'<div style="margin-top:5px"><code>{escape(key)}</code></div>'
            f'</td>'
            f'<td><div class="pe">{escape(row["plain_english"])}{edited}</div>'
            f'<div class="chk" style="margin-top:5px">'
            f'{escape(check) if check else "<span class=none>nothing "
                                          "visible on the forecourt</span>"}'
            f'</div></td>'
            f'<td><span class="pill area">{escape(row["repair_area"])}</span>'
            f'<div style="margin-top:5px"><span class="pill" '
            f'style="color:{EFFORT_COLOUR[row["effort"]]};'
            f'background:{EFFORT_COLOUR[row["effort"]]}1a">'
            f'{escape(row["effort"])}</span></div></td>'
            f'<td class="rk cov">{row["n_tests"]:,}</td>'
            f'</tr>')
    return "".join(out)


def _counts_html(table: pl.DataFrame) -> str:
    cards = [f'<div class="card"><b>{len(table):,}</b>'
             f'<span>defect descriptions</span></div>']
    nulls = int(table["forecourt_check"].is_null().sum())
    cards.append(f'<div class="card"><b>{nulls / len(table):.0%}</b>'
                 f'<span>no forecourt check &mdash; nothing visible</span>'
                 f'</div>')
    for effort in EFFORTS:
        n = int((table["effort"] == effort).sum())
        cards.append(f'<div class="card"><b>{n}</b><span>{effort}</span></div>')
    used = table["repair_area"].n_unique()
    cards.append(f'<div class="card"><b>{used}/{len(REPAIR_AREAS)}</b>'
                 f'<span>repair areas used</span></div>')
    return "".join(cards)


def build(table: pl.DataFrame, problems: list[str]) -> str:
    overrides = load_overrides()
    warning = ""
    if problems:
        items = "".join(f"<li>{escape(p)}</li>" for p in problems[:25])
        more = (f"<li>…and {len(problems) - 25} more</li>"
                if len(problems) > 25 else "")
        warning = (f'<div class="card warn" style="width:100%">'
                   f'<b>{len(problems)} check(s) failing</b>'
                   f'<span>these block the write</span>'
                   f'<ul>{items}{more}</ul></div>')
    return (f"<!doctype html><meta charset=utf-8>"
            f"<title>MOTIntel — defect labels for review</title>"
            f"<style>{CSS}</style><main>"
            f"<h1>Defect labels, for reading before they ship</h1>"
            f"<p class=sub>Commonest first. The percentage under each rank is "
            f"the running share of every recorded failure, so it shows where "
            f"reading stops paying. Disagree with a row by adding its "
            f"<code>key</code> to motintel/defect_overrides.json.</p>"
            f'<div class="cards">{_counts_html(table)}{warning}</div>'
            f"<table><thead><tr><th>#</th><th>DVSA wording</th>"
            f"<th>Plain English, and what to check</th><th>Labels</th>"
            f"<th>Tests</th></tr></thead>"
            f"<tbody>{_rows_html(table, overrides)}</tbody></table>"
            f"</main>")


def main() -> int:
    if not SOURCE.exists():
        print(f"{SOURCE.name} not found — run 'python -m motintel.enrich' "
              f"first.")
        return 1
    table = pl.read_parquet(SOURCE).select(*COLUMNS).sort(
        "n_tests", descending=True)
    problems = validate(table)
    OUTPUT.write_text(build(table, problems))
    print(f"{len(table):,} labels -> {OUTPUT}")
    if problems:
        print(f"{len(problems)} check(s) failing — listed at the top of the "
              f"page")
    webbrowser.open(OUTPUT.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
