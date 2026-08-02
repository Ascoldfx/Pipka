from datetime import datetime

from fastapi import APIRouter, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.database import async_session
from app.models.ops_event import OpsEvent

router = APIRouter()


@router.get("/health/live")
async def liveness():
    """Process liveness probe; does not touch external dependencies."""
    return {"status": "ok", "service": "pipka"}


@router.get("/health")
async def health(response: Response):
    """Readiness probe covering the database and in-process scheduler."""
    checks: dict[str, object] = {"database": "ok"}
    last_scan_at = None
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
            last_scan_at = (
                await session.execute(
                    select(func.max(OpsEvent.created_at)).where(
                        OpsEvent.event_type == "scan",
                        OpsEvent.status == "success",
                    )
                )
            ).scalar_one_or_none()
    except SQLAlchemyError:
        checks["database"] = "error"

    # Lazy import avoids loading the scheduler/source graph while this router
    # module is imported by FastAPI.
    from app.services.scheduler_service import scheduler  # noqa: PLC0415

    checks["scheduler"] = "ok" if scheduler.running else "starting"
    if last_scan_at:
        checks["last_scan_at"] = last_scan_at.isoformat()
        checks["last_scan_age_seconds"] = max(
            0, int((datetime.now() - last_scan_at).total_seconds())
        )

    ready = checks["database"] == "ok" and checks["scheduler"] == "ok"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if ready else "degraded",
        "service": "pipka",
        "checks": checks,
    }
