"""Static HTML pages: the SPA dashboard, infographic, and llms.txt manifest."""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.security_headers import content_security_policy

router = APIRouter()

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _nonce_html_response(filename: str) -> HTMLResponse:
    """Authorise only this page's known inline scripts with a per-response nonce."""
    nonce = secrets.token_urlsafe(18)
    content = (_STATIC_DIR / filename).read_text(encoding="utf-8")
    content = content.replace("<script>", f'<script nonce="{nonce}">')
    return HTMLResponse(
        content=content,
        headers={
            "Content-Security-Policy": content_security_policy(script_nonce=nonce),
            "Cache-Control": "no-store",
        },
    )


@router.get("/", response_class=HTMLResponse)
async def dashboard_page() -> HTMLResponse:
    return _nonce_html_response("dashboard.html")


@router.get("/llms.txt")
async def get_llms_txt() -> PlainTextResponse:
    path = _STATIC_DIR / "llms.txt"
    if path.exists():
        return PlainTextResponse(content=path.read_text(encoding="utf-8"))
    return PlainTextResponse(content="Error: llms.txt not found", status_code=404)


@router.get("/infographic", response_class=HTMLResponse)
async def public_infographic_page() -> HTMLResponse:
    return _nonce_html_response("infographic.html")
