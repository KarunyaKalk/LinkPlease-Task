from fastapi import APIRouter

from app import db
from app.schemas import StatsResponse

router = APIRouter()


@router.get("/stats", response_model=StatsResponse)
async def get_stats():
    return StatsResponse(**await db.compute_stats())
