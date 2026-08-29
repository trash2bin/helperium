"""Header policy checks for the public storefront reverse proxy.

The public Caddyfile is the only TLS termination point for the demo
storefront, so HSTS and a tailored CSP must be present in its shared
header block. These tests parse the Caddyfile text directly: the file is
configuration, not importable code, but drift here is a security issue.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CADDYFILE = ROOT / "demo" / "autoparts-store" / "Caddyfile.public"


def caddyfile_text() -> str:
    return CADDYFILE.read_text(encoding="utf-8")


def header_block(text: str) -> str:
    """Return the shared header block of the site (before routing)."""
    match = re.search(r"header \{(.*?)\}", text, re.DOTALL)
    assert match, "Caddyfile.public must keep its shared header block"
    return match.group(1)


def test_hsts_present_with_one_year_max_age():
    block = header_block(caddyfile_text())
    assert re.search(
        r'Strict-Transport-Security\s+"max-age=31536000"\s*$',
        block,
        re.MULTILINE,
    ), "HSTS with max-age=31536000 must be set at the public edge"


def test_hsts_omits_include_sub_domains_and_preload():
    block = header_block(caddyfile_text())
    hsts_line = next(
        line for line in block.splitlines() if "Strict-Transport-Security" in line
    )
    assert "includeSubDomains" not in hsts_line, (
        "the storefront domain also serves nothing else, but sibling demo "
        "subdomains are not ready for a blanket HSTS commitment"
    )
    assert "preload" not in hsts_line, (
        "do not opt the demo domain into browser preload lists"
    )


def test_csp_defaults_to_self_and_allows_required_widget_origins():
    block = header_block(caddyfile_text())
    csp_line = next(line for line in block.splitlines() if "Content-Security-Policy" in line)
    csp = csp_line.split("Content-Security-Policy", 1)[1].strip().strip('"')

    assert "default-src 'self'" in csp
    # The widget injects <style> elements into its Shadow DOM at runtime.
    assert "style-src 'self' 'unsafe-inline'" in csp
    # The widget plays base64 audio through blob: object URLs; the storefront
    # loads Google Fonts. Everything else stays same-origin.
    assert "media-src 'self' blob:" in csp
    assert "font-src 'self' https://fonts.gstatic.com" in csp
    assert "font-src" in csp and "fonts.googleapis.com" in csp.split("style-src", 1)[1].split(";", 1)[0]
    # Chat and voice both POST to same-origin API routes via fetch.
    assert "connect-src 'self'" in csp
    # Storefront and widget ship no <img> tags; keep images same-origin.
    assert "img-src 'self'" in csp


def test_csp_frame_ancestors_decision_is_documented_not_assumed():
    """frame-ancestors is a deployment decision; it must stay out of the file
    until the README states the storefront is never framed, and the README
    must carry that open decision instead of leaving it implicit."""
    text = caddyfile_text()
    block = header_block(text)
    csp_line = next(line for line in block.splitlines() if "Content-Security-Policy" in line)
    csp = csp_line.split("Content-Security-Policy", 1)[1].strip().strip('"')
    assert "frame-ancestors" not in csp
    readme = (ROOT / "demo" / "autoparts-store" / "README.md").read_text(encoding="utf-8")
    assert "frame-ancestors" in readme, (
        "the CSP header block must document the pending frame-ancestors decision"
    )


def test_existing_security_headers_are_preserved():
    block = header_block(caddyfile_text())
    assert "-Server" in block
    assert 'X-Content-Type-Options "nosniff"' in block
    assert 'Referrer-Policy "strict-origin-when-cross-origin"' in block
    assert 'Permissions-Policy "geolocation=(), microphone=(), camera=()"' in block
