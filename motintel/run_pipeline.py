"""FR1.7 — the whole offline pipeline, end to end, in one command.

    python -m motintel.run_pipeline

Re-runnable: unpacking skips files already on disk, and every table is dropped
and rebuilt, so a second run produces the same database as the first.
"""
from __future__ import annotations

import argparse
import time

import duckdb

from motintel import ingest
from motintel import transform
from motintel.config import DB_PATH, YEAR


def main(year: int, skip_unpack: bool) -> None:
    started = time.time()

    if not skip_unpack:
        print("[1/3] unpacking archives")
        ingest.unpack(year)
    print("[2/3] loading raw tables into duckdb")
    ingest.load(year)

    print("\n[3/3] transforming")
    con = duckdb.connect(str(DB_PATH))
    transform.build(con)
    transform.quality_report(con)
    con.close()

    print(f"\ndone in {time.time() - started:.0f}s -> {DB_PATH}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, default=YEAR)
    ap.add_argument("--skip-unpack", action="store_true",
                    help="archives are already unpacked into data/raw/csv")
    main(**vars(ap.parse_args()))
