# MOTIntel — UK Vehicle Reliability Intelligence

Evidence-based vehicle reliability profiles built from the DVSA's open MOT
dataset: enter a make, model and year, get failure probability and the specific
defects that cause it.

**Status:** Phase 0 complete (pre-flight sizing). Build in progress.

---

## Phase 0 — Pre-flight findings

The decision gate was "is a year of data under ~10GB uncompressed?" It is.

Sizes were established **without downloading the archives**, by reading each
ZIP's central directory over HTTP range requests (~1KB fetched per file).
The entries are stored with compression method 0 (`stored`, not deflated), so
the compressed and uncompressed sizes are identical.

| Archive | Entries | Size (= uncompressed) |
|---|---|---|
| `dft_test_result_extracts_2025.zip` | 12 monthly CSVs | 4.50 GB |
| `dft_test_item_extracts_2025.zip` | 12 monthly CSVs | 4.06 GB |
| `dft_test_result_extracts_2024.zip` | 12 monthly CSVs | 4.47 GB |
| `dft_test_item_extracts_2024.zip` | 12 monthly CSVs | 4.03 GB |
| `lookup.zip` | 6 lookup CSVs | 0.25 MB |
| **One year (results + failure items)** | | **~8.5 GB** |

Local disk free at time of check: 291 GB. No constraint.

**Gate decision: proceed locally with DuckDB.** No single-year restriction is
forced by size.

### Two findings that change the plan for the better

1. **The archives are split by month, not one CSV per year.** Each year ships as
   twelve `..._YYYYMM.csv` files. This gives a natural partition key for genuine
   incremental loading — load-one-month, re-run-safe — rather than the all-or-
   nothing yearly reload the PRD assumed.
2. **Because entries are stored rather than deflated, a single month can be
   pulled out of a remote archive with an HTTP range request** — roughly 390 MB
   instead of 4.5 GB. Useful for building the pipeline against real data before
   committing to a full download.

### File naming note

The 2024 and 2025 archives use the `*_extracts_*` naming and the monthly layout.
The 2023 and 2022 archives use older names (`dft_test_result_2023.zip`) and are
far smaller compressed (1.11 GB), implying a different packaging. v1 scope is
2024+ regardless, so this is recorded but not investigated.

---

## Data source and attribution

Contains public sector information licensed under the Open Government Licence.
Source: [DVSA Anonymised MOT Data](https://open.data.dvsa.gov.uk/mot-anonymised/index.html).

---

## Reproduction

To be written once the pipeline runs end to end (FR1.7).
