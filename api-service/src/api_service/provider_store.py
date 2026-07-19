"""ProviderStore — управление LLM-провайдерами для fallback.

Хранит провайдеров в JSON-файле (.data/providers.json).
API-ключи маскируются при чтении для API: только первые 4 символа + "****".
В памяти ключи хранятся полностью, на диске — тоже полностью (файл защищён правами ОС).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("api_service.provider_store")

DEFAULT_PROVIDERS_PATH = Path(".data/providers.json")


def get_litellm_provider_list() -> list[str]:
    """Возвращает список провайдеров из LiteLLM.

    Не хардкод — читает из litellm.provider_list при каждом вызове.
    При ошибке возвращает пустой список (LiteLLM может быть не установлен).
    """
    try:
        import litellm  # noqa: PLC0415

        return [p.value for p in litellm.provider_list]  # type: ignore[union-attr]
    except Exception:
        logger.warning("Failed to get LiteLLM provider list", exc_info=True)
        return []


# Список провайдеров, которые LiteLLM поддерживает "из коробки"
KNOWN_PROVIDERS = get_litellm_provider_list()


def mask_api_key(key: str | None) -> str | None:
    """Маскирует API-ключ: первые 4 символа + '****'.

    Если ключ короче 4 символов — '****'.
    """
    if not key:
        return None
    if len(key) <= 4:
        return "****"
    return key[:4] + "****"


class ProviderStore:
    """Thread-safe хранилище LLM-провайдеров.

    Провайдеры хранятся в JSON-файле на диске.
    Каждый провайдер имеет уникальное имя и содержит model, api_key, api_base, enabled.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path or DEFAULT_PROVIDERS_PATH)
        self._lock = threading.Lock()
        self._providers: dict[str, dict[str, Any]] = {}
        self._load()
        self._import_from_env_if_empty()

    def _import_from_env_if_empty(self) -> None:
        """Импортирует провайдеров из .env если стора пуста.

        Сканирует os.environ, находит все {PREFIX}_API_KEY + {PREFIX}_MODEL.
        Поддерживает:
          - MISTRAL_API_KEY / MISTRAL_MODEL / MISTRAL_API_BASE
          - OPENAI_API_KEY / OPENAI_MODEL / OPENAI_API_BASE
          - ANTHROPIC_API_KEY / ANTHROPIC_MODEL / ANTHROPIC_API_BASE
          - LLM_PRIMARY_API_KEY / LLM_PRIMARY_MODEL / … (бывшие legacy)
          - и любые другие *_API_KEY / *_MODEL пары

        Маркирует source="env" чтобы админка знала происхождение.
        """
        if self._providers:
            return  # уже есть данные — не трогаем

        import os as _os

        providers: list[dict[str, Any]] = []
        seen_prefixes: set[str] = set()

        for key, val in _os.environ.items():
            if not key.endswith("_API_KEY") or not val:
                continue
            prefix = key.removesuffix("_API_KEY")
            if not prefix or prefix.upper() in seen_prefixes:
                continue
            model = _os.environ.get(f"{prefix}_MODEL", "")
            if not model:
                continue
            api_base = _os.environ.get(f"{prefix}_API_BASE", "")
            providers.append(
                {
                    "name": prefix.lower(),
                    "model": model,
                    "api_key": val,
                    "api_base": api_base,
                    "enabled": True,
                    "source": "env",
                    "label": prefix.lower(),
                    "provider": "",
                }
            )
            seen_prefixes.add(prefix.upper())

        if not providers:
            return

        for ep in providers:
            if not ep["model"]:
                continue
            self._providers[ep["name"]] = {
                "model": ep["model"],
                "api_key": ep["api_key"] or "",
                "api_base": ep["api_base"] or "",
                "enabled": ep["enabled"],
                "source": "env",
                "label": ep["label"],
                "provider": ep["provider"],
            }

        self._save()
        logger.info(
            "[ENV] Imported %d providers from environment variables: %s",
            len(providers),
            [p["name"] for p in providers],
        )

    # ── File I/O ──────────────────────────────────────────────────────

    def _load(self) -> None:
        """Загружает провайдеров из JSON-файла."""
        if not self._path.exists():
            logger.info(
                "Provider store file not found at %s — starting empty", self._path
            )
            self._providers = {}
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            self._providers = data.get("providers", {})
            logger.info("Loaded %d providers from %s", len(self._providers), self._path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load provider store: %s — starting empty", exc)
            self._providers = {}

    def _save(self) -> None:
        """Сохраняет провайдеров в JSON-файл."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"providers": self._providers}, f, indent=2, ensure_ascii=False)
        # Restrict file permissions (Unix only)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    def reload(self) -> None:
        """Перезагружает провайдеров с диска (hot-reload)."""
        with self._lock:
            self._load()

    # ── CRUD ──────────��───────────────────────────────────────────────

    def list_providers(self) -> list[dict[str, Any]]:
        """Возвращает список всех провайдеров с замаскированными ключами."""
        with self._lock:
            result = []
            for name, p in self._providers.items():
                entry = {
                    "name": name,
                    "model": p.get("model", ""),
                    "api_base": p.get("api_base", ""),
                    "enabled": p.get("enabled", True),
                    "api_key_masked": mask_api_key(p.get("api_key")),
                    "provider": p.get("provider", ""),
                    "has_api_key": bool(p.get("api_key")),
                    "source": p.get("source", "store"),
                }
                result.append(entry)
            return sorted(result, key=lambda x: x["name"])

    def get_provider(self, name: str) -> dict[str, Any] | None:
        """Возвращает одного провайдера с маскированным ключом."""
        with self._lock:
            p = self._providers.get(name)
            if not p:
                return None
            return {
                "name": name,
                "model": p.get("model", ""),
                "api_base": p.get("api_base", ""),
                "enabled": p.get("enabled", True),
                "api_key_masked": mask_api_key(p.get("api_key")),
                "provider": p.get("provider", ""),
                "has_api_key": bool(p.get("api_key")),
                "label": p.get("label", name),
                "source": p.get("source", "store"),
            }

    def add_provider(
        self,
        name: str,
        model: str,
        provider: str = "",
        api_key: str | None = None,
        api_base: str | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Добавляет нового провайдера.

        Returns:
            dict с маскированными данными
        """
        with self._lock:
            if name in self._providers:
                raise ValueError(f"Provider '{name}' already exists")

            if not model:
                raise ValueError("model is required")

            entry: dict[str, Any] = {
                "model": model,
                "api_key": api_key or "",
                "api_base": api_base or "",
                "enabled": enabled,
                "provider": provider or "",
                "label": name,
                "source": "store",
            }
            self._providers[name] = entry
            self._save()

            return {
                "name": name,
                "model": model,
                "api_base": api_base or "",
                "enabled": enabled,
                "api_key_masked": mask_api_key(api_key),
                "provider": provider or "",
                "has_api_key": bool(api_key),
            }

    def update_provider(
        self,
        name: str,
        model: str | None = None,
        provider: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
        enabled: bool | None = None,
        label: str | None = None,
    ) -> dict[str, Any] | None:
        """Обновляет существующего провайдера.

        Если поле None — не меняется.
        Если api_key="" (пустая строка) — ключ не меняется (для маскировки).
        Если api_key="__clear__" — ключ очищается.

        Returns:
            dict с маскированными данными или None если не найден
        """
        with self._lock:
            p = self._providers.get(name)
            if not p:
                return None

            if model is not None:
                p["model"] = model
            if provider is not None:
                p["provider"] = provider
            if api_base is not None:
                p["api_base"] = api_base
            if enabled is not None:
                p["enabled"] = enabled
            if label is not None:
                p["label"] = label

            # При ручном редактировании через админку — снимаем маркер "env"
            p["source"] = "store"

            # Специальное значение __clear__ для очистки ключа
            if api_key == "__clear__":
                p["api_key"] = ""
            elif api_key is not None and api_key != "":
                p["api_key"] = api_key

            self._save()

            return {
                "name": name,
                "model": p.get("model", ""),
                "api_base": p.get("api_base", ""),
                "enabled": p.get("enabled", True),
                "api_key_masked": mask_api_key(p.get("api_key")),
                "provider": p.get("provider", ""),
                "has_api_key": bool(p.get("api_key")),
                "label": p.get("label", name),
                "source": p.get("source", "store"),
            }

    def delete_provider(self, name: str) -> bool:
        """Удаляет провайдера.

        Returns:
            True если удалён, False если не найден
        """
        with self._lock:
            if name not in self._providers:
                return False
            del self._providers[name]
            self._save()
            return True

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any] | None:
        """Включает/выключает провайдера."""
        return self.update_provider(name, enabled=enabled)

    # ── Получение данных для LiteLLM Router ───────────────────────────

    def get_active_router_config(self) -> list[dict[str, Any]]:
        """Возвращает конфигурацию для litellm.Router.

        Только enabled провайдеры с model и api_key.
        Ключи возвращаются полностью (для использования в коде).
        """
        with self._lock:
            model_list: list[dict[str, Any]] = []
            for name, p in self._providers.items():
                if not p.get("enabled", True):
                    continue
                model = p.get("model", "")
                api_key = p.get("api_key", "")
                if not model or not api_key:
                    continue
                entry: dict[str, Any] = {
                    "model_name": name,
                    "litellm_params": {
                        "model": model,
                        "api_key": api_key,
                        "timeout": 600,
                        "temperature": 0.5,
                    },
                }
                api_base = p.get("api_base", "")
                if api_base:
                    entry["litellm_params"]["api_base"] = api_base
                model_list.append(entry)
            return model_list

    def get_fallback_enabled(self) -> bool:
        """Возвращает, включён ли fallback (есть ли хотя бы один активный провайдер)."""
        return len(self.get_active_router_config()) > 0

    @property
    def all_providers_raw(self) -> dict[str, dict[str, Any]]:
        """Возвращает сырые данные (только для внутреннего использования)."""
        return self._providers


# ── Глобальный синглтон ──────────────────────────────────────────────

_provider_store: ProviderStore | None = None
_provider_store_lock = threading.Lock()


def get_provider_store() -> ProviderStore:
    """Возвращает глобальный ProviderStore (singleton)."""
    global _provider_store
    if _provider_store is None:
        with _provider_store_lock:
            if _provider_store is None:
                _provider_store = ProviderStore()
    return _provider_store
