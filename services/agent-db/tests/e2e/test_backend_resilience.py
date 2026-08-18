"""Docker E2E regressions for backend dependency failures."""

from __future__ import annotations

import uuid

import requests

from tests.e2e.helpers import admin_headers, data_service_url


def test_unavailable_tenant_database_returns_safe_retryable_error() -> None:
    """Onboarding must not leak a client DSN when its database is offline."""

    tenant_id = f"e2e-offline-db-{uuid.uuid4().hex[:8]}"
    config = {
        "version": 1,
        "data_source": {
            "driver": "postgres",
            "dsn": "postgres://e2e_user:secret@127.0.0.1:1/client_db?sslmode=disable",
        },
        "entities": [],
        "endpoints": [],
    }

    response = requests.post(
        f"{data_service_url()}/admin/tenants",
        headers=admin_headers(),
        json={"id": tenant_id, "config": config},
        timeout=15,
    )

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["error"] == "tenant_database_unavailable"
    assert "retry" in body["message"].lower()
    for sensitive in ("127.0.0.1", "e2e_user", "secret", "client_db", "postgres://"):
        assert sensitive not in response.text
