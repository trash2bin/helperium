"""Regression: ChatRequest.session_id must not silently default to a shared value.

Pentest 2026-09-13: ``session_id`` defaults to ``"default"``, so any client
that omits it is merged into one global ``direct:default`` conversation —
cross-client context bleed and transcript poisoning. The route already has a
"Missing session_id." guard, but it is dead code because the model never
leaves ``session_id`` unset. The field must be required (or default to None
with the route rejecting None) — either way an omitted session_id can never
resolve to a static shared key.
"""

from __future__ import annotations

from helperium_sdk.api.models import ChatRequest


def test_session_id_is_required() -> None:
    field = ChatRequest.model_fields["session_id"]
    assert field.is_required(), (
        f"session_id must be required, got default={field.default!r} — "
        "a static default merges all session-less clients into one transcript"
    )
