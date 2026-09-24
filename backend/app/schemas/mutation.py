"""
Project Musafir — Mutation Command Schemas (Milestone 3, Component 1)
Defines the canonical vocabulary, budget scopes, and strongly-validated models
for user-requested trip modifications.
"""

from datetime import date as dt_date
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, model_validator

from app.schemas.reference import EntityReference

SUPPORTED_TRAVEL_MODES = {"driving", "walking", "bicycling", "transit", "two_wheeler"}
VALID_PREFERENCE_CATEGORIES = {"dietary", "cuisine", "interests", "interest", "meal", "food_price"}


class MutationType(str, Enum):
    """Canonical vocabulary of mutations supported by the dynamic replanning engine."""
    # Destination & Timing
    SET_DESTINATION = "SET_DESTINATION"
    SET_START_DATE = "SET_START_DATE"
    SET_END_DATE = "SET_END_DATE"
    SET_DURATION = "SET_DURATION"

    # Budget & Travel Mode
    SET_BUDGET = "SET_BUDGET"
    SET_TRAVEL_MODE = "SET_TRAVEL_MODE"

    # Accommodation
    SELECT_HOTEL = "SELECT_HOTEL"
    CHANGE_HOTEL = "CHANGE_HOTEL"

    # Attractions / Places
    SELECT_PLACE = "SELECT_PLACE"
    REMOVE_PLACE = "REMOVE_PLACE"

    # Dining & Food
    SELECT_RESTAURANT = "SELECT_RESTAURANT"
    REMOVE_RESTAURANT = "REMOVE_RESTAURANT"
    SELECT_CAFE = "SELECT_CAFE"
    REMOVE_CAFE = "REMOVE_CAFE"

    # Preferences
    ADD_PREFERENCE = "ADD_PREFERENCE"
    REMOVE_PREFERENCE = "REMOVE_PREFERENCE"


class BudgetScope(str, Enum):
    """Explicit scope defining which budget constraint is being set."""
    NIGHTLY_HOTEL = "nightly_hotel"       # Daily accommodation rate ceiling (hotel_budget)
    TOTAL_HOTEL = "total_hotel"           # Accommodation total budget across all nights (hotel_total_budget)
    TOTAL_TRIP = "total_trip"             # Entire trip budget independent of hotel (trip_budget)


class ResolutionConfidence(str, Enum):
    """Controlled confidence classification for resolution provenance."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MutationCommand(BaseModel):
    """
    Canonical, validated representation of a single resolved state mutation.
    Represents concrete operations ready for deterministic state application.
    """
    mutation_type: MutationType = Field(..., description="Canonical mutation operation")
    target_id: Optional[str] = Field(None, description="Identifier of target entity (data_id, token, or ID)")
    target_name: Optional[str] = Field(None, description="Human-readable name of target entity or preference category")
    value: Optional[Any] = Field(None, description="Payload value for mutation (e.g. days, amount, date, entity dict)")
    scope: Optional[BudgetScope] = Field(None, description="Budget scope (REQUIRED when mutation_type is SET_BUDGET)")
    reference: Optional[EntityReference] = Field(None, description="Structured entity reference if resolved from screen/list")
    source_reference: Optional[str] = Field(None, description="Original user phrase or conversational reference")
    confidence: Optional[ResolutionConfidence] = Field(None, description="Resolution confidence provenance")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional context metadata")

    @model_validator(mode="after")
    def validate_mutation_contract(self) -> "MutationCommand":
        m_type = self.mutation_type

        # 1. SET_DESTINATION: must have a non-empty string in value
        if m_type == MutationType.SET_DESTINATION:
            if not isinstance(self.value, str) or not self.value.strip():
                raise ValueError("SET_DESTINATION requires a non-empty string in 'value'.")
            self.value = self.value.strip()

        # 2. SET_START_DATE & SET_END_DATE: must be dt_date or valid ISO string
        elif m_type in {MutationType.SET_START_DATE, MutationType.SET_END_DATE}:
            if self.value is None:
                raise ValueError(f"{m_type.value} requires a valid date in 'value'.")
            if isinstance(self.value, dt_date):
                pass
            elif isinstance(self.value, str):
                try:
                    self.value = dt_date.fromisoformat(self.value.strip())
                except ValueError:
                    raise ValueError(f"{m_type.value} requires an ISO date string (YYYY-MM-DD).")
            else:
                raise ValueError(f"{m_type.value} requires a date object or ISO date string.")

        # 3. SET_DURATION: must be an integer >= 1
        elif m_type == MutationType.SET_DURATION:
            if self.value is None or isinstance(self.value, bool):
                raise ValueError("SET_DURATION requires an integer >= 1 in 'value'.")
            try:
                days = int(self.value)
                if days < 1:
                    raise ValueError("Duration must be at least 1 day.")
                self.value = days
            except (TypeError, ValueError) as e:
                raise ValueError(f"Invalid duration value: {e}")

        # 4. SET_BUDGET: scope is strictly REQUIRED (no silent fallback), value must be float > 0
        elif m_type == MutationType.SET_BUDGET:
            if self.scope is None:
                raise ValueError(
                    "SET_BUDGET requires an explicit 'scope' (one of: 'nightly_hotel', 'total_hotel', 'total_trip'). "
                    "Cannot default without unambiguous context."
                )
            if self.value is None or isinstance(self.value, bool):
                raise ValueError("SET_BUDGET requires a positive numeric 'value'.")
            try:
                amt = float(self.value)
                if amt <= 0:
                    raise ValueError("Budget value must be > 0.")
                self.value = amt
            except (TypeError, ValueError) as e:
                raise ValueError(f"Invalid budget amount: {e}")

        # 5. SET_TRAVEL_MODE: must be one of supported travel modes
        elif m_type == MutationType.SET_TRAVEL_MODE:
            if not isinstance(self.value, str) or self.value.strip().lower() not in SUPPORTED_TRAVEL_MODES:
                raise ValueError(
                    f"Unsupported travel mode '{self.value}'. Must be one of: {sorted(SUPPORTED_TRAVEL_MODES)}"
                )
            self.value = self.value.strip().lower()

        # 6. SELECT_HOTEL / CHANGE_HOTEL: must specify reference, target, or hotel payload
        elif m_type in {MutationType.SELECT_HOTEL, MutationType.CHANGE_HOTEL}:
            has_ref = self.reference is not None and getattr(self.reference, "entity_type", "") in {"hotel", ""}
            has_target = bool((self.target_name and self.target_name.strip()) or (self.target_id and self.target_id.strip()))
            has_val = self.value is not None and (isinstance(self.value, dict) or hasattr(self.value, "name"))
            if not (has_ref or has_target or has_val):
                raise ValueError(f"{m_type.value} requires a hotel reference, target name/id, or hotel payload object.")

        # 7. SELECT_PLACE: must specify reference, target, or place payload
        elif m_type == MutationType.SELECT_PLACE:
            has_ref = self.reference is not None and getattr(self.reference, "entity_type", "") in {"place", ""}
            has_target = bool((self.target_name and self.target_name.strip()) or (self.target_id and self.target_id.strip()))
            has_val = self.value is not None and (isinstance(self.value, dict) or hasattr(self.value, "name"))
            if not (has_ref or has_target or has_val):
                raise ValueError("SELECT_PLACE requires a place reference, target name/id, or place payload object.")

        # 8. REMOVE_PLACE: must identify which place to remove
        elif m_type == MutationType.REMOVE_PLACE:
            has_ref = self.reference is not None
            has_target = bool((self.target_name and self.target_name.strip()) or (self.target_id and self.target_id.strip()))
            has_val_str = isinstance(self.value, str) and bool(self.value.strip())
            if not (has_ref or has_target or has_val_str):
                raise ValueError("REMOVE_PLACE requires a target_id, target_name, reference, or place name in value.")

        # 9. SELECT_RESTAURANT / SELECT_CAFE: must specify reference, target, or restaurant payload
        elif m_type in {MutationType.SELECT_RESTAURANT, MutationType.SELECT_CAFE}:
            has_ref = self.reference is not None
            has_target = bool((self.target_name and self.target_name.strip()) or (self.target_id and self.target_id.strip()))
            has_val = self.value is not None and (isinstance(self.value, dict) or hasattr(self.value, "name"))
            if not (has_ref or has_target or has_val):
                raise ValueError(f"{m_type.value} requires a food reference, target name/id, or restaurant payload object.")

        # 10. REMOVE_RESTAURANT / REMOVE_CAFE: must identify which item to remove
        elif m_type in {MutationType.REMOVE_RESTAURANT, MutationType.REMOVE_CAFE}:
            has_ref = self.reference is not None
            has_target = bool((self.target_name and self.target_name.strip()) or (self.target_id and self.target_id.strip()))
            has_val_str = isinstance(self.value, str) and bool(self.value.strip())
            if not (has_ref or has_target or has_val_str):
                raise ValueError(f"{m_type.value} requires a target_id, target_name, reference, or name in value.")

        # 11. ADD_PREFERENCE: target_name indicates category, value indicates preference
        elif m_type == MutationType.ADD_PREFERENCE:
            has_cat = bool((self.target_name and self.target_name.strip()) or (self.target_id and self.target_id.strip()))
            has_val = self.value is not None and (
                (isinstance(self.value, str) and bool(self.value.strip()))
                or isinstance(self.value, dict)
            )
            if not (has_cat and has_val):
                raise ValueError("ADD_PREFERENCE requires a preference category (target_name) and non-empty preference value.")

        # 12. REMOVE_PREFERENCE: target_name indicates category, value indicates preference item
        elif m_type == MutationType.REMOVE_PREFERENCE:
            has_cat = bool((self.target_name and self.target_name.strip()) or (self.target_id and self.target_id.strip()))
            has_val = isinstance(self.value, str) and bool(self.value.strip())
            if not (has_cat and has_val):
                raise ValueError("REMOVE_PREFERENCE requires a preference category (target_name) and preference item to remove (value).")

        return self


class MutationBatch(BaseModel):
    """
    Ordered collection of validated mutation commands resulting from user interaction.
    State application engine in Component 2 will enforce atomic execution semantics.
    """
    mutations: List[MutationCommand] = Field(default_factory=list, description="Ordered mutation commands")
    source_message: Optional[str] = Field(None, description="Original user message if applicable")
