"""
Project Musafir — State Update & Dependency Invalidation Layer (Step 12.5)
Calculates deterministic change sets, manages dependency invalidation,
readiness evaluations, and stale-result state versioning.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datetime import date as dt_date

from app.agent.state import TripState, PlanningStage
from app.agent.semantics import (
    calculate_nights,
    calculate_nightly_hotel_budget,
    derive_trip_dates,
    derive_trip_inferences,
)
from app.agent.context import ActiveQuestion
from app.agent.dependencies import DerivedResource, get_invalidated_resources
from app.agent.freshness import FreshnessRegistry, DerivedStateStatus


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

    # Synchronize derived_freshness if route or itinerary was assigned directly without marking
    if state.current_route is not None and state.get_derived_status(DerivedResource.CURRENT_ROUTE) == DerivedStateStatus.NOT_AVAILABLE:
        state.mark_derived_valid(DerivedResource.CURRENT_ROUTE)
    if state.current_itinerary is not None and state.get_derived_status(DerivedResource.CURRENT_ITINERARY) == DerivedStateStatus.NOT_AVAILABLE:
        state.mark_derived_valid(DerivedResource.CURRENT_ITINERARY)

    # Track pre-mutation derived resource presence for accurate change reporting
    had_route = state.current_route is not None
    had_itinerary = state.current_itinerary is not None

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

            if len(state.selected_cafes) > 0:
                state.selected_cafes = []
                change_set.invalidated_fields.append("selected_cafes")

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
            except (ValueError, TypeError):
                pass

    # 3B. Deterministic synchronization of dates, checkout, duration, and nights
    if any(k in updates for k in ["number_of_days", "trip_start_date", "trip_end_date", "number_of_nights"]) or state.trip_start_date:
        calc_end = None if ({"number_of_days", "trip_start_date"} & updates.keys() and "trip_end_date" not in updates) else state.trip_end_date
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

    # 3C. Explicit accommodation changes (Milestone 3, M3.2 Fix #1)
    if "accommodation_required" in updates and updates["accommodation_required"] is not None:
        new_ar = bool(updates["accommodation_required"])
        if new_ar != state.accommodation_required:
            change_set.changed_fields.append("accommodation_required")
            change_set.previous_values["accommodation_required"] = state.accommodation_required
            change_set.new_values["accommodation_required"] = new_ar
            state.accommodation_required = new_ar

    if "accommodation_booked" in updates and updates["accommodation_booked"] is not None:
        new_ab = bool(updates["accommodation_booked"])
        if new_ab != state.accommodation_booked:
            change_set.changed_fields.append("accommodation_booked")
            change_set.previous_values["accommodation_booked"] = state.accommodation_booked
            change_set.new_values["accommodation_booked"] = new_ab
            state.accommodation_booked = new_ab

    if "hotel_search_required" in updates and updates["hotel_search_required"] is not None:
        new_hsr = bool(updates["hotel_search_required"])
        if new_hsr != state.hotel_search_required:
            change_set.changed_fields.append("hotel_search_required")
            change_set.previous_values["hotel_search_required"] = state.hotel_search_required
            change_set.new_values["hotel_search_required"] = new_hsr
            state.hotel_search_required = new_hsr

    if "hotel_required" in updates and updates["hotel_required"] is not None:
        new_hr = bool(updates["hotel_required"])
        if new_hr != state.hotel_required:
            change_set.changed_fields.append("hotel_required")
            change_set.previous_values["hotel_required"] = state.hotel_required
            change_set.new_values["hotel_required"] = new_hr
            state.hotel_required = new_hr

    # 3D. Tracking of explicit fields and slot confidence (Milestone 3, M3.2)
    if "explicit_fields" in updates and updates["explicit_fields"]:
        for ef in updates["explicit_fields"]:
            if ef not in state.explicit_fields:
                state.explicit_fields.append(ef)

    if "extracted_confidence" in updates and updates["extracted_confidence"]:
        state.extracted_confidence.update(updates["extracted_confidence"])

    # 3E. Deterministic derivation of dependent fields (nights, hotel_required)
    if {"number_of_days", "number_of_nights", "trip_start_date", "trip_end_date"} & updates.keys():
        if "accommodation_required" not in state.explicit_fields and "hotel_required" not in state.explicit_fields:
            state.accommodation_required = None
    derive_trip_inferences(state, state.explicit_fields)

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

                        # Invalidate selected hotel if its rate exceeds new derived ceiling
                        if state.hotel_selection is not None:
                            hotel_rate = state.hotel_selection.price_per_night
                            if hotel_rate is not None and hotel_rate > derived_rate:
                                state.hotel_selection = None
                                change_set.invalidated_fields.append("selected_hotel")
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
            state.planning_stage = PlanningStage.PLACE_SELECTION

    # 4E-rem. Check remove_places change
    if "remove_places" in updates and updates["remove_places"] is not None:
        raw_rem_places = updates["remove_places"]
        if not isinstance(raw_rem_places, list):
            raw_rem_places = [raw_rem_places]
        prev_places = [p.name for p in state.selected_places]
        removed_any = False
        for p_ident in raw_rem_places:
            ident_str = str(p_ident.get("name") if isinstance(p_ident, dict) else p_ident).strip()
            if ident_str and state.remove_place(ident_str):
                removed_any = True

        if removed_any:
            if "selected_places" not in change_set.changed_fields:
                change_set.changed_fields.append("selected_places")
                change_set.previous_values["selected_places"] = prev_places
                change_set.new_values["selected_places"] = [p.name for p in state.selected_places]

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

    # 4H. Check selected_restaurants change (multi or single selection)
    if "selected_restaurants" in updates and updates["selected_restaurants"] is not None:
        raw_rests = updates["selected_restaurants"]
        if not isinstance(raw_rests, list):
            raw_rests = [raw_rests]
        from app.schemas.restaurant import Restaurant
        added_rests = []
        for r in raw_rests:
            rest_obj = None
            if isinstance(r, Restaurant):
                rest_obj = r
            elif isinstance(r, dict):
                try:
                    rest_obj = Restaurant(**r)
                except Exception:
                    name = r.get("name") or r.get("title")
                    if name:
                        rest_obj = Restaurant(
                            data_id=str(r.get("data_id") or r.get("id") or name),
                            name=str(name),
                            latitude=r.get("latitude"),
                            longitude=r.get("longitude"),
                            rating=r.get("rating"),
                            cuisine=r.get("cuisine") or [],
                            price_level=r.get("price_level"),
                        )
            if rest_obj and state.add_restaurant(rest_obj):
                added_rests.append(rest_obj.name)

        if added_rests:
            change_set.changed_fields.append("selected_restaurants")
            change_set.new_values["selected_restaurants"] = [r.name for r in state.selected_restaurants]
            state.planning_stage = PlanningStage.FOOD_SELECTION

    # 4H-rem. Check remove_restaurants change
    if "remove_restaurants" in updates and updates["remove_restaurants"] is not None:
        raw_rem_rests = updates["remove_restaurants"]
        if not isinstance(raw_rem_rests, list):
            raw_rem_rests = [raw_rem_rests]
        prev_rests = [r.name for r in state.selected_restaurants]
        removed_any = False
        for r_ident in raw_rem_rests:
            ident_str = str(r_ident.get("name") if isinstance(r_ident, dict) else r_ident).strip()
            if ident_str and state.remove_restaurant(ident_str):
                removed_any = True

        if removed_any:
            if "selected_restaurants" not in change_set.changed_fields:
                change_set.changed_fields.append("selected_restaurants")
                change_set.previous_values["selected_restaurants"] = prev_rests
                change_set.new_values["selected_restaurants"] = [r.name for r in state.selected_restaurants]

    # 4I. Check selected_cafes change
    if "selected_cafes" in updates and updates["selected_cafes"] is not None:
        raw_cafes = updates["selected_cafes"]
        if not isinstance(raw_cafes, list):
            raw_cafes = [raw_cafes]
        from app.schemas.restaurant import Restaurant
        added_cafes = []
        for c in raw_cafes:
            cafe_obj = None
            if isinstance(c, Restaurant):
                cafe_obj = c
            elif isinstance(c, dict):
                try:
                    cafe_obj = Restaurant(**c)
                except Exception:
                    name = c.get("name") or c.get("title")
                    if name:
                        cafe_obj = Restaurant(
                            data_id=str(c.get("data_id") or c.get("id") or name),
                            name=str(name),
                            latitude=c.get("latitude"),
                            longitude=c.get("longitude"),
                            rating=c.get("rating"),
                            cuisine=c.get("cuisine") or ["Cafe"],
                            price_level=c.get("price_level"),
                        )
            if cafe_obj and state.add_cafe(cafe_obj):
                added_cafes.append(cafe_obj.name)

        if added_cafes:
            change_set.changed_fields.append("selected_cafes")
            change_set.new_values["selected_cafes"] = [c.name for c in state.selected_cafes]
            state.planning_stage = PlanningStage.FOOD_SELECTION

    # 4J. Check rejected_restaurants change
    if "rejected_restaurants" in updates and updates["rejected_restaurants"] is not None:
        raw_rej_r = updates["rejected_restaurants"]
        if not isinstance(raw_rej_r, list):
            raw_rej_r = [raw_rej_r]
        newly_rejected_r = []
        for r in raw_rej_r:
            r_str = str(r.get("name") if isinstance(r, dict) else r).strip()
            if r_str and state.reject_restaurant(r_str):
                newly_rejected_r.append(r_str)

        if newly_rejected_r:
            change_set.changed_fields.append("rejected_restaurants")
            change_set.new_values["rejected_restaurants"] = list(state.rejected_restaurants)
            state.planning_stage = PlanningStage.FOOD_SELECTION

    # 4K. Check rejected_cafes change
    if "rejected_cafes" in updates and updates["rejected_cafes"] is not None:
        raw_rej_c = updates["rejected_cafes"]
        if not isinstance(raw_rej_c, list):
            raw_rej_c = [raw_rej_c]
        newly_rejected_c = []
        for c in raw_rej_c:
            c_str = str(c.get("name") if isinstance(c, dict) else c).strip()
            if c_str and state.reject_cafe(c_str):
                newly_rejected_c.append(c_str)

        if newly_rejected_c:
            change_set.changed_fields.append("rejected_cafes")
            change_set.new_values["rejected_cafes"] = list(state.rejected_cafes)
            state.planning_stage = PlanningStage.FOOD_SELECTION

    # 4K-rem. Check remove_cafes change
    if "remove_cafes" in updates and updates["remove_cafes"] is not None:
        raw_rem_cafes = updates["remove_cafes"]
        if not isinstance(raw_rem_cafes, list):
            raw_rem_cafes = [raw_rem_cafes]
        prev_cafes = [c.name for c in state.selected_cafes]
        removed_any = False
        for c_ident in raw_rem_cafes:
            ident_str = str(c_ident.get("name") if isinstance(c_ident, dict) else c_ident).strip()
            if ident_str and state.remove_cafe(ident_str):
                removed_any = True

        if removed_any:
            if "selected_cafes" not in change_set.changed_fields:
                change_set.changed_fields.append("selected_cafes")
                change_set.previous_values["selected_cafes"] = prev_cafes
                change_set.new_values["selected_cafes"] = [c.name for c in state.selected_cafes]

    # 4L. Check cuisine_preferences change
    if "cuisine_preferences" in updates and updates["cuisine_preferences"] is not None:
        raw_cuisines = updates["cuisine_preferences"]
        if isinstance(raw_cuisines, list):
            clean_cuisines = [str(c).strip() for c in raw_cuisines if str(c).strip()]
            if clean_cuisines != state.cuisine_preferences:
                change_set.changed_fields.append("cuisine_preferences")
                change_set.previous_values["cuisine_preferences"] = list(state.cuisine_preferences)
                change_set.new_values["cuisine_preferences"] = clean_cuisines
                state.cuisine_preferences = clean_cuisines

    # 4M. Check food_price_preference change
    if "food_price_preference" in updates and updates["food_price_preference"] is not None:
        new_price_pref = str(updates["food_price_preference"]).strip().lower()
        if new_price_pref != state.food_price_preference:
            change_set.changed_fields.append("food_price_preference")
            change_set.previous_values["food_price_preference"] = state.food_price_preference
            change_set.new_values["food_price_preference"] = new_price_pref
            state.food_price_preference = new_price_pref

    # 5. Check travel_mode change
    if "travel_mode" in updates and updates["travel_mode"] is not None:
        new_mode = str(updates["travel_mode"]).strip().lower()
        if new_mode and new_mode != state.travel_mode:
            change_set.changed_fields.append("travel_mode")
            change_set.previous_values["travel_mode"] = state.travel_mode
            change_set.new_values["travel_mode"] = new_mode
            state.travel_mode = new_mode

    # 6. Check interests change
    if "interests" in updates and updates["interests"] is not None:
        raw_interests = updates["interests"]
        if isinstance(raw_interests, list):
            clean_interests = []
            for i in raw_interests:
                s = str(i).strip()
                if s and s not in clean_interests:
                    clean_interests.append(s)
            if clean_interests != state.interests:
                change_set.changed_fields.append("interests")
                change_set.previous_values["interests"] = list(state.interests)
                change_set.new_values["interests"] = clean_interests
                state.interests = clean_interests

    # 7. Check dietary_preferences change
    if "dietary_preferences" in updates and updates["dietary_preferences"] is not None:
        raw_diet = updates["dietary_preferences"]
        if isinstance(raw_diet, list):
            clean_diet = []
            for d in raw_diet:
                s = str(d).strip()
                if s and s not in clean_diet:
                    clean_diet.append(s)
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

    # 8B. Check add_preferences
    if "add_preferences" in updates and updates["add_preferences"] is not None:
        raw_add_pref = updates["add_preferences"]
        if not isinstance(raw_add_pref, list):
            raw_add_pref = [raw_add_pref]
        for p in raw_add_pref:
            if not isinstance(p, dict):
                continue
            cat = str(p.get("category", "")).strip().lower()
            val = p.get("value")
            if not cat or val is None:
                continue

            if cat in ("dietary", "dietary_preferences", "diet"):
                val_str = str(val).strip()
                if val_str and val_str.lower() not in [x.lower() for x in state.dietary_preferences]:
                    if "dietary_preferences" not in change_set.changed_fields:
                        change_set.changed_fields.append("dietary_preferences")
                        change_set.previous_values["dietary_preferences"] = list(state.dietary_preferences)
                    state.dietary_preferences.append(val_str)
                    change_set.new_values["dietary_preferences"] = list(state.dietary_preferences)

            elif cat in ("cuisine", "cuisine_preferences"):
                val_str = str(val).strip()
                if val_str and val_str.lower() not in [x.lower() for x in state.cuisine_preferences]:
                    if "cuisine_preferences" not in change_set.changed_fields:
                        change_set.changed_fields.append("cuisine_preferences")
                        change_set.previous_values["cuisine_preferences"] = list(state.cuisine_preferences)
                    state.cuisine_preferences.append(val_str)
                    change_set.new_values["cuisine_preferences"] = list(state.cuisine_preferences)

            elif cat in ("interests", "interest"):
                val_str = str(val).strip()
                if val_str and val_str.lower() not in [x.lower() for x in state.interests]:
                    if "interests" not in change_set.changed_fields:
                        change_set.changed_fields.append("interests")
                        change_set.previous_values["interests"] = list(state.interests)
                    state.interests.append(val_str)
                    change_set.new_values["interests"] = list(state.interests)

            elif cat in ("meal", "meal_preferences"):
                if isinstance(val, dict):
                    if "meal_preferences" not in change_set.changed_fields:
                        change_set.changed_fields.append("meal_preferences")
                        change_set.previous_values["meal_preferences"] = dict(state.meal_preferences)
                    state.meal_preferences.update(val)
                    change_set.new_values["meal_preferences"] = dict(state.meal_preferences)

            elif cat in ("food_price", "food_price_preference", "price"):
                val_str = str(val).strip().lower()
                if val_str != state.food_price_preference:
                    if "food_price_preference" not in change_set.changed_fields:
                        change_set.changed_fields.append("food_price_preference")
                        change_set.previous_values["food_price_preference"] = state.food_price_preference
                    state.food_price_preference = val_str
                    change_set.new_values["food_price_preference"] = val_str

    # 8C. Check remove_preferences
    if "remove_preferences" in updates and updates["remove_preferences"] is not None:
        raw_rem_pref = updates["remove_preferences"]
        if not isinstance(raw_rem_pref, list):
            raw_rem_pref = [raw_rem_pref]
        for p in raw_rem_pref:
            if not isinstance(p, dict):
                continue
            cat = str(p.get("category", "")).strip().lower()
            val = p.get("value")
            if not cat or val is None:
                continue

            val_str = str(val).strip().lower()
            if cat in ("dietary", "dietary_preferences", "diet"):
                new_list = [x for x in state.dietary_preferences if x.strip().lower() != val_str]
                if len(new_list) < len(state.dietary_preferences):
                    if "dietary_preferences" not in change_set.changed_fields:
                        change_set.changed_fields.append("dietary_preferences")
                        change_set.previous_values["dietary_preferences"] = list(state.dietary_preferences)
                    state.dietary_preferences = new_list
                    change_set.new_values["dietary_preferences"] = list(state.dietary_preferences)

            elif cat in ("cuisine", "cuisine_preferences"):
                new_list = [x for x in state.cuisine_preferences if x.strip().lower() != val_str]
                if len(new_list) < len(state.cuisine_preferences):
                    if "cuisine_preferences" not in change_set.changed_fields:
                        change_set.changed_fields.append("cuisine_preferences")
                        change_set.previous_values["cuisine_preferences"] = list(state.cuisine_preferences)
                    state.cuisine_preferences = new_list
                    change_set.new_values["cuisine_preferences"] = list(state.cuisine_preferences)

            elif cat in ("interests", "interest"):
                new_list = [x for x in state.interests if x.strip().lower() != val_str]
                if len(new_list) < len(state.interests):
                    if "interests" not in change_set.changed_fields:
                        change_set.changed_fields.append("interests")
                        change_set.previous_values["interests"] = list(state.interests)
                    state.interests = new_list
                    change_set.new_values["interests"] = list(state.interests)

            elif cat in ("meal", "meal_preferences"):
                key_to_del = str(val).strip().lower()
                if key_to_del in [k.lower() for k in state.meal_preferences]:
                    if "meal_preferences" not in change_set.changed_fields:
                        change_set.changed_fields.append("meal_preferences")
                        change_set.previous_values["meal_preferences"] = dict(state.meal_preferences)
                    state.meal_preferences = {
                        k: v for k, v in state.meal_preferences.items() if k.lower() != key_to_del
                    }
                    change_set.new_values["meal_preferences"] = dict(state.meal_preferences)

    # 8C. Check mark_required_stops and mark_stop_priority (Milestone 3, Batch 3)
    if "mark_required_stops" in updates and updates["mark_required_stops"] is not None:
        raw_req = updates["mark_required_stops"]
        if not isinstance(raw_req, list):
            raw_req = [raw_req]
        prev_req = list(state.required_stops)
        changed_any = False
        for s in raw_req:
            s_name = str(s).strip()
            if s_name and state.mark_stop_required(s_name):
                changed_any = True
        if changed_any or (set(state.required_stops) != set(prev_req)):
            if "required_stops" not in change_set.changed_fields:
                change_set.changed_fields.append("required_stops")
                change_set.previous_values["required_stops"] = prev_req
                change_set.new_values["required_stops"] = list(state.required_stops)

    if "mark_stop_priority" in updates and updates["mark_stop_priority"] is not None:
        raw_prio = updates["mark_stop_priority"]
        if not isinstance(raw_prio, list):
            raw_prio = [raw_prio]
        prev_req = list(state.required_stops)
        changed_any = False
        for p in raw_prio:
            if isinstance(p, dict) and "name" in p and "priority" in p:
                if state.mark_stop_priority(str(p["name"]).strip(), str(p["priority"]).strip()):
                    changed_any = True
        if changed_any:
            if "required_stops" not in change_set.changed_fields:
                change_set.changed_fields.append("required_stops")
                change_set.previous_values["required_stops"] = prev_req
                change_set.new_values["required_stops"] = list(state.required_stops)

    # 8D. Check constraints list
    if "constraints" in updates and updates["constraints"] is not None:
        raw_constraints = updates["constraints"]
        if not isinstance(raw_constraints, list):
            raw_constraints = [raw_constraints]
        from app.schemas.constraint import Constraint
        prev_count = len(state.constraints)
        for c in raw_constraints:
            if isinstance(c, Constraint):
                state.add_constraint(c)
            elif isinstance(c, dict):
                try:
                    state.add_constraint(Constraint(**c))
                except Exception:
                    pass
        if len(state.constraints) != prev_count:
            change_set.changed_fields.append("constraints")
            change_set.previous_values["constraints"] = prev_count
            change_set.new_values["constraints"] = len(state.constraints)

    # 9. Deterministic Dependency Invalidation Phase (Component 3 & 4)
    # The set of triggers includes both explicitly changed fields and invalidated source selections
    triggers = set(change_set.changed_fields) | set(change_set.invalidated_fields)
    invalidated_resources = get_invalidated_resources(triggers)

    # Component 4: Apply freshness transitions (VALID -> STALE, NOT_AVAILABLE stays NOT_AVAILABLE)
    FreshnessRegistry.apply_invalidation(state.derived_freshness, invalidated_resources)

    if DerivedResource.CURRENT_ROUTE in invalidated_resources:
        if had_route or state.current_route is not None:
            state.current_route = None
            if "current_route" not in change_set.invalidated_fields:
                change_set.invalidated_fields.append("current_route")

    if DerivedResource.CURRENT_ITINERARY in invalidated_resources:
        if had_itinerary or state.current_itinerary is not None:
            state.current_itinerary = None
            if "current_itinerary" not in change_set.invalidated_fields:
                change_set.invalidated_fields.append("current_itinerary")

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

    # 4. Check accommodation budget only if hotel search is required (Milestone 3, M3.2 Fix #1)
    hotel_needed = state.hotel_search_required
    if hotel_needed is None:
        hotel_needed = state.hotel_required is True or (state.hotel_required is None and (state.number_of_nights or 0) > 0)
        if state.hotel_required is False or (state.number_of_nights is not None and state.number_of_nights == 0):
            hotel_needed = False

    if hotel_needed:
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
