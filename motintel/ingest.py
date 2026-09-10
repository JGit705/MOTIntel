"""Phase 1 — load the raw DVSA extracts into DuckDB. No transforms here.

Raw tables are a faithful copy of what DVSA shipped. Every cleaning decision
lives in transform.py so that the two can be reasoned about separately, and so
a bad cleaning rule can be re-run without re-reading 8.5GB of CSV.
"""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

import duckdb

from motintel.config import (CSV_DIR, DB_PATH, EXTRACT_DELIM, EXTRACT_ESCAPE,
                    EXTRACT_QUOTE, LOOKUP_DELIM, LOOKUP_DIR, RAW, YEAR)

RESULT_ZIP = "dft_test_result_extracts_{year}.zip"
ITEM_ZIP = "dft_test_item_extracts_{year}.zip"

LOOKUPS = {
    "lu_item_detail": "item_detail.csv",
    "lu_item_group": "item_group.csv",
    "lu_fuel_type": "mdr_fuel_types.csv",
    "lu_rfr_location": "mdr_rfr_location.csv",
    "lu_test_outcome": "mdr_test_outcome.csv",
    "lu_test_type": "mdr_test_type.csv",
}


def unpack(year: int = YEAR) -> None:
    """Extract the monthly CSVs. Entries are stored, not deflated, so this is
    a copy rather than a decompress — fast, but it does need the disk."""
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    for template in (RESULT_ZIP, ITEM_ZIP):
        archive = RAW / template.format(year=year)
        if not archive.exists():
            raise FileNotFoundError(f"missing archive: {archive}")
        with zipfile.ZipFile(archive) as z:
            members = [m for m in z.namelist() if m.endswith(".csv")]
            for m in members:
                target = CSV_DIR / Path(m).name
                if target.exists() and target.stat().st_size > 0:
                    continue  # already unpacked; re-runnable (FR1.7)
                with z.open(m) as src, open(target, "wb") as dst:
                    while chunk := src.read(1 << 22):
                        dst.write(chunk)
            print(f"  unpacked {len(members)} files from {archive.name}")


def load(year: int = YEAR) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))

    # DuckDB reads the monthly CSVs straight off disk via glob and only
    # materialises what the query needs. filename=true keeps the source month
    # on every row, which is what makes month-at-a-time reloading possible.
    results_glob = str(CSV_DIR / f"dft_test_result_extract_{year}??.csv")
    items_glob = str(CSV_DIR / f"dft_test_item_extract_{year}??.csv")

    print("  loading raw_tests ...")
    con.execute("DROP TABLE IF EXISTS raw_tests")
    con.execute(f"""
        CREATE TABLE raw_tests AS
        SELECT * FROM read_csv(
            '{results_glob}',
            delim = '{EXTRACT_DELIM}',
            quote = '{EXTRACT_QUOTE}',
            escape = '{EXTRACT_ESCAPE}',
            header = true,
            filename = true,
            types = {{
                'test_id': 'BIGINT', 'vehicle_id': 'BIGINT',
                'test_date': 'DATE', 'test_class_id': 'VARCHAR',
                'test_type': 'VARCHAR', 'test_result': 'VARCHAR',
                'test_mileage': 'BIGINT', 'postcode_area': 'VARCHAR',
                'make': 'VARCHAR', 'model': 'VARCHAR', 'colour': 'VARCHAR',
                'fuel_type': 'VARCHAR', 'cylinder_capacity': 'INTEGER',
                'first_use_date': 'DATE'
            }}
        )
    """)

    print("  loading raw_defects ...")
    con.execute("DROP TABLE IF EXISTS raw_defects")
    con.execute(f"""
        CREATE TABLE raw_defects AS
        SELECT * FROM read_csv(
            '{items_glob}',
            delim = '{EXTRACT_DELIM}',
            quote = '{EXTRACT_QUOTE}',
            escape = '{EXTRACT_ESCAPE}',
            header = true,
            filename = true,
            types = {{
                'test_id': 'BIGINT', 'rfr_id': 'INTEGER',
                'rfr_type_code': 'VARCHAR',
                'mot_test_rfr_location_type_id': 'INTEGER',
                'dangerous_mark': 'VARCHAR'
            }}
        )
    """)

    for table, filename in LOOKUPS.items():
        path = LOOKUP_DIR / filename
        con.execute(f"DROP TABLE IF EXISTS {table}")
        con.execute(f"""
            CREATE TABLE {table} AS
            SELECT * FROM read_csv('{path}',
                                   delim = '{LOOKUP_DELIM}', header = true)
        """)

    print("\n  row counts")
    for table in ["raw_tests", "raw_defects", *LOOKUPS]:
        n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        print(f"    {table:<20} {n:>14,}")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, default=YEAR)
    ap.add_argument("--skip-unpack", action="store_true")
    args = ap.parse_args()

    if not args.skip_unpack:
        print("unpacking archives")
        unpack(args.year)
    print("loading into duckdb")
    load(args.year)
