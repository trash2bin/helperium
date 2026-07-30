"""Контрактный тест #2: API key persistence — masked fields не затирают секреты.

Проверяет что PUT /api/voice-config с пустым api_key НЕ удаляет старый ключ.
Это защита от бага: фронтенд присылает маскированные поля (пустая строка),
и сервер не должен перезаписывать существующий ключ пустотой.

Related: api-service/src/api_service/server/routes/voice.py update_voice_config()

Voice config теперь хранится в SQLite (таблица global_config в agents.sqlite).
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient


# Save original session_db_path for cleanup between tests
_ORIGINAL_SESSION_DB_PATH: str = ""


def _save_original_session_db_path() -> None:
    global _ORIGINAL_SESSION_DB_PATH
    if not _ORIGINAL_SESSION_DB_PATH:
        import helperium_sdk.settings as sdk_settings

        _ORIGINAL_SESSION_DB_PATH = sdk_settings.settings.session_db_path


@pytest.fixture(autouse=True)
def _restore_settings():
    """Restore global env after each test to avoid cross-test contamination."""
    yield
    if _ORIGINAL_SESSION_DB_PATH:
        import helperium_sdk.settings as sdk_settings

        sdk_settings.settings.session_db_path = _ORIGINAL_SESSION_DB_PATH
        # Reset voice config repo singleton too
        import api_service.audio.voice_config as vc_mod

        vc_mod._repo = None


def _get_app(monkeypatch, tmp_path):
    """Load app with voice config pre-seeded in a temp SQLite DB."""
    voice_dir = tmp_path / "sessions"
    voice_dir.mkdir(parents=True, exist_ok=True)
    agents_db = voice_dir / "agents.sqlite"

    # Seed global_config table with initial voice config
    initial = {
        "enabled": True,
        "stt_providers": [
            {
                "name": "Test STT",
                "provider": "litellm",
                "model": "whisper-1",
                "api_key": "secret-123",
                "api_base": "https://api.test.com/v1",
                "enabled": True,
            }
        ],
        "stt_fallback_enabled": True,
        "max_voice_message_size": 10485760,
        "min_voice_interval_seconds": 10,
        "max_voice_duration_seconds": 120,
    }
    conn = sqlite3.connect(str(agents_db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS global_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO global_config (key, value) VALUES (?, ?)",
        ("voice", json.dumps(initial)),
    )
    conn.commit()
    conn.close()

    # Point session_db_path so agents.sqlite lands in our temp dir
    import helperium_sdk.settings as sdk_settings

    sdk_settings.settings.session_db_path = str(voice_dir / "sessions.db")

    # Set AGENT_DB_PATH explicitly so voice_config finds it
    monkeypatch.setenv("AGENT_DB_PATH", str(agents_db))

    # Reset voice config repo singleton so it re-creates from temp path
    import api_service.audio.voice_config as vc_mod

    vc_mod._repo = None

    app_mod = importlib.reload(sys.modules["api_service.server.app"])
    return app_mod.app


class TestVoiceConfigKeyPreservation:
    """API ключи не должны затираться маскированными полями."""

    def test_put_empty_api_key_does_not_erase_stt_key(self, monkeypatch, tmp_path):
        """PUT с пустым api_key не удаляет STT ключ."""
        app = _get_app(monkeypatch, tmp_path)
        with TestClient(app) as client:
            # PUT с пустым api_key (фронт присылает маскированное поле)
            put_resp = client.put(
                "/api/voice-config",
                json={
                    "stt_providers": [
                        {
                            "name": "Test STT",
                            "provider": "litellm",
                            "model": "whisper-1",
                            "api_key": "",
                            "api_base": "",
                            "enabled": True,
                        }
                    ],
                    "stt_fallback_enabled": True,
                    "max_voice_message_size": 10485760,
                    "min_voice_interval_seconds": 10,
                    "max_voice_duration_seconds": 120,
                },
            )
            assert put_resp.status_code == 200, (
                f"PUT /api/voice-config failed: {put_resp.status_code} {put_resp.text[:200]}"
            )
            body = put_resp.json()

            # STT api_key должен сохраниться
            stt_key = body["stt_providers"][0].get("api_key")
            assert stt_key == "secret-123", (
                f"STT api_key был перезаписан пустотой! "
                f"Ожидалось 'secret-123', получено {stt_key!r}"
            )

            # STT api_base должен сохраниться
            stt_base = body["stt_providers"][0].get("api_base")
            assert stt_base == "https://api.test.com/v1", (
                f"STT api_base был перезаписан пустотой! "
                f"Ожидалось 'https://api.test.com/v1', получено {stt_base!r}"
            )

    def test_put_new_api_key_can_override(self, monkeypatch, tmp_path):
        """PUT с НОВЫМ api_key должен обновлять ключ (это intentional update)."""
        app = _get_app(monkeypatch, tmp_path)
        with TestClient(app) as client:
            put_resp = client.put(
                "/api/voice-config",
                json={
                    "stt_providers": [
                        {
                            "name": "Test STT",
                            "provider": "litellm",
                            "model": "whisper-1",
                            "api_key": "new-secret-789",
                            "api_base": "https://api.new.com/v1",
                            "enabled": True,
                        }
                    ],
                    "stt_fallback_enabled": True,
                    "max_voice_message_size": 10485760,
                    "min_voice_interval_seconds": 10,
                    "max_voice_duration_seconds": 120,
                },
            )
            assert put_resp.status_code == 200
            body = put_resp.json()

            stt_key = body["stt_providers"][0].get("api_key")
            assert stt_key == "new-secret-789", (
                f"Новый STT api_key не применился! "
                f"Ожидалось 'new-secret-789', получено {stt_key!r}"
            )

    def test_persistence_across_calls(self, monkeypatch, tmp_path):
        """Изменения voice config сохраняются между GET/PUT запросами."""
        app = _get_app(monkeypatch, tmp_path)
        with TestClient(app) as client:
            # 1. Устанавливаем ключ
            client.put(
                "/api/voice-config",
                json={
                    "stt_providers": [
                        {
                            "name": "Test STT",
                            "provider": "litellm",
                            "model": "whisper-1",
                            "api_key": "persist-key",
                            "api_base": "https://api.example.com/v1",
                            "enabled": True,
                        }
                    ],
                    "stt_fallback_enabled": True,
                    "max_voice_message_size": 10485760,
                    "min_voice_interval_seconds": 10,
                    "max_voice_duration_seconds": 120,
                },
            )

            # 2. Перечитываем — ключ должен быть на месте
            get_resp = client.get("/api/voice-config")
            assert get_resp.status_code == 200
            body = get_resp.json()
            assert body["stt_providers"][0]["api_key"] == "persist-key"
