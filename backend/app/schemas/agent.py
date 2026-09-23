"""
Project Musafir — Agent Chat Schemas (Step 10.5)
Request and Response models for the /agent/chat public API.
"""

from typing import Any, Dict, List, Optional, Union
from uuid import UUID
from pydantic import BaseModel, Field, field_validator

from app.schemas.hotel import Hotel


class AgentResults(BaseModel):
    """Structured tool execution results formatted for presentation UI."""
    hotels: Optional[List[Hotel]] = Field(
        None,
        description="Structured hotel search results returned by search_hotels tool."
    )


class AgentChatRequest(BaseModel):
    """Payload for conversational interaction with Musafir agent."""
    conversation_id: Optional[UUID] = Field(
        None,
        description="Optional session UUID. If omitted, a new conversation session is created."
    )
    message: str = Field(
        ...,
        description="User message text. Cannot be empty or purely whitespace."
    )

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("Message must be a string.")
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Message cannot be empty or whitespace only.")
        return trimmed


class AgentChatResponse(BaseModel):
    """Sanitized response payload returned by the Musafir agent chat endpoint."""
    conversation_id: UUID = Field(
        ...,
        description="Unique UUID identifying this conversational trip planning session."
    )
    response: str = Field(
        ...,
        description="The agent's natural-language reply."
    )
    tool_calls: List[str] = Field(
        default_factory=list,
        description="List of deterministic tools executed during this turn."
    )
    iterations: int = Field(
        ...,
        description="Number of model iterations / roundtrips required to complete the turn."
    )
    state_summary: Union[Dict[str, Any], str] = Field(
        ...,
        description="Compact human-readable summary of the current trip planning state."
    )
    results: Optional[AgentResults] = Field(
        None,
        description="Structured results (e.g. hotel cards) for frontend presentation; null if no structured results."
    )
    metrics: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional turn execution and performance metrics."
    )
