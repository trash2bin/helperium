"""Tests for api_service.utils — shared utility functions."""

from __future__ import annotations


from api_service.utils import preserve_fields


class _FakeProvider:
    """Minimal provider-like object for testing preserve_fields."""

    def __init__(self, name: str, **attrs):
        self.name = name
        for k, v in attrs.items():
            setattr(self, k, v)


class TestPreserveFields:
    """Tests for preserve_fields() — backfill falsy fields from existing."""

    def test_fills_back_falsy_fields(self):
        """Falsy field in body → filled from existing provider."""
        existing = [_FakeProvider("p1", api_key="secret-123", api_base="https://old")]
        body = [{"name": "p1", "api_key": "", "api_base": ""}]
        preserve_fields(body, existing, ["api_key", "api_base"])
        assert body[0]["api_key"] == "secret-123"
        assert body[0]["api_base"] == "https://old"

    def test_does_not_overwrite_non_falsy(self):
        """Non-falsy field in body → kept as-is."""
        existing = [_FakeProvider("p1", api_key="old-key", api_base="https://old")]
        body = [{"name": "p1", "api_key": "new-key", "api_base": "https://new"}]
        preserve_fields(body, existing, ["api_key", "api_base"])
        assert body[0]["api_key"] == "new-key"
        assert body[0]["api_base"] == "https://new"

    def test_empty_body_providers(self):
        """Empty body list → no crash, no mutation."""
        existing = [_FakeProvider("p1", api_key="secret")]
        body: list[dict] = []
        preserve_fields(body, existing, ["api_key"])
        assert body == []

    def test_no_matching_existing_provider(self):
        """Body provider name not in existing → field stays falsy."""
        existing = [_FakeProvider("p1", api_key="secret")]
        body = [{"name": "unknown", "api_key": ""}]
        preserve_fields(body, existing, ["api_key"])
        assert body[0]["api_key"] == ""

    def test_partial_fill(self):
        """Only matching fields are filled; others untouched."""
        existing = [_FakeProvider("p1", api_key="secret", api_base="https://old")]
        body = [{"name": "p1", "api_key": "", "api_base": "https://new"}]
        preserve_fields(body, existing, ["api_key", "api_base"])
        assert body[0]["api_key"] == "secret"  # filled
        assert body[0]["api_base"] == "https://new"  # kept

    def test_multiple_providers(self):
        """Multiple body providers each matched to their own existing."""
        existing = [
            _FakeProvider("p1", api_key="key-1"),
            _FakeProvider("p2", api_key="key-2"),
        ]
        body = [
            {"name": "p1", "api_key": ""},
            {"name": "p2", "api_key": ""},
        ]
        preserve_fields(body, existing, ["api_key"])
        assert body[0]["api_key"] == "key-1"
        assert body[1]["api_key"] == "key-2"

    def test_existing_attr_is_none(self):
        """Existing provider has attr=None → field stays falsy."""
        existing = [_FakeProvider("p1", api_key=None)]
        body = [{"name": "p1", "api_key": ""}]
        preserve_fields(body, existing, ["api_key"])
        assert body[0]["api_key"] == ""

    def test_field_missing_from_body_dict(self):
        """Field not present in body dict at all → no crash."""
        existing = [_FakeProvider("p1", api_key="secret")]
        body: list[dict] = [{"name": "p1"}]
        preserve_fields(body, existing, ["api_key"])
        # .get("api_key") returns None which is falsy, so it gets filled
        assert body[0]["api_key"] == "secret"

    def test_multiple_fields(self):
        """Multiple fields filled correctly."""
        existing = [_FakeProvider("p1", api_key="k", api_base="b", voice="v")]
        body = [{"name": "p1", "api_key": "", "api_base": "", "voice": ""}]
        preserve_fields(body, existing, ["api_key", "api_base", "voice"])
        assert body[0]["api_key"] == "k"
        assert body[0]["api_base"] == "b"
        assert body[0]["voice"] == "v"
