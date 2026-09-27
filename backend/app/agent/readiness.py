"""
Project Musafir — Deterministic Trip Readiness Layer (Milestone 3, M3.2)
Phase 4: Decides whether to ask a targeted question or move forward to planning.
Evaluates current belief state (extracted + derived values) against required schema fields.
Enforces the discipline: pure deterministic code, zero LLM calls, zero guessing.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

from app.agent.state import TripState
from app.agent.context import ActiveQuestion
from app.agent.state_update import get_next_active_question


class ReadinessAction(str, Enum):
    ASK = "ASK"
    PROCEED = "PROCEED"


# Core invariant (M3.2 Fix #2): Required fields for first-turn readiness gate are strictly
# destination and duration (days or nights).
# travel_mode is optional/enrichment: its absence NEVER blocks PROCEED.
REQUIRED_READINESS_FIELDS = ["destination", "duration"]
OPTIONAL_ENRICHMENT_FIELDS = ["travel_mode", "interests", "dietary_preferences"]


class ReadinessDecision(BaseModel):
    """
    Deterministic readiness evaluation outcome.
    """
    action: ReadinessAction = Field(..., description="ASK or PROCEED")
    missing_field: Optional[str] = Field(None, description="The single highest-priority missing field if asking")
    question: Optional[ActiveQuestion] = Field(None, description="The targeted question to present if asking")
    reason: str = Field(..., description="Deterministic rationale for this decision")

    @property
    def is_ready_to_proceed(self) -> bool:
        """True if all required fields are satisfied and Musafir can move to planning."""
        return self.action == ReadinessAction.PROCEED


def evaluate_readiness(state: TripState) -> ReadinessDecision:
    """
    Step 4 & 5 of the Turn Intelligence Loop:
    Evaluates what Musafir knows vs still does not know.
    - If something required is missing: identifies the single most important missing field
      and generates one targeted question about it.
    - If nothing required is missing: returns PROCEED with zero follow-up questions.
    """
    next_q = get_next_active_question(state)
    if next_q:
        return ReadinessDecision(
            action=ReadinessAction.ASK,
            missing_field=next_q.field,
            question=next_q,
            reason=next_q.reason or f"missing_{next_q.field}",
        )

    return ReadinessDecision(
        action=ReadinessAction.PROCEED,
        missing_field=None,
        question=None,
        reason="all_required_fields_satisfied",
    )
