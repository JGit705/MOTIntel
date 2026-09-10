"""Phase 3 + the Phase 5 grounding test.

Two vehicles are summarised: one with plenty of data, and one deliberately
sparse. The sparse case is the real test — if the model invents plausible
failure modes for a vehicle it has almost no rows for, the AI layer is not
trustworthy and nothing else in the app matters.
"""
from __future__ import annotations

import duckdb
from dotenv import load_dotenv

load_dotenv()

import llm  # noqa: E402
from config import CAR_TEST_CLASS, DB_PATH  # noqa: E402
from queries import build_profile  # noqa: E402


def show(con, make: str, model: str, age: int, label: str) -> None:
    print("=" * 70)
    print(f"{label}: {make} {model}, {age} years old")
    print("=" * 70)
    profile = build_profile(con, make, model, age)
    print("\n--- DATA BLOCK GIVEN TO THE MODEL (its entire world) ---")
    print(llm._render(profile))
    print("\n--- SUMMARY ---")
    summary = llm.summarise(profile)
    print(summary if summary else "[LLM layer unavailable — app would degrade]")
    print()


if __name__ == "__main__":
    con = duckdb.connect(str(DB_PATH), read_only=True)

    # A sparse-but-real vehicle: pick the thinnest group we actually hold.
    sparse = con.execute(f"""
        SELECT make, model, count(*) n
        FROM analytical_tests
        WHERE test_class_id = '{CAR_TEST_CLASS}'
          AND make IS NOT NULL AND model IS NOT NULL
          AND vehicle_age_years >= 6 AND vehicle_age_years < 10
        GROUP BY 1, 2 HAVING count(*) BETWEEN 3 AND 15
        ORDER BY n LIMIT 1
    """).fetchone()

    show(con, "FORD", "FIESTA", 8, "WELL-COVERED VEHICLE")
    if sparse:
        show(con, sparse[0], sparse[1], 8,
             f"SPARSE VEHICLE ({sparse[2]} tests) — grounding test")
    con.close()
