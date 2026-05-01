from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import cast

from assignment_parser.models.base import Classifier
from assignment_parser.models.schema import Block, Document, Section, Task, TaskType, excerpt_raw_text

logger = logging.getLogger(__name__)

_ENV_PROVIDER = "ASSIGNMENT_PARSER_LLM_PROVIDER"


def _load_provider(spec: str) -> Callable[[str], str]:
    if ":" not in spec:
        raise ValueError(
            f"LLM provider spec must be 'module:callable', got {spec!r}"
        )
    mod_name, attr = spec.split(":", 1)
    import importlib

    mod = importlib.import_module(mod_name)
    fn = getattr(mod, attr, None)
    if not callable(fn):
        raise ValueError(f"{spec!r} is not a callable")
    return cast(Callable[[str], str], fn)


def _collect_section_blocks(sec: Section) -> list[Block]:
    out = list(sec.blocks)
    for ch in sec.children:
        out.extend(_collect_section_blocks(ch))
    return out


def _all_sections(sections: list[Section]) -> list[Section]:
    out: list[Section] = []
    for s in sections:
        out.append(s)
        out.extend(_all_sections(s.children))
    return out


class LLMClassifier(Classifier):
    def __init__(
        self,
        provider: Callable[[str], str] | None = None,
        *,
        env_var: str = _ENV_PROVIDER,
    ) -> None:
        if provider is None:
            spec = os.environ.get(env_var, "")
            if not spec:
                raise ValueError(
                    f"LLMClassifier requires provider=... or {env_var} environment variable"
                )
            provider = _load_provider(spec)
        self._provider = provider

    def classify(self, sections: list[Section], document: Document) -> list[Task]:
        id_to_i = {id(b): i for i, b in enumerate(document.blocks)}
        tasks: list[Task] = []
        for sec in _all_sections(sections):
            chunk = _collect_section_blocks(sec)
            if not chunk:
                continue
            indices = [id_to_i[id(b)] for b in chunk if id(b) in id_to_i]
            if not indices:
                continue
            min_i, max_i = min(indices), max(indices)
            prompt = self._build_prompt(sec, chunk, id_to_i, min_i, max_i)
            try:
                raw = self._provider(prompt)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "LLMClassifier provider failed for section %r: %s",
                    sec.title,
                    exc,
                    exc_info=True,
                )
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    "LLMClassifier invalid JSON for section %r: %s",
                    sec.title,
                    raw[:500],
                )
                continue
            if not isinstance(data, list):
                logger.warning("LLMClassifier expected JSON list for section %r", sec.title)
                continue
            for item in data:
                if not isinstance(item, dict):
                    continue
                tname = item.get("task_type")
                try:
                    tt = TaskType(str(tname))
                except (ValueError, TypeError):
                    logger.warning("LLMClassifier skipping unknown task_type: %r", tname)
                    continue
                bi = item.get("block_index")
                if not isinstance(bi, int) or bi < min_i or bi > max_i:
                    logger.warning(
                        "LLMClassifier skipping out-of-range block_index %r (valid %d–%d)",
                        bi,
                        min_i,
                        max_i,
                    )
                    continue
                blk = document.blocks[bi]
                desc = str(item.get("description", ""))[:2000]
                conf = item.get("confidence", 0.5)
                try:
                    conf_f = float(conf)
                except (TypeError, ValueError):
                    conf_f = 0.5
                conf_f = max(0.0, min(1.0, conf_f))
                tasks.append(
                    Task(
                        task_type=tt,
                        description=desc,
                        location=blk.location,
                        section_title=sec.title,
                        raw_text=excerpt_raw_text(blk.text),
                        confidence=conf_f,
                        metadata={"source": "llm"},
                    )
                )
        return tasks

    def _build_prompt(
        self,
        sec: Section,
        blocks: list[Block],
        id_to_i: dict[int, int],
        min_i: int,
        max_i: int,
    ) -> str:
        lines = [
            "Identify discrete student tasks in this assignment section.",
            f"Section title: {sec.title}",
            f"Valid block_index range (inclusive): {min_i} to {max_i}.",
            "Return a JSON array of objects with keys: task_type, description, block_index, confidence.",
            f"task_type must be one of: {[e.value for e in TaskType]}",
            "Blocks (use block_index values exactly as shown):",
        ]
        for b in blocks:
            idx = id_to_i[id(b)]
            lines.append(f"  [{idx}] kind={b.kind} text={b.text[:800]!r}")
        return "\n".join(lines)
