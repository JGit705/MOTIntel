"""FR2 — baseline, classifier, and an honest calibration check.

Order matters here. The baseline is computed first and kept, because the
interesting question is not "what AUC did XGBoost get" but "did any of this
beat a group-by". If it did not, that is the finding.
"""
from __future__ import annotations

import argparse
import json

import duckdb
import numpy as np
import polars as pl
from sklearn.calibration import calibration_curve
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

from config import CAR_TEST_CLASS, DB_PATH, PROCESSED

# The split is by date: train on the earlier months, test on the later ones.
# A random split would put the same vehicle's January and November tests on
# opposite sides and leak the answer, and it would also let the model learn
# from a future it would not have at prediction time.
TRAIN_END = "2025-10-01"

# Model cardinality is the practical constraint: tens of thousands of distinct
# model strings, most with a handful of tests. Rarer models fall back to an
# OTHER bucket rather than getting a column each.
TOP_N_MODELS = 300
SAMPLE_ROWS = 3_000_000


def load(con) -> pl.DataFrame:
    print(f"sampling up to {SAMPLE_ROWS:,} class-{CAR_TEST_CLASS} tests ...")
    return con.execute(f"""
        SELECT make, model, fuel_type, test_class_id,
               vehicle_age_years, odometer_miles, cylinder_capacity,
               test_date, vehicle_id, failed
        FROM analytical_tests
        WHERE test_class_id = '{CAR_TEST_CLASS}'
          AND make IS NOT NULL AND model IS NOT NULL
          AND odometer_miles IS NOT NULL
          AND vehicle_age_years BETWEEN 0 AND 40
        USING SAMPLE {SAMPLE_ROWS} ROWS
    """).pl()


def baseline(con, test: pl.DataFrame) -> np.ndarray:
    """FR2.1 — failure rate by make / model / age band, learned on the training
    period only so it is scored on the same footing as the classifiers."""
    rates = con.execute(f"""
        SELECT make, model, CAST(floor(vehicle_age_years / 3) AS INT) AS age_band,
               avg(CASE WHEN failed THEN 1.0 ELSE 0.0 END) AS rate
        FROM analytical_tests
        WHERE test_class_id = '{CAR_TEST_CLASS}' AND test_date < DATE '{TRAIN_END}'
          AND make IS NOT NULL AND model IS NOT NULL
        GROUP BY 1, 2, 3 HAVING count(*) >= 30
    """).pl()
    overall = con.execute(f"""
        SELECT avg(CASE WHEN failed THEN 1.0 ELSE 0.0 END) FROM analytical_tests
        WHERE test_class_id = '{CAR_TEST_CLASS}' AND test_date < DATE '{TRAIN_END}'
    """).fetchone()[0]

    joined = (test.with_columns(
        (pl.col("vehicle_age_years") / 3).floor().cast(pl.Int32).alias("age_band"))
        .join(rates, on=["make", "model", "age_band"], how="left"))
    return joined["rate"].fill_null(overall).to_numpy()


def featurise(train: pl.DataFrame, test: pl.DataFrame, top_models: list[str]):
    def prep(df: pl.DataFrame) -> pl.DataFrame:
        return df.with_columns(
            pl.when(pl.col("model").is_in(top_models))
              .then(pl.col("model")).otherwise(pl.lit("OTHER")).alias("model"))

    cat_cols = ["make", "model", "fuel_type"]
    num_cols = ["vehicle_age_years", "odometer_miles", "cylinder_capacity"]
    train, test = prep(train), prep(test)

    enc = OneHotEncoder(handle_unknown="ignore", min_frequency=50,
                        sparse_output=True)
    Xc_tr = enc.fit_transform(train.select(cat_cols).to_pandas())
    Xc_te = enc.transform(test.select(cat_cols).to_pandas())

    # Numeric features are standardised. Odometer runs to 500,000 while age
    # runs to 40, and on that scale lbfgs does not converge in any sane number
    # of iterations — the unscaled logistic regression stopped at the limit and
    # came out worse calibrated than the group-by it was meant to improve on.
    # Trees are invariant to monotonic rescaling, so XGBoost is unaffected.
    from scipy.sparse import csr_matrix, hstack
    scaler = StandardScaler()
    Xn_tr = csr_matrix(scaler.fit_transform(
        np.nan_to_num(train.select(num_cols).to_numpy())))
    Xn_te = csr_matrix(scaler.transform(
        np.nan_to_num(test.select(num_cols).to_numpy())))
    return (hstack([Xc_tr, Xn_tr]).tocsr(), hstack([Xc_te, Xn_te]).tocsr())


def evaluate(name: str, y_true, y_prob) -> dict:
    auc = roc_auc_score(y_true, y_prob)
    brier = brier_score_loss(y_true, y_prob)
    frac_pos, mean_pred = calibration_curve(y_true, y_prob, n_bins=10,
                                            strategy="quantile")
    # Mean absolute calibration error: how far a stated probability sits from
    # the observed rate. FR2.3 — for this product this matters more than AUC.
    mace = float(np.mean(np.abs(frac_pos - mean_pred)))
    print(f"  {name:<22} AUC {auc:.4f}   Brier {brier:.4f}   "
          f"calibration error {mace:.4f}")
    return {"model": name, "auc": auc, "brier": brier,
            "calibration_error": mace,
            "reliability_curve": [
                {"predicted": float(p), "observed": float(o)}
                for p, o in zip(mean_pred, frac_pos)]}


def main() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    df = load(con)

    train = df.filter(pl.col("test_date") < pl.lit(TRAIN_END).str.to_date())
    test = df.filter(pl.col("test_date") >= pl.lit(TRAIN_END).str.to_date())
    print(f"train {len(train):,} rows  |  test {len(test):,} rows  "
          f"(split at {TRAIN_END})")
    print(f"base failure rate: train {train['failed'].mean():.2%}  "
          f"test {test['failed'].mean():.2%}")

    y_tr = train["failed"].to_numpy().astype(int)
    y_te = test["failed"].to_numpy().astype(int)

    top_models = (train.group_by("model").len().sort("len", descending=True)
                  .head(TOP_N_MODELS)["model"].to_list())
    X_tr, X_te = featurise(train, test, top_models)
    print(f"feature matrix: {X_tr.shape[1]:,} columns")

    print("\nresults")
    results = [evaluate("baseline (group-by)", y_te, baseline(con, test))]

    lr = LogisticRegression(max_iter=1000, solver="lbfgs")
    lr.fit(X_tr, y_tr)
    results.append(evaluate("logistic regression", y_te,
                            lr.predict_proba(X_te)[:, 1]))

    xgb = XGBClassifier(n_estimators=400, max_depth=7, learning_rate=0.08,
                        subsample=0.8, colsample_bytree=0.8,
                        eval_metric="logloss", n_jobs=-1, tree_method="hist")
    xgb.fit(X_tr, y_tr)
    results.append(evaluate("xgboost", y_te, xgb.predict_proba(X_te)[:, 1]))

    best = max(results, key=lambda r: r["auc"])
    lift = best["auc"] - results[0]["auc"]
    print(f"\nbest: {best['model']} (AUC {best['auc']:.4f}), "
          f"{lift:+.4f} against the baseline")
    if lift < 0.01:
        print("  NOTE: the classifiers barely beat a group-by. That is a "
              "finding to report, not to hide.")

    PROCESSED.mkdir(parents=True, exist_ok=True)
    (PROCESSED / "model_comparison.json").write_text(json.dumps(results, indent=2))
    xgb.save_model(PROCESSED / "xgboost_failure.json")   # FR2.5
    print(f"\nwrote {PROCESSED / 'model_comparison.json'}")
    con.close()


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    main()
