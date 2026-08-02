"""Shared Content Security Policy construction for middleware and HTML pages."""
from __future__ import annotations


def content_security_policy(*, script_nonce: str | None = None) -> str:
    """Return a strict CSP, optionally authorising this response's inline scripts."""
    script_sources = ["'self'"]
    if script_nonce:
        script_sources.append(f"'nonce-{script_nonce}'")
    return (
        "default-src 'self'; "
        f"script-src {' '.join(script_sources)}; "
        "script-src-attr 'none'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self' https://accounts.google.com; "
        "base-uri 'self'; "
        "object-src 'none'"
    )
