# ai-automatic-grader-tool

Monorepo for the **AGT** grading platform. The production API, Celery workers, and **multimodal grading pipeline** live under `AGT_platform/backend/` (Python package `app`).

## Backend tests

From the repo root (with dev dependencies installed for the backend):

```bash
cd AGT_platform/backend
pytest tests/ -q
```

Or using root `pyproject.toml` paths:

```bash
pytest -q
```

### Multimodal pipeline (single run vs repeated research runs)

- **One full local multimodal grade** (writes under `grading_output/` and `RAG_embedding/`): from `AGT_platform/backend/`, run  
  `pytest tests/test_grading_pipeline_local_files.py::TestGradingPipelineLocalFiles::test_grade_local_assignments_write_json -v`  
  Requires `assignments_to_grade/`, `rubric/`, and `OPENAI_API_KEY` in `.env`. Optional env vars are listed in that test’s docstring.

- **Many repeated runs** (e.g. **15**), for analysis under `research analysis/`: set **`MULTIMODAL_RESEARCH_ASSIGNMENT_ID`** to the assignment stem and **`MULTIMODAL_RESEARCH_RUN_COUNT=15`**, then run **`pytest tests/test_multimodal_research_runs.py -v -rs`** from `AGT_platform/backend/`. Full examples, resume behavior, and outputs are documented in **`AGT_platform/backend/ReadMe.md`** (section *Multimodal grading pipeline (local)*).

## Layout (high level)

- `AGT_platform/backend/` — Flask app, SQLAlchemy models, Celery tasks, `app/grading/multimodal/`
- `AGT_platform/frontend/` — web UI
- `assignments_to_grade/`, `rubric/`, `answer_key/` — optional local fixtures for integration-style runs (see backend test docstrings)

The former standalone `assignment-parser` library and `specs/` example bundles were removed; they were not imported by the multimodal pipeline.
