"""Tests for embedding-based dataset file matching (``assignments_to_grade``)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import Config
from app.grading.dataset_resolve import (
    list_data_asset_files,
    resolve_dataset_for_notebook,
)


class DatasetResolveTests(unittest.TestCase):
    def test_list_data_asset_files_skips_readme(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "foo.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            (root / "README.txt").write_text("notes", encoding="utf-8")
            names = {p.name for p in list_data_asset_files(root)}
            self.assertEqual(names, {"foo.csv"})

    def test_resolve_single_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "only.csv").write_text("x,y\n1,2\n", encoding="utf-8")
            cfg = Config()
            text, fname, sim = resolve_dataset_for_notebook(
                "Any assignment text.", root, cfg
            )
            self.assertEqual(fname, "only.csv")
            self.assertIsNotNone(text)
            self.assertIsInstance(sim, float)

    def test_resolve_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            text, fname, sim = resolve_dataset_for_notebook("x", Path(td), Config())
            self.assertIsNone(text)
            self.assertIsNone(fname)
            self.assertEqual(sim, 0.0)


if __name__ == "__main__":
    unittest.main()
