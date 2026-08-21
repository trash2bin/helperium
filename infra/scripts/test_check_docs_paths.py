#!/usr/bin/env python3
"""Regression tests for the documentation path checker."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import tempfile
import unittest
from pathlib import Path

CHECKER_PATH = Path(__file__).with_name("check_docs_paths.py")
SPEC = importlib.util.spec_from_file_location("check_docs_paths", CHECKER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load documentation checker from {CHECKER_PATH}")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class DocumentationPathCheckerTests(unittest.TestCase):
    def with_temp_root(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        original_root = checker.REPO_ROOT
        checker.REPO_ROOT = root
        self.addCleanup(setattr, checker, "REPO_ROOT", original_root)
        self.addCleanup(temporary.cleanup)
        return root

    def test_ignored_agent_local_bare_filename_does_not_satisfy_reference(self):
        root = self.with_temp_root()
        ignored_file = root / ".pi" / "skills" / "reference" / "design.md"
        ignored_file.parent.mkdir(parents=True)
        ignored_file.write_text("generated agent-local file", encoding="utf-8")

        self.assertFalse(checker.has_non_ignored_basename("design.md"))

    def test_bare_filename_resolves_only_to_live_documentation(self):
        root = self.with_temp_root()
        document = root / "doc" / "design.md"
        document.parent.mkdir(parents=True)
        document.write_text("live document", encoding="utf-8")

        self.assertTrue(checker.has_non_ignored_basename("design.md"))

    def test_missing_bare_filename_fails_with_english_diagnostics(self):
        root = self.with_temp_root()
        (root / "AGENTS.md").write_text("# Project guide\n", encoding="utf-8")
        guide = root / "doc" / "guide.md"
        guide.parent.mkdir(parents=True)
        guide.write_text("The planned artifact is `design.md`.\n", encoding="utf-8")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = checker.main()

        report = output.getvalue()
        self.assertEqual(status, 1)
        self.assertIn("Documents checked: 2", report)
        self.assertIn("ERROR: Found 1 issue(s):", report)
        self.assertIn("path `design.md` not found (bare filename)", report)
        self.assertNotIn("путь", report)

    def test_checker_source_contains_no_cyrillic_diagnostics(self):
        source = CHECKER_PATH.read_text(encoding="utf-8")
        self.assertIsNone(
            re.search(r"[А-Яа-яЁё]", source),
            "check_docs_paths.py must keep all comments and diagnostics in English",
        )


if __name__ == "__main__":
    unittest.main()
