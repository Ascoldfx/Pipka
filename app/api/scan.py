"""Manual scan trigger + status endpoint (admin-only for trigger)."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request

from app.api._helpers import get_role
from app.services.scheduler_service import is_scan_running

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/scan")
async def trigger_scan(request: Request):
    """Kick off a one-shot background scan. Admin-only — scrapes are expensive."""
    if get_role(request, None) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    # Imported lazily to avoid a circular import via scheduler -> api.
    from app.services.scheduler_service import _background_scan, scheduler

    if is_scan_running():
        return {"status": "already_running"}

    bg_job = scheduler.get_job("background_scan")
    if not bg_job:
        return {"error": "Scheduler not initialized"}

    bot_app = bg_job.args[0]

    async def _run():
        try:
            await _background_scan(bot_app, trigger="manual")
        except Exception as e:
            logger.error("Manual scan failed: %s", e)

    task = asyncio.create_task(_run())
    # Surface unhandled exceptions to the event loop's default handler instead
    # of swallowing them in a discarded Future.
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    return {"status": "started"}


@router.get("/api/scan/status")
async def scan_status():
    from app.services.scheduler_service import scheduler

    bg_job = scheduler.get_job("background_scan")
    if not bg_job:
        return {"next_run": None, "running": is_scan_running()}
    next_run = bg_job.next_run_time
    return {"next_run": next_run.isoformat() if next_run else None, "running": is_scan_running()}
