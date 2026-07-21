This walks through how to run the different experiments conducted for my MEng thesis.

### Run one multimodal grading pass (first-time clone)

Use this to exercise the **full multimodal pipeline** once on local fixtures (OpenAI per-chunk grading), before running the thesis scripts below. All paths are relative to the **repository root** (the folder that contains `AGT_platform/` and `thesis_research_analysis/`).

1. **Python and dependencies** — From the backend package directory:

   ```bash
   cd AGT_platform/backend
   pip install -r requirements.txt
   ```

   (Use a virtual environment if you prefer.)

2. **Fixture layout at repo root** — The integration test expects:

   - `assignments_to_grade/` — at least one supported submission (e.g. `.ipynb`, `.py`, `.pdf`; files with the same basename are grouped as one assignment).
   - `rubric/` — either the four `[Generic] …` JSON templates under `rubric/`, or legacy `rubric/default.json` / `generic.*` / `rubric.*`, or `rubric/<assignment_basename>.json`.
   - `answer_key/` — optional; if present, the best-matching key file is passed into grading hints. The run still works with no answer key (empty hints).

3. **Environment** — Per-chunk multimodal grading needs **`OPENAI_API_KEY`**. Copy or merge variables from the repo’s `env.example` into `AGT_platform/backend/.env` or export them in your shell. Do **not** set `SKIP_LOCAL_LLM_TESTS=1` for this test. Optional: `OPENAI_MULTIMODAL_GRADING_MODEL` and other multimodal vars documented in `AGT_platform/backend/tests/test_grading_pipeline_local_files.py`.

4. **Run the test** — Still from `AGT_platform/backend`:

   ```bash
   pytest tests/test_grading_pipeline_local_files.py::TestGradingPipelineLocalFiles::test_grade_local_assignments_write_json -v --log-cli-level=WARNING
   ```

   **Defaults keep the run short:** only the **first** assignment basename (`MULTIMODAL_LOCAL_TEST_MAX_ASSIGNMENTS=1`), **one** sample per model for grading (`MULTIMODAL_LOCAL_TEST_GRADING_SAMPLES`), and at most **8** grading units (`MULTIMODAL_LOCAL_TEST_MAX_GRADING_UNITS`). Set those env vars to `0` or `all` where documented to remove caps. If the test skips, run with `-rs` to print reasons.

5. **Outputs** — Check the repo root for `grading_output/<stem>_grade_output.json` and RAG artifacts under `RAG_embedding/`.

For a heavier integration that grades **every** assignment under `assignments_to_grade/` and enforces stricter gates (including a required answer key per stem), see `LocalAssignmentGradingTests` in `AGT_platform/backend/tests/test_multimodal_pipeline.py`. The **multi-run research harness** is `tests/test_multimodal_research_runs.py` (not a single pipeline pass).

### Research Question 1- Determines if there is AI and human score disagreement and if yes by how much?


python compute_descriptive_stats.py code_features_extracted_final.csv /des_stats

# Default — group by modality, AI - human convention, 0.10 threshold
python compute_descriptive_stats.py code_features_extracted_final.csv

# Specify output path
python compute_descriptive_stats.py data.csv ./stats.csv

# Group by modality AND rubric_type (splits Programming into scaffolded vs EDA)
python compute_descriptive_stats.py data.csv --group-by both

# Use the methodology Equation 4 convention instead
python compute_descriptive_stats.py data.csv --convention human_minus_ai

# Different consequential-disagreement threshold
python compute_descriptive_stats.py data.csv --threshold 0.15


### Research Question 2- Why does the AI and human scores discrepancy exist and what are the quanitfiable reasons?

# make sure you are in the correct directory
cd thesis_research_analysis

<!-- run the regresssion models- ridge / logistic (must input structured csv file with model features as 2nd argument); output is csv file -->
python run_disagreement_regression.py code_features_extracted_final.csv

<!-- or specify an output directory -->
python run_disagreement_regression.py code_features_extracted_final.csv ./results

<!-- help -->
python run_disagreement_regression.py --help

python run_disagreement_regression.py data.csv --variant modality     # V1 (default)
python run_disagreement_regression.py data.csv --variant rubric_type  # V2
python run_disagreement_regression.py data.csv --variant both         # both, side by side

### Research Question 3- How to mitigate the impact of AI and human discrepancy and review potentially harmful AI generated scores?

# Default — 0.10 disagreement threshold, 10,000 permutations
python run_se_disagreement_test.py code_features_extracted_final.csv

# Custom output path
python run_se_disagreement_test.py data.csv ./results/se_test.csv

# Different threshold (e.g. 15-point gap)
python run_se_disagreement_test.py data.csv --threshold 0.1

# More permutations for the stratified test
python run_se_disagreement_test.py data.csv --n-perm 50000

# Custom random seed
python run_se_disagreement_test.py data.csv --seed 12345

### Extracting Model Features


### Model Features

python run_wilcoxon_section_4_2.py code_features_extracted_final.csv
python run_disagreement_regression.py code_features_extracted_final.csv --variant modality