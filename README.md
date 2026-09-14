# MOTIntel

**Should you buy that used car? 42.7 million real UK MOT tests from 2025, one page of answers.**

**Project page: [jgit705.github.io/MOTIntel](https://jgit705.github.io/MOTIntel/)**

Pick a make, model and age. MOTIntel shows how often that car failed its MOT in
2025 compared with every car the same age, what goes wrong with it, whether
mileage matters, how it stacks up against the cars you're weighing it against,
and a checklist of what to look at when you go and see one. An LLM interprets
the findings, and every answer it gives is checked against the data before
anyone sees it.

> **About the data:** every figure comes from the DVSA's anonymised MOT results
> for tests carried out between **1 January and 30 December 2025**. It is one
> year of tests, not a car's full history.

![MOTIntel showing a Ford Fiesta's 2025 MOT record against every car its age](docs/dashboard.png)

![Comparing three cars at the same age](docs/compare.png)

---

## Three findings from 2025

**1. Old cars fail *less*, because the bad ones are gone.** The failure rate
climbs from 11.0% at 0–3 years old to a peak of **42.7% at 18–21 years**, then
falls to 33.3% by 27–30. Old cars don't get better. The neglected ones were
scrapped, so the cars still taking an MOT at 25 are the looked-after minority.
That is survivorship bias, measured directly.

| Vehicle age | Tests in 2025 | Failure rate |
|---|---|---|
| 0–3 years | 1,475,242 | 11.01% |
| 6–9 years | 7,465,670 | 20.91% |
| 12–15 years | 4,874,900 | 37.53% |
| **18–21 years** | 1,858,569 | **42.72%** (peak) |
| 27–30 years | 121,698 | 33.33% |

**2. Machine learning barely beat a simple average, so the app doesn't use it.**
Trained on January to September 2025 and tested on October to December 2025,
XGBoost beat a plain make/model/age average by **0.017 AUC**, and the average
was *better calibrated*. The page shows the observed rate, not a prediction.

| Model | AUC | Brier | Calibration error |
|---|---|---|---|
| Baseline (group-by make/model/age) | 0.6743 | 0.1877 | **0.0058** |
| Logistic regression | 0.6813 | 0.1872 | 0.0173 |
| XGBoost | **0.6910** | **0.1846** | 0.0069 |

**3. Mileage is brutal.** A Ford Fiesta failed 9.8% of its tests under 20k miles
and **51.3%** over 120k.

---

## How it works

```
 DVSA open data for 2025 (8.5 GB of ZIPs)
        │
        ▼
 ① INGEST ─────────► DuckDB raw tables          motintel/ingest.py
        │
        ▼
 ② CLEAN ──────────► 35.4M-row analytical table  motintel/transform.py
        │
        ├──► ③ MODEL COMPARISON (evaluation only) motintel/model.py
        │
        ▼
 ④ EXPORT ─────────► 1.9 MB of Parquet            motintel/export.py
        │
        ├──► ⑤ LLM ENRICHMENT (offline, checked)  motintel/enrich.py
        │
        ▼
 ⑥ FINDINGS ───────► one profile per car          motintel/serving.py
        │
        ├──────────────────────┐
        ▼                      ▼
 ⑦ THE PAGE              ⑧ AI INTERPRETATION ──► checks ──► page
   motintel_app.py          motintel/llm.py          motintel/grounding.py
```

### ① Ingest: get 135 million rows off disk

The DVSA publishes each year of MOT results as ZIPs of monthly CSVs. For 2025
that is 42.7 million tests and 92.5 million defect records.
[`ingest.py`](motintel/ingest.py) unpacks them and loads them into **DuckDB**
as they are, with no changes, so every later step can be re-run without
unpacking again.

DuckDB rather than pandas, because it reads straight off disk and only loads
what a query needs; pandas would want all of it in memory. DuckDB rather than
Postgres, because this is one person running analytical queries on a laptop,
with no server and no concurrent writes. The whole load and clean takes about
**49 seconds** once the files are unpacked.

### ② Clean: count everything removed

[`transform.py`](motintel/transform.py) turns the raw rows into one row per
test, resolves defect codes through the lookup tables, and works out each car's
age. Every filter is counted and reported, never applied silently:

| Removed | Rows | Why |
|---|---|---|
| Retests | 7,140,990 (16.7%) | A retest answers a different question from a first test |
| No pass/fail verdict | 218,545 (0.5%) | Abandoned or aborted tests |
| Odometer reads 0 | 326,771, set to *unknown* | 0 means "no reading taken", not zero miles |
| Odometer over 500,000 | 5,395 | Typing errors |

The files also contradict their own user guide, and each of these would have
quietly corrupted every number downstream:

- **"Pass with Rectification at Station" is a fail.** The car arrived failing
  and was fixed on the spot. Counting it as a pass pushes the car pass rate from
  **72.3% to 81.3%**.
- **Quotes are escaped with a backslash**, so the default CSV reader stops at
  the first model name that contains a comma.
- **The same defect wording exists with and without a trailing space.** That
  split 43 descriptions in two and listed identical faults twice in the app.
- **Defect categories only resolve through a two-column join.** The failure file
  doesn't carry them.

### ③ Model comparison: a baseline first

[`model.py`](motintel/model.py) compares a plain group-by average with logistic
regression and XGBoost. The split is **by date**, not random: a random split
puts the same car's January and November tests on both sides and leaks the
answer. The group-by came within 0.017 AUC and was better calibrated (see
finding 2), so the model is kept as an evaluation and not served.

### ④ Export: heavy work offline, light serving

[`export.py`](motintel/export.py) pre-calculates everything the page can show
into **1.9 MB of Parquet**: failure rates by make, model, age and mileage; the
ten commonest failure reasons, with how often DVSA graded each one Dangerous;
age curves; and national averages. The app never touches the 6 GB database.
Groups with fewer than 30 tests are dropped, and ties in a top ten are broken by
name, so two exports always agree.

### ⑤ LLM enrichment: the model as a pipeline tool

DVSA describes a failure as a category plus a fragment, such as `Suspension` /
`fractured or broken`. From that, a buyer can't tell a £15 bulb from a rotten
subframe. [`enrich.py`](motintel/enrich.py) runs once, offline, and turns each
of the **515** fragments behind the app into:

- a plain-English sentence;
- the part of the car it affects, from a fixed list;
- how big the repair is (*small, medium or big job*), kept separate from DVSA's
  severity grade, because worn brake pads are a small job *and* a dangerous
  fault;
- what to check when viewing the car, or nothing where it can't be seen outside
  a workshop.

Nothing it writes reaches the app unchecked.
[`defect_labels.py`](motintel/defect_labels.py) rejects the whole table unless
every defect is labelled exactly once, every category comes from the allowed
list, no sentence contains a price, a mileage or a figure that isn't in its
source, and nine hand-written test cases agree. A person then reads the labels
on a review page ([`review.py`](motintel/review.py)), and every disagreement is
recorded in [`defect_overrides.json`](motintel/defect_overrides.json).

The work is sent 50 defects at a time and each batch is saved as it arrives, so
a full run fits inside the free tier's 20 requests a day and picks up where it
stopped if interrupted.

### ⑥ Findings: decided in code, not by the AI

[`serving.py`](motintel/serving.py) builds one profile per car that both the
page and the AI read, so a car can't show one number on screen and a different
one in its summary. It also works out what the numbers *mean*:

- **Better than, about or worse than average**, against every car the same age.
- **How much to trust it**, from the 95% margin of error on the rate.
- **Where on the car the failures cluster**, from the enriched labels.
- **The mileage where failures jump**, if there is one.
- **A like-for-like rank.** Sorted by the raw rate, the "most reliable"
  12-year-old cars are Ferraris and Rolls-Royces, because a 12-year-old Ferrari
  has done 9,100 miles and a Focus 91,700. Each model is reweighted onto the
  mileage spread of all cars its age, and models without enough spread are left
  out rather than guessed at.

### ⑦ The page: a buyer's questions, in order

[`motintel_app.py`](motintel_app.py) is a Streamlit app laid out as five
questions: *Is it reliable? What usually goes wrong? Does age or mileage
matter? How does it compare? What should I check?* One search box covers all
**5,998** cars. Up to three more can be compared, always **at the same age**,
because a 4-year-old car against a 12-year-old one mostly measures the eight
years between them. The car, age and comparison are kept in the URL, so any
view can be shared.

### ⑧ AI interpretation: checked before it's shown

```
 profile ──► data block ──► Gemini ──► structured JSON ──► checks ──┬──► saved and shown
 (facts)     (everything                                            └──► rejected, not saved
              it can see)
```

[`llm.py`](motintel/llm.py) gives Gemini a block of the car's figures and
findings, and nothing else: no tools and no web access. It returns structured
JSON (a headline, "should I be concerned?", key points and what to check),
which the page lays out. Before an answer is saved or shown, it is rejected if
it:

- contains **a figure that isn't in the data block** ([`grounding.py`](motintel/grounding.py));
- mentions **anything outside the data**, such as a price, a recall, a body type
  or advice to buy or avoid;
- **describes a car with too few tests** instead of declining;
- **declines a well-covered car**, because a model that refused everything would
  otherwise pass every other check.

It only runs when you press the button, at temperature 0, and answers are saved
to disk. Retrieval is plain SQL rather than a vector database: the lookup is an
exact make, model and age, and grounding is about the data, not embeddings.

**Measured, not assumed.** [`tests/test_llm_eval.py`](tests/test_llm_eval.py)
re-checks every saved answer against the exact data it was given, without
spending any API quota:

| | |
|---|---|
| Figures checked against their source data | **199** across 18 answers |
| Answers with a figure not in that data | **0** |
| Answers mentioning prices, recalls or buying advice | **0** |
| Well-covered cars answered rather than declined | **10 / 10** |
| Thin-data cars correctly declined | **5 / 6** |

The one miss is kept in the numbers. An answer for an Abarth 595C with only 90
tests said the data was too thin, then described the car anyway. That answer,
like most of this set, was written before the current structured format, which
checks the refusal explicitly. Growing the evaluation on the current format is
the next step.

---

## In numbers

| | |
|---|---|
| Data covers | MOT tests from **1 January to 30 December 2025** |
| MOT tests loaded | **42,728,066** |
| Defect records loaded | **92,473,454** |
| Rows after cleaning | **35,368,333** (82.8% kept) |
| Cars you can look up | **5,998** across 122 makes |
| Load and clean | about **49 seconds** |
| Serving layer | **1.9 MB** of Parquet |

---

## Run it yourself

You'll need **Python 3.11 or newer** and around **25 GB of free disk space**
for the raw files and the database. None of the data is included in this
repository.

### 1. Get the code

```bash
git clone https://github.com/JGit705/MOTIntel.git
cd MOTIntel
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

On macOS, XGBoost also needs OpenMP: `brew install libomp`.

### 2. Download the 2025 data

From the DVSA's [anonymised MOT data page](https://open.data.dvsa.gov.uk/mot-anonymised/index.html),
download these three files and put them in a `data/raw/` folder inside the
project:

- `dft_test_result_extracts_2025.zip`
- `dft_test_item_extracts_2025.zip`
- `lookup.zip`

Leave the two extract ZIPs as they are (the pipeline unpacks them), and unzip
the lookup tables into their own folder:

```bash
unzip data/raw/lookup.zip -d data/raw/lookup
```

### 3. Build everything

Run these from the project folder, in order:

```bash
./.venv/bin/python -m motintel.run_pipeline   # ① ② unpack, load, clean, print a quality report
./.venv/bin/python -m motintel.model          # ③ baseline vs logistic regression vs XGBoost
./.venv/bin/python -m motintel.export         # ④ write the serving layer to data/processed/
./.venv/bin/streamlit run motintel_app.py     # ⑦ open the app in your browser
```

Add `?theme=light` to the app's URL for the light theme.

### 4. Optional: the AI features

The AI parts need a free Google Gemini API key. Get one from
[Google AI Studio](https://aistudio.google.com/apikey), then create a file
called `.env` in the project folder containing:

```
GEMINI_API_KEY=your-key-here
```

`.env` is ignored by git, so the key is never committed. With a key you can:

```bash
./.venv/bin/python -m motintel.enrich         # ⑤ plain-English labels for the defects (about 11 requests)
./.venv/bin/python -m motintel.review         #    build data/processed/defect_review.html to read them
./.venv/bin/python -m motintel.short_labels   #    short names for the failure reasons
```

and press **Get AI buying insight** in the app. Without a key, everything else
works and the AI panel says a key is needed.

### Tests

```bash
./.venv/bin/python -m tests.test_app_smoke       # drives the real app: 159 cars, every panel
./.venv/bin/python -m tests.test_llm_eval        # measures the saved AI answers (no API calls)
./.venv/bin/python -m tests.test_llm_grounding   # grounding rules; the live part needs a key
./.venv/bin/python -m tests.test_defect_labels   # the enrichment's rules
./.venv/bin/python -m tests.test_enrich          # enrichment machinery (no API calls)
./.venv/bin/python -m tests.test_names           # display names: MX-5, not Mx-5
./.venv/bin/python -m tests.test_short_labels    # short failure-reason labels
```

Tests that need the exported data skip those parts when it isn't there. CI on
GitHub runs lint, an import check and every test that can run without the data.

---

## What an MOT failure rate is not

It isn't a reliability score. The MOT checks safety and emissions once a year,
can't see anything repaired in between, and reflects how a car was looked after
as much as how it was built. This project covers **2025 only**, so it can't show
how a model changed from year to year. Fuel type is unreliable for older
hybrids, and the data has no repair costs. The page says so wherever it shows a
figure.

---

## Licence

Code: [MIT](LICENSE). Use it however you like.

Data: contains public sector information licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
Source: [DVSA anonymised MOT testing data](https://open.data.dvsa.gov.uk/mot-anonymised/index.html).
