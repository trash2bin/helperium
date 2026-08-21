#!/usr/bin/env python3
"""Check Helperium documentation for dead file paths and AGENTS.md coverage.

The checker scans project documentation, validates Markdown links and inline
file-like references, and ensures that live documentation is discoverable from
AGENTS.md. Paths may resolve from either repository root or the source document.
External URLs, anchors, glob/template patterns, and candidates containing spaces
are ignored.

Exit code 0 means no issues were found. Exit code 1 means CI-relevant issues
were found.

Usage:
  python3 infra/scripts/check_docs_paths.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Documentation files considered live project documentation.
DOC_GLOBS = [
    "AGENTS.md",
    "CHANGELOG.md",
    "README.md",
    "doc/**/*.md",
    "services/**/README.md",
    "services/**/README.md.j2",
    "specs/**/*.md",
    "demo/**/*.md",
]

# Directories excluded because they are dependencies, generated artifacts, or
# agent-local state rather than repository documentation.
IGNORED_DIRS = {
    "node_modules",
    ".pi",
    ".pi-subagents",
    ".agents",
    "vendor",
    ".git",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "backlog",
    "bench-backlog",
    ".data",
}

# Files excluded by repository ignore conventions or generated artifact names.
IGNORED_PATTERNS = [
    re.compile(r"plan-refactor-.*\.md$"),
    re.compile(r"DEEP_DIVE_.*\.md$"),
    re.compile(r"ARCHITECTURE_REPORT\.md$"),
    re.compile(r".*\.bak2?$"),
    re.compile(r".*test-results.*"),
    re.compile(r".*playwright-report.*"),
    re.compile(r".*\.pyc$"),
]


def is_ignored(path: Path) -> bool:
    """Return whether a path is under an ignored directory or pattern."""
    if any(part in IGNORED_DIRS for part in path.parts):
        return True
    return any(pattern.search(str(path)) for pattern in IGNORED_PATTERNS)


def collect_docs(root: Path) -> list[Path]:
    """Collect deduplicated live documentation files under root."""
    docs: list[Path] = []
    for glob_pattern in DOC_GLOBS:
        for document in root.glob(glob_pattern):
            if document.is_file() and not is_ignored(document.relative_to(root)):
                docs.append(document)

    seen: set[str] = set()
    result: list[Path] = []
    for document in docs:
        key = str(document.resolve())
        if key not in seen:
            seen.add(key)
            result.append(document)
    return sorted(result, key=lambda path: str(path).lower())


def extract_paths(text: str) -> list[str]:
    """Extract local paths from Markdown links and inline file-like references."""
    paths: list[str] = []

    for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", text):
        target = match.group(1).strip()
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#", maxsplit=1)[0].strip()
        if target and not target.startswith(("<", ">")):
            paths.append(target)

    for match in re.finditer(r"`([^`]+\.md(?:\.j2)?)`", text):
        paths.append(match.group(1).strip())

    return paths


def resolve_candidates(raw: str, source_dir: Path) -> list[Path]:
    """Build repository-root and source-relative candidates for a local path."""
    candidates: list[Path] = []
    for base in (REPO_ROOT, source_dir):
        candidate = base / raw
        candidates.append(candidate)
        if not raw.endswith(".md"):
            candidates.append(base / f"{raw}.md")
        else:
            candidates.append(base / raw[:-3])

    # Service paths in inline code sometimes omit the services/ prefix.
    if "/" in raw and not raw.startswith(("services/", "../")):
        service_name = raw.split("/", maxsplit=1)[0]
        service_roots = {
            "data-service",
            "api-service",
            "mcp-gateway",
            "admin-dashboard",
            "rag",
            "agent-db",
            "helperium-go",
            "helperium-sdk",
        }
        if service_name in service_roots:
            candidates.append(REPO_ROOT / "services" / raw)
            if raw.endswith(".md"):
                candidates.append(REPO_ROOT / "services" / raw[:-3])
            else:
                candidates.append(REPO_ROOT / "services" / f"{raw}.md")

    return candidates


def exists(raw: str, source_dir: Path) -> bool:
    """Return whether raw resolves to a non-ignored file or directory."""
    expects_directory = raw.endswith("/")
    for candidate in resolve_candidates(raw, source_dir):
        try:
            relative = candidate.relative_to(REPO_ROOT)
        except ValueError:
            continue
        if is_ignored(relative):
            continue
        if expects_directory and candidate.is_dir():
            return True
        if not expects_directory and candidate.is_file():
            return True
    return False


def has_non_ignored_basename(name: str) -> bool:
    """Return whether a bare filename exists outside ignored repository paths."""
    for hit in REPO_ROOT.rglob(name):
        try:
            relative = hit.relative_to(REPO_ROOT)
        except ValueError:
            continue
        if hit.is_file() and not is_ignored(relative):
            return True
    return False


def check_agents_coverage(docs: list[Path], agents_md: Path) -> list[str]:
    """Return live documentation that is not discoverable from AGENTS.md."""
    try:
        agents_text = agents_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [f"{agents_md}: cannot read file"]

    required: list[Path] = []
    for document in docs:
        relative = document.relative_to(REPO_ROOT).as_posix()
        if (
            relative.startswith("doc/agents/")
            or relative.startswith("specs/")
            or relative.startswith("doc/benchmark/")
            or (relative.startswith("services/") and relative.endswith("/README.md"))
            or (relative.startswith("demo/") and relative.endswith("/README.md"))
        ):
            required.append(document)

    missing: list[str] = []
    for document in required:
        if document.name not in agents_text:
            relative = document.relative_to(REPO_ROOT)
            missing.append(f"{relative}: not listed in AGENTS.md")
    return missing


def main() -> int:
    docs = collect_docs(REPO_ROOT)
    errors: list[str] = []
    checked = 0
    ignored = 0

    agents_md = REPO_ROOT / "AGENTS.md"
    missing = check_agents_coverage(docs, agents_md)
    errors.extend(missing)

    for document in docs:
        relative = document.relative_to(REPO_ROOT)
        try:
            text = document.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            errors.append(f"{relative}: cannot read file ({error})")
            continue

        for raw in extract_paths(text):
            if any(character in raw for character in "*{}?") or " " in raw or not raw:
                ignored += 1
                continue

            if "/" not in raw:
                if exists(raw, document.parent) or has_non_ignored_basename(raw):
                    checked += 1
                    continue
                errors.append(f"{relative}: path `{raw}` not found (bare filename)")
                continue

            if exists(raw, document.parent):
                checked += 1
            else:
                errors.append(f"{relative}: path `{raw}` does not exist")

    print(f"Documents checked: {len(docs)}")
    print(f"Paths checked: {checked}")
    print(f"Ignored candidates: {ignored}")
    if missing:
        print(f"\nERROR: {len(missing)} documentation file(s) are not listed in AGENTS.md:")
        for error in sorted(set(missing)):
            print(f"  {error}")
    if errors:
        print(f"\nERROR: Found {len(errors)} issue(s):")
        for error in sorted(set(errors)):
            print(f"  {error}")
        return 1

    print("\nSUCCESS: All paths exist and all required documentation is listed in AGENTS.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
