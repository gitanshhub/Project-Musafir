import uuid
from datetime import date as dt_date
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, model_validator

from app.schemas.hotel import Hotel, HotelDetail
from app.schemas.place import Place
from app.schemas.restaurant import Restaurant
from app.schemas.route import OptimizedRoute
from app.schemas.itinerary import ItineraryResponse
from app.schemas.constraint import (
    Constraint,
    ConstraintPriority,
    ConstraintScope,
    ConstraintType,
    ConstraintStatus,
    ConstraintSource,
    FeasibilityStatus,
    FeasibilityResult,
)
from app.agent.dependencies import DerivedResource
from app.agent.freshness import (
    DerivedStateStatus,
    DerivedResourceFreshness,
    FreshnessRegistry,
    init_derived_freshness,
)


class PlanningStage(str, Enum):
    """
    Deterministic workflow stages for agentic travel planning.
    Guides state progression without duplicate LLM classification.
    """
    DISCOVERY = "DISCOVERY"
    HOTEL_SELECTION = "HOTEL_SELECTION"
    PLACE_DISCOVERY = "PLACE_DISCOVERY"
    PLACE_SELECTION = "PLACE_SELECTION"
    FOOD_DISCOVERY = "FOOD_DISCOVERY"
    FOOD_SELECTION = "FOOD_SELECTION"
    ROUTE_PLANNING = "ROUTE_PLANNING"
    ITINERARY_PLANNING = "ITINERARY_PLANNING"
    READY = "READY"
    REPLANNING = "REPLANNING"


class TripState(BaseModel):
    """
    Structured state representing an ongoing trip planning session.
    Tracks user intent, choices, preferences, and generated route/itinerary.
    """
    conversation_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique session identifier for the conversation"
    )
    state_version: int = Field(
        1,
        description="Monotonically increasing version tracking meaningful state updates"
    )
    planning_stage: PlanningStage = Field(
        default=PlanningStage.DISCOVERY,
        description="Current workflow stage in deterministic planner"
    )
    
    # Destination & timing
    destination: Optional[str] = Field(None, description="Primary trip destination city (e.g. 'Jaipur')")
    trip_start_date: Optional[dt_date] = Field(None, description="Start date of the trip (YYYY-MM-DD)")
    trip_end_date: Optional[dt_date] = Field(None, description="End date of the trip (YYYY-MM-DD)")
    number_of_days: Optional[int] = Field(None, ge=1, description="Total duration of the trip in days")
    number_of_nights: Optional[int] = Field(None, ge=0, description="Total nights for hotel accommodation")
    
    # Accommodation Semantics (M3.2 Fix #1)
    accommodation_required: Optional[bool] = Field(
        None, description="Whether the trip involves staying somewhere overnight at all"
    )
    accommodation_booked: Optional[bool] = Field(
        None, description="Whether accommodation has already been booked or arranged by traveler"
    )
    hotel_search_required: Optional[bool] = Field(
        None, description="Purely derived: whether system should search for hotel (accommodation_required and not accommodation_booked)"
    )
    hotel_required: Optional[bool] = Field(
        None, description="Legacy compatibility alias: mirrors hotel_search_required"
    )
    hotel_budget: Optional[float] = Field(None, gt=0, description="Max acceptable nightly hotel rate in INR")
    hotel_total_budget: Optional[float] = Field(None, gt=0, description="Total budget for accommodation in INR")
    trip_budget: Optional[float] = Field(None, gt=0, description="Overall total trip budget in INR")
    hotel_selection: Optional[Union[HotelDetail, Hotel]] = Field(
        None, description="Selected hotel for accommodation / trip starting point"
    )

    # Intelligence & Confidence Tracking (Milestone 3, M3.2)
    feasibility_notes: List[str] = Field(
        default_factory=list, description="Non-blocking informational feasibility notes/advisories"
    )
    extracted_confidence: Dict[str, str] = Field(
        default_factory=dict, description="Confidence per slot: 'HIGH' | 'LOW'"
    )
    explicit_fields: List[str] = Field(
        default_factory=list, description="List of field names explicitly stated by user"
    )

    @model_validator(mode="before")
    @classmethod
    def _handle_selected_hotel_alias(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "selected_hotel" in data and "hotel_selection" not in data:
                data["hotel_selection"] = data.pop("selected_hotel")
        return data

    @property
    def selected_hotel(self) -> Optional[Union[HotelDetail, Hotel]]:
        """Alias for canonical hotel_selection to prevent state drift."""
        return self.hotel_selection

    @selected_hotel.setter
    def selected_hotel(self, val: Optional[Union[HotelDetail, Hotel]]) -> None:
        self.hotel_selection = val
    
    # Transit
    travel_mode: str = Field(
        "driving",
        description="Preferred travel mode: driving, walking, bicycling, transit, two_wheeler"
    )
    
    # Interests & selections
    interests: List[str] = Field(
        default_factory=list,
        description="User interests and themes (e.g. ['historical', 'palaces', 'local food'])"
    )
    selected_places: List[Place] = Field(
        default_factory=list,
        description="List of selected attractions/places to visit"
    )
    rejected_places: List[str] = Field(
        default_factory=list,
        description="List of place names or IDs rejected/skipped by user"
    )
    selected_restaurants: List[Restaurant] = Field(
        default_factory=list,
        description="List of selected restaurants"
    )
    selected_cafes: List[Restaurant] = Field(
        default_factory=list,
        description="List of selected cafes and coffee shops"
    )
    rejected_restaurants: List[str] = Field(
        default_factory=list,
        description="List of restaurant names or IDs rejected/skipped by user"
    )
    rejected_cafes: List[str] = Field(
        default_factory=list,
        description="List of cafe names or IDs rejected/skipped by user"
    )

    @property
    def selected_food(self) -> List[Restaurant]:
        """Unified collection of all selected dining places (restaurants + cafes)."""
        return self.selected_restaurants + self.selected_cafes

    @property
    def rejected_food(self) -> List[str]:
        """Unified list of rejected dining places."""
        seen = set()
        res = []
        for item in self.rejected_restaurants + self.rejected_cafes:
            low = item.strip().lower()
            if low not in seen:
                seen.add(low)
                res.append(item.strip())
        return res

    # Preferences
    dietary_preferences: List[str] = Field(
        default_factory=list,
        description="Dietary requirements (e.g. ['vegetarian', 'vegan', 'halal', 'jain'])"
    )
    meal_preferences: Dict[str, str] = Field(
        default_factory=dict,
        description="Meal specific preferences (e.g. {'lunch': 'traditional thali', 'dinner': 'rooftop'})"
    )
    cuisine_preferences: List[str] = Field(
        default_factory=list,
        description="Preferred cuisines (e.g. ['South Indian', 'Rajasthani', 'Italian'])"
    )
    food_price_preference: Optional[str] = Field(
        None,
        description="Preferred food price level (e.g. 'cheap', 'mid-range', 'fine-dining')"
    )

    # Daily schedule constraints
    start_time: str = Field("09:00", description="Start time on Day 1 (HH:MM)")
    day_start_time: str = Field("09:00", description="Standard daily morning start time (HH:MM)")
    day_end_time: str = Field("22:00", description="Daily evening wind-down time (HH:MM)")

    # Deterministic outputs
    current_route: Optional[OptimizedRoute] = Field(
        None, description="Current optimized route computed by the route optimization engine"
    )
    current_itinerary: Optional[ItineraryResponse] = Field(
        None, description="Current day-by-day timetable computed by the itinerary engine"
    )
    messages: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Multi-turn conversation message history for this session"
    )
    derived_freshness: Dict[str, DerivedResourceFreshness] = Field(
        default_factory=init_derived_freshness,
        description="Freshness metadata and state version provenance for derived planning resources"
    )

    # Constraint & Feasibility model (Milestone 3, Batch 3)
    constraints: List[Constraint] = Field(
        default_factory=list,
        description="Formal constraints governing trip planning and feasibility"
    )
    required_stops: List[str] = Field(
        default_factory=list,
        description="List of attraction or food stop names explicitly designated as MUST_VISIT / REQUIRED"
    )
    feasibility_result: Optional[FeasibilityResult] = Field(
        None,
        description="Most recent deterministic feasibility evaluation outcome"
    )

    @model_validator(mode="after")
    def _sync_initial_derived_freshness(self) -> "TripState":
        """Synchronizes initial freshness for directly-provided route or itinerary."""
        if self.current_route is not None:
            entry = self.derived_freshness.get(DerivedResource.CURRENT_ROUTE.value)
            if not entry or entry.status == DerivedStateStatus.NOT_AVAILABLE:
                self.mark_derived_valid(DerivedResource.CURRENT_ROUTE)
        if self.current_itinerary is not None:
            entry = self.derived_freshness.get(DerivedResource.CURRENT_ITINERARY.value)
            if not entry or entry.status == DerivedStateStatus.NOT_AVAILABLE:
                self.mark_derived_valid(DerivedResource.CURRENT_ITINERARY)
        return self

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name in ("current_route", "current_itinerary") and value is not None:
            if hasattr(self, "derived_freshness") and isinstance(self.derived_freshness, dict):
                res = DerivedResource.CURRENT_ROUTE if name == "current_route" else DerivedResource.CURRENT_ITINERARY
                self.mark_derived_valid(res)

    def invalidate_itinerary(self) -> None:
        """Invalidates computed route & itinerary when stops or constraints change."""
        self.mark_derived_stale(DerivedResource.CURRENT_ROUTE)
        self.mark_derived_stale(DerivedResource.CURRENT_ITINERARY)
        self.current_route = None
        self.current_itinerary = None

    def get_derived_status(self, resource: Union[DerivedResource, str]) -> DerivedStateStatus:
        """Returns the current DerivedStateStatus (VALID, STALE, NOT_AVAILABLE) for a derived resource."""
        return FreshnessRegistry.get_status(self.derived_freshness, resource)

    def get_derived_version(self, resource: Union[DerivedResource, str]) -> Optional[int]:
        """Returns the state_version provenance for a derived resource."""
        return FreshnessRegistry.get_version(self.derived_freshness, resource)

    def is_derived_valid(self, resource: Union[DerivedResource, str]) -> bool:
        """Checks if a derived resource is currently VALID."""
        return self.get_derived_status(resource) == DerivedStateStatus.VALID

    def is_derived_stale(self, resource: Union[DerivedResource, str]) -> bool:
        """Checks if a derived resource is currently STALE."""
        return self.get_derived_status(resource) == DerivedStateStatus.STALE

    def is_derived_available(self, resource: Union[DerivedResource, str]) -> bool:
        """Checks if a derived resource is available (VALID or STALE, not NOT_AVAILABLE)."""
        return self.get_derived_status(resource) != DerivedStateStatus.NOT_AVAILABLE

    def mark_derived_valid(
        self,
        resource: Union[DerivedResource, str],
        result_state_version: Optional[int] = None,
    ) -> bool:
        """
        Marks a derived resource as VALID against current state_version.
        Rejects stale late results if result_state_version < self.state_version.
        """
        return FreshnessRegistry.mark_valid(
            self.derived_freshness,
            resource=resource,
            current_state_version=self.state_version,
            result_state_version=result_state_version,
        )

    def mark_derived_stale(self, resource: Union[DerivedResource, str]) -> bool:
        """Marks an existing derived resource as STALE. If NOT_AVAILABLE, remains NOT_AVAILABLE."""
        return FreshnessRegistry.mark_stale(self.derived_freshness, resource=resource)

    def mark_derived_not_available(self, resource: Union[DerivedResource, str]) -> None:
        """Resets a derived resource to NOT_AVAILABLE status."""
        FreshnessRegistry.mark_not_available(self.derived_freshness, resource=resource)

    def add_place(self, place: Place) -> bool:
        """
        Adds an attraction if not already selected.
        Returns True if added, False if duplicate.
        """
        for p in self.selected_places:
            if (p.data_id and p.data_id == place.data_id) or (p.name.strip().lower() == place.name.strip().lower()):
                return False
        self.selected_places.append(place)
        self.invalidate_itinerary()
        return True

    def remove_place(self, identifier: str) -> bool:
        """
        Removes an attraction by data_id or name.
        Returns True if found and removed, False otherwise.
        """
        clean_id = identifier.strip().lower()
        initial_len = len(self.selected_places)
        self.selected_places = [
            p for p in self.selected_places
            if (p.data_id and p.data_id.lower() == clean_id) is False
            and p.name.strip().lower() != clean_id
        ]
        if len(self.selected_places) < initial_len:
            self.invalidate_itinerary()
            return True
        return False

    def add_restaurant(self, restaurant: Restaurant) -> bool:
        """
        Adds a restaurant if not already selected.
        Returns True if added, False if duplicate.
        """
        for r in self.selected_restaurants:
            if (r.data_id and r.data_id == restaurant.data_id) or (r.name.strip().lower() == restaurant.name.strip().lower()):
                return False
        self.selected_restaurants.append(restaurant)
        self.invalidate_itinerary()
        return True

    def remove_restaurant(self, identifier: str) -> bool:
        """
        Removes a restaurant by data_id or name.
        Returns True if found and removed, False otherwise.
        """
        clean_id = identifier.strip().lower()
        initial_len = len(self.selected_restaurants)
        self.selected_restaurants = [
            r for r in self.selected_restaurants
            if (r.data_id and r.data_id.lower() == clean_id) is False
            and r.name.strip().lower() != clean_id
        ]
        if len(self.selected_restaurants) < initial_len:
            self.invalidate_itinerary()
            return True
        return False

    def add_cafe(self, cafe: Restaurant) -> bool:
        """
        Adds a cafe if not already selected.
        Returns True if added, False if duplicate.
        """
        for c in self.selected_cafes:
            if (c.data_id and c.data_id == cafe.data_id) or (c.name.strip().lower() == cafe.name.strip().lower()):
                return False
        self.selected_cafes.append(cafe)
        self.invalidate_itinerary()
        return True

    def remove_cafe(self, identifier: str) -> bool:
        """
        Removes a cafe by data_id or name.
        Returns True if found and removed, False otherwise.
        """
        clean_id = identifier.strip().lower()
        initial_len = len(self.selected_cafes)
        self.selected_cafes = [
            c for c in self.selected_cafes
            if (c.data_id and c.data_id.lower() == clean_id) is False
            and c.name.strip().lower() != clean_id
        ]
        if len(self.selected_cafes) < initial_len:
            self.invalidate_itinerary()
            return True
        return False

    def reject_place(self, identifier: str) -> bool:
        """
        Marks an attraction as rejected by name or data_id, and removes it from selected_places if present.
        Returns True if newly rejected, False if already in rejected_places.
        """
        clean_id = identifier.strip().lower()
        self.remove_place(identifier)
        for r in self.rejected_places:
            if r.strip().lower() == clean_id:
                return False
        self.rejected_places.append(identifier.strip())
        return True

    def unreject_place(self, identifier: str) -> bool:
        """Removes an attraction from rejected_places."""
        clean_id = identifier.strip().lower()
        initial_len = len(self.rejected_places)
        self.rejected_places = [
            r for r in self.rejected_places
            if r.strip().lower() != clean_id
        ]
        return len(self.rejected_places) < initial_len

    def reject_restaurant(self, identifier: str) -> bool:
        """
        Marks a restaurant as rejected by name or data_id, and removes it from selected_restaurants if present.
        Returns True if newly rejected, False if already in rejected_restaurants.
        """
        clean_id = identifier.strip().lower()
        self.remove_restaurant(identifier)
        for r in self.rejected_restaurants:
            if r.strip().lower() == clean_id:
                return False
        self.rejected_restaurants.append(identifier.strip())
        return True

    def unreject_restaurant(self, identifier: str) -> bool:
        """Removes a restaurant from rejected_restaurants."""
        clean_id = identifier.strip().lower()
        initial_len = len(self.rejected_restaurants)
        self.rejected_restaurants = [
            r for r in self.rejected_restaurants
            if r.strip().lower() != clean_id
        ]
        return len(self.rejected_restaurants) < initial_len

    def reject_cafe(self, identifier: str) -> bool:
        """
        Marks a cafe as rejected by name or data_id, and removes it from selected_cafes if present.
        Returns True if newly rejected, False if already in rejected_cafes.
        """
        clean_id = identifier.strip().lower()
        self.remove_cafe(identifier)
        for c in self.rejected_cafes:
            if c.strip().lower() == clean_id:
                return False
        self.rejected_cafes.append(identifier.strip())
        return True

    def unreject_cafe(self, identifier: str) -> bool:
        """Removes a cafe from rejected_cafes."""
        clean_id = identifier.strip().lower()
        initial_len = len(self.rejected_cafes)
        self.rejected_cafes = [
            c for c in self.rejected_cafes
            if c.strip().lower() != clean_id
        ]
        return len(self.rejected_cafes) < initial_len

    def reject_food(self, identifier: str, is_cafe: bool = False) -> bool:
        """Generic food rejection helper routing to cafe or restaurant rejection."""
        if is_cafe:
            return self.reject_cafe(identifier)
        return self.reject_restaurant(identifier)

    def clear_selected_stops(self) -> None:
        """Clears all selected places, restaurants, and cafes."""
        self.selected_places = []
        self.selected_restaurants = []
        self.selected_cafes = []
        self.invalidate_itinerary()

    def is_ready_for_routing(self) -> bool:
        """
        Checks if the trip has sufficient data to generate an optimized route:
        Requires destination, a hotel or start location (unless hotel_required is False), and at least 1 stop.
        """
        has_destination = bool(self.destination and self.destination.strip())
        if (
            self.hotel_search_required is False
            or self.hotel_required is False
            or (self.number_of_nights is not None and self.number_of_nights == 0)
        ):
            has_hotel = True
        else:
            has_hotel = self.hotel_selection is not None and self.hotel_selection.latitude is not None
        has_stops = (len(self.selected_places) + len(self.selected_restaurants) + len(self.selected_cafes)) >= 1
        return has_destination and has_hotel and has_stops

    # --- Constraint & Priority Helpers (Milestone 3, Batch 3) ---

    def mark_stop_required(self, identifier: str) -> bool:
        """
        Marks an attraction or dining stop as REQUIRED / MUST_VISIT.
        Returns True if a matching stop was found and updated.
        """
        clean_id = identifier.strip().lower()
        found = False
        canonical_name = identifier.strip()

        for p in self.selected_places:
            if (p.data_id and p.data_id.lower() == clean_id) or p.name.strip().lower() == clean_id or clean_id in p.name.strip().lower():
                p.is_required = True
                p.priority = "REQUIRED"
                canonical_name = p.name
                found = True
                break

        if not found:
            for r in self.selected_food:
                if (r.data_id and r.data_id.lower() == clean_id) or r.name.strip().lower() == clean_id or clean_id in r.name.strip().lower():
                    canonical_name = r.name
                    found = True
                    break

        if found:
            if canonical_name not in self.required_stops:
                self.required_stops.append(canonical_name)
            self.invalidate_itinerary()
            return True
        return False

    def mark_stop_priority(self, identifier: str, priority: Union[ConstraintPriority, str]) -> bool:
        """
        Sets explicit priority (REQUIRED, PREFERRED, OPTIONAL) on a stop.
        """
        p_val = priority.value if hasattr(priority, "value") else str(priority).upper()
        if p_val == "REQUIRED":
            return self.mark_stop_required(identifier)

        clean_id = identifier.strip().lower()
        found = False
        canonical_name = identifier.strip()

        for p in self.selected_places:
            if (p.data_id and p.data_id.lower() == clean_id) or p.name.strip().lower() == clean_id or clean_id in p.name.strip().lower():
                p.is_required = False
                p.priority = p_val
                canonical_name = p.name
                found = True
                break

        if found:
            self.required_stops = [s for s in self.required_stops if s.lower() != canonical_name.lower()]
            self.invalidate_itinerary()
            return True
        return False

    def is_stop_required(self, identifier: str) -> bool:
        """Checks if a stop is explicitly designated as REQUIRED."""
        clean_id = identifier.strip().lower()
        for s in self.required_stops:
            if s.lower() == clean_id or clean_id in s.lower():
                return True
        for p in self.selected_places:
            if ((p.data_id and p.data_id.lower() == clean_id) or p.name.strip().lower() == clean_id) and (p.is_required or p.priority == "REQUIRED"):
                return True
        return False

    def set_hotel(self, hotel: Optional[Hotel]) -> None:
        """Sets the selected hotel."""
        self.hotel_selection = hotel

    def add_constraint(self, constraint: Constraint) -> None:
        """Adds or updates a Constraint in state.constraints."""
        self.constraints = [
            c for c in self.constraints
            if c.id != constraint.id and getattr(c, "constraint_type", None) != getattr(constraint, "constraint_type", None)
        ]
        self.constraints.append(constraint)

    def remove_constraint(self, identifier: str) -> bool:
        """Removes a constraint by ID or type string."""
        initial_len = len(self.constraints)
        self.constraints = [
            c for c in self.constraints
            if c.id != identifier and getattr(c.constraint_type, "value", str(c.constraint_type)) != identifier
        ]
        return len(self.constraints) < initial_len


    def summary(self) -> Dict[str, Union[str, int, float, list, None]]:
        """Returns a human-readable summary of the current trip planning state."""
        return {
            "conversation_id": self.conversation_id,
            "destination": self.destination,
            "dates": f"{self.trip_start_date} to {self.trip_end_date}" if self.trip_start_date else None,
            "number_of_days": self.number_of_days,
            "number_of_nights": self.number_of_nights,
            "accommodation_required": self.accommodation_required,
            "accommodation_booked": self.accommodation_booked,
            "hotel_search_required": self.hotel_search_required,
            "hotel_required": self.hotel_required,
            "hotel": self.hotel_selection.name if self.hotel_selection else None,
            "hotel_budget": self.hotel_budget,
            "hotel_total_budget": self.hotel_total_budget,
            "trip_budget": self.trip_budget,
            "feasibility_notes": list(self.feasibility_notes),
            "planning_stage": self.planning_stage.value if hasattr(self.planning_stage, "value") else str(self.planning_stage),
            "places_count": len(self.selected_places),
            "restaurants_count": len(self.selected_restaurants),
            "cafes_count": len(self.selected_cafes),
            "places": [p.name for p in self.selected_places],
            "required_stops": list(self.required_stops),
            "rejected_places": list(self.rejected_places),
            "restaurants": [r.name for r in self.selected_restaurants],
            "rejected_restaurants": list(self.rejected_restaurants),
            "cafes": [c.name for c in self.selected_cafes],
            "rejected_cafes": list(self.rejected_cafes),
            "rejected_food": self.rejected_food,
            "dietary_preferences": list(self.dietary_preferences),
            "interests": list(self.interests),
            "cuisine_preferences": list(self.cuisine_preferences),
            "food_price_preference": self.food_price_preference,
            "travel_mode": self.travel_mode,
            "has_route": self.current_route is not None,
            "has_itinerary": self.current_itinerary is not None,
            "feasibility_status": self.feasibility_result.status.value if self.feasibility_result else None,
            "constraints_count": len(self.constraints),
            "derived_freshness": {
                k: v.status.value for k, v in self.derived_freshness.items()
            },
            "state_version": self.state_version,
        }



# --- In-Memory State Repository ---

_CONVERSATION_STORE: Dict[str, TripState] = {}


def get_or_create_state(conversation_id: Optional[str] = None) -> TripState:
    """
    Retrieves the existing TripState for a conversation_id,
    or creates and initializes a new TripState if not found or if conversation_id is None.
    """
    if conversation_id and conversation_id in _CONVERSATION_STORE:
        return _CONVERSATION_STORE[conversation_id]
    
    new_state = TripState(conversation_id=conversation_id or str(uuid.uuid4()))
    _CONVERSATION_STORE[new_state.conversation_id] = new_state
    return new_state


def get_state(conversation_id: str) -> Optional[TripState]:
    """Retrieves an existing TripState, returning None if not found."""
    return _CONVERSATION_STORE.get(conversation_id)


def save_state(state: TripState) -> TripState:
    """Saves or updates a TripState in the in-memory store."""
    _CONVERSATION_STORE[state.conversation_id] = state
    return state


def reset_state(conversation_id: str) -> TripState:
    """Resets the state for a given conversation_id to an empty state."""
    new_state = TripState(conversation_id=conversation_id)
    _CONVERSATION_STORE[conversation_id] = new_state
    return new_state


def delete_state(conversation_id: str) -> bool:
    """Deletes a conversation state. Returns True if deleted, False if not found."""
    if conversation_id in _CONVERSATION_STORE:
        del _CONVERSATION_STORE[conversation_id]
        return True
    return False


def clear_all_states() -> None:
    """Clears all in-memory conversation states (useful for test isolation)."""
    _CONVERSATION_STORE.clear()
