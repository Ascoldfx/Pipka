"""Static HTML pages: the SPA dashboard, infographic, and llms.txt manifest."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse

router = APIRouter()

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/", response_class=HTMLResponse)
async def dashboard_page() -> HTMLResponse:
    return HTMLResponse(content=(_STATIC_DIR / "dashboard.html").read_text(encoding="utf-8"))


@router.get("/llms.txt")
async def get_llms_txt() -> PlainTextResponse:
    path = _STATIC_DIR / "llms.txt"
    if path.exists():
        return PlainTextResponse(content=path.read_text(encoding="utf-8"))
    return PlainTextResponse(content="Error: llms.txt not found", status_code=404)


@router.get("/infographic", response_class=HTMLResponse)
async def public_infographic_page() -> HTMLResponse:
    return HTMLResponse(content=(_STATIC_DIR / "infographic.html").read_text(encoding="utf-8"))
