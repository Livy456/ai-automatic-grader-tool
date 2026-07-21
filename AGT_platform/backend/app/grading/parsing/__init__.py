"""
Submission parsing: modality detection, artifact-bytes -> plaintext extraction, ingestion
envelope construction, and reference resolution (dataset / answer-key / blank-template file
matching) used before chunking.

Question/response **chunking** (structured, notebook-aware, LLM/Claude-assisted,
blank-template-aligned) has its own sibling package, :mod:`app.grading.chunking`, which builds
on the plaintext this package produces.
"""

from __future__ import annotations
