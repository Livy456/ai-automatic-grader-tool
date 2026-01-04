import io, json, subprocess, tempfile, os
import nbformat
from pypdf import PdfReader

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()

def extract_from_ipynb(ipynb_bytes: bytes) -> dict:
    nb = nbformat.reads(ipynb_bytes.decode("utf-8"), as_version=4)
    code, md = [], []
    for cell in nb.cells:
        if cell.cell_type == "code":
            code.append(cell.source)
        elif cell.cell_type == "markdown":
            md.append(cell.source)
    return {"code":"\n\n".join(code), "markdown":"\n\n".join(md)}

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
    # Wire to Whisper later (faster-whisper / whisper.cpp)
    return "[TRANSCRIPTION_DISABLED_IN_MVP]"
