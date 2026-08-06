"""Тест: OpenAPI-спецификация API-сервера соответствует specs/api.openapi.yaml."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def _find_project_root() -> Path:
    p = Path(__file__).resolve().parent
    for _ in range(10):
        if (p / "specs").is_dir():
            return p
        p = p.parent
    raise RuntimeError(f"Cannot find specs/ from {__file__}")


SPEC_PATH = _find_project_root() / "specs" / "api.openapi.yaml"


def test_openapi_spec_matches_generated():
    """Проверяет, что specs/api.openapi.yaml соответствует тому,
    что генерирует FastAPI из кода api-service/src/api_service/server.py.

    Если тест упал — значит API изменился, но spec не обновлён.
    Обнови spec командой из specs/README.md.
    """
    # Импортируем app без запуска сервиса
    from api_service.server import app

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
