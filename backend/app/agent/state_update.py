"""
Project Musafir — State Update & Dependency Invalidation Layer (Step 12.5)
Calculates deterministic change sets, manages dependency invalidation,
readiness evaluations, and stale-result state versioning.
"""

from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field
from datetime import date as dt_date

from app.agent.state import TripState, PlanningStage
from app.agent.semantics import (
    calculate_nights,
    calculate_nightly_hotel_budget,
    derive_trip_dates,
)
from app.agent.context import ActiveQuestion


class TripChangeSet(BaseModel):
    """
    Deterministic record of state modifications resulting from user conversation.
    Explicitly separates fields that changed from fields that were invalidated.
    """
    changed_fields: List[str] = Field(
        default_factory=list,
        description="Fields explicitly updated by the user"
    )
    invalidated_fields: List[str] = Field(
        default_factory=list,
        description="Dependent fields cleared or reset due to cascading dependency rules"
    )
    previous_values: Dict[str, Any] = Field(
        default_factory=dict,
        description="Previous values of changed fields before mutation"
    )
    new_values: Dict[str, Any] = Field(
        default_factory=dict,
        description="New values of changed fields after mutation"
    )

    def has_changes(self) -> bool:
        """Returns True if any fields were changed or invalidated."""
        return len(self.changed_fields) > 0 or len(self.invalidated_fields) > 0

    def summary(self) -> str:
        """Compact textual summary of changes for debugging and agent prompt context."""
        parts = []
        if self.changed_fields:
            changed_str = ", ".join(
                f"{f} ('{self.previous_values.get(f)}' → '{self.new_values.get(f)}')"
                for f in self.changed_fields
            )
            parts.append(f"Changed: {changed_str}")
        if self.invalidated_fields:
            parts.append(f"Invalidated: {', '.join(self.invalidated_fields)}")
        return "; ".join(parts) if parts else "No changes"


# Conservative list of overly broad regions that require clarification
BROAD_DESTINATIONS = {
    "south india",
    "north india",
    "east india",
    "west india",
    "central india",
    "northeast india",
    "europe",
    "asia",
    "himalayas",
    "scandinavia",
}


def is_broad_destination(destination: Optional[str]) -> bool:
    """
    Checks if destination is too broad for direct hotel or place discovery.
    E.g. 'South India' requires clarification, but 'Kashmir' is accepted as a destination.
    """
    if not destination or not destination.strip():
        return False
    return destination.strip().lower() in BROAD_DESTINATIONS


def apply_trip_state_update(state: TripState, updates: Dict[str, Any]) -> TripChangeSet:
    """
    Deterministically applies structured updates to a TripState instance.
    - Preserves all omitted fields (omission is never treated as deletion).
    - Enforces dependency invalidation (e.g. changing destination clears hotels/places/routes).
    - Increments state.state_version when any meaningful changes occur.
    """
    change_set = TripChangeSet()
    if not updates or not isinstance(updates, dict):
        return change_set

    # 1. Check destination change
    if "destination" in updates and updates["destination"] is not None:
        new_dest = str(updates["destination"]).strip()
        old_dest = state.destination
        if new_dest and new_dest.lower() != (old_dest or "").lower():
            change_set.changed_fields.append("destination")
            change_set.previous_values["destination"] = old_dest
            change_set.new_values["destination"] = new_dest
            state.destination = new_dest

            # Invalidate destination-dependent data
            if state.hotel_selection is not None:
                state.hotel_selection = None
                change_set.invalidated_fields.append("selected_hotel")

            if len(state.selected_places) > 0:
                state.selected_places = []
                change_set.invalidated_fields.append("selected_places")

            if len(state.selected_restaurants) > 0:
                state.selected_restaurants = []
                change_set.invalidated_fields.append("selected_restaurants")

            if state.current_route is not None:
                state.current_route = None
                change_set.invalidated_fields.append("current_route")

            if state.current_itinerary is not None:
                state.current_itinerary = None
                change_set.invalidated_fields.append("current_itinerary")

    # 2. Check duration / number_of_days change
    if "number_of_days" in updates and updates["number_of_days"] is not None:
        try:
            new_days = int(updates["number_of_days"])
            if new_days >= 1 and new_days != state.number_of_days:
                change_set.changed_fields.append("number_of_days")
                change_set.previous_values["number_of_days"] = state.number_of_days
                change_set.new_values["number_of_days"] = new_days
                state.number_of_days = new_days

                # Automatically calculate derived number_of_nights if not explicitly set in updates
                if "number_of_nights" not in updates:
                    derived_nights = calculate_nights(number_of_days=new_days)
                    if state.number_of_nights != derived_nights:
                        state.number_of_nights = derived_nights

                # If hotel_total_budget is known, derive or update nightly hotel budget ceiling
                if state.hotel_total_budget and state.number_of_nights:
                    derived_rate = calculate_nightly_hotel_budget(
                        total_hotel_budget=state.hotel_total_budget,
                        explicit_nights=state.number_of_nights,
                    )
                    if derived_rate and derived_rate != state.hotel_budget:
                        state.hotel_budget = derived_rate

                # Changing duration invalidates the generated day-by-day timetable
                if state.current_itinerary is not None:
                    state.current_itinerary = None
                    change_set.invalidated_fields.append("current_itinerary")
        except (ValueError, TypeError):
            pass

    # 2B. Check explicit number_of_nights change
    if "number_of_nights" in updates and updates["number_of_nights"] is not None:
        try:
            new_nights = int(updates["number_of_nights"])
            if new_nights >= 0 and new_nights != state.number_of_nights:
                change_set.changed_fields.append("number_of_nights")
                change_set.previous_values["number_of_nights"] = state.number_of_nights
                change_set.new_values["number_of_nights"] = new_nights
                state.number_of_nights = new_nights

                if state.hotel_total_budget:
                    derived_rate = calculate_nightly_hotel_budget(
                        total_hotel_budget=state.hotel_total_budget,
                        explicit_nights=new_nights,
                    )
                    if derived_rate and derived_rate != state.hotel_budget:
                        state.hotel_budget = derived_rate
        except (ValueError, TypeError):
            pass

    # 3. Check dates changes
    for date_field in ["trip_start_date", "trip_end_date"]:
        if date_field in updates and updates[date_field] is not None:
            raw_val = updates[date_field]
            try:
                parsed_date = dt_date.fromisoformat(str(raw_val).strip()) if isinstance(raw_val, str) else raw_val
                old_date = getattr(state, date_field)
                if parsed_date != old_date:
                    change_set.changed_fields.append(date_field)
                    change_set.previous_values[date_field] = old_date
                    change_set.new_values[date_field] = parsed_date
                    setattr(state, date_field, parsed_date)

                    if state.current_itinerary is not None and "current_itinerary" not in change_set.invalidated_fields:
                        state.current_itinerary = None
                        change_set.invalidated_fields.append("current_itinerary")
            except (ValueError, TypeError):
                pass

    # 3B. Deterministic synchronization of dates, checkout, duration, and nights
    if any(k in updates for k in ["number_of_days", "trip_start_date", "trip_end_date", "number_of_nights"]) or state.trip_start_date:
        calc_end = None if ("number_of_days" in updates and "trip_end_date" not in updates) else state.trip_end_date
        derived = derive_trip_dates(
            start_date=state.trip_start_date,
            end_date=calc_end,
            number_of_days=state.number_of_days,
            explicit_nights=updates.get("number_of_nights"),
        )
        if "trip_end_date" in derived and derived["trip_end_date"] != state.trip_end_date:
            state.trip_end_date = derived["trip_end_date"]
        if "number_of_days" in derived and derived["number_of_days"] != state.number_of_days:
            if "number_of_days" not in change_set.changed_fields:
                change_set.changed_fields.append("number_of_days")
                change_set.previous_values["number_of_days"] = state.number_of_days
                change_set.new_values["number_of_days"] = derived["number_of_days"]
            state.number_of_days = derived["number_of_days"]
        if "number_of_nights" in derived and derived["number_of_nights"] != state.number_of_nights:
            state.number_of_nights = derived["number_of_nights"]

        # Recalculate nightly hotel budget ceiling
        if state.number_of_nights == 0:
            state.hotel_budget = None
        elif state.hotel_total_budget and state.number_of_nights:
            derived_rate = calculate_nightly_hotel_budget(
                total_hotel_budget=state.hotel_total_budget,
                explicit_nights=state.number_of_nights,
            )
            if derived_rate and derived_rate != state.hotel_budget:
                state.hotel_budget = derived_rate

    # 4A. Check hotel_total_budget change (total accommodation budget across all nights)
    if "hotel_total_budget" in updates and updates["hotel_total_budget"] is not None:
        try:
            new_total = float(updates["hotel_total_budget"])
            if new_total > 0 and new_total != state.hotel_total_budget:
                change_set.changed_fields.append("hotel_total_budget")
                change_set.previous_values["hotel_total_budget"] = state.hotel_total_budget
                change_set.new_values["hotel_total_budget"] = new_total
                state.hotel_total_budget = new_total

                # Check if nights are known to derive hotel_budget per night
                nights = state.number_of_nights
                if nights is None and state.number_of_days:
                    nights = calculate_nights(number_of_days=state.number_of_days)
                    state.number_of_nights = nights
                elif nights is None and state.trip_start_date and state.trip_end_date:
                    nights = calculate_nights(start_date=state.trip_start_date, end_date=state.trip_end_date)
                    state.number_of_nights = nights

                if nights and nights > 0:
                    derived_rate = calculate_nightly_hotel_budget(
                        total_hotel_budget=new_total,
                        explicit_nights=nights,
                    )
                    if derived_rate and derived_rate != state.hotel_budget:
                        if "hotel_budget" not in change_set.changed_fields:
                            change_set.changed_fields.append("hotel_budget")
                            change_set.previous_values["hotel_budget"] = state.hotel_budget
                            change_set.new_values["hotel_budget"] = derived_rate
                        state.hotel_budget = derived_rate
        except (ValueError, TypeError):
            pass

    # 4B. Check hotel_budget change (accommodation budget ceiling per night)
    if "hotel_budget" in updates and updates["hotel_budget"] is not None:
        try:
            new_budget = float(updates["hotel_budget"])
            if new_budget > 0 and new_budget != state.hotel_budget:
                if "hotel_budget" not in change_set.changed_fields:
                    change_set.changed_fields.append("hotel_budget")
                    change_set.previous_values["hotel_budget"] = state.hotel_budget
                    change_set.new_values["hotel_budget"] = new_budget
                state.hotel_budget = new_budget

                # Only invalidate selected hotel if its price exceeds the new budget
                if state.hotel_selection is not None:
                    hotel_rate = state.hotel_selection.price_per_night
                    if hotel_rate is not None and hotel_rate > new_budget:
                        state.hotel_selection = None
                        change_set.invalidated_fields.append("selected_hotel")
                        if state.current_route is not None and "current_route" not in change_set.invalidated_fields:
                            state.current_route = None
                            change_set.invalidated_fields.append("current_route")
                        if state.current_itinerary is not None and "current_itinerary" not in change_set.invalidated_fields:
                            state.current_itinerary = None
                            change_set.invalidated_fields.append("current_itinerary")
        except (ValueError, TypeError):
            pass

    # 4C. Check trip_budget change (overall whole-trip budget, independent of hotel)
    if "trip_budget" in updates and updates["trip_budget"] is not None:
        try:
            new_trip_budget = float(updates["trip_budget"])
            if new_trip_budget > 0 and new_trip_budget != state.trip_budget:
                change_set.changed_fields.append("trip_budget")
                change_set.previous_values["trip_budget"] = state.trip_budget
                change_set.new_values["trip_budget"] = new_trip_budget
                state.trip_budget = new_trip_budget
        except (ValueError, TypeError):
            pass

    # 4D. Check hotel_selection change (direct card selection)
    if "hotel_selection" in updates and updates["hotel_selection"] is not None:
        raw_hotel = updates["hotel_selection"]
        if isinstance(raw_hotel, dict):
            from app.schemas.hotel import HotelDetail, Hotel
            try:
                state.hotel_selection = HotelDetail(**raw_hotel)
            except Exception:
                try:
                    state.hotel_selection = Hotel(**raw_hotel)
                except Exception:
                    pass
        elif hasattr(raw_hotel, "name"):
            state.hotel_selection = raw_hotel
        change_set.changed_fields.append("hotel_selection")
        change_set.new_values["hotel_selection"] = state.hotel_selection.name if state.hotel_selection else str(raw_hotel)

        # Stage transition: Selecting a hotel transitions workflow to PLACE_DISCOVERY
        if state.planning_stage in (PlanningStage.DISCOVERY, PlanningStage.HOTEL_SELECTION):
            state.planning_stage = PlanningStage.PLACE_DISCOVERY
            change_set.changed_fields.append("planning_stage")
            change_set.new_values["planning_stage"] = state.planning_stage.value

    # 4E. Check selected_places change (multi or single selection)
    if "selected_places" in updates and updates["selected_places"] is not None:
        raw_places = updates["selected_places"]
        if not isinstance(raw_places, list):
            raw_places = [raw_places]
        from app.schemas.place import Place
        added_places = []
        for p in raw_places:
            place_obj = None
            if isinstance(p, Place):
                place_obj = p
            elif isinstance(p, dict):
                try:
                    place_obj = Place(**p)
                except Exception:
                    name = p.get("name") or p.get("title")
                    if name:
                        place_obj = Place(
                            data_id=str(p.get("data_id") or p.get("id") or name),
                            name=str(name),
                            latitude=p.get("latitude") or 0.0,
                            longitude=p.get("longitude") or 0.0,
                        )
            if place_obj and state.add_place(place_obj):
                added_places.append(place_obj.name)

        if added_places:
            change_set.changed_fields.append("selected_places")
            change_set.new_values["selected_places"] = [p.name for p in state.selected_places]
            # Selecting places keeps the workflow in PLACE_SELECTION
            state.planning_stage = PlanningStage.PLACE_SELECTION

    # 4F. Check rejected_places change
    if "rejected_places" in updates and updates["rejected_places"] is not None:
        raw_rejected = updates["rejected_places"]
        if not isinstance(raw_rejected, list):
            raw_rejected = [raw_rejected]
        newly_rejected = []
        for r in raw_rejected:
            r_str = str(r.get("name") if isinstance(r, dict) else r).strip()
            if r_str and state.reject_place(r_str):
                newly_rejected.append(r_str)

        if newly_rejected:
            change_set.changed_fields.append("rejected_places")
            change_set.new_values["rejected_places"] = list(state.rejected_places)
            state.planning_stage = PlanningStage.PLACE_SELECTION

    # 4G. Explicit planning_stage update
    if "planning_stage" in updates and updates["planning_stage"] is not None:
        raw_stage = updates["planning_stage"]
        try:
            target_stage = PlanningStage(raw_stage)
            if target_stage != state.planning_stage:
                change_set.changed_fields.append("planning_stage")
                change_set.previous_values["planning_stage"] = state.planning_stage.value
                change_set.new_values["planning_stage"] = target_stage.value
                state.planning_stage = target_stage
        except ValueError:
            pass

    # 5. Check travel_mode change
    if "travel_mode" in updates and updates["travel_mode"] is not None:
        new_mode = str(updates["travel_mode"]).strip().lower()
        if new_mode and new_mode != state.travel_mode:
            change_set.changed_fields.append("travel_mode")
            change_set.previous_values["travel_mode"] = state.travel_mode
            change_set.new_values["travel_mode"] = new_mode
            state.travel_mode = new_mode

            # Changing travel mode invalidates travel timings and route legs
            if state.current_route is not None and "current_route" not in change_set.invalidated_fields:
                state.current_route = None
                change_set.invalidated_fields.append("current_route")
            if state.current_itinerary is not None and "current_itinerary" not in change_set.invalidated_fields:
                state.current_itinerary = None
                change_set.invalidated_fields.append("current_itinerary")

    # 6. Check interests change
    if "interests" in updates and updates["interests"] is not None:
        raw_interests = updates["interests"]
        if isinstance(raw_interests, list):
            clean_interests = [str(i).strip() for i in raw_interests if str(i).strip()]
            if clean_interests != state.interests:
                change_set.changed_fields.append("interests")
                change_set.previous_values["interests"] = list(state.interests)
                change_set.new_values["interests"] = clean_interests
                state.interests = clean_interests

    # 7. Check dietary_preferences change
    if "dietary_preferences" in updates and updates["dietary_preferences"] is not None:
        raw_diet = updates["dietary_preferences"]
        if isinstance(raw_diet, list):
            clean_diet = [str(d).strip() for d in raw_diet if str(d).strip()]
            if clean_diet != state.dietary_preferences:
                change_set.changed_fields.append("dietary_preferences")
                change_set.previous_values["dietary_preferences"] = list(state.dietary_preferences)
                change_set.new_values["dietary_preferences"] = clean_diet
                state.dietary_preferences = clean_diet

    # 8. Check meal_preferences change
    if "meal_preferences" in updates and updates["meal_preferences"] is not None:
        raw_meals = updates["meal_preferences"]
        if isinstance(raw_meals, dict) and raw_meals != state.meal_preferences:
            change_set.changed_fields.append("meal_preferences")
            change_set.previous_values["meal_preferences"] = dict(state.meal_preferences)
            change_set.new_values["meal_preferences"] = dict(raw_meals)
            state.meal_preferences = dict(raw_meals)

    # Increment state_version if any changes occurred
    if change_set.has_changes():
        state.state_version += 1

    return change_set


def check_trip_readiness(state: TripState) -> Dict[str, bool]:
    """
    Evaluates what next conversational action or discovery capability is feasible.
    """
    has_dest = bool(state.destination and state.destination.strip())
    is_broad = is_broad_destination(state.destination)
    effective_dest = has_dest and not is_broad

    has_duration = bool(state.number_of_days and state.number_of_days >= 1)
    has_budget = bool(state.hotel_budget and state.hotel_budget > 0)
    has_hotel = bool(state.hotel_selection and state.hotel_selection.latitude is not None)
    stops_count = len(state.selected_places) + len(state.selected_restaurants)

    return {
        "has_destination": effective_dest,
        "is_broad_destination": is_broad,
        "has_duration": has_duration,
        "has_hotel_budget": has_budget,
        "has_selected_hotel": has_hotel,
        "ready_for_hotel_search": effective_dest and has_budget,
        "ready_for_place_search": effective_dest,
        "ready_for_route": effective_dest and has_hotel and stops_count >= 1,
        "ready_for_itinerary": bool(state.current_route and has_duration),
    }


def get_next_missing_requirement(state: TripState) -> Optional[str]:
    """
    Progressive Interviewing Helper:
    Identifies the single most important missing field to ask the user next.
    Returns: 'destination' | 'destination_clarification' | 'duration' | 'hotel_budget' | 'interests' | None
    """
    if not state.destination or not state.destination.strip():
        return "destination"

    if is_broad_destination(state.destination):
        return "destination_clarification"

    if not state.number_of_days or state.number_of_days < 1:
        return "duration"

    if not state.hotel_budget or state.hotel_budget <= 0:
        return "hotel_budget"

    if not state.interests:
        return "interests"

    return None


def get_next_active_question(state: TripState) -> Optional[ActiveQuestion]:
    """
    Deterministic readiness layer helper:
    Identifies the next required field and generates an ActiveQuestion with reason.
    Priority:
    1. Destination missing -> required_for_trip_planning
    2. Broad destination -> clarification_required_for_broad_destination
    3. Duration missing -> required_for_itinerary
    4. Accommodation budget missing -> required_for_hotel_search
    5. Start date missing -> required_for_exact_dates
    """
    if not state.destination or not state.destination.strip():
        return ActiveQuestion(
            field="destination",
            expected_type="string",
            scope="destination",
            prompt_text="Where would you like to travel?",
            reason="required_for_trip_planning",
        )

    if is_broad_destination(state.destination):
        return ActiveQuestion(
            field="destination",
            expected_type="string",
            scope="destination",
            prompt_text=f"Which specific city or region in {state.destination} are you considering?",
            reason="clarification_required_for_broad_destination",
        )

    if not state.number_of_days or state.number_of_days < 1:
        return ActiveQuestion(
            field="number_of_days",
            expected_type="number",
            scope="trip",
            prompt_text="How many days are you planning for your trip?",
            reason="required_for_itinerary",
        )

    if (not state.hotel_budget or state.hotel_budget <= 0) and (not state.hotel_total_budget or state.hotel_total_budget <= 0):
        return ActiveQuestion(
            field="hotel_total_budget",
            expected_type="money",
            scope="accommodation",
            prompt_text="What budget would you like to keep for accommodation?",
            reason="required_for_hotel_search",
        )

    if not state.trip_start_date:
        return ActiveQuestion(
            field="trip_start_date",
            expected_type="date",
            scope="trip",
            prompt_text="When are you planning to start your trip?",
            reason="required_for_exact_dates",
        )

    return None
