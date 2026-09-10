"""Phase 6 — export the serving layer.

The database cannot ship. Streamlit Community Cloud gives roughly 1GB of RAM
and the DuckDB file is tens of gigabytes, so the app is served pre-aggregated
Parquet instead: the heavy work happens once, offline, here.

This offline/serving split is the point of the exercise as much as the numbers
are. Aggregations are cut to the grain the app actually queries — no finer.
"""
from __future__ import annotations

import duckdb

from config import CAR_TEST_CLASS, DB_PATH, PROCESSED
from queries import MIN_TESTS_FOR_CONFIDENCE

# Any cell thinner than this is dropped rather than shipped. It would be too
# noisy to display, and dropping it keeps the artefact small.
MIN_CELL = 30


def export() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH), read_only=True)

    print("exporting failure rates by make / model / age band / mileage band")
    con.execute(f"""
        COPY (
            SELECT make, model,
                   CAST(floor(vehicle_age_years / 3) * 3 AS INT) AS age_band,
                   CAST(floor(odometer_miles / 20000) * 20000 AS INT) AS mileage_band,
                   count(*) AS n_tests,
                   avg(CASE WHEN failed THEN 1.0 ELSE 0.0 END) AS failure_rate
            FROM analytical_tests
            WHERE test_class_id = '{CAR_TEST_CLASS}'
              AND make IS NOT NULL AND model IS NOT NULL
              AND vehicle_age_years BETWEEN 0 AND 40
            GROUP BY 1, 2, 3, 4
            HAVING count(*) >= {MIN_CELL}
        ) TO '{PROCESSED / "failure_rates.parquet"}' (FORMAT parquet, COMPRESSION zstd)
    """)

    print("exporting top defect categories per make / model / age band")
    con.execute(f"""
        COPY (
            WITH scoped AS (
                SELECT test_id, make, model,
                       CAST(floor(vehicle_age_years / 3) * 3 AS INT) AS age_band
                FROM analytical_tests
                WHERE test_class_id = '{CAR_TEST_CLASS}'
                  AND make IS NOT NULL AND model IS NOT NULL
            ),
            totals AS (
                SELECT make, model, age_band, count(*) AS group_tests
                FROM scoped GROUP BY 1, 2, 3
                HAVING count(*) >= {MIN_CELL}
            ),
            ranked AS (
                SELECT s.make, s.model, s.age_band,
                       d.defect_category, d.defect_desc,
                       count(DISTINCT s.test_id) AS n_tests,
                       row_number() OVER (
                           PARTITION BY s.make, s.model, s.age_band
                           ORDER BY count(DISTINCT s.test_id) DESC) AS rk
                FROM scoped s
                JOIN analytical_defects d USING (test_id)
                WHERE d.rfr_type_code IN ('F', 'P')
                GROUP BY 1, 2, 3, 4, 5
            )
            SELECT r.make, r.model, r.age_band, r.defect_category, r.defect_desc,
                   r.n_tests, t.group_tests,
                   r.n_tests * 1.0 / t.group_tests AS share_of_tests
            FROM ranked r JOIN totals t USING (make, model, age_band)
            WHERE r.rk <= 10
        ) TO '{PROCESSED / "top_defects.parquet"}' (FORMAT parquet, COMPRESSION zstd)
    """)

    print("exporting model volumes for the selection controls")
    con.execute(f"""
        COPY (
            SELECT make, model, count(*) AS n_tests,
                   avg(CASE WHEN failed THEN 1.0 ELSE 0.0 END) AS failure_rate
            FROM analytical_tests
            WHERE test_class_id = '{CAR_TEST_CLASS}'
              AND make IS NOT NULL AND model IS NOT NULL
            GROUP BY 1, 2 HAVING count(*) >= {MIN_TESTS_FOR_CONFIDENCE}
        ) TO '{PROCESSED / "models.parquet"}' (FORMAT parquet, COMPRESSION zstd)
    """)

    con.close()
    total = 0
    for f in sorted(PROCESSED.glob("*.parquet")):
        mb = f.stat().st_size / 1048576
        total += mb
        print(f"  {f.name:<28} {mb:8.1f} MB")
    print(f"  {'TOTAL':<28} {total:8.1f} MB   (target: under 100MB)")
    if total > 100:
        print("  WARNING: over the serving-layer budget — coarsen the grain.")


if __name__ == "__main__":
    export()
