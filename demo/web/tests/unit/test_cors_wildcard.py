"""Pentest F5: WEB_ORIGIN wildcard must fail closed on the demo edge.

``WEB_ORIGIN=*`` used to be handed straight to CORSMiddleware, reflecting
``access-control-allow-origin: *`` for every origin. Combined with the
bearer-injected proxy routes this enabled drive-by exfiltration from any
web page. The wildcard is now rejected at parse time: only explicit origins
are honoured, otherwise the documented dev default applies.
"""

from __future__ import annotations

from demo.web.server import _cors_origins_from

DEFAULT = ["http://localhost:8080"]


def test_wildcard_alone_falls_back_to_default():
    assert _cors_origins_from("*") == DEFAULT


def test_mixed_wildcard_falls_back_to_default():
    assert _cors_origins_from("http://a.example, *") == DEFAULT


def test_explicit_origins_are_kept():
    assert _cors_origins_from("http://a.example, http://b.example") == [
        "http://a.example",
        "http://b.example",
    ]


def test_empty_falls_back_to_default():
    assert _cors_origins_from("") == DEFAULT


def test_whitespace_only_falls_back_to_default():
    assert _cors_origins_from("  ") == DEFAULT
