"""
Project Musafir — Deterministic Travel Planner (Milestone 1)
Pure Python decision layer that determines the next system action based on
current TripState, ConversationContext, and normalized intent.

Guiding Principles:
- 100% deterministic: Zero LLM calls inside this module.
- Zero side-effects: Does not call external APIs, mutate state, or execute tools.
- Strict stage boundaries: Selecting places keeps the system in PLACE_SELECTION;
  routing is only triggered by explicit route intent.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.agent.state import TripState, PlanningStage
from app.agent.context import ConversationContext
from app.agent.state_update import get_next_active_question


class PlanningActionType(str, Enum):
    """Actions the Musafir agent can take next."""
    ASK = "ASK"
    SEARCH_HOTELS = "SEARCH_HOTELS"
    WAIT_FOR_HOTEL_SELECTION = "WAIT_FOR_HOTEL_SELECTION"
    SEARCH_PLACES = "SEARCH_PLACES"
    WAIT_FOR_PLACE_SELECTION = "WAIT_FOR_PLACE_SELECTION"
    SEARCH_FOOD = "SEARCH_FOOD"
    WAIT_FOR_FOOD_SELECTION = "WAIT_FOR_FOOD_SELECTION"
    OPTIMIZE_ROUTE = "OPTIMIZE_ROUTE"
    ANSWER = "ANSWER"


class PlanningAction(BaseModel):
    """
    Structured outcome of the planner's decision.
    Guides the API / AgentLoop on what tool to run or prompt to return.
    """
    action_type: PlanningActionType = Field(..., description="Action to perform")
    reason: str = Field(..., description="Deterministic rationale for this decision")
    tool_name: Optional[str] = Field(None, description="Tool name to execute if applicable")
    tool_args: Dict[str, Any] = Field(default_factory=dict, description="Arguments for tool call")
    prompt_message: Optional[str] = Field(None, description="Message to prompt or clarify with traveler")
    target_stage: PlanningStage = Field(..., description="Stage to transition or remain in")


def decide_next_planning_action(
    state: TripState,
    context: Optional[ConversationContext] = None,
    user_intent: Optional[str] = None,
) -> PlanningAction:
    """
    Evaluates current TripState, ConversationContext, and normalized user intent
    to decide the next action deterministically without any LLM invocations.
    """
    norm_intent = (user_intent or "").strip().upper()

    # 1. Informational queries / greetings -> direct answer
    if norm_intent in ("GENERAL_QUERY", "GREETING", "INFORMATIONAL", "HELP", "FAQ"):
        return PlanningAction(
            action_type=PlanningActionType.ANSWER,
            reason="informational_intent",
            prompt_message=None,
            target_stage=state.planning_stage,
        )

    # 2. Check foundational requirements (Destination & Duration)
    # If the user is not actively answering or providing details, prompt for missing basics
    if not state.destination or not state.destination.strip():
        return PlanningAction(
            action_type=PlanningActionType.ASK,
            reason="missing_destination",
            prompt_message="Where would you like to travel?",
            target_stage=PlanningStage.DISCOVERY,
        )

    # 3. Explicit Route Intent Handling
    # ROUTE_REQUEST, BUILD_ROUTE, OPTIMIZE_ROUTE
    if norm_intent in ("ROUTE_REQUEST", "BUILD_ROUTE", "OPTIMIZE_ROUTE"):
        if not state.hotel_selection:
            needs_hotel = state.hotel_search_required is not False and state.hotel_required is not False and (state.number_of_nights or 0) > 0
            if needs_hotel:
                return PlanningAction(
                    action_type=PlanningActionType.ASK,
                    reason="route_requires_hotel",
                    prompt_message="Please select your starting hotel first so we can anchor the route.",
                    target_stage=PlanningStage.HOTEL_SELECTION,
                )
        if len(state.selected_places) < 1:
            return PlanningAction(
                action_type=PlanningActionType.ASK,
                reason="route_requires_places",
                prompt_message="Please select at least one attraction to visit before generating the route.",
                target_stage=PlanningStage.PLACE_SELECTION,
            )

        # Traveler has hotel or day-trip first stop and at least 1 place -> Ready for route optimization
        if state.hotel_selection:
            hotel = state.hotel_selection
            origin_payload = {
                "name": hotel.name,
                "latitude": hotel.latitude,
                "longitude": hotel.longitude,
            }
        else:
            first_p = state.selected_places[0]
            origin_payload = {
                "name": first_p.name,
                "latitude": first_p.latitude,
                "longitude": first_p.longitude,
            }
        stops_payload = [
            {
                "name": p.name,
                "latitude": p.latitude,
                "longitude": p.longitude,
                "stop_type": "attraction",
            }
            for p in state.selected_places
        ]
        for r in state.selected_restaurants:
            stops_payload.append({
                "name": r.name,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "stop_type": "restaurant",
            })
        for c in state.selected_cafes:
            stops_payload.append({
                "name": c.name,
                "latitude": c.latitude,
                "longitude": c.longitude,
                "stop_type": "cafe",
            })

        mode_is_explicit = "travel_mode" in state.explicit_fields
        planning_assumption = None
        if not mode_is_explicit:
            planning_assumption = "Defaulted to driving for intra-city transit. You can switch to walking, transit, or two-wheeler anytime."
            if planning_assumption not in state.feasibility_notes:
                state.feasibility_notes.append(planning_assumption)

        return PlanningAction(
            action_type=PlanningActionType.OPTIMIZE_ROUTE,
            reason="explicit_route_intent_with_sufficient_stops",
            tool_name="optimize_route",
            tool_args={
                "origin": origin_payload,
                "destination": origin_payload,
                "stops": stops_payload,
                "travel_mode": state.travel_mode or "driving",
                "planning_assumption": planning_assumption,
            },
            target_stage=PlanningStage.ROUTE_PLANNING,
        )

    # 4. Place & Food Selection & Rejection Actions
    # CRITICAL RULE: Modifying places stays in PLACE_SELECTION; modifying food stays in FOOD_SELECTION; does NOT trigger routing.
    if norm_intent in ("SELECT_PLACE", "SELECT_PLACES", "REJECT_PLACE", "DESELECT_PLACE"):
        return PlanningAction(
            action_type=PlanningActionType.WAIT_FOR_PLACE_SELECTION,
            reason=f"place_updated_{norm_intent.lower()}",
            prompt_message=None,
            target_stage=PlanningStage.PLACE_SELECTION,
        )

    if norm_intent in (
        "SELECT_FOOD",
        "SELECT_RESTAURANT",
        "SELECT_RESTAURANTS",
        "SELECT_CAFE",
        "SELECT_CAFES",
        "REJECT_FOOD",
        "REJECT_RESTAURANT",
        "REJECT_CAFE",
    ):
        return PlanningAction(
            action_type=PlanningActionType.WAIT_FOR_FOOD_SELECTION,
            reason=f"food_updated_{norm_intent.lower()}",
            prompt_message=None,
            target_stage=PlanningStage.FOOD_SELECTION,
        )

    # 5. Food Search / Discovery Requests
    if norm_intent in ("SEARCH_FOOD", "SEARCH_RESTAURANTS", "SEARCH_CAFES", "FIND_LUNCH", "FIND_DINNER", "DISCOVER_FOOD") or (
        state.planning_stage == PlanningStage.FOOD_DISCOVERY
    ):
        category = "cafe" if "CAFE" in norm_intent else "restaurant"
        meal = "lunch" if "LUNCH" in norm_intent else ("dinner" if "DINNER" in norm_intent else None)
        food_query = "cafes" if category == "cafe" else "restaurants"
        if meal:
            food_query = f"{meal} {food_query}"

        tool_args: Dict[str, Any] = {
            "destination": state.destination,
            "category": category,
            "query": food_query,
            "exclude_names": list(state.rejected_food),
        }
        if meal:
            tool_args["meal_type"] = meal

        if state.hotel_selection:
            tool_args["location_anchor"] = state.hotel_selection.name
            tool_args["latitude"] = state.hotel_selection.latitude
            tool_args["longitude"] = state.hotel_selection.longitude

        return PlanningAction(
            action_type=PlanningActionType.SEARCH_FOOD,
            reason="anchored_food_search",
            tool_name="search_restaurants",
            tool_args=tool_args,
            target_stage=PlanningStage.FOOD_SELECTION,
        )

    # 5. Place Search / Discovery Requests
    if norm_intent in ("SEARCH_PLACES", "SEARCH_NEARBY_PLACES", "DISCOVER_PLACES") or (
        state.planning_stage == PlanningStage.PLACE_DISCOVERY
    ):
        if state.hotel_selection:
            hotel_name = state.hotel_selection.name
            return PlanningAction(
                action_type=PlanningActionType.SEARCH_PLACES,
                reason="anchored_place_search_around_hotel",
                tool_name="search_places",
                tool_args={
                    "destination": state.destination,
                    "query": f"attractions near {hotel_name}, {state.destination}",
                    "location_anchor": hotel_name,
                    "latitude": state.hotel_selection.latitude,
                    "longitude": state.hotel_selection.longitude,
                    "exclude_names": list(state.rejected_places),
                },
                target_stage=PlanningStage.PLACE_SELECTION,
            )
        else:
            return PlanningAction(
                action_type=PlanningActionType.SEARCH_PLACES,
                reason="destination_place_search",
                tool_name="search_places",
                tool_args={
                    "destination": state.destination,
                    "query": f"top attractions in {state.destination}",
                    "exclude_names": list(state.rejected_places),
                },
                target_stage=PlanningStage.PLACE_SELECTION,
            )

    # 6. Hotel Selection Intent -> Immediately Triggers Anchored Place Discovery
    if norm_intent in ("SELECT_HOTEL", "HOTEL_SELECTED"):
        if state.hotel_selection:
            hotel_name = state.hotel_selection.name
            return PlanningAction(
                action_type=PlanningActionType.SEARCH_PLACES,
                reason="hotel_selected_trigger_anchored_place_discovery",
                tool_name="search_places",
                tool_args={
                    "destination": state.destination,
                    "query": f"attractions near {hotel_name}, {state.destination}",
                    "location_anchor": hotel_name,
                    "latitude": state.hotel_selection.latitude,
                    "longitude": state.hotel_selection.longitude,
                    "exclude_names": list(state.rejected_places),
                },
                target_stage=PlanningStage.PLACE_SELECTION,
            )

    # 7. Currently in FOOD_SELECTION or PLACE_SELECTION stage
    if state.planning_stage == PlanningStage.FOOD_SELECTION:
        return PlanningAction(
            action_type=PlanningActionType.WAIT_FOR_FOOD_SELECTION,
            reason="in_food_selection_awaiting_user_choice",
            prompt_message=None,
            target_stage=PlanningStage.FOOD_SELECTION,
        )

    if state.planning_stage == PlanningStage.PLACE_SELECTION:
        # Awaiting traveler to select more places or ask to build route
        return PlanningAction(
            action_type=PlanningActionType.WAIT_FOR_PLACE_SELECTION,
            reason="in_place_selection_awaiting_user_choice",
            prompt_message=None,
            target_stage=PlanningStage.PLACE_SELECTION,
        )

    # 8. Hotel Search & Selection Flow
    if not state.hotel_selection:
        # If hotel search is not required (e.g. 1-day trip / 0 nights / hotel booked separately), bypass hotel selection entirely
        if state.hotel_search_required is False or state.hotel_required is False or (state.number_of_nights is not None and state.number_of_nights == 0):
            if len(state.selected_places) == 0:
                return PlanningAction(
                    action_type=PlanningActionType.SEARCH_PLACES,
                    reason="day_trip_no_hotel_initiate_place_discovery",
                    tool_name="search_places",
                    tool_args={
                        "destination": state.destination,
                        "query": f"top attractions in {state.destination}",
                        "exclude_names": list(state.rejected_places),
                    },
                    target_stage=PlanningStage.PLACE_SELECTION,
                )
            else:
                return PlanningAction(
                    action_type=PlanningActionType.WAIT_FOR_PLACE_SELECTION,
                    reason="day_trip_awaiting_user_place_selection",
                    prompt_message=None,
                    target_stage=PlanningStage.PLACE_SELECTION,
                )

        # If hotels are already visible on screen, wait for selection
        if context and context.visible_hotels and len(context.visible_hotels) > 0:
            return PlanningAction(
                action_type=PlanningActionType.WAIT_FOR_HOTEL_SELECTION,
                reason="visible_hotels_awaiting_user_selection",
                prompt_message=None,
                target_stage=PlanningStage.HOTEL_SELECTION,
            )

        # If user explicitly wants to search hotels or is in hotel selection stage
        if norm_intent in ("SEARCH_HOTELS", "DISCOVER_HOTELS") or state.planning_stage in (
            PlanningStage.DISCOVERY,
            PlanningStage.HOTEL_SELECTION,
        ):
            # Check if accommodation budget or duration needs asking first
            active_q = get_next_active_question(state)
            if active_q and active_q.field in ("hotel_total_budget", "number_of_days", "trip_start_date"):
                return PlanningAction(
                    action_type=PlanningActionType.ASK,
                    reason=f"missing_{active_q.field}",
                    prompt_message=active_q.prompt_text,
                    target_stage=PlanningStage.HOTEL_SELECTION,
                )

            # Ready to search hotels
            return PlanningAction(
                action_type=PlanningActionType.SEARCH_HOTELS,
                reason="search_hotels_for_destination",
                tool_name="search_hotels",
                tool_args={
                    "destination": state.destination,
                    "budget": state.hotel_budget or state.hotel_total_budget,
                },
                target_stage=PlanningStage.HOTEL_SELECTION,
            )

    # 9. Hotel is already selected, but no places selected yet
    if state.hotel_selection and len(state.selected_places) == 0:
        hotel_name = state.hotel_selection.name
        return PlanningAction(
            action_type=PlanningActionType.SEARCH_PLACES,
            reason="hotel_present_initiate_place_discovery",
            tool_name="search_places",
            tool_args={
                "destination": state.destination,
                "query": f"attractions near {hotel_name}, {state.destination}",
                "location_anchor": hotel_name,
                "latitude": state.hotel_selection.latitude,
                "longitude": state.hotel_selection.longitude,
                "exclude_names": list(state.rejected_places),
            },
            target_stage=PlanningStage.PLACE_SELECTION,
        )

    # 10. Default fallback: keep current stage and answer
    return PlanningAction(
        action_type=PlanningActionType.ANSWER,
        reason="default_no_active_action",
        prompt_message=None,
        target_stage=state.planning_stage,
    )
