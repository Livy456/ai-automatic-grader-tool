"""
Original-form document view: renders an uploaded assignment material (blank template / answer
key) close to how the instructor authored it, instead of flattened plaintext — a Jupyter/Colab
style cell list for notebooks, a spreadsheet grid for CSV/XLSX, or a native embed for PDFs.

Used by the Assignment Creation review page's "Blank Assignment" / "Answer Key" tabs (see
``app.routes.assignment_library``). Falls back to the same plaintext extraction the rest of the
grading pipeline uses (:mod:`app.grading.parsing.artifact_plaintext`) for formats with no richer
"original form" rendering (docx, txt, md, py, ...), and to a download-only response when even
that yields nothing (binary/unsupported formats).
"""
from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any

from app.grading.parsing.artifact_plaintext import bytes_with_suffix_to_plain

_log = logging.getLogger(__name__)

# Defensive caps so one huge upload can't bloat a single JSON response.
_MAX_NOTEBOOK_CELLS = 300
_MAX_CELL_CHARS = 20_000
_MAX_SPREADSHEET_ROWS = 500
_MAX_SPREADSHEET_SHEETS = 10


def _suffix_from_filename(filename: str) -> str:
    name = (filename or "").strip()
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1].lower()


def _cell_source_to_text(source: Any) -> str:
    if isinstance(source, list):
        text = "".join(str(s) for s in source)
    else:
        text = str(source or "")
    if len(text) > _MAX_CELL_CHARS:
        return text[:_MAX_CELL_CHARS] + "\n...[truncated]"
    return text


def _parse_notebook_cells(data: bytes) -> list[dict[str, str]] | None:
    try:
        nb = json.loads(data.decode("utf-8", errors="replace"))
    except Exception:
        return None
    cells = nb.get("cells") if isinstance(nb, dict) else None
    if not isinstance(cells, list):
        return None
    out: list[dict[str, str]] = []
    for cell in cells[:_MAX_NOTEBOOK_CELLS]:
        if not isinstance(cell, dict):
            continue
        cell_type = str(cell.get("cell_type") or "code")
        out.append({"cell_type": cell_type, "source": _cell_source_to_text(cell.get("source"))})
    return out


def _parse_csv_sheets(data: bytes) -> list[dict[str, Any]]:
    text = data.decode("utf-8", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))[:_MAX_SPREADSHEET_ROWS]
    return [{"name": "Sheet1", "rows": rows}]


def _parse_xlsx_sheets(data: bytes) -> list[dict[str, Any]] | None:
    try:
        import openpyxl  # type: ignore[import-untyped]
    except ImportError:
        return None
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheets: list[dict[str, Any]] = []
        for sheet in wb.worksheets[:_MAX_SPREADSHEET_SHEETS]:
            rows = [
                ["" if c is None else str(c) for c in row]
                for row in list(sheet.iter_rows(values_only=True))[:_MAX_SPREADSHEET_ROWS]
            ]
            sheets.append({"name": sheet.title, "rows": rows})
        return sheets
    except Exception:
        _log.debug("original_view: openpyxl xlsx read failed", exc_info=True)
        return None


def build_original_view(data: bytes, filename: str) -> dict[str, Any]:
    """
    Best-effort "original form" view for ``data`` (the raw uploaded bytes), keyed by ``type``:

    - ``notebook``: ``{"cells": [{"cell_type", "source"}, ...]}`` — render as a Colab-style cell list.
    - ``spreadsheet``: ``{"sheets": [{"name", "rows": [[...]]}]}`` — render as a data grid.
    - ``pdf``: no payload; the caller embeds the file directly via its presigned download URL.
    - ``text``: ``{"text": "..."}`` — plaintext fallback (docx, txt, md, py, ...).
    - ``unsupported``: no richer view available; caller offers a download link only.
    """
    suffix = _suffix_from_filename(filename)

    if suffix == ".ipynb":
        cells = _parse_notebook_cells(data)
        if cells:
            return {"type": "notebook", "cells": cells}
    elif suffix == ".csv":
        return {"type": "spreadsheet", "sheets": _parse_csv_sheets(data)}
    elif suffix == ".xlsx":
        sheets = _parse_xlsx_sheets(data)
        if sheets:
            return {"type": "spreadsheet", "sheets": sheets}
    elif suffix == ".pdf":
        return {"type": "pdf"}

    text = bytes_with_suffix_to_plain(data, suffix)
    if text.strip():
        return {"type": "text", "text": text}
    return {"type": "unsupported"}
