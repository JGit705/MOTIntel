"""Phase 6 — export the serving layer.

The database cannot ship. Streamlit Community Cloud gives roughly 1GB of RAM
and the DuckDB file is tens of gigabytes, so the app is served pre-aggregated
Parquet instead: the heavy work happens once, offline, here.

This offline/serving split is the point of the exercise as much as the numbers
are. Aggregations are cut to the grain the app actually queries — no finer.
"""
from __future__ import annotations

import duckdb

from motintel.config import CAR_TEST_CLASS, DB_PATH, PROCESSED

# Any cell thinner than this is dropped rather than shipped. It would be too
# noisy to display, and dropping it keeps the artefact small.
#
# The app's selection list is derived from these exports rather than from a
# separate model list: a vehicle is offered only if the panels can actually be
# drawn for it. A standalone list with a different threshold put 134 models in
# the dropdown that dead-ended on a warning.
MIN_CELL = 30


def export_top_defects(con) -> None:
    """The ten commonest failure reasons per make / model / age band.

    Carries how many of each reason's tests had it graded Dangerous. DVSA
    grades each defect line, not each description — the same wording can be
    Major on one car and Dangerous on another — so a reason's severity is a
    share, not a label. Without it the table could say how often a tyre was
    cut but not how often the cut made the car unsafe to drive, and its only
    other column, the size of the repair, reads as a severity it is not.
    """
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
            counted AS (
                SELECT s.make, s.model, s.age_band,
                       -- 0.34% of defect codes are absent from the lookup
                       -- tables. The analytical table keeps them NULL, which
                       -- is the honest record; labelling happens here, at the
                       -- presentation boundary, so the app never renders
                       -- "None is the most common problem".
                       coalesce(d.defect_category, 'Unclassified')
                           AS defect_category,
                       coalesce(d.defect_desc,
                                'defect code not present in the DVSA lookup '
                                || 'tables') AS defect_desc,
                       count(DISTINCT s.test_id) AS n_tests,
                       count(DISTINCT CASE WHEN d.deficiency_category
                                               = 'Dangerous'
                                           THEN s.test_id END) AS n_dangerous
                FROM scoped s
                JOIN analytical_defects d USING (test_id)
                WHERE d.rfr_type_code IN ('F', 'P')
                GROUP BY 1, 2, 3, 4, 5
            ),
            ranked AS (
                -- Ties broken by name. Ordered on the count alone, which of
                -- two reasons tied for tenth place made the cut was up to the
                -- engine, and two runs of this query swapped 10,120 rows —
                -- each one a defect the enrichment might not have labelled.
                SELECT *, row_number() OVER (
                           PARTITION BY make, model, age_band
                           ORDER BY n_tests DESC, defect_category,
                                    defect_desc) AS rk
                FROM counted
            )
            SELECT r.make, r.model, r.age_band, r.defect_category, r.defect_desc,
                   r.n_tests, r.n_dangerous, t.group_tests,
                   r.n_tests * 1.0 / t.group_tests AS share_of_tests
            FROM ranked r JOIN totals t USING (make, model, age_band)
            WHERE r.rk <= 10
        ) TO '{PROCESSED / "top_defects.parquet"}' (FORMAT parquet, COMPRESSION zstd)
    """)


def export(only: str | None = None) -> None:
    """Every serving table — or, with `only="top_defects"`, just that one, so
    adding a column to it does not mean rescanning for the other five."""
    PROCESSED.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    if only == "top_defects":
        export_top_defects(con)
        con.close()
        return

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
              -- A test with no odometer reading has no place on a mileage
              -- axis: floor(NULL / 20000) is NULL, which formed a phantom
              -- band and crashed the label built from it. Those tests are
              -- still counted everywhere that does not split by mileage,
              -- which is what age_curve exists for.
              AND odometer_miles IS NOT NULL
            GROUP BY 1, 2, 3, 4
            HAVING count(*) >= {MIN_CELL}
        ) TO '{PROCESSED / "failure_rates.parquet"}' (FORMAT parquet, COMPRESSION zstd)
    """)

    export_top_defects(con)

    # Age curve per model. Built without the mileage split so it keeps rows
    # where no odometer reading was taken, which the failure_rates grain drops.
    print("exporting failure rate by age band, per model")
    con.execute(f"""
        COPY (
            SELECT make, model,
                   CAST(floor(vehicle_age_years / 3) * 3 AS INT) AS age_band,
                   count(*) AS n_tests,
                   avg(CASE WHEN failed THEN 1.0 ELSE 0.0 END) AS failure_rate
            FROM analytical_tests
            WHERE test_class_id = '{CAR_TEST_CLASS}'
              AND make IS NOT NULL AND model IS NOT NULL
              AND vehicle_age_years BETWEEN 0 AND 30
            GROUP BY 1, 2, 3 HAVING count(*) >= {MIN_CELL}
        ) TO '{PROCESSED / "age_curve.parquet"}' (FORMAT parquet, COMPRESSION zstd)
    """)

    # The all-cars benchmark the headline number is judged against, and the
    # curve every model's age line is drawn over.
    print("exporting the all-cars benchmark by age band")
    con.execute(f"""
        COPY (
            SELECT CAST(floor(vehicle_age_years / 3) * 3 AS INT) AS age_band,
                   count(*) AS n_tests,
                   avg(CASE WHEN failed THEN 1.0 ELSE 0.0 END) AS failure_rate
            FROM analytical_tests
            WHERE test_class_id = '{CAR_TEST_CLASS}'
              AND vehicle_age_years BETWEEN 0 AND 30
            GROUP BY 1
        ) TO '{PROCESSED / "benchmark.parquet"}' (FORMAT parquet, COMPRESSION zstd)
    """)

    # Severity. Only Dangerous and Major appear here, and that is correct
    # rather than a gap: a Minor defect does not cause a failure, so it cannot
    # appear among failure items. Counted as distinct tests, because one test
    # carrying three dangerous items is still one dangerous car.
    print("exporting defect severity split")
    con.execute(f"""
        COPY (
            WITH scoped AS (
                SELECT test_id, make, model,
                       CAST(floor(vehicle_age_years / 3) * 3 AS INT) AS age_band
                FROM analytical_tests
                WHERE test_class_id = '{CAR_TEST_CLASS}'
                  AND make IS NOT NULL AND model IS NOT NULL
            )
            SELECT s.make, s.model, s.age_band,
                   d.deficiency_category,
                   count(DISTINCT s.test_id) AS n_tests
            FROM scoped s
            JOIN analytical_defects d USING (test_id)
            WHERE d.rfr_type_code IN ('F', 'P')
              AND d.deficiency_category IN ('Dangerous', 'Major')
            GROUP BY 1, 2, 3, 4
            HAVING count(DISTINCT s.test_id) >= 5
        ) TO '{PROCESSED / "severity.parquet"}' (FORMAT parquet, COMPRESSION zstd)
    """)

    # Vehicle identity: what the header states about the car itself, rather
    # than about its failures. Year range is clipped to the 2nd/98th
    # percentile because a handful of re-registered or mis-keyed first-use
    # dates would otherwise stretch every model back to the 1970s.
    print("exporting vehicle metadata")
    con.execute(f"""
        COPY (
            WITH fuels AS (
                SELECT t.make, t.model, f.fuel_type AS fuel,
                       count(*) * 1.0 / sum(count(*))
                           OVER (PARTITION BY t.make, t.model) AS share
                FROM analytical_tests t
                LEFT JOIN lu_fuel_type f ON f.type_code = t.fuel_type
                WHERE t.test_class_id = '{CAR_TEST_CLASS}'
                  AND t.make IS NOT NULL AND t.model IS NOT NULL
                GROUP BY 1, 2, 3
            ),
            main_fuels AS (
                SELECT make, model,
                       string_agg(fuel, ' / ' ORDER BY share DESC) AS fuels
                FROM fuels WHERE share >= 0.12 AND fuel IS NOT NULL
                GROUP BY 1, 2
            )
            SELECT t.make, t.model,
                   CAST(quantile_cont(year(t.first_use_date), 0.02) AS INT) AS year_from,
                   CAST(quantile_cont(year(t.first_use_date), 0.98) AS INT) AS year_to,
                   avg(t.vehicle_age_years) AS avg_age,
                   count(*) AS n_tests,
                   any_value(m.fuels) AS fuels
            FROM analytical_tests t
            LEFT JOIN main_fuels m USING (make, model)
            WHERE t.test_class_id = '{CAR_TEST_CLASS}'
              AND t.make IS NOT NULL AND t.model IS NOT NULL
            GROUP BY 1, 2
            HAVING count(*) >= {MIN_CELL}
        ) TO '{PROCESSED / "vehicle_meta.parquet"}' (FORMAT parquet, COMPRESSION zstd)
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
    import sys
    export(sys.argv[1] if len(sys.argv) > 1 else None)
