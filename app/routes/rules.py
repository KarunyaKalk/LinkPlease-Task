import uuid
from fastapi import APIRouter

from app import db
from app.schemas import RuleCreate, RuleResponse

router = APIRouter()


@router.post("/rules", response_model=RuleResponse, status_code=201)
async def create_rule(rule: RuleCreate):
    rule_id = str(uuid.uuid4())
    await db.insert_rule(rule_id, rule.keyword, rule.dm_message)
    return RuleResponse(rule_id=rule_id, keyword=rule.keyword, dm_message=rule.dm_message)
