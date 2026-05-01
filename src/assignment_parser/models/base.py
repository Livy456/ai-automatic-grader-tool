from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from assignment_parser.models.schema import Document, Section, Task


class Extractor(ABC):
    modality: Any  # Modality enum on subclasses

    @abstractmethod
    def can_handle(self, path: Path) -> bool:
        ...

    @abstractmethod
    def extract(self, path: Path) -> Document:
        ...


class Segmenter(ABC):
    @abstractmethod
    def segment(self, document: Document) -> list[Section]:
        ...


class Classifier(ABC):
    @abstractmethod
    def classify(self, sections: list[Section], document: Document) -> list[Task]:
        ...


class Renderer(ABC):
    name: str

    @abstractmethod
    def render(self, parsed: Any) -> str:
        ...


class ClassifierRule(ABC):
    priority: int = 50

    @abstractmethod
    def apply(self, block: Any, section: Any, context: Any) -> Any | None:
        ...
