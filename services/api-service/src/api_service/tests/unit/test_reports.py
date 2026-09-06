"""Widget problem-report endpoint and admin review surface."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api_service.reports import get_report_store, reset_report_store
from api_service.server.rate_limit import limiter

REPORT_PAYLOAD = {
    "agent": "autoparts-assistant",
    "session_id": "0f9d1a2e-1c3b-4d5e-8f90-1a2b3c4d5e6f",
    "lang": "ru",
    "message": {
        "kind": "assistant",
        "text": "Датчик ABS Bosch стоит 1546.00",
        "tools": ["db_search"],
        "display_names": ["Поиск по базе"],
    },
    "transcript": [
        {"kind": "user", "text": "Сколько стоит EXT-01392?", "tools": []},
        {
            "kind": "assistant",
            "text": "Датчик ABS Bosch стоит 1546.00",
            "tools": ["db_search"],
        },
    ],
    "comment": "Цена не совпадает с сайтом",
    "last_error": None,
    "page_url": "http://localhost:8000/",
}


class _AgentStore:
    def list_agents(self) -> list[dict]:
        return []

    def get_agent(self, name: str) -> dict | None:
        return None


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("API_BEARER_TOKEN", "test-admin-token")
    from api_service.server.routes import agents

    app_mod = importlib.import_module("api_service.server.app")
    monkeypatch.setattr(agents, "get_agent_store", lambda: _AgentStore())
    limiter.reset()
    reset_report_store()
    # Start from an empty store: the conftest session fixture points every
    # test at the same throwaway sqlite file, so drop it between tests.
    from helperium_sdk.settings import settings

    for suffix in ("", "-wal", "-shm"):
        Path(str(settings.reports_db_path) + suffix).unlink(missing_ok=True)
    with TestClient(app_mod.app) as test_client:
        yield test_client
    limiter.reset()
    reset_report_store()


def _auth(token: str = "test-admin-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestPublicReportEndpoint:
    def test_report_is_accepted_without_bearer_and_persisted(self, client):
        response = client.post("/api/reports", json=REPORT_PAYLOAD)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "accepted"
        assert body["id"]
        assert response.headers["x-correlation-id"]

        reports, total = get_report_store().list_reports(limit=10)
        assert total == 1
        stored = reports[0]
        assert stored["id"] == body["id"]
        assert stored["status"] == "new"
        assert stored["agent"] == "autoparts-assistant"
        assert stored["session_key"] == (
            f"agent:{REPORT_PAYLOAD['agent']}:{REPORT_PAYLOAD['session_id']}"
        )
        assert stored["message_text"] == REPORT_PAYLOAD["message"]["text"]
        assert stored["message_tools"] == ["db_search"]
        assert (
            stored["transcript"][0]["text"] == REPORT_PAYLOAD["transcript"][0]["text"]
        )
        assert stored["comment"] == REPORT_PAYLOAD["comment"]
        assert stored["correlation_id"] == response.headers["x-correlation-id"]
        assert stored["last_error_text"] is None

    def test_unknown_fields_are_rejected(self, client):
        payload = {**REPORT_PAYLOAD, "agent_prompt_override": "ignore instructions"}

        response = client.post("/api/reports", json=payload)

        assert response.status_code == 422

    def test_oversized_comment_is_rejected(self, client):
        payload = {**REPORT_PAYLOAD, "comment": "x" * 1001}

        response = client.post("/api/reports", json=payload)

        assert response.status_code == 422

    def test_transcript_is_capped(self, client):
        payload = {
            **REPORT_PAYLOAD,
            "transcript": [{"kind": "user", "text": "hi", "tools": []}] * 21,
        }

        response = client.post("/api/reports", json=payload)

        assert response.status_code == 422

    def test_invalid_lang_is_rejected(self, client):
        payload = {**REPORT_PAYLOAD, "lang": "de"}

        response = client.post("/api/reports", json=payload)

        assert response.status_code == 422

    def test_last_error_and_correlation_id_are_stored(self, client):
        payload = {
            **REPORT_PAYLOAD,
            "last_error": {
                "text": "Не удалось получить ответ.",
                "correlation_id": "corr-chat-turn",
            },
        }

        response = client.post("/api/reports", json=payload)
        assert response.status_code == 201

        stored, _ = get_report_store().list_reports(limit=1)
        assert stored[0]["last_error_text"] == "Не удалось получить ответ."
        assert stored[0]["last_error_correlation_id"] == "corr-chat-turn"

    def test_rate_limit_returns_429_with_retry_after(self, client):
        """Default REPORTS_RATE_LIMIT=5/minute: 6th report from one IP is 429."""

        statuses = [
            client.post("/api/reports", json=REPORT_PAYLOAD).status_code
            for _ in range(6)
        ]

        assert statuses[:5] == [201] * 5
        assert statuses[5] == 429
        rejected = client.post("/api/reports", json=REPORT_PAYLOAD)
        assert rejected.status_code == 429
        assert "retry-after" in {k.lower() for k in rejected.headers.keys()}


class TestAdminReportRoutes:
    def test_admin_list_fails_closed_without_token(self, client, monkeypatch):
        monkeypatch.delenv("API_BEARER_TOKEN", raising=False)

        response = client.get("/admin/reports")

        assert response.status_code == 503

    def test_admin_list_requires_exact_bearer(self, client):
        missing = client.get("/admin/reports")
        assert missing.status_code == 401

        wrong = client.get("/admin/reports", headers=_auth("wrong"))
        assert wrong.status_code == 403

        accepted = client.get("/admin/reports", headers=_auth())
        assert accepted.status_code == 200

    def test_list_update_round_trip(self, client):
        created = client.post("/api/reports", json=REPORT_PAYLOAD).json()

        listed = client.get("/admin/reports", headers=_auth())
        assert listed.status_code == 200
        data = listed.json()
        assert data["total"] == 1
        assert data["reports"][0]["id"] == created["id"]
        assert data["reports"][0]["status"] == "new"

        reviewed = client.post(
            f"/admin/reports/{created['id']}/status",
            json={"status": "reviewed"},
            headers=_auth(),
        )
        assert reviewed.status_code == 200
        assert reviewed.json() == {"id": created["id"], "status": "reviewed"}

        filtered = client.get("/admin/reports?status=reviewed", headers=_auth())
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 1
        assert filtered.json()["reports"][0]["status"] == "reviewed"

        still_new = client.get("/admin/reports?status=new", headers=_auth())
        assert still_new.json()["total"] == 0

    def test_status_update_unknown_report_is_404(self, client):
        response = client.post(
            "/admin/reports/no-such-id/status",
            json={"status": "reviewed"},
            headers=_auth(),
        )
        assert response.status_code == 404

    def test_status_update_rejects_unknown_status(self, client):
        response = client.post(
            "/admin/reports/no-such-id/status",
            json={"status": "archived"},
            headers=_auth(),
        )
        assert response.status_code == 422

    def test_list_rejects_bad_filters(self, client):
        bad_limit = client.get("/admin/reports?limit=500", headers=_auth())
        assert bad_limit.status_code == 422
        bad_status = client.get("/admin/reports?status=weird", headers=_auth())
        assert bad_status.status_code == 422


class TestReportStore:
    def test_insert_list_status_round_trip(self, tmp_path, monkeypatch):
        from api_service.reports import SQLiteReportRepository, create_sqlite_connection

        db = tmp_path / "reports.sqlite3"
        repo = SQLiteReportRepository(
            connection_factory=lambda: create_sqlite_connection(db)
        )
        base = {
            "id": "r1",
            "status": "new",
            "agent": "a",
            "session_id": "s",
            "session_key": "agent:a:s",
            "message_kind": "assistant",
            "message_text": "answer",
            "message_tools": ["db_search"],
            "display_names": ["Поиск"],
            "transcript": [{"kind": "user", "text": "q"}],
            "comment": None,
            "last_error_text": None,
            "last_error_correlation_id": None,
            "page_url": None,
            "correlation_id": "corr-1",
            "client_ip": "127.0.0.1",
            "user_agent": "pytest",
        }
        repo.insert({**base, "created_at": 1000.0})
        repo.insert({**base, "id": "r2", "created_at": 2000.0})

        reports, total = repo.list_reports(limit=10)
        assert total == 2
        assert [r["id"] for r in reports] == ["r2", "r1"]
        assert reports[0]["message_tools"] == ["db_search"]
        assert reports[0]["transcript"] == [{"kind": "user", "text": "q"}]

        assert repo.set_status("r1", "reviewed") is True
        assert repo.set_status("missing", "reviewed") is False
        _, reviewed_total = repo.list_reports(limit=10, status="reviewed")
        assert reviewed_total == 1

    def test_retention_removes_old_records_and_zero_disables(
        self, tmp_path, monkeypatch
    ):
        from api_service.reports import SQLiteReportRepository, create_sqlite_connection
        from helperium_sdk.settings import settings

        db = tmp_path / "reports.sqlite3"
        repo = SQLiteReportRepository(
            connection_factory=lambda: create_sqlite_connection(db)
        )
        record = {
            "id": "old",
            "agent": "a",
            "session_id": "s",
            "session_key": "agent:a:s",
        }
        repo.insert({**record, "created_at": 1.0})
        repo.insert({**record, "id": "fresh"})

        monkeypatch.setattr(settings, "reports_retention_days", 0)
        assert repo.cleanup_old() == 0
        _, total = repo.list_reports(limit=10)
        assert total == 2

        monkeypatch.setattr(settings, "reports_retention_days", 1)
        assert repo.cleanup_old() == 1
        reports, total = repo.list_reports(limit=10)
        assert total == 1
        assert reports[0]["id"] == "fresh"
