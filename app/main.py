import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api.health import router as health_router
from app.api.dashboard import router as dashboard_router
from app.api.auth import router as auth_router
from app.config import settings
from app.database import init_db

_access_log = logging.getLogger("pipka.access")


class NoCacheAPIMiddleware(BaseHTTPMiddleware):
    """Prevent browsers/CDNs from caching API responses. Also logs API/auth requests."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/") or path.startswith("/auth/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            # Log non-2xx responses and all mutating requests to help diagnose issues
            if response.status_code >= 400 or request.method in ("POST", "DELETE", "PATCH"):
                try:
                    user_id = request.session.get("user_id", "anon")
                except Exception:
                    user_id = "?"
                _access_log.warning(
                    "%s %s → %s (user=%s)",
                    request.method, path, response.status_code, user_id,
                )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Pipka API", version="0.1.0", lifespan=lifespan)

# Session middleware (signed cookies — stores user_id after OAuth login)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="pipka_session",
    max_age=30 * 24 * 3600,  # 30 days
    same_site="lax",
    https_only=True,
)
app.add_middleware(NoCacheAPIMiddleware)

# Auth routes first (no auth dependency), then dashboard (session-based)
app.include_router(auth_router)
app.include_router(health_router, tags=["health"])
app.include_router(dashboard_router, tags=["dashboard"])
