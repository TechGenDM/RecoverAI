from typing import Literal

from pydantic import BaseModel, Field

MAX_DELAY_HOURS = 48


class RecoveryDecisionSchema(BaseModel):
    case_id: str
    action: Literal["WAIT", "SEND_PAYMENT_LINK", "ESCALATE", "STOP"]
    delay_hours: float = Field(default=0, ge=0, le=MAX_DELAY_HOURS)
    llm_confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    risk_factors: list[str] = Field(max_length=20)


class SafetyResult(BaseModel):
    effective_action: str
    policy_verdict: str
    policy_reason: str
    policy_modification_detail: dict | None
    delay_hours: float | None
