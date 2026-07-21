#!/usr/bin/env python3
"""
Disagreement regression analysis on AI-vs-human grading data.

Two model variants are available, selectable with --variant:

  --variant modality     (V1, default)
      9 features routed by `modality`. 3 features per modality.
      This is the original analysis used in Section 4.3.

  --variant rubric_type  (V2)
      12 features routed by `rubric_type`. 3 features per rubric type.
      Programming is split into 'scaffolded_coding' and 'open_end_eda',
      with the EDA bucket using EDA-specific features.

  --variant both         Run both V1 and V2 back-to-back and write both
      output sets.

Each variant fits two models on the same feature set:
  1. Ridge regression on signed disagreement   (S_human - S_AI)
  2. Logistic regression on high-disagreement  (|S_human - S_AI| >= 0.10)

Both models are evaluated with repeated k-fold cross-validation
(5-fold x 10 repeats = 50 fits). Standardized coefficients from a final
fit on all data are exported.

USAGE
-----
    python run_disagreement_regression.py <input_csv> [output_dir] \\
        [--variant modality|rubric_type|both]

    # examples
    python run_disagreement_regression.py data.csv
    python run_disagreement_regression.py data.csv ./results --variant both
    python run_disagreement_regression.py data.csv --variant rubric_type

OUTPUTS (suffixed by variant: '_9feat' for V1, '_12feat_rubric' for V2)
  - ridge_coefficients{suffix}.csv
  - logreg_coefficients{suffix}.csv
  - regression_cv_metrics{suffix}.csv
  - disagreement_summary_by_{modality|rubric_type}.csv

INPUT COLUMNS REQUIRED
  student_id, assignment_id, modality, rubric_type, ai_score_norm,
  human_score_norm, and the source feature columns for whichever variant
  is run.

V1 FEATURE MAP (modality-routed, 9 features)
  text         -> response_length_words, lexical_diversity_ttr, has_evidence_marker
  Programming  -> code_length_lines, cyclomatic_complexity_proxy, num_functions
  oral_video   -> speaking_tempo_wpm, filler_word_rate, star_completeness_score

V2 FEATURE MAP (rubric_type-routed, 12 features)
  scaffolded_coding -> code_length_lines, cyclomatic_complexity_proxy, num_functions
  open_end_eda      -> code_length_lines, num_detected_transformations, has_title
  free_response     -> response_length_words, lexical_diversity_ttr, has_evidence_marker
  mock_interview    -> speaking_tempo_wpm, filler_word_rate, star_completeness_score

FEATURE PROXY NOTES (called out explicitly in column naming)
  V1 / V2 shared:
    has_evidence_marker -> 'sentiment'      (no sentiment column exists)
    num_functions       -> 'test_coverage'  (no coverage column on scaffolded code)
  V2 only:
    num_detected_transformations -> 'code_style_metric' for EDA (this column
        is explicitly tagged 'Code style' in the upstream feature schema)
    has_title -> 'test_coverage' for EDA. None of the available EDA
        features measures testing; has_title is the closest available
        analog to a verification/checkability metric (whether the
        student labeled their plot at all). This is a stretch and is
        flagged as such.

METHODOLOGY NOTES
  * Signed convention: positive disagreement = human gave more credit.
  * Each row is one matched AI-human pair on a single submission.
  * Routed feature matrix: each row gets every feature column populated
    only for its own group (modality in V1, rubric_type in V2) and NaN
    elsewhere. After median imputation + StandardScaler, off-group cells
    become 0, so a feature contributes only to predictions on rows where
    it applies.
  * Preprocessing lives inside the sklearn Pipeline, so each CV fold
    computes fit-statistics on training rows only -- no test leakage.
  * Ridge alpha = 1.0, Logistic class_weight = 'balanced'.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import (
    RepeatedKFold,
    RepeatedStratifiedKFold,
    cross_val_predict,
    cross_validate,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


# ---------- configuration ----------
HIGH_DISAGREEMENT_THRESHOLD = 0.10
N_SPLITS = 5
N_REPEATS = 10
RANDOM_STATE = 42
RIDGE_ALPHA = 1.0

# V1: modality-routed, 9 features (the original analysis)
FEATURE_MAP_V1: Dict[str, Dict[str, str]] = {
    "text": {
        "length_of_written_response": "response_length_words",
        "lexical_sophistication":     "lexical_diversity_ttr",
        "sentiment":                  "has_evidence_marker",          # proxy
    },
    "Programming": {
        "code_length":       "code_length_lines",
        "code_style_metric": "cyclomatic_complexity_proxy",
        "test_coverage":     "num_functions",                          # proxy
    },
    "oral_video": {
        "tempo":                  "speaking_tempo_wpm",
        "fluency":                "filler_word_rate",
        "conceptual_explanation": "star_completeness_score",
    },
}

# V2: rubric_type-routed, 12 features. Programming is split into
# scaffolded_coding and open_end_eda, with the EDA bucket using EDA-specific
# features for code-style and a labeling-presence flag as the test-coverage
# proxy on EDA rows (no test-coverage column exists in the upstream schema).
FEATURE_MAP_V2: Dict[str, Dict[str, str]] = {
    "scaffolded_coding": {
        "code_length":       "code_length_lines",
        "code_style_metric": "cyclomatic_complexity_proxy",
        "test_coverage":     "num_functions",                          # proxy
    },
    "open_end_eda": {
        "code_length":       "code_length_lines",                      # reused
        "code_style_metric": "num_detected_transformations",           # EDA style
        "test_coverage":     "has_title",                              # proxy
    },
    "free_response": {
        "length_of_written_response": "response_length_words",
        "lexical_sophistication":     "lexical_diversity_ttr",
        "sentiment":                  "has_evidence_marker",           # proxy
    },
    "mock_interview": {
        "tempo":                  "speaking_tempo_wpm",
        "fluency":                "filler_word_rate",
        "conceptual_explanation": "star_completeness_score",
    },
}


VARIANTS = {
    "modality": {
        "feature_map":     FEATURE_MAP_V1,
        "routing_col":     "modality",
        "output_suffix":   "_9feat",
        "summary_suffix":  "by_modality",
        "label":           "V1 (modality-routed, 9 features)",
    },
    "rubric_type": {
        "feature_map":     FEATURE_MAP_V2,
        "routing_col":     "rubric_type",
        "output_suffix":   "_12feat_rubric",
        "summary_suffix":  "by_rubric_type",
        "label":           "V2 (rubric_type-routed, 12 features)",
    },
}


# ---------- helpers ----------
def build_routed_features(df: pd.DataFrame,
                          feature_map: Dict[str, Dict[str, str]],
                          routing_col: str
                          ) -> Tuple[pd.DataFrame, List[str]]:
    """Build a feature matrix where each row's features are populated only
    for its own group (defined by `routing_col`) and NaN elsewhere."""
    pieces: Dict[str, pd.Series] = {}
    feature_cols: List[str] = []
    for group, mapping in feature_map.items():
        mask = df[routing_col] == group
        for char_name, source_col in mapping.items():
            if source_col not in df.columns:
                raise KeyError(f"Source column '{source_col}' missing for "
                               f"{group}.{char_name}")
            colname = f"{group}__{char_name}"
            s = pd.Series(np.nan, index=df.index, name=colname, dtype="float64")
            s.loc[mask] = pd.to_numeric(df.loc[mask, source_col], errors="coerce")
            pieces[colname] = s
            feature_cols.append(colname)
    return pd.DataFrame(pieces), feature_cols


def make_preprocessor(feature_cols: List[str]) -> ColumnTransformer:
    pipe = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler",  StandardScaler()),
    ])
    return ColumnTransformer([("num", pipe, feature_cols)])


def descriptive_summary(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    return (
        df.groupby(group_col)
          .agg(
              n=("disagreement", "count"),
              mean_signed_disagreement=("disagreement", "mean"),
              median_signed_disagreement=("disagreement", "median"),
              mean_absolute_disagreement=("disagreement", lambda s: s.abs().mean()),
              mean_ai_confidence=("ai_confidence", "mean"),
              high_disagreement_rate=("high_disagreement", "mean"),
          )
          .reset_index()
    )


# ---------- models ----------
def train_ridge(df: pd.DataFrame, feature_map, routing_col) -> Dict:
    X, feature_cols = build_routed_features(df, feature_map, routing_col)
    y = df["disagreement"].to_numpy()

    model = Pipeline([
        ("preprocess", make_preprocessor(feature_cols)),
        ("regressor",  Ridge(alpha=RIDGE_ALPHA)),
    ])
    cv = RepeatedKFold(n_splits=N_SPLITS, n_repeats=N_REPEATS,
                       random_state=RANDOM_STATE)
    scoring = {"neg_mae": "neg_mean_absolute_error", "r2": "r2"}
    cv_res = cross_validate(model, X, y, cv=cv, scoring=scoring,
                            return_train_score=True, n_jobs=-1)

    model.fit(X, y)
    ridge = model.named_steps["regressor"]
    coef_df = pd.DataFrame({
        "feature": feature_cols,
        "standardized_coef": ridge.coef_,
    }).sort_values("standardized_coef",
                   key=lambda s: s.abs(), ascending=False)

    return {
        "feature_cols": feature_cols,
        "coef_df": coef_df,
        "cv_mae_test_mean":  -cv_res["test_neg_mae"].mean(),
        "cv_mae_test_std":    cv_res["test_neg_mae"].std(),
        "cv_mae_train_mean": -cv_res["train_neg_mae"].mean(),
        "cv_r2_test_mean":    cv_res["test_r2"].mean(),
        "cv_r2_test_std":     cv_res["test_r2"].std(),
        "cv_r2_train_mean":   cv_res["train_r2"].mean(),
    }


def train_logreg(df: pd.DataFrame, feature_map, routing_col) -> Dict:
    X, feature_cols = build_routed_features(df, feature_map, routing_col)
    y = df["high_disagreement"].to_numpy()
    if len(np.unique(y)) < 2:
        return {"note": "Only one class present in high_disagreement; skipping."}

    model = Pipeline([
        ("preprocess", make_preprocessor(feature_cols)),
        ("classifier", LogisticRegression(max_iter=1000,
                                          class_weight="balanced")),
    ])
    cv = RepeatedStratifiedKFold(n_splits=N_SPLITS, n_repeats=N_REPEATS,
                                 random_state=RANDOM_STATE)
    scoring = {"accuracy": "accuracy", "roc_auc": "roc_auc"}
    cv_res = cross_validate(model, X, y, cv=cv, scoring=scoring,
                            return_train_score=False, n_jobs=-1)

    single_cv = RepeatedStratifiedKFold(n_splits=N_SPLITS, n_repeats=1,
                                        random_state=RANDOM_STATE)
    oof_preds = cross_val_predict(model, X, y, cv=single_cv, n_jobs=-1)

    model.fit(X, y)
    logreg = model.named_steps["classifier"]
    coef_df = pd.DataFrame({
        "feature": feature_cols,
        "standardized_coef": logreg.coef_[0],
    }).sort_values("standardized_coef",
                   key=lambda s: s.abs(), ascending=False)

    return {
        "feature_cols": feature_cols,
        "coef_df": coef_df,
        "cv_accuracy_mean": cv_res["test_accuracy"].mean(),
        "cv_accuracy_std":  cv_res["test_accuracy"].std(),
        "cv_roc_auc_mean":  cv_res["test_roc_auc"].mean(),
        "cv_roc_auc_std":   cv_res["test_roc_auc"].std(),
        "classification_report_oof": classification_report(y, oof_preds, zero_division=0),
        "confusion_matrix_oof":      confusion_matrix(y, oof_preds),
    }


# ---------- pipeline ----------
def prepare_dataset(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    if "disagreement" not in df.columns:
        df["disagreement"] = df["human_score_norm"] - df["ai_score_norm"]
    before = len(df)
    df = df.dropna(subset=["disagreement"]).copy()
    df["high_disagreement"] = (df["disagreement"].abs()
                                 >= HIGH_DISAGREEMENT_THRESHOLD).astype(int)
    print(f"Loaded {before} rows; {len(df)} usable matched pairs "
          f"({before - len(df)} dropped for missing scores).")
    print(df["modality"].value_counts().to_string())
    if "rubric_type" in df.columns:
        print("\nrubric_type counts:")
        print(df["rubric_type"].value_counts().to_string())
    print(f"\nHigh-disagreement rate: {df['high_disagreement'].mean():.3f}")
    return df


def run_variant(df: pd.DataFrame, variant_key: str, out_dir: Path) -> None:
    cfg = VARIANTS[variant_key]
    print(f"\n{'=' * 70}")
    print(f"Running {cfg['label']}")
    print('=' * 70)

    if cfg["routing_col"] not in df.columns:
        raise KeyError(f"Required column '{cfg['routing_col']}' not in CSV")

    print(f"\n=== Descriptive summary by {cfg['routing_col']} ===")
    desc = descriptive_summary(df, cfg["routing_col"])
    print(desc.to_string(index=False))
    desc.to_csv(out_dir / f"disagreement_summary_{cfg['summary_suffix']}.csv",
                index=False)

    print(f"\n=== Ridge regression on signed disagreement "
          f"(RepeatedKFold {N_SPLITS}x{N_REPEATS}) ===")
    ridge = train_ridge(df, cfg["feature_map"], cfg["routing_col"])
    for k in ("cv_mae_test_mean", "cv_mae_test_std", "cv_mae_train_mean",
              "cv_r2_test_mean",  "cv_r2_test_std",  "cv_r2_train_mean"):
        print(f"  {k}: {ridge[k]:+.4f}")
    print("\nRidge coefficients (positive => feature pushes prediction toward human>AI):")
    print(ridge["coef_df"].to_string(index=False))
    ridge["coef_df"].to_csv(out_dir / f"ridge_coefficients{cfg['output_suffix']}.csv",
                            index=False)

    print(f"\n=== Logistic regression on high-disagreement flag "
          f"(RepeatedStratifiedKFold {N_SPLITS}x{N_REPEATS}) ===")
    logreg = train_logreg(df, cfg["feature_map"], cfg["routing_col"])
    if "note" in logreg:
        print(f"  {logreg['note']}")
    else:
        for k in ("cv_accuracy_mean", "cv_accuracy_std",
                  "cv_roc_auc_mean",  "cv_roc_auc_std"):
            print(f"  {k}: {logreg[k]:.4f}")
        print("\n  Out-of-fold classification report:")
        print(logreg["classification_report_oof"])
        print("  Out-of-fold confusion matrix [[TN, FP], [FN, TP]]:")
        print(logreg["confusion_matrix_oof"])
        print("\nLogistic regression coefficients (positive => raises P(high disagreement)):")
        print(logreg["coef_df"].to_string(index=False))
        logreg["coef_df"].to_csv(
            out_dir / f"logreg_coefficients{cfg['output_suffix']}.csv", index=False)

    rows = [
        {"model": "ridge",    "metric": "mae_test",   "value": ridge["cv_mae_test_mean"],   "std": ridge["cv_mae_test_std"]},
        {"model": "ridge",    "metric": "mae_train",  "value": ridge["cv_mae_train_mean"],  "std": float("nan")},
        {"model": "ridge",    "metric": "r2_test",    "value": ridge["cv_r2_test_mean"],    "std": ridge["cv_r2_test_std"]},
        {"model": "ridge",    "metric": "r2_train",   "value": ridge["cv_r2_train_mean"],   "std": float("nan")},
    ]
    if "note" not in logreg:
        rows.extend([
            {"model": "logistic", "metric": "accuracy_test", "value": logreg["cv_accuracy_mean"], "std": logreg["cv_accuracy_std"]},
            {"model": "logistic", "metric": "roc_auc_test",  "value": logreg["cv_roc_auc_mean"],  "std": logreg["cv_roc_auc_std"]},
        ])
    pd.DataFrame(rows).to_csv(
        out_dir / f"regression_cv_metrics{cfg['output_suffix']}.csv", index=False)


def run(input_csv: Path, out_dir: Path, variant: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    df = prepare_dataset(input_csv)
    if variant == "both":
        run_variant(df, "modality",    out_dir)
        run_variant(df, "rubric_type", out_dir)
    else:
        run_variant(df, variant, out_dir)
    print(f"\nWrote outputs to {out_dir}/")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Run disagreement regression analysis.",
        epilog="See module docstring for full feature-map details.",
    )
    parser.add_argument("input_csv", type=Path,
                        help="Path to features CSV.")
    parser.add_argument("output_dir", type=Path, nargs="?", default=None,
                        help="Where to write outputs (default: input CSV's folder).")
    parser.add_argument("--variant", choices=["modality", "rubric_type", "both"],
                        default="modality",
                        help="Which model variant to run (default: modality).")
    args = parser.parse_args(argv[1:])

    input_csv = args.input_csv.expanduser().resolve()
    if not input_csv.exists():
        print(f"ERROR: input file not found: {input_csv}", file=sys.stderr)
        return 1
    out_dir = (args.output_dir.expanduser().resolve()
               if args.output_dir else input_csv.parent)

    run(input_csv, out_dir, args.variant)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
