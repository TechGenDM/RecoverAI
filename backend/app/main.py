from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import engine, get_db
from app.routers.dashboard import router as dashboard_router
from app.routers.scheduler import router as scheduler_router
from app.routers.webhooks import router as webhooks_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure engine is ready
    yield
    # Shutdown: clean up connection pool
    await engine.dispose()


app = FastAPI(
    title="RecoverAI",
    description="AI Revenue Recovery Backend",
    version="0.1.0",
    lifespan=lifespan,
)


def _parse_cors_origins(raw: str) -> list[str]:
    if not raw:
        return ["http://localhost:3000", "http://127.0.0.1:3000"]
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if "*" in origins:
        raise ValueError("CORS_ORIGINS cannot contain '*' when allow_credentials=True. Use specific origins.")
    return origins if origins else ["http://localhost:3000", "http://127.0.0.1:3000"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors_origins(settings.CORS_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount webhooks router with v1 prefix and root alias
app.include_router(webhooks_router, prefix="/v1/webhooks")
app.include_router(webhooks_router, prefix="/webhooks")

app.include_router(scheduler_router)
app.include_router(dashboard_router)


@app.get("/health")
async def health_check(db: Annotated[AsyncSession, Depends(get_db)]):
    # Verify database connection
    result = await db.execute(text("SELECT 1"))
    db_ok = result.scalar() == 1
    return {
        "status": "ok" if db_ok else "error",
        "service": "recoverai",
        "database": "connected" if db_ok else "disconnected",
    }
