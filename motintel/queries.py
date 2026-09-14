"""FR3 — the retrieval layer.

These functions are the only route from a vehicle selection to data. The LLM
layer calls them and is given nothing else, so grounding is enforced by the
architecture rather than by asking the model nicely.

Retrieval is SQL, not vector search. The data is structured and the filter is
exact — make, model, an age band. Embedding a defect-code table to do fuzzy
similarity over it would be strictly worse engineering than a WHERE clause.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from motintel.config import CAR_TEST_CLASS

# Three-year bands, matching src/export.py exactly. They used to differ, which
# meant the same vehicle produced one failure rate here and a different one in
# the app — the sort of quiet disagreement that destroys trust in a number.
AGE_BANDS = [(lo, lo + 3) for lo in range(0, 30, 3)]
MILEAGE_BANDS = [(0, 20_000), (20_000, 50_000), (50_000, 80_000),
                 (80_000, 120_000), (120_000, 500_000)]

# Below this, a make/model/age cell is too thin to say anything responsible
# about. The LLM layer is told to refuse rather than speculate.
MIN_TESTS_FOR_CONFIDENCE = 100


@dataclass
class VehicleProfile:
    """Everything retrieved for one vehicle selection. This object is the whole
    of what the LLM is allowed to see."""
    make: str
    model: str
    age_years: int
    n_tests: int
    failure_rate: float | None
    # The band the figures actually describe. Without it the summary calls a
    # 6-9 year band "seven years old", which is precise about something the
    # data never claimed.
    age_band: tuple[int, int] | None = None
    top_defects: list[dict] = field(default_factory=list)
    by_mileage: list[dict] = field(default_factory=list)
    peers: list[dict] = field(default_factory=list)
    # What the page's sections show, carried here so the summary can be asked
    # to cover them — and can use nothing the reader cannot also see.
    benchmark: float | None = None
    severity: dict = field(default_factory=dict)
    repair_areas: list = field(default_factory=list)
    rank: tuple[int, int] | None = None
    age_curve: list[dict] = field(default_factory=list)
    # Layer three: what the statistics mean, decided by serving.findings in
    # code rather than left for the model to conclude.
    findings: dict = field(default_factory=dict)

    @property
    def is_sparse(self) -> bool:
        return self.n_tests < MIN_TESTS_FOR_CONFIDENCE


def _age_band(age_years: int) -> tuple[int, int]:
    for lo, hi in AGE_BANDS:
        if lo <= age_years < hi:
            return lo, hi
    return AGE_BANDS[-1]


def headline(con, make: str, model: str, age_years: int) -> tuple[int, float | None]:
    lo, hi = _age_band(age_years)
    row = con.execute("""
        SELECT count(*), avg(CASE WHEN failed THEN 1.0 ELSE 0.0 END)
        FROM analytical_tests
        WHERE test_class_id = ? AND make = ? AND model = ?
          AND vehicle_age_years >= ? AND vehicle_age_years < ?
    """, [CAR_TEST_CLASS, make, model, lo, hi]).fetchone()
    return row[0], row[1]


def top_defects(con, make: str, model: str, age_years: int, limit: int = 10):
    """FR3.1. Only Fail and PRS items count: a Minor or Advisory item is
    recorded at the test but does not cause the vehicle to fail."""
    lo, hi = _age_band(age_years)
    return [dict(zip(("category", "defect", "n_tests", "share_of_tests"), r))
            for r in con.execute("""
        WITH scope AS (
            SELECT test_id FROM analytical_tests
            WHERE test_class_id = ? AND make = ? AND model = ?
              AND vehicle_age_years >= ? AND vehicle_age_years < ?
        )
        SELECT coalesce(d.defect_category, 'Unclassified'),
               coalesce(d.defect_desc,
                        'defect code not present in the DVSA lookup tables'),
               count(DISTINCT d.test_id) AS n,
               count(DISTINCT d.test_id) * 1.0 / (SELECT count(*) FROM scope)
        FROM analytical_defects d
        JOIN scope USING (test_id)
        WHERE d.rfr_type_code IN ('F', 'P')
        GROUP BY 1, 2
        ORDER BY n DESC
        LIMIT ?
    """, [CAR_TEST_CLASS, make, model, lo, hi, limit]).fetchall()]


def failure_by_mileage(con, make: str, model: str):
    """FR3.2. Age is deliberately not filtered here — mileage and age are
    correlated, and the point of this view is to show the mileage gradient."""
    cases = " ".join(
        f"WHEN odometer_miles >= {lo} AND odometer_miles < {hi} "
        f"THEN '{lo // 1000}k-{hi // 1000}k'" for lo, hi in MILEAGE_BANDS)
    return [dict(zip(("mileage_band", "n_tests", "failure_rate"), r))
            for r in con.execute(f"""
        SELECT CASE {cases} END AS band,
               count(*),
               avg(CASE WHEN failed THEN 1.0 ELSE 0.0 END)
        FROM analytical_tests
        WHERE test_class_id = ? AND make = ? AND model = ?
          AND odometer_miles IS NOT NULL
        GROUP BY band HAVING band IS NOT NULL
        ORDER BY min(odometer_miles)
    """, [CAR_TEST_CLASS, make, model]).fetchall()]


def peer_comparison(con, make: str, model: str, age_years: int, limit: int = 3):
    """FR3.3. Peers are the highest-volume other models in the same age band.
    Volume is a crude proxy for 'comparable class of car', and the README says
    so — a proper peer group would need body-type data this dataset lacks."""
    lo, hi = _age_band(age_years)
    return [dict(zip(("make", "model", "n_tests", "failure_rate"), r))
            for r in con.execute("""
        SELECT make, model, count(*) AS n,
               avg(CASE WHEN failed THEN 1.0 ELSE 0.0 END)
        FROM analytical_tests
        WHERE test_class_id = ? AND vehicle_age_years >= ? AND vehicle_age_years < ?
          AND make IS NOT NULL AND model IS NOT NULL
          AND NOT (make = ? AND model = ?)
        GROUP BY 1, 2 HAVING count(*) >= ?
        ORDER BY n DESC LIMIT ?
    """, [CAR_TEST_CLASS, lo, hi, make, model,
          MIN_TESTS_FOR_CONFIDENCE, limit]).fetchall()]


def build_profile(con, make: str, model: str, age_years: int) -> VehicleProfile:
    make, model = make.upper().strip(), model.upper().strip()
    n, rate = headline(con, make, model, age_years)
    profile = VehicleProfile(make=make, model=model, age_years=age_years,
                             n_tests=n, failure_rate=rate)
    if n:
        profile.top_defects = top_defects(con, make, model, age_years)
        profile.by_mileage = failure_by_mileage(con, make, model)
        profile.peers = peer_comparison(con, make, model, age_years)
    return profile


def popular_models(con, limit: int = 50):
    """Drives the app's selection controls (FR5.1)."""
    return con.execute("""
        SELECT make, model, count(*) AS n
        FROM analytical_tests
        WHERE test_class_id = ? AND make IS NOT NULL AND model IS NOT NULL
        GROUP BY 1, 2 ORDER BY n DESC LIMIT ?
    """, [CAR_TEST_CLASS, limit]).fetchall()
