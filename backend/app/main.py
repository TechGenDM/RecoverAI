from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine, get_db
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

# Mount webhooks router with v1 prefix and root alias
app.include_router(webhooks_router, prefix="/v1/webhooks")
app.include_router(webhooks_router, prefix="/webhooks")


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
