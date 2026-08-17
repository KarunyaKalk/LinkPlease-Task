import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.db import init_db
from app.routes import rules, webhook, stats
from app.worker import worker_loop
from app.reconciler import reconciler_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    worker_task = asyncio.create_task(worker_loop())
    reconciler_task = asyncio.create_task(reconciler_loop())
    yield
    worker_task.cancel()
    reconciler_task.cancel()
    await asyncio.gather(worker_task, reconciler_task, return_exceptions=True)


app = FastAPI(title="Instagram Automation Backend", lifespan=lifespan)

app.include_router(rules.router)
app.include_router(webhook.router)
app.include_router(stats.router)


@app.get("/")
async def root():
    stats_data = await get_stats()
    return {
        "status": "online",
        "stats": stats_data,
        "endpoints": {
            "stats": "/stats",
            "rules": "/rules",
            "webhook": "/webhook",
            "docs": "/docs"
        }
    }


