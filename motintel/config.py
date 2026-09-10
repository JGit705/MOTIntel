"""Shared paths and data-scope constants for the MOTIntel pipeline."""
from pathlib import Path

# motintel/config.py -> motintel -> project root
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
CSV_DIR = RAW / "csv"
LOOKUP_DIR = RAW / "lookup"
PROCESSED = ROOT / "data" / "processed"
DB_PATH = ROOT / "data" / "motintel.duckdb"

YEAR = 2025

# --- Format facts, established in Phase 0 by inspecting the real files ---
# The yearly *extract* CSVs are comma-delimited with a header row and ISO
# dates. The lookup tables shipped in lookup.zip are pipe-delimited. The
# user guide (v5.1) describes the older yearly files and is wrong on both
# counts for these extracts, so nothing here is inferred from it.
EXTRACT_DELIM = ","
LOOKUP_DELIM = "|"

# Model names contain commas ("SERIES 1, 80 INCH"), so fields are quoted — and
# an embedded quote is escaped with a BACKSLASH ("STREETZONE 50 2T 12\\""),
# not by the RFC-4180 doubling DuckDB's sniffer expects. Left to auto-detect,
# the sniffer picks an empty quote character and the load fails on the first
# comma-bearing model name; told to expect RFC quoting, it fails on the first
# backslash-escaped inch mark. Both are pinned here.
EXTRACT_QUOTE = '"'
EXTRACT_ESCAPE = "\\"

# --- Scope decisions, each with a reason ---

# Retests are excluded from the analytical table. RT/PL/PV rows describe a
# vehicle returning after repair, so they pass at a far higher rate than an
# initial test. Including them answers "does a repaired car pass?", which is
# not the question a used-car buyer is asking.
INITIAL_TEST_TYPES = ("NT",)

# Only outcomes where a pass was genuinely determined. ABA/ABR are tests that
# could not be completed; R is a refusal. None of them carry a roadworthiness
# verdict, so they are not labelled either way.
DECIDED_RESULTS = ("P", "F", "PRS")

# PRS counts as a failure. The vehicle entered the test in a failing condition
# and was repaired within the hour; treating it as a pass would understate the
# defect rate. This matches DVSA's own "initial failure" definition, which
# counts tests carrying one or more Fail or PRS items.
FAIL_RESULTS = ("F", "PRS")

# Odometer plausibility. test_mileage is an unsigned integer, so negatives
# cannot occur; zero is documented as "no reading taken" and becomes NULL
# rather than a real zero-mile vehicle.
MAX_PLAUSIBLE_MILES = 500_000

# The DVLA allocates 1971-01-01 as the first-use date for any vehicle whose
# date of manufacture is unknown. Left in, these become ~55-year-old vehicles
# and distort every age-based figure.
UNKNOWN_FIRST_USE = "1971-01-01"

# FR1.5. The May 2018 regime change altered the defect categories, so rows
# either side of it are not comparable. Only 2025 is loaded today, which makes
# this filter inert — it is here so that adding an older year narrows the scope
# instead of silently mixing two incompatible schemes.
EARLIEST_TEST_DATE = "2019-01-01"

# Class 4 is cars and light passenger vehicles: the audience for this product.
# Other classes stay in the analytical table but the app scopes to class 4.
CAR_TEST_CLASS = "4"
