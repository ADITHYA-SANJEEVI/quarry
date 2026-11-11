"""Quarry's isolated public application; no commerce database or payment routes."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.api.hpc_experiments import router, shutdown_experiments
from app.services.hpc.runtime import shutdown_runtime


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        await run_in_threadpool(shutdown_experiments)
        await run_in_threadpool(shutdown_runtime)


app = FastAPI(title="Quarry · deterministic parallel commerce search", lifespan=lifespan)
app.include_router(router)
static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static), name="static")


@app.get("/", include_in_schema=False)
def workbench():
    return FileResponse(static / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "application": "quarry", "catalogue": "synthetic"}
