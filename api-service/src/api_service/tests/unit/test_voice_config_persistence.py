"""Контрактный тест #2: API key persistence — masked fields не затирают секреты.

Проверяет что PUT /api/voice-config с пустым api_key НЕ удаляет старый ключ.
Это защита от бага: фронтенд присылает маскированные поля (пустая строка),
и сервер не должен перезаписывать существующий ключ пустотой.

Related: api-service/src/api_service/server.py update_voice_config()
"""

from __future__ import annotations

import importlib
import json

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
    """Restore global session_db_path after each test to avoid cross-test contamination."""
    yield
    if _ORIGINAL_SESSION_DB_PATH:
        import helperium_sdk.settings as sdk_settings

        sdk_settings.settings.session_db_path = _ORIGINAL_SESSION_DB_PATH
        # Reset VoiceConfigStore singleton too
        import api_service.audio.voice_config as vc_mod

        vc_mod._voice_config_store = None


def _get_app(monkeypatch, tmp_path):
    """Load app with voice config pointing to a temp file."""
    # VoiceConfigStore вычисляет путь как:
    #   Path(settings.session_db_path).parent / "voice_config.json"
    # settings — единожды созданный модульный синглтон.
    # Устанавливаем путь напрямую, а не через monkeypatch.setenv.
    voice_dir = tmp_path / "sessions"
    voice_path = voice_dir / "voice_config.json"

    # Write initial config with a real key
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
        "tts_providers": [
            {
                "name": "Test TTS",
                "provider": "litellm",
                "model": "tts-1",
                "voice": "alloy",
                "api_key": "tts-secret-456",
                "api_base": None,
                "enabled": False,
            }
        ],
        "stt_fallback_enabled": True,
        "tts_fallback_enabled": True,
        "max_voice_message_size": 10485760,
        "min_voice_interval_seconds": 10,
        "max_voice_duration_seconds": 120,
    }
    voice_path.parent.mkdir(parents=True, exist_ok=True)
    voice_path.write_text(json.dumps(initial, indent=2))

    # Set session_db_path directly on the shared singleton — monkeypatch.setenv
    # won't affect already-imported `settings` objects.
    import helperium_sdk.settings as sdk_settings

    sdk_settings.settings.session_db_path = str(voice_dir / "sessions.db")

    # Clear VoiceConfigStore singleton so it re-reads settings on next get()
    import api_service.audio.voice_config as vc_mod

    vc_mod._voice_config_store = None

    import api_service.server as sv

    if hasattr(sv, "app"):
        del sv.app
    importlib.reload(sv)
    return sv.app, voice_path


class TestVoiceConfigKeyPreservation:
    """API ключи не должны затираться маскированными полями."""

    def test_put_empty_api_key_does_not_erase_stt_key(self, monkeypatch, tmp_path):
        """PUT с пустым api_key не удаляет STT ключ."""
        app, voice_path = _get_app(monkeypatch, tmp_path)
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
                    "tts_providers": [
                        {
                            "name": "Test TTS",
                            "provider": "litellm",
                            "model": "tts-1",
                            "voice": "alloy",
                            "api_key": "",
                            "api_base": "",
                            "enabled": False,
                        }
                    ],
                    "stt_fallback_enabled": True,
                    "tts_fallback_enabled": True,
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

    def test_put_empty_api_key_does_not_erase_tts_key(self, monkeypatch, tmp_path):
        """PUT с пустым api_key не удаляет TTS ключ."""
        app, voice_path = _get_app(monkeypatch, tmp_path)
        with TestClient(app) as client:
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
                    "tts_providers": [
                        {
                            "name": "Test TTS",
                            "provider": "litellm",
                            "model": "tts-1",
                            "voice": "",
                            "api_key": "",
                            "api_base": "",
                            "enabled": False,
                        }
                    ],
                    "stt_fallback_enabled": True,
                    "tts_fallback_enabled": True,
                    "max_voice_message_size": 10485760,
                    "min_voice_interval_seconds": 10,
                    "max_voice_duration_seconds": 120,
                },
            )
            assert put_resp.status_code == 200
            body = put_resp.json()

            # TTS api_key должен сохраниться
            tts_key = body["tts_providers"][0].get("api_key")
            assert tts_key == "tts-secret-456", (
                f"TTS api_key был перезаписан пустотой! "
                f"Ожидалось 'tts-secret-456', получено {tts_key!r}"
            )

            # TTS voice должен сохраниться
            tts_voice = body["tts_providers"][0].get("voice")
            assert tts_voice == "alloy", (
                f"TTS voice был перезаписан пустотой! "
                f"Ожидалось 'alloy', получено {tts_voice!r}"
            )

    def test_put_new_api_key_can_override(self, monkeypatch, tmp_path):
        """PUT с НОВЫМ api_key должен обновлять ключ (это intentional update)."""
        app, voice_path = _get_app(monkeypatch, tmp_path)
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
                    "tts_providers": [],
                    "stt_fallback_enabled": True,
                    "tts_fallback_enabled": True,
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
        app, voice_path = _get_app(monkeypatch, tmp_path)
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
                    "tts_providers": [],
                    "stt_fallback_enabled": True,
                    "tts_fallback_enabled": True,
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
