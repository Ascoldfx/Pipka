from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.health import router as health_router
from app.api.dashboard import router as dashboard_router
from app.database import init_db


class NoCacheAPIMiddleware(BaseHTTPMiddleware):
    """Prevent browsers/CDNs from caching API responses (avoids stale-cache bugs behind Cloudflare)."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="JobHunt API", version="0.1.0", lifespan=lifespan)
app.add_middleware(NoCacheAPIMiddleware)
app.include_router(health_router, tags=["health"])
app.include_router(dashboard_router, tags=["dashboard"])
