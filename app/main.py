import logging
from contextlib import asynccontextmanager
import time

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api.health import router as health_router
from app.api.dashboard import router as dashboard_router
from app.api.auth import router as auth_router
from app.config import settings
from app.database import init_db
from app.services.ops_service import record_ops_event
_access_log = logging.getLogger("pipka.access")


class NoCacheAPIMiddleware(BaseHTTPMiddleware):
    """Prevent browsers/CDNs from caching API responses. Also logs API/auth requests."""

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        path = request.url.path
        try:
            response: Response = await call_next(request)
        except Exception as exc:
            if path.startswith("/api"):
                await record_ops_event(
                    "api_error",
                    "error",
                    source=path,
                    message=f"{request.method} {path} raised {exc.__class__.__name__}",
                    payload={
                        "method": request.method,
                        "path": path,
                        "status_code": 500,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
            raise

        if path.startswith("/api/") or path.startswith("/auth/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            if response.status_code >= 400 or request.method in ("POST", "DELETE", "PATCH"):
                try:
                    user_id = request.session.get("user_id", "anon")
                except Exception:
                    user_id = "?"
                _access_log.warning(
                    "%s %s → %s (user=%s)",
                    request.method, path, response.status_code, user_id,
                )
                if path.startswith("/api"):
                    await record_ops_event(
                        "api_error",
                        "error" if response.status_code >= 500 else "warn",
                        source=path,
                        message=f"{request.method} {path} -> {response.status_code}",
                        payload={
                            "method": request.method,
                            "path": path,
                            "status_code": response.status_code,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                        },
                    )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Pipka API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="pipka_session",
    max_age=30 * 24 * 3600,
    same_site="lax",
    https_only=True,
)
app.add_middleware(NoCacheAPIMiddleware)

app.include_router(auth_router)
app.include_router(health_router, tags=["health"])
app.include_router(dashboard_router, tags=["dashboard"])
