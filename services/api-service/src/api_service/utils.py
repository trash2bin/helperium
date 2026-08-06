"""Shared utilities for api-service."""

from __future__ import annotations


def preserve_fields(
    body_providers: list[dict], current_providers: list, fields: list[str]
) -> None:
    """Preserve masked sensitive fields when updating provider configs.

    Frontend sends empty strings for api_key/api_base/voice to avoid
    exposing secrets in the PUT body. This function fills them back
    from the currently stored values when the incoming value is falsy.
    """
    existing = {p.name: p for p in current_providers}
    for p in body_providers:
        for field in fields:
            if not p.get(field):
                prev = existing.get(p.get("name", ""))
                if prev and getattr(prev, field, None):
                    p[field] = getattr(prev, field)
