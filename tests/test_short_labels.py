"""The rules a short failure-reason label has to satisfy.

Runs offline. Every case builds a batch that passes, breaks one thing, and
checks the break is reported — a check never seen to fail is not evidence. If
the labels have been produced, the shipped table is held to the same rules.

Run from the repository root:  python -m tests.test_short_labels
"""
from __future__ import annotations

import sys

import polars as pl

from motintel import short_labels as sl

ITEM = {"id": 0, "defect_category": "Tyres",
        "defect_desc": "has a cut in excess of the requirements deep enough to "
                       "reach the ply or cords",
        "plain_english": "A tyre has a deep cut that exposes its inner "
                         "structural cords."}
TREAD = {"id": 1, "defect_category": "Tyres",
         "defect_desc": "tread depth below requirements of 1.6mm",
         "plain_english": "A tyre's tread depth is worn below the legal "
                          "minimum limit."}
GOOD = [{"id": 0, "headline": "Deep tyre cut",
         "detail": "Structural cords exposed"},
        {"id": 1, "headline": "Tread too shallow",
         "detail": "Below the 1.6mm limit"}]


def run() -> int:
    fails = []
    batch = [ITEM, TREAD]

    def expect(rows, clean: bool, why: str):
        faults = sl.validate(batch, rows)
        if bool(faults) == clean:
            fails.append(f"{why}: got {faults or 'no faults'}")

    expect(GOOD, True, "a good batch")
    expect(GOOD[:1], False, "a missing id")
    expect([GOOD[0], {**GOOD[1], "headline": "A tread depth that is far too "
                                             "shallow"}],
           False, "a headline over the word limit")
    expect([{**GOOD[0], "detail": "Cut deeper than 3mm"}, GOOD[1]], False,
           "a number the source does not contain")
    expect([{**GOOD[0], "headline": "£80 tyre cut"}, GOOD[1]], False,
           "a price")

    if sl.OUTPUT.exists():
        shipped = pl.read_parquet(sl.OUTPUT)
        long = shipped.filter(
            (pl.col("headline").str.split(" ").list.len()
             > sl.MAX_HEADLINE_WORDS)
            | (pl.col("detail").str.split(" ").list.len()
               > sl.MAX_DETAIL_WORDS))
        if not long.is_empty():
            fails.append(f"{long.height} shipped labels are over the limit")
        print(f"  shipped table: {shipped.height} labels checked")

    for f in fails:
        print(f"  FAIL: {f}")
    print(f"failures: {len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(run())
