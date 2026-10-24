"""Pydantic schema and enums for post-call structured summaries."""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class CustomerIntent(str, Enum):
    ORDER_TRACKING = "ORDER_TRACKING"
    CANCELLATION = "CANCELLATION"
    RETURN_REFUND = "RETURN_REFUND"
    POLICY_QUESTION = "POLICY_QUESTION"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    OTHER = "OTHER"


class ResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    ESCALATION_NEEDED = "ESCALATION_NEEDED"


class CallSummary(BaseModel):
    customer_intent: CustomerIntent
    order_id: Optional[str] = None
    resolution_status: ResolutionStatus
    call_summary: str = Field(description="1-2 sentences describing customer query and outcome.")
