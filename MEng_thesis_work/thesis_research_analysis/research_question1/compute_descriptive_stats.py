#!/usr/bin/env python3
"""
Compute descriptive AI-vs-human disagreement statistics from the
code-features-extracted dataset and save them to CSV.

For each modality (and optionally each rubric_type), reports:
  - n                       number of paired AI/human assignments
  - mean_D_score            mean of (AI - human)  -- sign convention:
                            negative => AI scored lower
  - median_D_score          median of (AI - human)
  - sd_D_score              standard deviation of the signed difference
  - mean_abs_D_score        mean of |AI - human|  (typical-gap magnitude,
                            independent of direction)
  - median_abs_D_score      median of |AI - human|
  - D_rate                  proportion with |AI - human| >= threshold
                            (default 0.10, i.e. one letter-grade band)
  - n_high_disagreement     numerator behind D_rate, useful for sanity checks
  - mean_ai_score / mean_human_score / mean_ai_confidence / mean_semantic_entropy
                            modality-level grading-pipeline summaries

USAGE
-----
    python compute_descriptive_stats.py <input_csv> [output_csv] \\
        [--group-by modality|rubric_type|both] [--threshold FLOAT] \\
        [--convention ai_minus_human|human_minus_ai]

EXAMPLES
    # default: group by modality, 0.10 threshold, AI - human convention
    python compute_descriptive_stats.py code_features_extracted_final.csv

    # group by both modality and rubric_type, write to a chosen path
    python compute_descriptive_stats.py data.csv results.csv --group-by both

    # use the methodology's original sign convention
    python compute_descriptive_stats.py data.csv --convention human_minus_ai

OUTPUT
------
A CSV with one row per group plus a POOLED row at the bottom. Columns are
listed in the order above. Values are written with 6-decimal precision so
they can be re-rounded for presentation without loss.

INPUT
-----
A CSV with these columns at minimum:
  student_id, assignment_id, modality, ai_score_norm, human_score_norm,
  ai_confidence, semantic_entropy
  (rubric_type is also required if --group-by includes 'rubric_type')

Rows missing either ai_score_norm or human_score_norm are dropped with a
warning; all other rows are included.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd


DEFAULT_THRESHOLD = 0.10
REQUIRED_COLUMNS = (
    "student_id", "assignment_id", "modality",
    "ai_score_norm", "human_score_norm",
)
# These columns are reported when present but not strictly required;
# missing ones simply produce NaN in the output without failing the run.
OPTIONAL_SUMMARY_COLUMNS = ("ai_confidence", "semantic_entropy")


def load_and_validate(csv_path: Path, group_by: List[str]) -> pd.DataFrame:
    """Load the CSV, validate required columns, drop rows missing scores."""
    df = pd.read_csv(csv_path)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if "rubric_type" in group_by and "rubric_type" not in df.columns:
        missing.append("rubric_type")
    if missing:
        raise ValueError(
            f"Input CSV missing required columns: {missing}\n"
            f"Available columns: {list(df.columns)}"
        )

    before = len(df)
    df = df.dropna(subset=["ai_score_norm", "human_score_norm"]).copy()
    dropped = before - len(df)
    if dropped:
        print(f"Note: dropped {dropped} row(s) with missing scores.")
    print(f"Loaded {len(df)} usable paired assignments from {csv_path.name}")
    return df


def compute_group_stats(df: pd.DataFrame, group_col: str | None,
                        threshold: float, convention: str) -> pd.DataFrame:
    """Compute one row of descriptive stats per value of `group_col`, or a
    single POOLED row if group_col is None."""
    if convention == "ai_minus_human":
        df = df.assign(D_score=df["ai_score_norm"] - df["human_score_norm"])
    elif convention == "human_minus_ai":
        df = df.assign(D_score=df["human_score_norm"] - df["ai_score_norm"])
    else:
        raise ValueError(f"Unknown convention: {convention!r}")
    df = df.assign(abs_D=df["D_score"].abs())
    df = df.assign(D_high=(df["abs_D"] >= threshold).astype(int))

    if group_col is None:
        # Pooled: treat the whole frame as one group
        df = df.assign(_group="POOLED")
        group_col = "_group"
        group_label_col = "group"
    else:
        group_label_col = group_col

    rows = []
    for value in sorted(df[group_col].dropna().unique()):
        sub = df[df[group_col] == value]
        row = {
            group_label_col: value,
            "n": int(len(sub)),
            "mean_D_score":      float(sub["D_score"].mean()),
            "median_D_score":    float(sub["D_score"].median()),
            "sd_D_score":        float(sub["D_score"].std(ddof=1)),
            "mean_abs_D_score":  float(sub["abs_D"].mean()),
            "median_abs_D_score": float(sub["abs_D"].median()),
            "D_rate": float(sub["D_high"].mean()),
            "n_high_disagreement": int(sub["D_high"].sum()),
            "mean_ai_score":     float(sub["ai_score_norm"].mean()),
            "mean_human_score":  float(sub["human_score_norm"].mean()),
        }
        for col in OPTIONAL_SUMMARY_COLUMNS:
            row[f"mean_{col}"] = (float(sub[col].mean())
                                  if col in sub.columns else float("nan"))
        rows.append(row)
    return pd.DataFrame(rows)


def build_output_table(df: pd.DataFrame, group_by: List[str],
                       threshold: float, convention: str) -> pd.DataFrame:
    """Concatenate per-group tables plus a final POOLED row."""
    pieces: List[pd.DataFrame] = []
    for col in group_by:
        piece = compute_group_stats(df, col, threshold, convention)
        piece.insert(0, "grouping", col)
        # Rename the per-piece label column to a stable name so the pieces
        # share schema. The actual group label lives in 'group_value'.
        label_col = col if col in piece.columns else "group"
        piece = piece.rename(columns={label_col: "group_value"})
        pieces.append(piece)

    pooled = compute_group_stats(df, None, threshold, convention)
    pooled.insert(0, "grouping", "pooled")
    pooled = pooled.rename(columns={"group": "group_value"})
    pieces.append(pooled)

    out = pd.concat(pieces, ignore_index=True)

    # Round numeric columns to 6 decimal places for presentation, leaving
    # full precision available in the raw frame for downstream re-use.
    float_cols = [c for c in out.columns if c not in
                  ("grouping", "group_value", "n", "n_high_disagreement")]
    out[float_cols] = out[float_cols].round(6)
    return out


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Compute descriptive disagreement statistics.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input_csv", type=Path,
                        help="Path to the features CSV.")
    parser.add_argument("output_csv", type=Path, nargs="?", default=None,
                        help="Where to write the stats CSV (default: "
                             "<input_dir>/descriptive_stats.csv).")
    parser.add_argument("--group-by",
                        choices=("modality", "rubric_type", "both"),
                        default="modality",
                        help="Grouping variable for the per-group rows "
                             "(default: modality). 'both' produces rows "
                             "for each modality AND each rubric_type.")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"Absolute-disagreement threshold for D_rate "
                             f"(default: {DEFAULT_THRESHOLD}).")
    parser.add_argument("--convention",
                        choices=("ai_minus_human", "human_minus_ai"),
                        default="ai_minus_human",
                        help="Sign convention for D_score. "
                             "'ai_minus_human' (default): negative means AI "
                             "scored lower. 'human_minus_ai': matches the "
                             "methodology's Equation 4.")
    args = parser.parse_args(argv[1:])

    input_csv = args.input_csv.expanduser().resolve()
    if not input_csv.exists():
        print(f"ERROR: input file not found: {input_csv}", file=sys.stderr)
        return 1

    output_csv = (args.output_csv.expanduser().resolve()
                  if args.output_csv
                  else input_csv.with_name("descriptive_stats.csv"))

    group_by = (["modality", "rubric_type"] if args.group_by == "both"
                else [args.group_by])

    df = load_and_validate(input_csv, group_by)
    out = build_output_table(df, group_by, args.threshold, args.convention)

    print(f"\n=== Descriptive disagreement stats "
          f"(D_score = {args.convention.replace('_', ' ')}, "
          f"threshold = {args.threshold}) ===\n")
    print(out.to_string(index=False))

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_csv, index=False)
    print(f"\nWrote {len(out)} rows to {output_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
