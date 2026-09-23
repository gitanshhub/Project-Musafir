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
    
    # Accommodation
    hotel_budget: Optional[float] = Field(None, gt=0, description="Max acceptable nightly hotel rate in INR")
    hotel_total_budget: Optional[float] = Field(None, gt=0, description="Total budget for accommodation in INR")
    trip_budget: Optional[float] = Field(None, gt=0, description="Overall total trip budget in INR")
    hotel_selection: Optional[Union[HotelDetail, Hotel]] = Field(
        None, description="Selected hotel for accommodation / trip starting point"
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
        description="List of selected restaurants and cafes"
    )
    
    # Preferences
    dietary_preferences: List[str] = Field(
        default_factory=list,
        description="Dietary requirements (e.g. ['vegetarian', 'vegan', 'halal'])"
    )
    meal_preferences: Dict[str, str] = Field(
        default_factory=dict,
        description="Meal specific preferences (e.g. {'lunch': 'traditional thali', 'dinner': 'rooftop'})"
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

    def invalidate_itinerary(self) -> None:
        """Invalidates computed route & itinerary when stops or constraints change."""
        self.current_route = None
        self.current_itinerary = None

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

    def clear_selected_stops(self) -> None:
        """Clears all selected places and restaurants."""
        self.selected_places = []
        self.selected_restaurants = []
        self.invalidate_itinerary()

    def is_ready_for_routing(self) -> bool:
        """
        Checks if the trip has sufficient data to generate an optimized route:
        Requires destination, a hotel or start location, and at least 1 stop.
        """
        has_destination = bool(self.destination and self.destination.strip())
        has_hotel = self.hotel_selection is not None and self.hotel_selection.latitude is not None
        has_stops = (len(self.selected_places) + len(self.selected_restaurants)) >= 1
        return has_destination and has_hotel and has_stops

    def summary(self) -> Dict[str, Union[str, int, float, list, None]]:
        """Returns a human-readable summary of the current trip planning state."""
        return {
            "conversation_id": self.conversation_id,
            "destination": self.destination,
            "dates": f"{self.trip_start_date} to {self.trip_end_date}" if self.trip_start_date else None,
            "number_of_days": self.number_of_days,
            "number_of_nights": self.number_of_nights,
            "hotel": self.hotel_selection.name if self.hotel_selection else None,
            "hotel_budget": self.hotel_budget,
            "hotel_total_budget": self.hotel_total_budget,
            "trip_budget": self.trip_budget,
            "planning_stage": self.planning_stage.value if hasattr(self.planning_stage, "value") else str(self.planning_stage),
            "places_count": len(self.selected_places),
            "restaurants_count": len(self.selected_restaurants),
            "places": [p.name for p in self.selected_places],
            "rejected_places": list(self.rejected_places),
            "restaurants": [r.name for r in self.selected_restaurants],
            "travel_mode": self.travel_mode,
            "has_route": self.current_route is not None,
            "has_itinerary": self.current_itinerary is not None,
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
