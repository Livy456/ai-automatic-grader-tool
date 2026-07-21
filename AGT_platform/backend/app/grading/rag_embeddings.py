# REVIEW THIS FILE LATER!! Need to make it more readable and more efficient.

"""
Build embedding vectors for submission text (SentenceTransformers, OpenAI, or hash fallback).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

import numpy as np
import requests

from ..config import Config

_log = logging.getLogger(__name__)

_st_lock = threading.Lock()
_st_models: dict[str, Any] = {}


def deterministic_hash_embedding(text: str, dimensions: int = 256) -> list[float]:
    """
    Offline-stable pseudo-embedding (not semantic). Used when no API is available.
    Fills a vector in blocks via SHA-256 streams; values vectorized as uint16 → float.
    """
    seed = hashlib.sha256(text.encode("utf-8", errors="replace")).digest()
    out = np.empty(dimensions, dtype=np.float64)
    filled = 0
    counter = 0
    scale = 1.0 / 65535.0

    while filled < dimensions:
        block = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        counter += 1
        need = min(dimensions - filled, len(block) // 2)
        u16 = np.frombuffer(block, dtype=np.uint16, count=need)
        out[filled : filled + need] = u16.astype(np.float64) * scale - 0.5
        filled += need

    return out.tolist()


def _openai_embed_snippet(snippet: str, cfg: Config) -> tuple[list[float], str] | None:
    key = (cfg.OPENAI_API_KEY or "").strip()
    if not key or not snippet:
        return None
    model = (
        (getattr(cfg, "OPENAI_TRIO_RAG_EMBEDDING_MODEL", "") or "").strip()
        or "text-embedding-3-small"
    )
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        resp = client.embeddings.create(
            model=model,
            input=snippet[:8000],
        )
        vec = list(resp.data[0].embedding)
        return vec, f"openai:{model}"
    except Exception as exc:
        _log.warning("OpenAI embedding failed (%s); trying other fallbacks", exc)
        return None


def _get_sentence_transformer(model_name: str) -> Any:
    """Lazy singleton per model id (thread-safe)."""
    with _st_lock:
        if model_name not in _st_models:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise RuntimeError(
                    "sentence-transformers is not installed. "
                    "pip install sentence-transformers"
                ) from e
            _log.info("Loading SentenceTransformer %r …", model_name)
            _st_models[model_name] = SentenceTransformer(model_name)
        return _st_models[model_name]


def sentence_transformers_embed_text(text: str, cfg: Config) -> tuple[list[float], str] | None:
    """
    Encode a single text chunk with :class:`sentence_transformers.SentenceTransformer`.

    Returns ``None`` on empty input, import failure, or encode errors (caller may fall back).
    """
    snippet = (text or "").strip()
    if not snippet:
        return None
    model_name = (getattr(cfg, "SENTENCE_TRANSFORMERS_MODEL", "") or "").strip()
    if not model_name:
        model_name = "all-MiniLM-L6-v2"
    try:
        model = _get_sentence_transformer(model_name)
    except Exception as exc:
        _log.warning("SentenceTransformer load failed for %r: %s", model_name, exc)
        return None
    try:
        vec = model.encode(
            snippet,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        if getattr(vec, "ndim", 0) > 1:
            vec = vec[0]
        out = np.asarray(vec, dtype=np.float64).ravel()
        if out.size < 8:
            return None
        return out.tolist(), f"sentence_transformers:{model_name}"
    except Exception as exc:
        _log.warning("SentenceTransformer encode failed: %s", exc)
        return None


def compute_submission_embedding(text: str, cfg: Config) -> tuple[list[float], str]:
    """
    Return (vector, source_description).

    Primary path is ``RAG_EMBEDDING_BACKEND``:

    - ``sentence_transformers`` (default): local :class:`sentence_transformers.SentenceTransformer`
      (``SENTENCE_TRANSFORMERS_MODEL``, default ``all-MiniLM-L6-v2``). On failure, falls back
      to OpenAI per ``RAG_EMBED_ORDER``, then deterministic hash.
    - ``openai``: OpenAI Embeddings API (``OPENAI_TRIO_RAG_EMBEDDING_MODEL``, requires
      ``OPENAI_API_KEY``); on failure falls back to sentence_transformers then hash.
    """
    max_c = int(getattr(cfg, "RAG_EMBED_MAX_CHARS", 24000))
    snippet = (text or "")[:max_c]

    backend = (getattr(cfg, "RAG_EMBEDDING_BACKEND", "") or "sentence_transformers").strip().lower()
    if backend not in ("sentence_transformers", "openai"):
        _log.warning("Unknown RAG_EMBEDDING_BACKEND=%r; using sentence_transformers", backend)
        backend = "sentence_transformers"

    if backend == "openai":
        hit = _openai_embed_snippet(snippet, cfg)
        if hit:
            return hit
        _log.warning(
            "RAG_EMBEDDING_BACKEND=openai failed; falling back to sentence_transformers"
        )
        hit = sentence_transformers_embed_text(snippet, cfg)
        if hit:
            return hit
        dim = 256
        return deterministic_hash_embedding(snippet, dim), "deterministic_hash:sha256×256"

    if backend == "sentence_transformers":
        hit = sentence_transformers_embed_text(snippet, cfg)
        if hit:
            return hit
        _log.warning(
            "RAG_EMBEDDING_BACKEND=sentence_transformers failed; falling back to "
            "OpenAI per RAG_EMBED_ORDER"
        )

    order = (getattr(cfg, "RAG_EMBED_ORDER", "auto") or "auto").strip().lower()
    key_ok = bool((cfg.OPENAI_API_KEY or "").strip())

    methods: list[str]
    if order == "openai_only":
        methods = ["openai"]
    elif order == "openai_first":
        methods = ["openai"]
    elif key_ok:
        methods = ["openai"]
    else:
        methods = []

    for m in methods:
        if m == "openai" and key_ok:
            hit = _openai_embed_snippet(snippet, cfg)
            if hit:
                return hit

    dim = 256
    return deterministic_hash_embedding(snippet, dim), "deterministic_hash:sha256×256"


def compute_submission_embeddings_batch(
    texts: list[str], cfg: Config
) -> list[tuple[list[float], str]]:
    """
    Embed many snippets with fewer model / HTTP round-trips when the backend allows it.

    Used when ``MULTIMODAL_RAG_EMBED_BATCH=on`` from :func:`enrich_chunks_with_rag_embeddings`.
    On batch failure, falls back to sequential :func:`compute_submission_embedding`.
    """
    max_c = int(getattr(cfg, "RAG_EMBED_MAX_CHARS", 24000))
    snippets = [(t or "")[:max_c] for t in texts]
    n = len(snippets)
    if n == 0:
        return []

    backend = (getattr(cfg, "RAG_EMBEDDING_BACKEND", "") or "sentence_transformers").strip().lower()
    if backend not in ("sentence_transformers", "openai"):
        backend = "sentence_transformers"

    # OpenAI: one request with multiple inputs (batched).
    if backend == "openai":
        key = (cfg.OPENAI_API_KEY or "").strip()
        if key:
            model = (
                (getattr(cfg, "OPENAI_TRIO_RAG_EMBEDDING_MODEL", "") or "").strip()
                or "text-embedding-3-small"
            )
            try:
                from openai import OpenAI

                client = OpenAI(api_key=key)
                out: list[tuple[list[float], str]] = []
                batch_size = int(os.getenv("MULTIMODAL_RAG_EMBED_BATCH_SIZE", "64") or "64")
                batch_size = max(1, min(batch_size, 128))
                offset = 0
                while offset < n:
                    batch = snippets[offset : offset + batch_size]
                    inputs: list[str] = []
                    nonempty_j: list[int] = []
                    for j, s in enumerate(batch):
                        st = (s or "").strip()[:8000]
                        if st:
                            inputs.append(st)
                            nonempty_j.append(j)
                    if not inputs:
                        for s in batch:
                            out.append(compute_submission_embedding(s, cfg))
                    else:
                        resp = client.embeddings.create(model=model, input=inputs)
                        rows = sorted(
                            resp.data or [],
                            key=lambda d: int(getattr(d, "index", 0)),
                        )
                        if len(rows) != len(nonempty_j):
                            raise RuntimeError(
                                f"OpenAI embeddings batch mismatch: "
                                f"got {len(rows)} rows for {len(nonempty_j)} inputs"
                            )
                        vec_by_j: dict[int, tuple[list[float], str]] = {}
                        for j_local, row in zip(nonempty_j, rows):
                            emb = getattr(row, "embedding", None)
                            if not isinstance(emb, list) or not emb:
                                raise RuntimeError("OpenAI embedding row missing vector")
                            vec_by_j[j_local] = (
                                [float(x) for x in emb],
                                f"openai_batch:{model}",
                            )
                        for j, s in enumerate(batch):
                            if j in vec_by_j:
                                out.append(vec_by_j[j])
                            else:
                                out.append(compute_submission_embedding(s, cfg))
                    offset += len(batch)
                if len(out) == n:
                    return out
            except Exception:
                _log.warning(
                    "OpenAI batch embedding failed; falling back to per-text embed",
                    exc_info=True,
                )
        return [compute_submission_embedding(t, cfg) for t in snippets]

    if backend == "sentence_transformers":
        model_name = (getattr(cfg, "SENTENCE_TRANSFORMERS_MODEL", "") or "").strip()
        if not model_name:
            model_name = "all-MiniLM-L6-v2"
        try:
            model = _get_sentence_transformer(model_name)
        except Exception:
            return [compute_submission_embedding(t, cfg) for t in snippets]
        out_st: list[tuple[list[float], str] | None] = [None] * n
        nonempty_idx: list[int] = []
        nonempty_texts: list[str] = []
        for i, s in enumerate(snippets):
            st = (s or "").strip()
            if st:
                nonempty_idx.append(i)
                nonempty_texts.append(st)
        if nonempty_texts:
            try:
                emb = model.encode(
                    nonempty_texts,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
                arr = np.asarray(emb, dtype=np.float64)
                if arr.ndim == 1:
                    arr = arr.reshape(1, -1)
                for row, orig_i in enumerate(nonempty_idx):
                    vec = arr[row].ravel()
                    if vec.size < 8:
                        out_st[orig_i] = compute_submission_embedding(snippets[orig_i], cfg)
                    else:
                        out_st[orig_i] = (
                            vec.tolist(),
                            f"sentence_transformers_batch:{model_name}",
                        )
            except Exception:
                _log.warning(
                    "SentenceTransformer batch encode failed; falling back to sequential",
                    exc_info=True,
                )
                return [compute_submission_embedding(t, cfg) for t in snippets]
        for i in range(n):
            if out_st[i] is None:
                out_st[i] = compute_submission_embedding(snippets[i], cfg)
        return [out_st[i] for i in range(n)]

    # Unknown backend: sequential per-text embedding.
    return [compute_submission_embedding(t, cfg) for t in snippets]


def save_rag_embedding_bundle(
    out_dir: Path,
    *,
    assignment_stem: str,
    artifacts_keys: list[str],
    plaintext_chars: int,
    embedding: list[float],
    embedding_source: str,
    parsed_preview: str,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write ``<stem>_embedding.json`` and ``<stem>_parsed_preview.txt`` under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    preview_path = out_dir / f"{assignment_stem}_parsed_preview.txt"
    preview_path.write_text(parsed_preview[:50000], encoding="utf-8")
    payload = {
        "assignment_stem": assignment_stem,
        "artifacts_keys": artifacts_keys,
        "plaintext_char_count": plaintext_chars,
        "embedding_dimension": len(embedding),
        "embedding_source": embedding_source,
        "embedding": embedding,
        "extra": extra or {},
    }
    json_path = out_dir / f"{assignment_stem}_embedding.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return json_path
