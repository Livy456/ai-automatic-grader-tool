"""
Optional **audio half-split** path for long single-file oral submissions.

``auto`` uses ``MULTIMODAL_AUDIO_HALF_SPLIT_AUTO_MIN_BYTES`` (default ~3 MiB), or the lower
``MULTIMODAL_AUDIO_HALF_SPLIT_AUTO_MIN_BYTES_ORAL`` when ``modality_hints["task_type"]`` is
``oral_interview``.

When ``MULTIMODAL_AUDIO_HALF_SPLIT`` is ``on`` or ``auto`` (and prerequisites match), the
pipeline:

1. Splits the audio bytes at the **midpoint duration** using ``ffmpeg`` / ``ffprobe``.
2. Transcribes each half with :func:`app.grading.parsing.tools.transcribe_submission_media_bytes`.
3. Embeds each half's transcript (same stack as :func:`app.grading.rag_embeddings.compute_submission_embedding`).
4. Replaces ``envelope.extracted_plaintext`` with a two-part transcript and stores
   ``modality_hints["audio_half_split"]`` so :func:`openai_trio_rag_frontload.run_openai_trio_rag_frontload`
   can run **two** trio extractions and blend each chunk's canonical OpenAI embedding with the
   corresponding half-transcript embedding.

Requires ``ffmpeg`` and ``ffprobe`` on ``PATH`` for splitting. If unavailable, the helper is a no-op.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.config import Config
from app.grading.rag_embeddings import _openai_embed_snippet, compute_submission_embedding
from app.grading.parsing.tools import transcribe_submission_media_bytes

from .ingestion import IngestionEnvelope

_log = logging.getLogger(__name__)

# Artifact keys treated as standalone **audio** for half-split (single-artifact submissions only).
_AUDIO_ONLY_KEYS = frozenset({"mp3", "wav", "m4a", "webm"})


def multimodal_audio_half_split_enabled(cfg: Config | None = None) -> bool:
    """True when half-split preprocessing may run (still requires single audio artifact + ffmpeg)."""
    if cfg is None:
        return False
    mode = str(getattr(cfg, "MULTIMODAL_AUDIO_HALF_SPLIT", "off") or "off").strip().lower()
    if mode == "on":
        return True
    if mode == "off":
        return False
    if not (cfg.OPENAI_API_KEY or "").strip():
        return False
    return mode == "auto"


def _artifact_suffix_for_whisper(artifact_key: str) -> str:
    k = (artifact_key or "").strip().lower()
    return f".{k}" if k else ".m4a"


def _ffmpeg_probe_duration_seconds(path: str) -> float | None:
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            timeout=120,
            check=False,
        )
        if r.returncode != 0:
            return None
        return float((r.stdout or b"").decode().strip() or 0.0)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return None


def _ffmpeg_split_duration_halves(
    data: bytes, *, suffix: str, work: Path
) -> tuple[Path, Path] | None:
    """Write ``half1`` and ``half2`` paths under ``work``; return paths or None."""
    suf = suffix if suffix.startswith(".") else f".{suffix}"
    inp = work / f"whole{suf}"
    h1 = work / f"half1{suf}"
    h2 = work / f"half2{suf}"
    inp.write_bytes(data)
    dur = _ffmpeg_probe_duration_seconds(str(inp))
    if dur is None or dur <= 1.0:
        return None
    mid = dur / 2.0
    # First half [0, mid)
    r1 = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-i",
            str(inp),
            "-t",
            str(mid),
            "-c",
            "copy",
            str(h1),
        ],
        capture_output=True,
        timeout=600,
        check=False,
    )
    if r1.returncode != 0 or not h1.is_file() or h1.stat().st_size < 32:
        r1b = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-i",
                str(inp),
                "-t",
                str(mid),
                "-c:a",
                "libmp3lame",
                "-q:a",
                "4",
                str(h1),
            ],
            capture_output=True,
            timeout=600,
            check=False,
        )
        if r1b.returncode != 0 or not h1.is_file():
            return None
    # Second half [mid, end]
    r2 = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-ss",
            str(mid),
            "-i",
            str(inp),
            "-c",
            "copy",
            str(h2),
        ],
        capture_output=True,
        timeout=600,
        check=False,
    )
    if r2.returncode != 0 or not h2.is_file() or h2.stat().st_size < 32:
        r2b = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-ss",
                str(mid),
                "-i",
                str(inp),
                "-c:a",
                "libmp3lame",
                "-q:a",
                "4",
                str(h2),
            ],
            capture_output=True,
            timeout=600,
            check=False,
        )
        if r2b.returncode != 0 or not h2.is_file():
            return None
    return h1, h2


def split_audio_bytes_into_duration_halves(data: bytes, *, suffix: str) -> tuple[bytes, bytes] | None:
    """Return ``(first_half_bytes, second_half_bytes)`` using ffmpeg, or ``None`` if unsupported."""
    if not data or len(data) < 4096:
        return None
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        _log.warning("audio_half_split: ffmpeg/ffprobe not on PATH; skipping split")
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="agt_audio_half_") as td:
            work = Path(td)
            paths = _ffmpeg_split_duration_halves(data, suffix=suffix, work=work)
            if paths is None:
                return None
            h1, h2 = paths
            return h1.read_bytes(), h2.read_bytes()
    except OSError as exc:
        _log.warning("audio_half_split: io failed (%s)", exc)
        return None


def _mean_embedding(
    a: list[float] | None, b: list[float] | None
) -> tuple[list[float], bool]:
    """Element-wise mean when lengths match; else prefer non-empty ``a`` then ``b``."""
    la = list(a or [])
    lb = list(b or [])
    if la and lb and len(la) == len(lb):
        return [((float(la[i]) + float(lb[i])) / 2.0) for i in range(len(la))], True
    if la:
        return la, False
    if lb:
        return lb, False
    return [], False


def maybe_prepare_audio_half_split(envelope: IngestionEnvelope, cfg: Config) -> None:
    """
    Mutate ``envelope`` when half-split applies: new ``extracted_plaintext`` and
    ``modality_hints['audio_half_split']`` with transcripts + embeddings.

    No-op when disabled, ffmpeg missing, not a single-audio submission, or split fails.
    """
    if not multimodal_audio_half_split_enabled(cfg):
        return
    hints = envelope.modality_hints if isinstance(envelope.modality_hints, dict) else {}
    if hints.get("audio_half_split_done"):
        return
    arts = envelope.artifacts or {}
    if not isinstance(arts, dict):
        return

    nonempty: list[tuple[str, bytes]] = []
    for k, v in arts.items():
        if not isinstance(k, str):
            continue
        key = k.strip().lower()
        if key not in _AUDIO_ONLY_KEYS:
            continue
        if not isinstance(v, (bytes, bytearray)) or len(bytes(v).strip()) < 4096:
            continue
        nonempty.append((key, bytes(v)))

    if len(nonempty) != 1:
        return

    artifact_key, blob = nonempty[0]
    suf = _artifact_suffix_for_whisper(artifact_key)

    mode = str(getattr(cfg, "MULTIMODAL_AUDIO_HALF_SPLIT", "off") or "off").strip().lower()
    # auto: when submission is fairly large (Whisper / context pressure); oral interviews use a
    # lower threshold so typical .m4a mock interviews still get duration halves + dual trio pass.
    if mode == "auto":
        min_b = int(
            getattr(cfg, "MULTIMODAL_AUDIO_HALF_SPLIT_AUTO_MIN_BYTES", 3_000_000) or 3_000_000
        )
        tt = str((envelope.modality_hints or {}).get("task_type") or "").strip().lower()
        if tt == "oral_interview":
            oral_min = int(
                getattr(cfg, "MULTIMODAL_AUDIO_HALF_SPLIT_AUTO_MIN_BYTES_ORAL", 1_200_000)
                or 1_200_000
            )
            min_b = min(min_b, oral_min)
        if len(blob) < min_b:
            return

    halves = split_audio_bytes_into_duration_halves(blob, suffix=suf)
    if not halves:
        return
    a, b = halves
    t1 = transcribe_submission_media_bytes(a, filename=f"submission_half1{suf}")
    t2 = transcribe_submission_media_bytes(b, filename=f"submission_half2{suf}")
    if not (str(t1).strip() or str(t2).strip()):
        _log.warning("audio_half_split: both transcript halves empty; skipping")
        return

    hit1 = _openai_embed_snippet((t1 or "").strip(), cfg)
    hit2 = _openai_embed_snippet((t2 or "").strip(), cfg)
    if hit1:
        e1, src1 = hit1
    else:
        e1, src1 = compute_submission_embedding(t1, cfg)
    if hit2:
        e2, src2 = hit2
    else:
        e2, src2 = compute_submission_embedding(t2, cfg)

    combined = (
        "[STUDENT_AUDIO_PART 1/2 — first half transcript]\n"
        + (t1 or "").strip()
        + "\n\n[STUDENT_AUDIO_PART 2/2 — second half transcript]\n"
        + (t2 or "").strip()
    ).strip()

    envelope.extracted_plaintext = combined
    hints = dict(envelope.modality_hints or {})
    hints["audio_half_split_done"] = True
    hints["audio_half_split"] = {
        "enabled": True,
        "artifact_key": artifact_key,
        "transcripts": [t1, t2],
        "embeddings": [list(e1), list(e2)],
        "embedding_sources": [src1, src2],
        "half_bytes": [len(a), len(b)],
    }
    envelope.modality_hints = hints
    _log.info(
        "audio_half_split: prepared two halves key=%s chars=(%s,%s) embed_dim=(%s,%s)",
        artifact_key,
        len(t1 or ""),
        len(t2 or ""),
        len(e1),
        len(e2),
    )


def blend_chunk_embedding_with_half(
    canonical: list[float],
    half_vecs: list[list[float]],
    half_index: int | None,
) -> tuple[list[float], str]:
    """Blend ``canonical`` with ``half_vecs[half_index]`` when shapes align."""
    if half_index is None or half_index < 0 or half_index >= len(half_vecs):
        return list(canonical or []), "canonical_only"
    hv = list(half_vecs[half_index] or [])
    merged, did_mean = _mean_embedding(canonical, hv)
    if did_mean:
        return merged, f"mean_canonical_and_half_{half_index}_transcript_embed"
    if canonical:
        return list(canonical), "canonical_only_half_mismatch"
    return hv, f"half_{half_index}_transcript_embed_only"
