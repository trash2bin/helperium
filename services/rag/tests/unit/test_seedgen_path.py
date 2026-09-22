"""Регрессия: seedgen пишет seed.json в корневой specs/, а не внутрь services/.

`specs/fixtures/seed.json` — общий артефакт: его описывает specs/fixtures/README.md,
читает `data-service --seed --seed-path ./specs/fixtures/seed.json` и проверяет
services/helperium-sdk/tests/unit/test_seedgen_validation.py. Пока модуль считал
путь через `parents[2]` (== `services/`), `uv run agent-seedgen` писал файл в
`services/specs/fixtures/seed.json`, а импорт модуля заодно создавал этот
посторонний каталог — на нём же спотыкался поиск корня в других тестах.
"""

from __future__ import annotations

from pathlib import Path

# tests/unit → tests → rag → services → repo root
REPO_ROOT = Path(__file__).resolve().parents[4]
EXPECTED_SEED_PATH = REPO_ROOT / "specs" / "fixtures" / "seed.json"


def test_seed_path_points_to_repo_root() -> None:
    """SEED_PATH — корневой specs/fixtures/seed.json, как у остальных потребителей."""
    from rag.fixtures import seedgen

    assert seedgen.SEED_PATH == EXPECTED_SEED_PATH, (
        f"seedgen пишет в {seedgen.SEED_PATH}, а потребители ждут {EXPECTED_SEED_PATH} "
        f"(specs/fixtures/README.md, test_seedgen_validation.py, data-service --seed-path)"
    )


def test_import_does_not_create_service_local_specs_dir() -> None:
    """Импорт модуля не создаёт служебный каталог specs/ внутри services/."""
    from rag.fixtures import seedgen  # noqa: F401

    stray = REPO_ROOT / "services" / "specs"
    assert not stray.exists(), (
        f"импорт rag.fixtures.seedgen создал посторонний каталог {stray} — "
        f"он же перехватывает поиск корня репозитория в соседних тестах"
    )
