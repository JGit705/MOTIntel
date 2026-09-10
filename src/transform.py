"""Phase 2 — build the analytical tables and run the data quality report.

Every filter applied here is counted and reported rather than applied silently,
because the drop rates are themselves a finding worth publishing.
"""
from __future__ import annotations

import duckdb

from config import (CAR_TEST_CLASS, DB_PATH, DECIDED_RESULTS, FAIL_RESULTS,
                    INITIAL_TEST_TYPES, MAX_PLAUSIBLE_MILES, UNKNOWN_FIRST_USE)


def _sql_list(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def build(con: duckdb.DuckDBPyConnection) -> None:
    print("building analytical_tests ...")
    con.execute("DROP TABLE IF EXISTS analytical_tests")
    con.execute(f"""
        CREATE TABLE analytical_tests AS
        SELECT
            test_id,
            vehicle_id,
            test_date,
            test_class_id,
            test_type,
            test_result,
            test_result IN ({_sql_list(FAIL_RESULTS)})        AS failed,
            -- zero is documented as "no reading taken", not a real reading
            CASE WHEN test_mileage BETWEEN 1 AND {MAX_PLAUSIBLE_MILES}
                 THEN test_mileage END                        AS odometer_miles,
            postcode_area,
            nullif(upper(trim(make)), 'UNCLASSIFIED')          AS make,
            nullif(upper(trim(model)), 'UNCLASSIFIED')         AS model,
            fuel_type,
            cylinder_capacity,
            first_use_date,
            datediff('day', first_use_date, test_date) / 365.25 AS vehicle_age_years
        FROM raw_tests
        WHERE test_type   IN ({_sql_list(INITIAL_TEST_TYPES)})
          AND test_result IN ({_sql_list(DECIDED_RESULTS)})
          AND first_use_date IS NOT NULL
          AND first_use_date <> DATE '{UNKNOWN_FIRST_USE}'
          AND first_use_date <= test_date
    """)

    # Defect rows resolved to readable text. The join is composite: an rfr_id
    # means different things in different test classes, so test_class_id has to
    # come along from the test row. The top-level category (Brakes, Lamps, ...)
    # is reached through the item hierarchy's section id, which the guide
    # documents as pointing at the top-level group for that class.
    print("building analytical_defects ...")
    con.execute("DROP TABLE IF EXISTS analytical_defects")
    con.execute("""
        CREATE TABLE analytical_defects AS
        SELECT
            d.test_id,
            t.test_class_id,
            d.rfr_id,
            d.rfr_type_code,
            det.rfr_desc                    AS defect_desc,
            det.rfr_insp_manual_desc        AS defect_manual_desc,
            det.rfr_deficiency_category     AS deficiency_category,
            grp.item_name                   AS defect_category
        FROM raw_defects d
        JOIN analytical_tests t USING (test_id)
        LEFT JOIN lu_item_detail det
               ON det.rfr_id = d.rfr_id
              AND CAST(det.test_class_id AS VARCHAR) = t.test_class_id
        LEFT JOIN lu_item_group grp
               ON grp.test_item_id = det.test_item_set_section_id
              AND CAST(grp.test_class_id AS VARCHAR) = t.test_class_id
    """)


def quality_report(con: duckdb.DuckDBPyConnection) -> None:
    """FR1.6. The pass rate at the end is the checkpoint that catches a broken
    join: for class 4 it should land somewhere near two thirds."""
    print("\n" + "=" * 62)
    print("DATA QUALITY REPORT")
    print("=" * 62)

    raw_n = con.execute("SELECT count(*) FROM raw_tests").fetchone()[0]
    ana_n = con.execute("SELECT count(*) FROM analytical_tests").fetchone()[0]
    if not raw_n or not ana_n:
        print("\n  no rows to report on — the load produced an empty table.")
        return
    print(f"\nraw test rows            {raw_n:>14,}")
    print(f"analytical test rows     {ana_n:>14,}   "
          f"({ana_n / raw_n:.1%} retained)")

    print("\nrows removed, by reason (evaluated against raw_tests)")
    reasons = {
        "not an initial test (RT/PL/PV)":
            f"test_type NOT IN ({_sql_list(INITIAL_TEST_TYPES)})",
        "no pass/fail verdict (ABA/ABR/R)":
            f"test_type IN ({_sql_list(INITIAL_TEST_TYPES)}) "
            f"AND test_result NOT IN ({_sql_list(DECIDED_RESULTS)})",
        "first_use_date is the 1971 unknown sentinel":
            f"first_use_date = DATE '{UNKNOWN_FIRST_USE}'",
        "first_use_date missing or after test date":
            "first_use_date IS NULL OR first_use_date > test_date",
    }
    for label, predicate in reasons.items():
        n = con.execute(
            f"SELECT count(*) FROM raw_tests WHERE {predicate}").fetchone()[0]
        print(f"  {label:<45} {n:>12,}  {n / raw_n:6.2%}")

    print("\nodometer")
    od = con.execute(f"""
        SELECT
          count(*) FILTER (WHERE test_mileage = 0)                    AS zero,
          count(*) FILTER (WHERE test_mileage > {MAX_PLAUSIBLE_MILES}) AS implausible,
          count(*) FILTER (WHERE test_mileage IS NULL)                AS missing
        FROM raw_tests
    """).fetchone()
    for label, n in zip(("zero (no reading taken)",
                         f"above {MAX_PLAUSIBLE_MILES:,} miles",
                         "null"), od):
        print(f"  {label:<45} {n:>12,}  {n / raw_n:6.2%}")

    print("\nnull rate per column, analytical_tests")
    cols = [r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'analytical_tests'").fetchall()]
    expr = ", ".join(
        f"count(*) FILTER (WHERE {c} IS NULL) AS {c}" for c in cols)
    for col, n in zip(cols, con.execute(
            f"SELECT {expr} FROM analytical_tests").fetchone()):
        flag = "  <-- " if n / ana_n > 0.05 else ""
        print(f"  {col:<45} {n:>12,}  {n / ana_n:6.2%}{flag}")

    lo, hi = con.execute(
        "SELECT min(test_date), max(test_date) FROM analytical_tests").fetchone()
    print(f"\ndate range               {lo}  to  {hi}")

    print("\npass rate  <-- the checkpoint")
    for label, where in (("all test classes", "TRUE"),
                         (f"class {CAR_TEST_CLASS} (cars)",
                          f"test_class_id = '{CAR_TEST_CLASS}'")):
        r = con.execute(f"""
            SELECT count(*),
                   avg(CASE WHEN failed THEN 0.0 ELSE 1.0 END),
                   count(*) FILTER (WHERE test_result = 'PRS')
            FROM analytical_tests WHERE {where}
        """).fetchone()
        print(f"  {label:<24} n={r[0]:>12,}   pass rate {r[1]:6.2%}"
              f"   (PRS counted as failure: {r[2]:,})")

    print("\ntop 20 makes by test volume")
    for make, n, rate in con.execute(f"""
        SELECT make, count(*) AS n,
               avg(CASE WHEN failed THEN 1.0 ELSE 0.0 END) AS fail_rate
        FROM analytical_tests
        WHERE test_class_id = '{CAR_TEST_CLASS}' AND make IS NOT NULL
        GROUP BY make ORDER BY n DESC LIMIT 20
    """).fetchall():
        print(f"  {make:<24} {n:>12,}   failure rate {rate:6.2%}")

    print("\ndefect rows")
    d = con.execute("""
        SELECT count(*),
               count(*) FILTER (WHERE defect_desc IS NULL),
               count(*) FILTER (WHERE defect_category IS NULL)
        FROM analytical_defects
    """).fetchone()
    print(f"  total                                        {d[0]:>12,}")
    print(f"  unresolved defect text (lookup miss)         {d[1]:>12,}"
          f"  {d[1] / d[0]:6.2%}")
    print(f"  unresolved category (hierarchy miss)         {d[2]:>12,}"
          f"  {d[2] / d[0]:6.2%}")
    print("=" * 62)


if __name__ == "__main__":
    con = duckdb.connect(str(DB_PATH))
    build(con)
    quality_report(con)
    con.close()
