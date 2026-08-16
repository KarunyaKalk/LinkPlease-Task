import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app import db
from app.worker import run_worker_loop
from app.reconciler import run_reconciler_loop
from app.routes import rules, webhook, stats


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    worker_task = asyncio.create_task(run_worker_loop())
    reconciler_task = asyncio.create_task(run_reconciler_loop())
    yield
    worker_task.cancel()
    reconciler_task.cancel()


app = FastAPI(title="LinkPlease", lifespan=lifespan)
app.include_router(rules.router)
app.include_router(webhook.router)
app.include_router(stats.router)
