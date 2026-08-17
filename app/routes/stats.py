from fastapi import APIRouter
from app.schemas import StatsResponse
from app.db import get_stats

router = APIRouter()


@router.get("/stats", response_model=StatsResponse)
async def get_system_stats():
    stats_data = await get_stats()
    return StatsResponse(**stats_data)
