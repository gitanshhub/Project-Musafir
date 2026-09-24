"""
Project Musafir — Entity & Item Reference Schemas (Milestone 3, Component 1)
Defines neutral reference models for identified or displayed entities across
hotels, places, restaurants, and cafes without coupling to the agent runtime.
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class EntityReference(BaseModel):
    """
    Neutral domain reference to a trip entity (hotel, place, restaurant, cafe).
    Serves as a clean identifier bridge in mutation commands and domain models.
    """
    entity_type: str = Field(
        ...,
        description="Type of entity: 'hotel' | 'place' | 'restaurant' | 'cafe'"
    )
    id: str = Field(
        ...,
        description="Unique entity ID (e.g. data_id, property_token, or slug)"
    )
    name: Optional[str] = Field(
        None,
        description="Human-readable name of the entity"
    )
    ordinal: Optional[int] = Field(
        None,
        ge=1,
        description="Optional 1-based display position if resolved from visible list"
    )
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Sanitized payload or metadata for the referenced entity"
    )


class VisibleItemReference(EntityReference):
    """
    Reference to an item displayed to the traveler on screen.
    Backward-compatible with Milestone 1 and 2 screen card representations.
    """
    index: int = Field(
        ...,
        ge=1,
        description="1-based ordinal display position (1st, 2nd, 3rd, ...)"
    )
    price_per_night: Optional[float] = Field(
        None,
        description="Nightly rate in INR if hotel"
    )
    rating: Optional[float] = Field(
        None,
        description="User review rating if available"
    )
    extra_data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Sanitized payload required for selection"
    )

    def model_post_init(self, __context: Any) -> None:
        # Sync index with base class ordinal and extra_data with data
        if self.ordinal is None:
            self.ordinal = self.index
        if not self.data and self.extra_data:
            self.data = self.extra_data
        elif not self.extra_data and self.data:
            self.extra_data = self.data
