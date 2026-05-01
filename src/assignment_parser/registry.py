from __future__ import annotations

import logging
from pathlib import Path

from assignment_parser.models.base import ClassifierRule, Extractor, Renderer

logger = logging.getLogger(__name__)


class _SingletonRegistry:
    def __init__(self) -> None:
        self._extractors: list[type[Extractor]] = []
        self._rules: list[type[ClassifierRule]] = []
        self._renderers: dict[str, type[Renderer]] = {}

    def register_extractor(self, cls: type[Extractor]) -> type[Extractor]:
        self._extractors.append(cls)
        logger.debug("Registered extractor: %s", cls.__name__)
        return cls

    def register_rule(self, cls: type[ClassifierRule]) -> type[ClassifierRule]:
        self._rules.append(cls)
        logger.debug("Registered classifier rule: %s", cls.__name__)
        return cls

    def register_renderer(self, cls: type[Renderer]) -> type[Renderer]:
        name = getattr(cls, "name", None)
        if not name or not isinstance(name, str):
            raise ValueError(f"Renderer {cls} must define class attribute 'name: str'")
        self._renderers[name] = cls
        logger.debug("Registered renderer: %s", name)
        return cls

    def find_extractor(self, path: Path) -> Extractor:
        for cls in self._extractors:
            inst = cls()
            if inst.can_handle(path):
                return inst
        names = [c.__name__ for c in self._extractors]
        raise ValueError(
            f"No extractor registered for {path!s}. "
            f"Registered extractors (in order): {names or '(none)'}"
        )

    def all_rules(self) -> list[ClassifierRule]:
        instances = [c() for c in self._rules]
        return sorted(instances, key=lambda r: r.priority, reverse=True)

    def get_renderer(self, name: str) -> Renderer:
        if name not in self._renderers:
            avail = ", ".join(sorted(self._renderers)) or "(none)"
            raise ValueError(f"Unknown renderer {name!r}. Available: {avail}")
        return self._renderers[name]()

    def available_renderers(self) -> list[str]:
        return sorted(self._renderers.keys())


registry = _SingletonRegistry()
register_extractor = registry.register_extractor
register_rule = registry.register_rule
register_renderer = registry.register_renderer
