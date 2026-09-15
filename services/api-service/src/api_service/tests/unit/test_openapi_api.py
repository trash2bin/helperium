"""Тест: OpenAPI-спецификация API-сервера соответствует specs/api.openapi.{yaml,json}.

Оба артефакта — снимки одного и того же `app.openapi()`: yaml удобно читать
в дифе, json — тот же контракт в форме, которую копируют инструменты. Раньше
json не проверялся ничем и молча отстал от кода на несколько коммитов
(в нём не было ни capability-токена, ни новых роутов), поэтому здесь проверяются
оба файла — снимок, который не сверяется, превращается в ложь.
"""

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


SPECS_DIR = _find_project_root() / "specs"
SPEC_PATH = SPECS_DIR / "api.openapi.yaml"
SPEC_JSON_PATH = SPECS_DIR / "api.openapi.json"


def _generated_spec() -> dict:
    """Свежая схема из кода: app импортируется без запуска сервиса."""
    from api_service.server import app

    return json.loads(json.dumps(app.openapi(), default=str))


def test_openapi_spec_matches_generated():
    """Проверяет, что specs/api.openapi.yaml соответствует тому,
    что генерирует FastAPI из кода api-service/src/api_service/server.py.

    Если тест упал — значит API изменился, но spec не обновлён.
    Обнови spec командой из specs/README.md.
    """
    generated = _generated_spec()

    # Читаем spec-файл
    with open(SPEC_PATH) as f:
        spec = yaml.safe_load(f)

    # Сравниваем — pytest покажет diff построчно
    assert generated == spec, (
        f"\n{SPEC_PATH} не совпадает с OpenAPI, который генерирует код.\n"
        f"Обнови spec командой:\n"
        f"  curl -s http://127.0.0.1:8081/openapi.json | python3 -m yaml > {SPEC_PATH}\n"
    )


def test_openapi_json_spec_matches_generated():
    """Тот же контракт в JSON-снимке (specs/api.openapi.json).

    Сравниваются разобранные структуры, поэтому форматирование и
    экранирование кириллицы на результат не влияют — воспроизводимость
    записи задаёт команда из specs/README.md.
    """
    generated = _generated_spec()

    with open(SPEC_JSON_PATH, encoding="utf-8") as f:
        spec = json.load(f)

    assert generated == spec, (
        f"\n{SPEC_JSON_PATH} не совпадает с OpenAPI, который генерирует код.\n"
        f"Обнови spec командой из specs/README.md (раздел про api.openapi.json)."
    )
