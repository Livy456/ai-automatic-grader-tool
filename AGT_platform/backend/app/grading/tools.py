import io
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import nbformat
from pypdf import PdfReader

_log = logging.getLogger(__name__)


def normalize_verticalized_pdf_text(text: str) -> str:
    """
    Reflow PDF text where extractors emit **one token per line** (common with
    pypdf layout / some Google Docs exports).

    Joins tokens into readable lines and inserts paragraph breaks on sentence
    boundaries so downstream line-based chunkers can pair prompts with answers.
    """
    stripped = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if len(stripped) < 12:
        return text
    avg_len = sum(len(s) for s in stripped) / len(stripped)
    # Normal prose PDFs: most lines are full-width sentences.
    if avg_len > 52:
        return text

    words: list[str] = []
    for ln in stripped:
        words.extend(ln.split())
    if len(words) < 20:
        return text

    # Continuations of the same instructor prompt after an internal "?" (journal rubrics).
    _Q_CONTINUATION = frozenset(
        {"if", "which", "or", "and", "then", "else", "also", "what", "how", "why", "when", "where"}
    )

    out_paras: list[str] = []
    cur: list[str] = []
    for i, w in enumerate(words):
        wl = w.lower().rstrip(".,;:!?")
        if (
            wl == "homework"
            and i + 1 < len(words)
            and words[i + 1].split(".", 1)[0].strip().isdigit()
            and cur
            and len(cur) >= 10
        ):
            out_paras.append(" ".join(cur))
            cur = []
        cur.append(w)
        if w.endswith("?") and len(cur) >= 6:
            nxt = words[i + 1] if i + 1 < len(words) else ""
            first = (nxt.split("-", 1)[0].strip("(\"'").lower() if nxt else "")
            if first in _Q_CONTINUATION:
                continue
            out_paras.append(" ".join(cur))
            cur = []
        elif w.endswith(".") and len(cur) >= 14:
            out_paras.append(" ".join(cur))
            cur = []
    if cur:
        out_paras.append(" ".join(cur))
    return "\n\n".join(out_paras)


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extract text from PDF bytes. Tries default extraction, then layout mode when output is tiny
    (common with some LaTeX / scan-like PDFs in pypdf).
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    parts: list[str] = []
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if len(text) < 30:
            try:
                alt = page.extract_text(extraction_mode="layout")  # type: ignore[call-arg]
            except TypeError:
                alt = ""
            if isinstance(alt, str) and len(alt.strip()) > len(text):
                text = alt.strip()
        parts.append(text)
    joined = "\n\n".join(p for p in parts if p).strip()
    return normalize_verticalized_pdf_text(joined)

def extract_from_ipynb(ipynb_bytes: bytes) -> dict:
    nb = nbformat.reads(ipynb_bytes.decode("utf-8"), as_version=4)
    code, md = [], []
    for cell in nb.cells:
        if cell.cell_type == "code":
            code.append(cell.source)
        elif cell.cell_type == "markdown":
            md.append(cell.source)
    return {"code":"\n\n".join(code), "markdown":"\n\n".join(md)}


def _summarize_output(output) -> str:
    """Short text summary of one nbformat output object."""
    try:
        if output.output_type == "stream":
            t = "".join(output.text or "")
            return (t[:2000] + "…") if len(t) > 2000 else t
        if output.output_type in ("display_data", "execute_result"):
            data = getattr(output, "data", {}) or {}
            if "text/plain" in data:
                t = str(data["text/plain"])
                return (t[:2000] + "…") if len(t) > 2000 else t
            return f"[{output.output_type}]"
        if output.output_type == "error":
            return f"ERROR {getattr(output, 'ename', '')}: {getattr(output, 'evalue', '')}"
    except Exception:
        return "[output parse error]"
    return ""


def extract_notebook_cells_structured(ipynb_bytes: bytes) -> list[dict]:
    """
    Deterministic per-cell structure for staged normalization (cell index order).
    """
    nb = nbformat.reads(ipynb_bytes.decode("utf-8"), as_version=4)
    cells_out: list[dict] = []
    for idx, cell in enumerate(nb.cells):
        entry: dict = {
            "index": idx,
            "cell_type": cell.cell_type,
            "source": (cell.source or "")[:8000],
        }
        if cell.cell_type == "code":
            outs = []
            errs = []
            for o in getattr(cell, "outputs", []) or []:
                s = _summarize_output(o)
                if s:
                    outs.append(s)
                if getattr(o, "output_type", "") == "error":
                    errs.append(s)
            entry["outputs_summary"] = outs[:20]
            entry["runtime_errors"] = errs
            entry["execution_count"] = getattr(cell, "execution_count", None)
        cells_out.append(entry)
    return cells_out

def run_python_tests(zip_or_py_bytes: bytes, filename_hint: str = "submission.py") -> dict:
    """
    MVP sandbox: writes file then runs pytest or a provided test runner.
    Upgrade later to Docker sandbox with no network + strict limits.
    """
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, filename_hint)
        with open(path, "wb") as f:
            f.write(zip_or_py_bytes)

        # Minimal: just run python -m py_compile
        try:
            subprocess.run(
                ["python", "-m", "py_compile", path],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
            )
            return {"ok": True, "tests":"py_compile", "stderr":"", "stdout":""}
        except subprocess.CalledProcessError as e:
            return {"ok": False, "tests":"py_compile", "stderr":e.stderr.decode(), "stdout":e.stdout.decode()}
        except subprocess.TimeoutExpired:
            return {"ok": False, "tests":"py_compile", "stderr":"timeout", "stdout":""}

def transcribe_video_stub(video_bytes: bytes) -> str:
    """Placeholder when Whisper is off or unavailable (length hint only)."""
    n = len(video_bytes or b"")
    return f"[TRANSCRIPTION_DISABLED_OR_UNAVAILABLE] ({n} bytes)"


_WHISPER_SAFE_SUFFIXES = frozenset(
    {".flac", ".m4a", ".mp3", ".mp4", ".mpeg", ".mpga", ".ogg", ".wav", ".webm"}
)


def transcribe_submission_media_bytes(
    data: bytes,
    *,
    filename: str = "submission.m4a",
) -> str:
    """
    One-shot transcript for parsing/chunking (OpenAI Whisper).

    Controlled by ``Config.MULTIMODAL_WHISPER_TRANSCRIBE`` (``off`` / ``on`` / ``auto``).
    ``auto`` calls Whisper only when ``OPENAI_API_KEY`` is set. Large files may exceed the
    API size limit (~25 MB).
    """
    if not data:
        return ""
    try:
        from app.config import Config

        mode = str(getattr(Config, "MULTIMODAL_WHISPER_TRANSCRIBE", "auto") or "auto").lower()
        model = str(getattr(Config, "OPENAI_WHISPER_MODEL", "") or "").strip() or "whisper-1"
    except Exception:
        mode, model = "auto", "whisper-1"

    if mode == "off":
        return transcribe_video_stub(data)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    want = mode == "on" or (mode == "auto" and bool(api_key))
    if not want:
        return transcribe_video_stub(data)
    if not api_key:
        _log.warning(
            "MULTIMODAL_WHISPER_TRANSCRIBE=%s but OPENAI_API_KEY is empty; skipping Whisper",
            mode,
        )
        return transcribe_video_stub(data)

    max_bytes = 24 * 1024 * 1024
    if len(data) > max_bytes:
        _log.warning(
            "submission media exceeds ~24MB Whisper limit (%s bytes); not transcribed",
            len(data),
        )
        return (
            "[AUDIO_TOO_LARGE_FOR_WHISPER_API]\n"
            "Compress or split the file; limit is about 25 MB."
        )

    suf = Path(filename).suffix.lower()
    if suf not in _WHISPER_SAFE_SUFFIXES:
        suf = ".m4a"

    try:
        from openai import OpenAI
    except ImportError:
        _log.warning("openai package not installed; cannot call Whisper")
        return transcribe_video_stub(data)

    try:
        client = OpenAI(api_key=api_key)
        with tempfile.NamedTemporaryFile(suffix=suf, delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            tmp.seek(0)
            resp = client.audio.transcriptions.create(model=model, file=tmp)
        text = str(getattr(resp, "text", "") or "").strip()
        if not text:
            return "[WHISPER_EMPTY_TRANSCRIPT]"
        return text
    except Exception as exc:
        _log.warning("OpenAI Whisper transcription failed: %s", exc, exc_info=True)
        return f"[WHISPER_TRANSCRIPTION_FAILED: {type(exc).__name__}]"
