# Multiagent Autograder

# Setting up the environment 
## Make a virtual environment
python3 -m venv .venv

## Install dependencies
```bash
cd AGT_platform/backend
pip install -r requirements.txt
```
# Running the Multimodal grading pipeline [Research Run ==> running the multimodal grading pipeline n times for a specific assignment]
### Backend tests

<!-- In order to run the multimodal grading pipeline you need to specific which assignment you want to grade and the number
of times you want to run the multimodal grading pipeline -->
```bash
cd AGT_platform/backend
export MULTIMODAL_RESEARCH_ASSIGNMENT_ID='[Student 1] Week7_JournalEntry7.3'
export MULTIMODAL_RESEARCH_RUN_COUNT=5
pytest tests/test_multimodal_research_runs.py -v # when you want to see what test is being run, see terminal output
```

<!-- In order to run all the test run [showing terminal output]: -->
``` bash
cd AGT_platform/backend
export MULTIMODAL_RESEARCH_ASSIGNMENT_ID='[Student 1] Week7_JournalEntry7.3'
export MULTIMODAL_RESEARCH_RUN_COUNT=5
pytest test/ -v 
```
<!-- when you want to hide the terminal output replace -v with -q -->


### Multimodal pipeline (single run vs repeated research runs)

- **One full local multimodal grade** (writes under `grading_output/` and `RAG_embedding/`): from `AGT_platform/backend/`, run  
  `pytest tests/test_grading_pipeline_local_files.py::TestGradingPipelineLocalFiles::test_grade_local_assignments_write_json -v`  
  Requires `assignments_to_grade/`, `rubric/`, and `OPENAI_API_KEY` in `.env`. Optional env vars are listed in that test’s docstring.

- **Many repeated runs** (e.g. **15**), for analysis under `research analysis/`: set **`MULTIMODAL_RESEARCH_ASSIGNMENT_ID`** to the assignment stem and **`MULTIMODAL_RESEARCH_RUN_COUNT=15`**, then run **`pytest tests/test_multimodal_research_runs.py -v -rs`** from `AGT_platform/backend/`. Full examples, resume behavior, and outputs are documented in **`AGT_platform/backend/ReadMe.md`** (section *Multimodal grading pipeline (local)*).

