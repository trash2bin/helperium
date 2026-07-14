"""Контрактный тест #1: Route ordering — FastAPI route resolution.

Проверяет что все chat-роуты отвечают 422/4xx, а НЕ 404.
Ловит баг: /api/chat/voice перехватывается /api/chat/{name}
(route defined earlier wins — /api/chat/voice ДОЛЖЕН быть ДО /{name}).

Related: api-service/src/api_service/server.py, строка ~1118
"""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def _get_app():
    """Load the API app via module reload (same pattern as test_embed_mount.py)."""
    import api_service.server as sv

    if hasattr(sv, "app"):
        del sv.app
    importlib.reload(sv)
    return sv.app


def _request(client, method: str, path: str, **kwargs):
    """Make a request and return (status_code, body)."""
    resp = client.request(method, path, **kwargs)
    body = b""
    try:
        body = resp.content
    except Exception:
        pass
    return resp.status_code, body


class TestChatRouteOrdering:
    """Все chat-эндпоинты должны быть resolvable (не 404)."""

    def test_chat_voice_route(self):
        """POST /api/chat/voice должен найтись (не 404)."""
        app = _get_app()
        with TestClient(app) as client:
            # Пустой multipart не проходит валидацию, но 422 ≠ 404
            status, body = _request(client, "POST", "/api/chat/voice")
            assert status != 404, (
                f"/api/chat/voice вернул 404 — route не сматчился. "
                f"Возможно /api/chat/{{name}} определён раньше '/chat/voice'. "
                f"Status: {status}, body: {body[:200]}"
            )

    def test_chat_agent_name_route(self):
        """POST /api/chat/default должен найтись (route {name})."""
        app = _get_app()
        with TestClient(app) as client:
            status, body = _request(client, "POST", "/api/chat/default")
            assert status != 404, (
                f"/api/chat/default вернул 404 — route {{{{name}}}} не сматчился. "
                f"Status: {status}, body: {body[:200]}"
            )

    def test_chat_no_name_route(self):
        """POST /api/chat должен найтись (route без имени)."""
        app = _get_app()
        with TestClient(app) as client:
            status, body = _request(client, "POST", "/api/chat")
            assert status != 404, (
                f"/api/chat вернул 404 — route без имени не сматчился. "
                f"Status: {status}, body: {body[:200]}"
            )

    def test_voice_not_captured_by_name_param(self):
        """/api/chat/voice не должен резолвиться как name='voice'.

        Если бы /api/chat/{name} стоял раньше /api/chat/voice, то
        name='voice' и поиск агента 'voice' дал бы 404 — а должен
        быть 422 (ValidationError от Form/File параметров).
        """
        app = _get_app()
        with TestClient(app) as client:
            status, body = _request(client, "POST", "/api/chat/voice")
            # 422 = валидация (нет multipart), 404 = route не найден
            assert status == 422, (
                f"/api/chat/voice резолвится как /api/chat/{{name}} с name='voice', "
                f"а не как /api/chat/voice. Status: {status}, body: {body[:200]}. "
                f"Ожидался 422 (ValidationError от UploadFile/Form)."
            )

    def test_nonexistent_agent_returns_404_from_handler_not_router(self):
        """POST /api/chat/nonexistent возвращает 404 (агент не найден),
        но тело содержит ошибку об агенте, не 'Not Found' от роутера.
        """
        app = _get_app()
        with TestClient(app) as client:
            status, body = _request(client, "POST", "/api/chat/nonexistent")
            # Route сматчился (это не 404 от FastAPI router), хендлер вернул 404
            assert status == 404, (
                f"/api/chat/nonexistent ожидался 404, получен {status}. "
                f"Проверь что route {{name}} существует."
            )
            # Тело должно быть SSE, не стандартный 'Not Found'
            body_str = (
                body.decode("utf-8", errors="replace")
                if isinstance(body, bytes)
                else ""
            )
            assert (
                "error" in body_str.lower()
                or "agent" in body_str.lower()
                or "not found" in body_str.lower()
                or body_str == ""
            ), f"Ожидался SSE с ошибкой об агенте, тело: {body_str[:200]}"

    def test_voice_config_get_route(self):
        """GET /api/voice-config должен найтись (не 404)."""
        app = _get_app()
        with TestClient(app) as client:
            status, body = _request(client, "GET", "/api/voice-config")
            assert status != 404, (
                f"/api/voice-config вернул 404. Status: {status}, body: {body[:200]}"
            )

    def test_voice_config_put_route(self):
        """PUT /api/voice-config должен найтись (не 404)."""
        app = _get_app()
        with TestClient(app) as client:
            status, body = _request(
                client, "PUT", "/api/voice-config", json={"stt_providers": []}
            )
            assert status != 404, (
                f"/api/voice-config (PUT) вернул 404. "
                f"Status: {status}, body: {body[:200]}"
            )
