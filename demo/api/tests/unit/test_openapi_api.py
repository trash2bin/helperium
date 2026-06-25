"""Тест: OpenAPI-спецификация API-сервера соответствует specs/api.openapi.yaml."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

# Тесты запускаются через `uv run pytest` из корня репо — cwd == repo root.
SPEC_PATH = Path("specs") / "api.openapi.yaml"


def test_openapi_spec_matches_generated():
    """Проверяет, что specs/api.openapi.yaml соответствует тому,
    что генерирует FastAPI из кода demo/api/server.py.

    Если тест упал — значит API изменился, но spec не обновлён.
    Обнови spec командой из specs/README.md.
    """
    # Импортируем app без запуска сервиса
    from demo.api.server import app

    # Генерируем свежую схему
    generated_raw = app.openapi()
    generated = json.loads(json.dumps(generated_raw, default=str))

    # Читаем spec-файл
    with open(SPEC_PATH) as f:
        spec = yaml.safe_load(f)

    # Сравниваем — pytest покажет diff построчно
    assert generated == spec, (
        f"\n{SPEC_PATH} не совпадает с OpenAPI, который генерирует код.\n"
        f"Обнови spec командой:\n"
        f"  curl -s http://127.0.0.1:8081/openapi.json | python3 -m yaml > {SPEC_PATH}\n"
    )
