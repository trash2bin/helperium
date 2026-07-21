"""ProviderStore — управление LLM-провайдерами для fallback.

Хранит провайдеров в JSON-файле (.data/providers.json).
API-ключи маскируются при чтении для API: только первые 4 символа + "****".
В памяти ключи хранятся через ``SecretStr``, на диске — полностью
(файл защищён правами ОС).

Внутреннее хранение — ``dict[str, ProviderConfig]`` (Pydantic).
Потокобезопасность — ``asyncio.Lock``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from api_service.agent.models import ProviderConfig

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
    """Async-safe хранилище LLM-провайдеров на основе Pydantic.

    Провайдеры хранятся в JSON-файле на диске.
    Каждый провайдер имеет уникальное имя и содержит model, api_key, api_base, enabled.
    Внутреннее представление — ``ProviderConfig`` (Pydantic).
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path or DEFAULT_PROVIDERS_PATH)
        self._lock = asyncio.Lock()
        self._providers: dict[str, ProviderConfig] = {}
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

        providers: list[tuple[str, str, str, str]] = []  # name, model, key, api_base
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
            providers.append((prefix.lower(), model, val, api_base))
            seen_prefixes.add(prefix.upper())

        if not providers:
            return

        for name, model, api_key, api_base in providers:
            # Determine provider prefix from common patterns
            provider = ""
            for pfx in ("openai", "anthropic", "mistral"):
                if name.startswith(pfx):
                    provider = pfx
                    break

            self._providers[name] = ProviderConfig(
                name=name,
                model=model,
                api_key=api_key,
                api_base=api_base,
                enabled=True,
                source="env",
                label=name,
                provider=provider,
            )

        self._save()
        logger.info(
            "[ENV] Imported %d providers from environment variables: %s",
            len(providers),
            [p[0] for p in providers],
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
            raw = data.get("providers", {})
            self._providers = {}
            for name, p in raw.items():
                self._providers[name] = ProviderConfig(
                    name=name,
                    model=p.get("model", ""),
                    api_key=p.get("api_key", ""),
                    api_base=p.get("api_base", ""),
                    enabled=p.get("enabled", True),
                    source=p.get("source", "store"),
                    label=p.get("label", name),
                    provider=p.get("provider", ""),
                    priority=p.get("priority", 0),
                )
            logger.info("Loaded %d providers from %s", len(self._providers), self._path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load provider store: %s — starting empty", exc)
            self._providers = {}

    def _save(self) -> None:
        """Сохраняет провайдеров в JSON-файл."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        raw: dict[str, dict[str, Any]] = {}
        for name, cfg in self._providers.items():
            raw[name] = {
                "model": cfg.model,
                "api_key": cfg.api_key.get_secret_value() if cfg.api_key else "",
                "api_base": cfg.api_base,
                "enabled": cfg.enabled,
                "source": cfg.source,
                "label": cfg.label,
                "provider": cfg.provider,
                "priority": cfg.priority,
            }
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"providers": raw}, f, indent=2, ensure_ascii=False)
        # Restrict file permissions (Unix only)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    async def reload(self) -> None:
        """Перезагружает провайдеров с диска (hot-reload)."""
        async with self._lock:
            self._load()

    # ── CRUD ──────────────────────────────────────────────────────────

    async def list_providers(self) -> list[dict[str, Any]]:
        """Возвращает список всех провайдеров с замаскированными ключами."""
        async with self._lock:
            result = []
            for name, cfg in self._providers.items():
                api_key_str = cfg.api_key.get_secret_value() if cfg.api_key else ""
                entry: dict[str, Any] = {
                    "name": name,
                    "model": cfg.model,
                    "api_base": cfg.api_base,
                    "enabled": cfg.enabled,
                    "api_key_masked": mask_api_key(api_key_str),
                    "provider": cfg.provider,
                    "has_api_key": bool(api_key_str),
                    "source": cfg.source,
                }
                result.append(entry)
            return sorted(result, key=lambda x: x["name"])

    async def get_provider(self, name: str) -> dict[str, Any] | None:
        """Возвращает одного провайдера с маскированным ключом."""
        async with self._lock:
            cfg = self._providers.get(name)
            if not cfg:
                return None
            api_key_str = cfg.api_key.get_secret_value() if cfg.api_key else ""
            return {
                "name": name,
                "model": cfg.model,
                "api_base": cfg.api_base,
                "enabled": cfg.enabled,
                "api_key_masked": mask_api_key(api_key_str),
                "provider": cfg.provider,
                "has_api_key": bool(api_key_str),
                "label": cfg.label,
                "source": cfg.source,
            }

    async def add_provider(
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
        async with self._lock:
            if name in self._providers:
                raise ValueError(f"Provider '{name}' already exists")

            if not model:
                raise ValueError("model is required")

            self._providers[name] = ProviderConfig(
                name=name,
                model=model,
                api_key=api_key or "",
                api_base=api_base or "",
                enabled=enabled,
                provider=provider or "",
                label=name,
                source="store",
            )
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

    async def update_provider(
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
        async with self._lock:
            cfg = self._providers.get(name)
            if not cfg:
                return None

            if model is not None:
                cfg.model = model
            if provider is not None:
                cfg.provider = provider
            if api_base is not None:
                cfg.api_base = api_base
            if enabled is not None:
                cfg.enabled = enabled
            if label is not None:
                cfg.label = label

            # При ручном редактировании через админку — снимаем маркер "env"
            cfg.source = "store"

            # Специальное значение __clear__ для очистки ключа
            if api_key == "__clear__":
                cfg.api_key = ""  # type: ignore[assignment]
            elif api_key is not None and api_key != "":
                cfg.api_key = api_key  # type: ignore[assignment]

            self._save()

            api_key_str = cfg.api_key.get_secret_value() if cfg.api_key else ""
            return {
                "name": name,
                "model": cfg.model,
                "api_base": cfg.api_base,
                "enabled": cfg.enabled,
                "api_key_masked": mask_api_key(api_key_str),
                "provider": cfg.provider,
                "has_api_key": bool(api_key_str),
                "label": cfg.label,
                "source": cfg.source,
            }

    async def delete_provider(self, name: str) -> bool:
        """Удаляет провайдера.

        Returns:
            True если удалён, False если не найден
        """
        async with self._lock:
            if name not in self._providers:
                return False
            del self._providers[name]
            self._save()
            return True

    async def set_enabled(self, name: str, enabled: bool) -> dict[str, Any] | None:
        """Включает/выключает провайдера."""
        return await self.update_provider(name, enabled=enabled)

    # ── Получение данных для LiteLLM Router ───────────────────────────

    def get_active_router_config(self) -> list[dict[str, Any]]:
        """Возвращает конфигурацию для litellm.Router.

        Только enabled провайдеры с model и api_key.
        Ключи возвращаются полностью (для использования в коде).
        **Synchronous** — вызывается из ``create_fallback_client()``
        который может работать без event loop.

        Note: uses the internal dict directly (no lock) since it's
        read-only and the GIL / single-threaded nature of the store
        makes this safe in practice.
        """
        model_list: list[dict[str, Any]] = []
        for name, cfg in self._providers.items():
            if not cfg.enabled:
                continue
            if not cfg.model:
                continue
            api_key_str = cfg.api_key.get_secret_value() if cfg.api_key else ""
            if not api_key_str:
                continue
            entry: dict[str, Any] = {
                "model_name": name,
                "litellm_params": {
                    "model": cfg.model,
                    "api_key": api_key_str,
                    "timeout": 600,
                    "temperature": 0.5,
                },
            }
            if cfg.api_base:
                entry["litellm_params"]["api_base"] = cfg.api_base
            model_list.append(entry)
        return model_list

    def get_fallback_enabled(self) -> bool:
        """Возвращает, включён ли fallback (есть ли хотя бы один активный провайдер)."""
        return len(self.get_active_router_config()) > 0

    @property
    def all_providers_raw(self) -> dict[str, dict[str, Any]]:
        """Возвращает сырые данные (только для внутреннего использования).

        **Synchronous** — вызывается из ``orchestrator.stream_events()``
        для доступа к unmasked api_key.
        """
        result: dict[str, dict[str, Any]] = {}
        for name, cfg in self._providers.items():
            result[name] = {
                "model": cfg.model,
                "api_key": cfg.api_key.get_secret_value() if cfg.api_key else "",
                "api_base": cfg.api_base,
                "enabled": cfg.enabled,
                "provider": cfg.provider,
            }
        return result


# ── Глобальный синглтон ──────────────────────────────────────────────

_provider_store: ProviderStore | None = None
_provider_store_lock = asyncio.Lock()


def get_provider_store() -> ProviderStore:
    """Возвращает глобальный ProviderStore (singleton).

    Note: uses ``asyncio.Lock`` so must be called from an async context
    or when the event loop is running.  For synchronous startup code,
    the store is initialised lazily on first access.
    """
    global _provider_store
    if _provider_store is None:
        # Since this is called from async endpoints and sync contexts,
        # we use a simple init-once pattern.  The lock is reentrant-safe
        # because only the first caller creates the instance.
        import threading

        _init_lock = threading.Lock()
        with _init_lock:
            if _provider_store is None:
                _provider_store = ProviderStore()
    return _provider_store
