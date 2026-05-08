# Answer keys (optional)

Place a **sample response** or **answer key** under this directory.

## Exact match (fastest)

`<Assignment Stem>.txt`, `.md`, `.json`, or `.ipynb` — same stem as the submission file basename (e.g. matches `[Student 1] Week 1 PSet Part 1.ipynb` → `[Student 1] Week 1 PSet Part 1.txt`).

## Fuzzy match

If no exact file exists, the resolver picks the best file by normalized string similarity (`difflib`), ignoring bracket tags like `[Student N]` and `[Answer_Key]` so names such as:

- Submission: `[Student 1] Week 1 PSet Part 1`
- Key file: `Week 1 PSet Part 1 [Answer_Key].ipynb`

can still match.

The student’s work does **not** need to match the key verbatim.

If nothing scores above the internal similarity threshold, grading runs without a key.
