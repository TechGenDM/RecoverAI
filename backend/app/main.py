from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: could initialize database connection pools here
    yield
    # Shutdown: clean up resources


app = FastAPI(
    title="RecoverAI",
    description="AI Revenue Recovery Backend",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "recoverai"}
