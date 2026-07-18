Make sure you are in the **`AGT_platform/backend`** directory for local Python commands (migrations, `app.main`).

1. Install backend dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. If you are using the autograder and not an MIT-affiliated course, consult your school’s IT docs for “OpenID Connect discovery URL” / “Issuer” / “OIDC”, then set **`OIDC_DISCOVERY_URL`** appropriately.

3. **Database migrations (Alembic)** — this repo **already** has `alembic/` and `alembic.ini`. Do **not** run `alembic init` again.

   From **`AGT_platform/backend/`** (so `alembic.ini` is found), with **`DATABASE_URL`** set:

   ```bash
   python -m alembic heads          # should show a single head (e.g. d4e5f6a7b8c9)
   python -m alembic upgrade head
   ```

   Or from the repo root:

   ```bash
   python -m alembic -c AGT_platform/backend/alembic.ini upgrade head
   ```

   With Docker, run migrations **inside** the backend container (see root **`README.md`**).

4. Run the API. The backend is a **FastAPI** app served by **uvicorn** (previously Flask):

   ```bash
   # Dev (auto-reload):
   python -m app.main
   # or, equivalently, explicit uvicorn invocation (recommended for prod-like runs):
   uvicorn app.main:app --host 0.0.0.0 --port 5000
   ```

5. Access the backend locally at the host/port your environment configures (see app defaults and `.env`). Interactive API docs are auto-generated at `/docs` (Swagger UI) and `/redoc`.

6. Run the Celery worker for grading tasks (course / standalone / assignment-upload all share the `gpu` queue):

   ```bash
   celery -A app.tasks worker -Q gpu -l info
   ```

   Worker process concurrency defaults to `CELERY_WORKER_CONCURRENCY` (3) — see `app/config.py`. Per-task LLM call concurrency (how many OpenAI calls one grading run has in flight at once) is separately controlled by `MULTIMODAL_LLM_CALL_CONCURRENCY` (also 3 by default); size both together against your OpenAI rate limit.

**Adding a new migration** (after model changes), from **`backend/`**:

```bash
python -m alembic revision --autogenerate -m "describe_change"
python -m alembic upgrade head
```

Review generated SQL carefully; autogenerate is not always complete.

## Meta llama-models (Llama 4 / Maverick) downloads

Official download instructions: [meta-llama/llama-models — Download](https://github.com/meta-llama/llama-models?tab=readme-ov-file#download).

**Two different CLIs**

- **`llama-model`** (from the `llama-models` PyPI package): `llama-model list`, `llama-model download`, `llama-model verify-download`. This is what the README refers to.
- **`llama`** from **Llama Stack** only supports `llama stack …`. It is **not** the model downloader; `llama model …` will fail with “invalid choice”.

**What your logs usually mean**

1. **`llama download: No module named 'llama_models.cli.model'`** — packaging/import bug in some `llama-models` wheels (wrong import path). Upgrade the package or align with the version pinned in Meta’s repo; the downloader lives under `llama_models`’s CLI, not under `llama stack`.
2. **`peer closed connection … (received N bytes, expected …)`** — the transfer was truncated; any later “100%” per file can still leave **corrupt shards**.
3. **`Not enough disk space. Required: ~454000 MB`** — full Maverick FP8 needs on the order of **hundreds of GB** free for download and extraction; a machine with ~258 GB free cannot complete a full re-download in the default layout.
4. **`llama-model verify-download` → hash mismatch on every file** — on-disk files do **not** match the manifest (incomplete download, bad resume, or wrong tree). Treat the checkpoint directory as **invalid**.

**Recover a bad checkpoint**

Remove the broken tree (example path from a successful-but-unverified run):

```bash
rm -rf ~/.llama/checkpoints/Llama-4-Maverick-17B-128E-Instruct-fp8
```

Then re-download only when you have **enough free space**, a **stable network**, and a **fresh signed URL** from [llama.com downloads](https://www.llama.com/llama-downloads/) if using `--source meta`. Alternatively use **`--source huggingface`** with a Hugging Face token if the HF path fits your disk and access.

**Hugging Face for multimodal structure / grading**

Use local **transformers** for structure + grading where configured; **RAG vectors** use **SentenceTransformers** by default (`all-MiniLM-L6-v2` via `requirements.txt`).

1. `pip install -r requirements.txt -r requirements-huggingface.txt`
2. Set **`MULTIMODAL_LLM_BACKEND=huggingface`** (alias: **`hf`**).
3. **`HUGGINGFACE_HUB_TOKEN`** or **`HF_TOKEN`** with access to the gated repo.
4. **`HUGGINGFACE_GRADING_MODEL_ID`** — optional; defaults to **`meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8`**. The Meta **llama-model** id `Llama-4-Maverick-17B-128E-Instruct:fp8` is accepted and mapped to that repo.
5. **`RAG_EMBEDDING_BACKEND`** / **`SENTENCE_TRANSFORMERS_MODEL`** — defaults match the multimodal pipeline.

Per-chunk multimodal **grading** uses **OpenAI** when **`OPENAI_API_KEY`** is set (`OPENAI_MULTIMODAL_GRADING_MODEL`). Optional extra graders **`GRADING_MODEL_2`** / **`GRADING_MODEL_3`** must be **`openai:…`** specs or bare OpenAI model ids.

**Integration test env** (`tests/test_multimodal_pipeline.py::LocalAssignmentGradingTests`): defaults to **`MULTIMODAL_INTEGRATION_LLM_BACKEND=huggingface`** (Maverick FP8 on the Hub + `HF_TOKEN`). Run **`pytest -rs`** for full skip text and **`--log-cli-level=WARNING`** for `[integration]` phase logs.

## LLM triplet chunking (blank + student + answer key)

For notebook submissions, you can use a **single structured LLM call** that reads the instructor **blank** (from `blank_assignments/`), the **student** `.ipynb`, and the resolved **answer key** text, and emits `units` with `question` / `student_response` / `answer_key_segment` (same JSON contract as OpenAI trio frontload). The multimodal pipeline then runs answer-key enrichment, **RAG embeddings** on each trio (unless OpenAI frontload already embedded), and writes `{assignment_id}_trio_chunks.json` under `RAG_embedding/` as usual.

- **`MULTIMODAL_LLM_TRIPLET_THREE_SOURCE=on`** — enable this path (default: off). Requires a resolved blank notebook in `modality_hints`, non-empty `answer_key_plaintext`, and **`OPENAI_API_KEY`** (recommended; uses `OPENAI_TRIO_RAG_CHAT_MODEL`) or a working structure LLM from `MULTIMODAL_LLM_BACKEND`.
- **`MULTIMODAL_LLM_TRIPLET_MAX_CHARS_PER_SOURCE`** — max characters per source (blank / student / key) sent to the model before truncation (default **1000000**). Provider context limits still apply.
- **`MULTIMODAL_LLM_TRIPLET_THREE_SOURCE_PREFER_OPENAI=0`** — force the structure client (HF/OpenAI per backend) instead of OpenAI JSON chat when a key exists.
- **`MULTIMODAL_TRIO_EMBED_NO_CAPS=1`** — when using SentenceTransformers (or non–OpenAI-frontload) RAG, embed full trio strings without the usual per-field character caps (watch memory on huge cells).

When triplet mode is on **and** blank + answer key are present, **OpenAI trio+RAG frontload** is skipped so this path owns chunk boundaries.

## Multimodal grading pipeline (local)

These flows use fixtures next to the repo root: `assignments_to_grade/`, `rubric/`, and (when present) `answer_key/`. Configure models and keys in **`AGT_platform/backend/.env`** (at minimum **`OPENAI_API_KEY`** for per-chunk multimodal grading; optional keys for embeddings, Whisper, and so on match `app/config.py`).

Work from **`AGT_platform/backend/`** so `pytest` picks up `tests/` and `app` on the path the same way CI does.

### Single pipeline run

Runs the full multimodal pipeline once per selected assignment stem, writes `grading_output/<stem>_grade_output.json`, and updates `RAG_embedding/` exports for that run.

```bash
cd AGT_platform/backend
pytest tests/test_grading_pipeline_local_files.py::TestGradingPipelineLocalFiles::test_grade_local_assignments_write_json -v
```

Useful overrides (see the test module docstring in `tests/test_grading_pipeline_local_files.py` for full detail):

- **`MULTIMODAL_LOCAL_TEST_MAX_ASSIGNMENTS`** — default **1** (first basename only). Use **`0`** or **`all`** to grade every stem under `assignments_to_grade/`.
- **`MULTIMODAL_LOCAL_TEST_GRADING_SAMPLES`** — default **1** (overrides **`MULTIMODAL_SAMPLES_PER_MODEL`** for this test). Use **`from_config`** to honor `.env`, or an integer **1–16**.
- **`MULTIMODAL_LOCAL_TEST_MAX_GRADING_UNITS`** — default **8** chunk cap per assignment; **`0`** / **`all`** removes the cap.
- **`SKIP_LOCAL_LLM_TESTS=1`** — skips the test so CI or laptops without API access do not call providers.

### Multiple runs (research harness, e.g. 15 runs)

The opt-in test **`tests/test_multimodal_research_runs.py`** repeats the multimodal pipeline for variance or evaluation. It is **skipped** unless **`MULTIMODAL_RESEARCH_ASSIGNMENT_ID`** is set, so normal `pytest` does not issue many paid calls.

**15 runs** for one assignment (stem = basename under `assignments_to_grade/`, without the file extension):

```bash
cd AGT_platform/backend
MULTIMODAL_RESEARCH_ASSIGNMENT_ID='[Student 1] Week7_Pset7' \
MULTIMODAL_RESEARCH_RUN_COUNT=15 \
pytest tests/test_multimodal_research_runs.py -v -rs
```

- Stems that start with **`[`** should be passed in **single quotes** (as above) or included in a **JSON array** if you grade several assignments in one go.
- **`MULTIMODAL_RESEARCH_RUN_COUNT`** defaults to **30** when unset; maximum **100**.
- **Resume (default on):** if `research analysis/<sanitized_stem>_run_01` … `_run_15/` already exist, only missing runs are executed and the CSV is rebuilt. Set **`MULTIMODAL_RESEARCH_RESUME=0`** (or `false` / `off`) to re-run all **N** passes from scratch.

**Outputs** (under repo root `research analysis/`):

- `research analysis/<sanitized_stem>_run_<NN>/grade_output.json` per run
- `research analysis/<sanitized_stem>_research_scores.csv` — one row per question per run

**Multiple assignments** (15 runs each, processed in list order):

```bash
MULTIMODAL_RESEARCH_ASSIGNMENT_ID='["[Student 1] Week6_pset6.2","[Student 2] Week6_pset6.2"]' \
MULTIMODAL_RESEARCH_RUN_COUNT=15 \
pytest tests/test_multimodal_research_runs.py -v -rs
```

Optional research-only toggles (**`MULTIMODAL_RESEARCH_USE_CHUNK_CACHE`**, **`MULTIMODAL_RESEARCH_FAST`**, and others) are documented in the module docstring at the top of `tests/test_multimodal_research_runs.py`.
