from __future__ import annotations

import logging

from assignment_parser.models.base import Classifier, ClassifierRule
from assignment_parser.models.schema import Block, Document, Section, Task, TaskType
from assignment_parser.registry import registry
from assignment_parser.segmenters.heading import walk_blocks_in_order

from .rules import CONTEXT_WINDOW, ClassificationContext

logger = logging.getLogger(__name__)


def _dedupe_likert_followon(tasks: list[Task]) -> list[Task]:
    if len(tasks) < 2:
        return tasks
    out: list[Task] = []
    i = 0
    while i < len(tasks):
        cur = tasks[i]
        nxt = tasks[i + 1] if i + 1 < len(tasks) else None
        if (
            nxt is not None
            and cur.task_type == TaskType.SELF_RATING
            and nxt.task_type in (TaskType.WRITTEN_RESPONSE, TaskType.CRITIQUE)
            and cur.section_title == nxt.section_title
        ):
            pfx = 30
            d1 = cur.description[:pfx]
            d2 = nxt.description[:pfx]
            if d1 and d2 and d1 == d2:
                out.append(cur)
                i += 2
                logger.debug(
                    "Dropped follow-on task after SELF_RATING (shared %d-char prefix)",
                    pfx,
                )
                continue
        out.append(cur)
        i += 1
    return out


class RuleBasedClassifier(Classifier):
    def __init__(self, rules: list[ClassifierRule] | None = None) -> None:
        self._rules = rules if rules is not None else registry.all_rules()

    def classify(self, sections: list[Section], document: Document) -> list[Task]:
        linear: list[tuple[Section, Block]] = list(walk_blocks_in_order(sections))
        all_blocks = [b for _, b in linear]
        index_map: dict[int, int] = {id(b): i for i, b in enumerate(all_blocks)}
        tasks: list[Task] = []
        matched: set[int] = set()

        for sec, block in linear:
            idx = index_map[id(block)]
            if idx in matched:
                continue
            prev_slice = all_blocks[max(0, idx - CONTEXT_WINDOW) : idx]
            next_slice = all_blocks[idx + 1 : idx + 1 + CONTEXT_WINDOW]
            ctx = ClassificationContext(
                previous_blocks=prev_slice,
                next_blocks=next_slice,
                section=sec,
            )
            for rule in self._rules:
                task = rule.apply(block, sec, ctx)
                if task is not None:
                    tasks.append(task)
                    matched.add(idx)
                    break

        return _dedupe_likert_followon(tasks)
