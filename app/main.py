import logging
import secrets
from contextlib import asynccontextmanager
import time

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.ops import router as ops_router
from app.api.pages import router as pages_router
from app.api.profile import router as profile_router
from app.api.scan import router as scan_router
from app.api.stats import router as stats_router
from app.config import settings
from app.database import init_db
from app.services.ops_service import record_ops_event
_access_log = logging.getLogger("pipka.access")

# Sentry — initialise BEFORE FastAPI() so the SDK can install its hooks on
# the ASGI app. Skipped entirely when SENTRY_DSN is empty.
if settings.sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
        # Attach context: which logger emitted the event, last 100 breadcrumbs.
        attach_stacktrace=True,
        send_default_pii=False,  # don't ship session cookies / IPs
        integrations=[AsyncioIntegration(), SqlalchemyIntegration()],
        # FastAPI integration is auto-loaded by sentry-sdk[fastapi].
    )
    logging.getLogger("pipka").info("Sentry initialised (env=%s)", settings.sentry_environment)

# Methods that mutate server state and therefore require a CSRF token.
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Endpoints that are exempt from CSRF (no session cookie present, or external
# redirect target). The OAuth callback is hit by Google as a top-level GET.
_CSRF_EXEMPT_PREFIXES = ("/auth/", "/health")


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit CSRF protection.

    On every request we ensure ``request.session["csrf_token"]`` exists. A
    matching ``csrf_token`` cookie is set on the response (NOT HttpOnly so JS
    can read it). On unsafe methods we require an ``X-CSRF-Token`` header that
    equals the session token.

    Why double-submit and not signed-token: SessionMiddleware already signs the
    cookie payload (incl. our token) — an attacker can't forge a session, so
    matching a JS-readable cookie against the session value is sufficient.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        is_exempt = any(path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES)

        # Lazily mint a per-session token. Must run AFTER SessionMiddleware
        # populates request.session — that's guaranteed because Starlette
        # walks middlewares outside-in (SessionMiddleware added first → outer).
        try:
            session = request.session
        except AssertionError:
            # No session middleware on this path (shouldn't happen post-mount)
            session = None

        token = None
        if session is not None:
            token = session.get("csrf_token")
            if not token:
                token = secrets.token_urlsafe(32)
                session["csrf_token"] = token

        if (
            request.method in _UNSAFE_METHODS
            and not is_exempt
            and session is not None
        ):
            sent = request.headers.get("x-csrf-token", "")
            if not sent or not secrets.compare_digest(sent, token or ""):
                return JSONResponse(
                    {"detail": "CSRF token missing or invalid"},
                    status_code=403,
                )

        response: Response = await call_next(request)

        # Refresh the cookie on every response so the JS layer always has a
        # current value. Path=/, SameSite=Lax matches session cookie scope.
        if token:
            response.set_cookie(
                "csrf_token",
                token,
                max_age=30 * 24 * 3600,
                httponly=False,
                samesite="lax",
                secure=True,
                path="/",
            )

        return response


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

        if path == "/" or path.startswith("/api/") or path.startswith("/auth/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            # Log to access log for 4xx/5xx and all mutating methods (debug aid)
            if response.status_code >= 400 or request.method in ("POST", "DELETE", "PATCH"):
                try:
                    user_id = request.session.get("user_id", "anon")
                except Exception:
                    user_id = "?"
                _access_log.warning(
                    "%s %s → %s (user=%s)",
                    request.method, path, response.status_code, user_id,
                )
            # Record ops events ONLY for real errors (≥400), not successful POSTs
            if response.status_code >= 400 and path.startswith("/api"):
                # Skip GET 404 — scanner probes (/api/.env, /api/config…), not our errors
                is_probe = response.status_code == 404 and request.method == "GET"
                if not is_probe:
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

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="pipka_session",
    max_age=30 * 24 * 3600,
    same_site="lax",
    https_only=True,
)
# Starlette wraps middleware so the FIRST one added is outermost (runs first
# on incoming requests). We need: Session → CSRF → NoCache → routes, so
# Session was added above, CSRF here, NoCache last.
app.add_middleware(CSRFMiddleware)
app.add_middleware(NoCacheAPIMiddleware)

app.include_router(auth_router)
app.include_router(health_router, tags=["health"])
# Former monolithic dashboard router, now split by concern. Order matters
# only insofar as `pages_router` claims `/` — must be mounted before any
# router whose paths could otherwise be matched.
app.include_router(pages_router, tags=["pages"])
app.include_router(jobs_router, tags=["jobs"])
app.include_router(stats_router, tags=["stats"])
app.include_router(profile_router, tags=["profile"])
app.include_router(scan_router, tags=["scan"])
app.include_router(ops_router, tags=["ops"])
app.include_router(admin_router, tags=["admin"])
