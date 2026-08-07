#!/usr/bin/env python3
"""
Проверка мёртвых путей и сирот в документации Helperium.

Что делает:
  — проходит по реальным докам: AGENTS.md, CHANGELOG.md, doc/**/*.md,
    services/**/README.md, specs/**/*.md, demo/**/*.md
  — извлекает markdown-ссылки [text](path) и бэктик-пути `path.md`
  — проверяет, что каждый путь существует на диске
    (как есть / с .md / без .md; от корня репо И от директории файла-источника)
  — игнорирует: внешние URL, якоря (#...), глобы (*...), пути с пробелами
  — проверяет, что каждый реальный док (doc/agents/, specs/, doc/benchmark/,
    services/*/README, demo/*/README) упомянут в AGENTS.md — иначе «сирота»
    (агент добавил док и забыл вписать в карту §5)

Exit code 0 = чисто, 1 = найдены проблемы (для CI).

Запуск:
  python3 infra/scripts/check_docs_paths.py
"""

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Какие .md считаются "реальной документацией" (не vendor/.pi/.agents/...)
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

# Директории, которые не проверяем (в .gitignore или не наша документация).
# Пара "префикс, начинающийся с" -> True означает: исключить весь поддерево.
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

# Файлы-исключения (глобы в .gitignore):
# plan-refactor-*.md, DEEP_DIVE_*.md, ARCHITECTURE_REPORT.md, *.bak, etc.
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
    """True, если файл в игнорируемой директории или под паттерном."""
    parts = path.parts
    for i, part in enumerate(parts):
        if part in IGNORED_DIRS:
            return True
    for pat in IGNORED_PATTERNS:
        if pat.search(str(path)):
            return True
    return False


def collect_docs(root: Path) -> list[Path]:
    """Собрать все реальные доки по глобам, пропуская игнорируемые."""
    docs = []
    for g in DOC_GLOBS:
        for d in root.glob(g):
            if d.is_file() and not is_ignored(d):
                docs.append(d)
    # убрать дубли
    seen = set()
    out = []
    for d in docs:
        key = str(d.resolve())
        if key not in seen:
            seen.add(key)
            out.append(d)
    return sorted(out, key=lambda p: str(p).lower())


def extract_paths(text: str) -> list[str]:
    """Извлечь пути из markdown-ссылок и бэктик-путей."""
    paths = []
    # markdown-ссылки [text](path) — path без якорей/URL
    for m in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", text):
        target = m.group(1).strip()
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        # срезать якорь внутри пути: doc/x.md#section
        target = target.split("#")[0].strip()
        if target and not target.startswith(("<", ">")):
            paths.append(target)
    # бэктик-пути `path.md` — только те, что похожи на путь к файлу
    for m in re.finditer(r"`([^`]+\.md(?:\.j2)?)`", text):
        paths.append(m.group(1).strip())
    return paths


def resolve_candidates(raw: str, src_dir: Path) -> list[Path]:
    """Варианты, где может лежать файл: от корня репо и от директории источника."""
    cands = []
    for base in (REPO_ROOT, src_dir):
        p = base / raw
        cands.append(p)
        if not raw.endswith(".md"):
            cands.append(base / (raw + ".md"))
        elif raw.endswith(".md"):
            cands.append(base / raw[:-3])  # без .md
    # сервисные пути в бэктиках часто пишут без `services/` (data-service/README.md → services/data-service/README.md)
    if "/" in raw and not raw.startswith("services/") and not raw.startswith("../"):
        first = raw.split("/")[0]
        if first in {"data-service", "api-service", "mcp-gateway", "admin-dashboard", "rag", "agent-db", "helperium-go", "helperium-sdk"}:
            cands.append(REPO_ROOT / "services" / raw)
            if raw.endswith(".md"):
                cands.append(REPO_ROOT / "services" / raw[:-3])
            else:
                cands.append(REPO_ROOT / "services" / (raw + ".md"))
    return cands


def exists(raw: str, src_dir: Path) -> bool:
    """True, если путь существует: от корня репо ИЛИ от директории источника.
    Поддерживает файлы и директории (путь с завершающим /)."""
    is_dir = raw.endswith("/")
    for c in resolve_candidates(raw, src_dir):
        if is_dir:
            if c.is_dir():
                return True
        elif c.is_file():
            return True
    return False


def check_agents_coverage(docs: list[Path], agents_md: Path) -> list[str]:
    """Проверка: каждый реальный док должен упоминаться в AGENTS.md (basename).
    Ловит «агент добавил док и забыл вписать в карту»."""
    try:
        agents_text = agents_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [f"{agents_md}: не могу прочитать"]

    # какие доки должны быть в AGENTS.md: doc/agents/*.md, specs/*.md,
    # services/*/README.md, doc/benchmark/*.md, demo/*/README.md
    required = []
    for d in docs:
        rel = d.relative_to(REPO_ROOT).as_posix()
        if (
            rel.startswith("doc/agents/")
            or rel.startswith("specs/")
            or rel.startswith("doc/benchmark/")
            or (rel.startswith("services/") and rel.endswith("/README.md"))
            or (rel.startswith("demo/") and rel.endswith("/README.md"))
        ):
            required.append(d)

    missing = []
    for d in required:
        name = d.name  # basename, напр. search-strategies.md
        if name not in agents_text:
            rel = d.relative_to(REPO_ROOT)
            missing.append(f"{rel}: не упомянут в AGENTS.md (забыл вписать в карту §5)")
    return missing


def main() -> int:
    docs = collect_docs(REPO_ROOT)
    errors = []
    checked = 0
    ignored = 0

    # Проверка «каждый док упомянут в AGENTS.md»
    agents_md = REPO_ROOT / "AGENTS.md"
    missing = check_agents_coverage(docs, agents_md)
    if missing:
        errors.extend(missing)

    for doc in docs:
        rel = doc.relative_to(REPO_ROOT)
        try:
            text = doc.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            errors.append(f"{rel}: не могу прочитать ({e})")
            continue

        for raw in extract_paths(text):
            # игнор: глобы, brace-паттерны, шаблоны с пробелами, пустое
            if any(ch in raw for ch in "*{}?") or " " in raw or not raw:
                ignored += 1
                continue
            # голые имена без слэша: ищем по basename во всех доках
            if "/" not in raw:
                # try relative to source first, then repo root, then basename search
                if exists(raw, doc.parent):
                    checked += 1
                    continue
                # basename search: search whole repo for the file
                hits = list(REPO_ROOT.rglob(raw))
                if hits:
                    checked += 1
                    continue
                errors.append(f"{rel}: путь `{raw}` не найден (голое имя)")
                continue

            if exists(raw, doc.parent):
                checked += 1
            else:
                errors.append(f"{rel}: путь `{raw}` не существует")

    # Отчёт
    print(f"Доков проверено: {len(docs)}")
    print(f"Путей проверено: {checked}")
    print(f"Проигнорировано (глобы/URL/якоря): {ignored}")
    if missing:
        print(f"\n❌ Сирот (не упомянуты в AGENTS.md): {len(missing)}")
        for e in sorted(set(missing)):
            print(f"  {e}")
    if errors:
        print(f"\n❌ Найдено {len(errors)} проблем:")
        for e in sorted(set(errors)):
            print(f"  {e}")
        return 1
    print("\n✅ Все пути существуют, сирот нет.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
