from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.dashboard import router as dashboard_router
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="JobHunt API", version="0.1.0", lifespan=lifespan)
app.include_router(health_router, tags=["health"])
app.include_router(dashboard_router, tags=["dashboard"])
