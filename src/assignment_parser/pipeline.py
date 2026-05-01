from __future__ import annotations

from pathlib import Path

# Side-effect imports so registry is populated regardless of import path.
import assignment_parser.classifiers.rules  # noqa: F401
import assignment_parser.extractors  # noqa: F401
import assignment_parser.renderers.output  # noqa: F401

from assignment_parser.classifiers.rule_based import RuleBasedClassifier
from assignment_parser.models.base import Classifier, Extractor, Renderer, Segmenter
from assignment_parser.models.schema import Modality, ParsedAssignment
from assignment_parser.registry import registry
from assignment_parser.segmenters.heading import HeadingSegmenter


def parse(
    path: str | Path,
    *,
    extractor: Extractor | None = None,
    segmenter: Segmenter | None = None,
    classifier: Classifier | None = None,
    title: str | None = None,
) -> ParsedAssignment:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No such file: {path}")
    extractor = extractor or registry.find_extractor(path)
    document = extractor.extract(path)
    segmenter = segmenter or HeadingSegmenter()
    sections = segmenter.segment(document)
    classifier = classifier or RuleBasedClassifier()
    tasks = classifier.classify(sections, document)
    modality = getattr(extractor, "modality", Modality.UNKNOWN)
    resolved_title = title if title is not None else path.stem
    return ParsedAssignment(
        title=resolved_title,
        modality=modality,
        sections=sections,
        tasks=tasks,
        source_path=str(path.resolve()),
        metadata={},
    )


def render(parsed: ParsedAssignment, format: str = "markdown") -> str:
    return registry.get_renderer(format).render(parsed)
