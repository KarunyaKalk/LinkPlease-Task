import uuid
from fastapi import APIRouter, status
from app.schemas import RuleCreate, RuleResponse
from app.db import insert_rule

router = APIRouter()


@router.post("/rules", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(rule: RuleCreate):
    rule_id = f"rule_{uuid.uuid4().hex[:12]}"
    created = await insert_rule(rule_id=rule_id, keyword=rule.keyword, dm_message=rule.dm_message)
    return RuleResponse(**created)
