"""
Protocol for k samples per chunk.

``MultiModelChunkRunner`` uses :func:`app.grading.llm_router.build_multimodal_grading_clients`
(OpenAI-only for per-chunk grading) and draws
``MULTIMODAL_SAMPLES_PER_MODEL`` stochastic samples **per client** at
``GRADING_SAMPLE_TEMPERATURE``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Protocol

from app.config import Config
from app.grading.llm_router import ChatClient, build_multimodal_grading_clients

from app.grading.schemas import GradingChunk, SampledChunkGrade

_log = logging.getLogger(__name__)

_JSON_OBJECT = {"type": "json_object"}
_MAX_GRADING_CHAT_ATTEMPTS = 3

ClientBuilder = Callable[[Config], list[tuple[ChatClient, str]]]


def _grading_chat_parsed_object(
    client: ChatClient,
    messages: list[dict[str, Any]],
    *,
    temperature: float,
) -> dict[str, Any]:
    """
    Prefer OpenAI ``json_object`` responses so long ``evidence`` strings with quotes
    (common in journal / PDF prose) do not break JSON syntax.
    """
    usage_fn = getattr(client, "chat_json_with_usage", None)
    if callable(usage_fn):
        obj, _u = usage_fn(
            messages,
            temperature=temperature,
            response_format=_JSON_OBJECT,
        )
        return obj
    return client.chat_json(messages, temperature=temperature)


class ChunkModelRunner(Protocol):
    """k samples per chunk; returns raw model outputs for parsing + entropy."""

    def run_chunk_samples(
        self,
        chunk: GradingChunk,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> list[SampledChunkGrade]: ...


class MultiModelChunkRunner:
    """
    For each configured grading client, run ``MULTIMODAL_SAMPLES_PER_MODEL``
    chat calls (default 3 for a single primary OpenAI model). OpenAI clients use
    ``response_format=json_object`` plus brief retries to reduce parse failures
    on journal-style evidence quotes.

    Semantic entropy over parsed outcomes is computed in
    :class:`MultimodalGradingPipeline` from cluster assignments of these samples.
    """

    def __init__(
        self,
        cfg: Config,
        *,
        build_clients: ClientBuilder | None = None,
    ):
        self._cfg = cfg
        self._build_clients: ClientBuilder = (
            build_clients or build_multimodal_grading_clients
        )

    @property
    def app_config(self) -> Config:
        return self._cfg

    def run_chunk_samples(
        self,
        chunk: GradingChunk,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> list[SampledChunkGrade]:
        clients = self._build_clients(self._cfg)
        k = max(1, int(getattr(self._cfg, "MULTIMODAL_SAMPLES_PER_MODEL", 5)))
        temp = float(getattr(self._cfg, "GRADING_SAMPLE_TEMPERATURE", 0.3))

        _log.debug(
            "Multimodal grading: %d model(s), %d sample(s) each → %d total calls/chunk",
            len(clients),
            k,
            len(clients) * k,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        out: list[SampledChunkGrade] = []
        idx = 0
        for client, model_label in clients:
            for _rep in range(k):
                raw_text = ""
                last_err: Exception | None = None
                for attempt in range(_MAX_GRADING_CHAT_ATTEMPTS):
                    try:
                        obj = _grading_chat_parsed_object(
                            client, messages, temperature=temp
                        )
                        raw_text = json.dumps(obj, ensure_ascii=True, default=str)
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        if attempt + 1 < _MAX_GRADING_CHAT_ATTEMPTS:
                            _log.debug(
                                "grading chat retry chunk_id=%s model=%s rep=%s/%s "
                                "attempt=%s/%s: %s: %s",
                                chunk.chunk_id,
                                model_label,
                                _rep + 1,
                                k,
                                attempt + 1,
                                _MAX_GRADING_CHAT_ATTEMPTS,
                                type(e).__name__,
                                e,
                            )
                if last_err is not None:
                    e = last_err
                    _log.warning(
                        "grading_llm_sample_failed (not chunking): chunk_id=%s model=%s "
                        "rep=%s/%s: %s: %s",
                        chunk.chunk_id,
                        model_label,
                        _rep + 1,
                        k,
                        type(e).__name__,
                        e,
                        exc_info=_log.isEnabledFor(logging.DEBUG),
                    )
                out.append(
                    SampledChunkGrade(
                        model_id=model_label,
                        sample_index=idx,
                        raw_text=raw_text,
                        parsed=None,
                        parse_ok=False,
                        parse_warnings=[],
                    )
                )
                idx += 1
        return out
