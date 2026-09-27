"""
Project Musafir — Deterministic Replanning Controller (Milestone 3, Component 5 & 6)
Analyzes current TripState, derived resource freshness, and user intent to determine
the precise next replanning action (e.g. REBUILD_ROUTE, REBUILD_ITINERARY, WAIT, ASK, ANSWER).

Guiding Principles:
- 100% Deterministic: Pure Python, zero LLM calls, zero network operations.
- Stale != Rebuild Now: Distinguishes explicit replan requests from lazy mutations.
- Dependency Ordering: Rebuilds upstream prerequisites (e.g. Route) before downstream outputs (Itinerary).
- Finite Execution: Strict termination guarantees preventing infinite replanning loops.
- Provenance Protected: Stale or obsolete version responses are rejected.
"""

from enum import Enum
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from pydantic import BaseModel, Field

from app.agent.dependencies import DerivedResource
from app.agent.freshness import DerivedStateStatus, FreshnessRegistry
from app.agent.state import TripState, PlanningStage
from app.agent.state_update import TripChangeSet
from app.agent.context import ConversationContext
from app.agent.tools import optimize_route_tool, generate_itinerary_tool, search_places_tool, search_restaurants_tool
from app.schemas.route import OptimizedRoute
from app.schemas.itinerary import ItineraryResponse


class ReplanningActionType(str, Enum):
    """Canonical operations determined by the Replanning Controller."""
    NO_ACTION = "NO_ACTION"
    ASK = "ASK"
    WAIT = "WAIT"
    SEARCH_HOTELS = "SEARCH_HOTELS"
    SEARCH_PLACES = "SEARCH_PLACES"
    SEARCH_FOOD = "SEARCH_FOOD"
    REBUILD_ROUTE = "REBUILD_ROUTE"
    REBUILD_ITINERARY = "REBUILD_ITINERARY"
    ANSWER = "ANSWER"


class ReplanningAction(BaseModel):
    """
    Structured outcome of the replanning decision engine.
    Specifies the required action, rationale, target derived resource, and prerequisites.
    """
    action_type: ReplanningActionType = Field(..., description="Action to perform")
    reason: str = Field(..., description="Deterministic rationale for this decision")
    target_resource: Optional[DerivedResource] = Field(None, description="DerivedResource being acted upon")
    prerequisites_met: bool = Field(True, description="True if all required inputs exist")
    missing_prerequisites: List[str] = Field(default_factory=list, description="Fields required before execution")
    tool_name: Optional[str] = Field(None, description="Tool name to execute if applicable")
    tool_args: Dict[str, Any] = Field(default_factory=dict, description="Arguments for tool call")
    prompt_message: Optional[str] = Field(None, description="User-facing prompt or clarification text")
    chained_action: Optional["ReplanningAction"] = Field(
        None,
        description="Downstream action to execute after this action successfully completes"
    )


# --- Prerequisite Evaluation Helpers ---

def check_route_prerequisites(state: TripState) -> Tuple[bool, List[str], Optional[str]]:
    """
    Evaluates whether TripState satisfies all requirements to compute an OptimizedRoute:
    - Destination specified
    - Starting hotel anchor selected
    - At least 1 stop selected (places, restaurants, or cafes)
    Returns (is_met, missing_fields, clarification_prompt).
    """
    missing: List[str] = []
    if not state.destination or not state.destination.strip():
        missing.append("destination")
    if not state.hotel_selection:
        missing.append("hotel_selection")
    elif state.hotel_selection.latitude is None or state.hotel_selection.longitude is None:
        missing.append("hotel_coordinates")
    
    total_stops = (
        len(state.selected_places)
        + len(state.selected_restaurants)
        + len(state.selected_cafes)
    )
    if total_stops < 1:
        missing.append("selected_places")

    if missing:
        if "destination" in missing:
            prompt = "Where would you like to travel?"
        elif "hotel_selection" in missing:
            prompt = "Please select your starting hotel first so we can anchor the route."
        elif "hotel_coordinates" in missing:
            prompt = "The selected hotel is missing location coordinates. Please select a valid hotel with location details to anchor the route."
        else:
            prompt = "Please select at least one attraction or food stop before building the route."
        return False, missing, prompt

    return True, [], None


def check_itinerary_prerequisites(state: TripState) -> Tuple[bool, List[str], Optional[str]]:
    """
    Evaluates whether TripState satisfies all requirements to synthesize an Itinerary:
    - CURRENT_ROUTE must be VALID (cannot generate itinerary from stale or missing route)
    - Duration (number_of_days) or start date specified
    Returns (is_met, missing_fields, clarification_prompt).
    """
    missing: List[str] = []
    
    # Critical rule: Itinerary depends on a VALID route
    if not state.is_derived_valid(DerivedResource.CURRENT_ROUTE):
        missing.append("current_route")

    if not state.number_of_days and not state.trip_start_date:
        missing.append("duration")

    if missing:
        if "current_route" in missing:
            prompt = "A valid route is required before generating the itinerary."
        else:
            prompt = "Please specify the duration or dates of your trip for the daily timetable."
        return False, missing, prompt

    return True, [], None


# --- Intent Pattern Matching ---

_ROUTE_INTENT_PATTERNS = [
    r"\b(?:build|rebuild|create|generate|plan|make|optimize|show|calculate|update)\s+(?:the\s+|a\s+|our\s+)?(?:new\s+)?(?:route|trip\s+route)\b",
    r"\b(?:build\s+route|optimize\s+route|plan\s+route|generate\s+route|rebuild\s+route|recalculate\s+route)\b",
    r"^route\s+please\b",
]

_ITINERARY_INTENT_PATTERNS = [
    r"\b(?:build|rebuild|create|generate|plan|make|show|calculate|update)\s+(?:the\s+|a\s+|our\s+)?(?:new\s+)?(?:itinerary|schedule|timetable|daily\s+plan)\b",
    r"\b(?:build\s+itinerary|plan\s+itinerary|generate\s+itinerary|rebuild\s+itinerary|update\s+itinerary)\b",
    r"^itinerary\s+please\b",
]

_DISCOVERY_PLACES_PATTERNS = [
    r"\b(?:show|search|find|discover|get)\s+(?:me\s+)?(?:places|attractions|sights|things\s+to\s+do)(?:\s+again)?\b",
]

_DISCOVERY_FOOD_PATTERNS = [
    r"\b(?:show|search|find|discover|get)\s+(?:me\s+)?(?:restaurants|cafes|food|places\s+to\s+eat)(?:\s+again)?\b",
]

_UNRELATED_QUERY_PATTERNS = [
    r"^is\s+[a-z\s]+\s+(?:a\s+)?good\s+time\s+to\s+visit\b",
    r"^what\s+(?:is|are)\s+the\s+best\s+(?:time|months?|season)\s+to\s+visit\b",
    r"^(?:how\s+is\s+the\s+weather|what\s+is\s+the\s+weather)\b",
    r"^(?:hello|hi|hey|good\s+(?:morning|afternoon|evening))\b",
    r"^(?:help|what\s+can\s+you\s+do)\b",
    r"^(?:tell\s+me\s+about|can\s+you\s+tell\s+me\s+about)\b",
]


def classify_replanning_intent(
    user_intent: Optional[str] = None,
    user_message: Optional[str] = None,
) -> Dict[str, bool]:
    """
    Deterministically extracts replanning intent flags from normalized intent or raw user message.
    """
    text = (user_message or "").strip().lower()
    intent_norm = (user_intent or "").strip().upper()

    is_route = (
        intent_norm in ("ROUTE_REQUEST", "BUILD_ROUTE", "OPTIMIZE_ROUTE", "REBUILD_ROUTE")
        or any(bool(re.search(p, text)) for p in _ROUTE_INTENT_PATTERNS)
    )
    is_itinerary = (
        intent_norm in ("ITINERARY_REQUEST", "BUILD_ITINERARY", "UPDATE_ITINERARY", "REBUILD_ITINERARY")
        or any(bool(re.search(p, text)) for p in _ITINERARY_INTENT_PATTERNS)
    )
    is_places = (
        intent_norm in ("SEARCH_PLACES", "DISCOVER_PLACES")
        or any(bool(re.search(p, text)) for p in _DISCOVERY_PLACES_PATTERNS)
    )
    is_food = (
        intent_norm in ("SEARCH_FOOD", "DISCOVER_FOOD", "SEARCH_RESTAURANTS", "SEARCH_CAFES")
        or any(bool(re.search(p, text)) for p in _DISCOVERY_FOOD_PATTERNS)
    )
    is_unrelated = (
        intent_norm in ("GENERAL_QUERY", "GREETING", "INFORMATIONAL", "HELP", "FAQ")
        or any(bool(re.search(p, text)) for p in _UNRELATED_QUERY_PATTERNS)
    )

    return {
        "is_route": is_route,
        "is_itinerary": is_itinerary,
        "is_places": is_places,
        "is_food": is_food,
        "is_unrelated": is_unrelated,
    }


# --- Replanning Controller Decision Engine ---

class ReplanningController:
    """
    Deterministic Replanning Controller.
    Takes TripState, freshness status, and traveler intent to decide the next planning action.
    """

    @staticmethod
    def decide(
        state: TripState,
        user_intent: Optional[str] = None,
        user_message: Optional[str] = None,
        change_set: Optional[TripChangeSet] = None,
        context: Optional[ConversationContext] = None,
    ) -> ReplanningAction:
        """
        Determines the next replanning action deterministically.
        Rules:
        1. Unrelated queries -> ANSWER (never trigger stale recomputation).
        2. Explicit Itinerary Request:
           - If CURRENT_ITINERARY is already VALID -> ANSWER (no redundant compute).
           - If CURRENT_ROUTE is STALE or NOT_AVAILABLE -> REBUILD_ROUTE first (chained).
           - If CURRENT_ROUTE is VALID -> REBUILD_ITINERARY.
        3. Explicit Route Request:
           - If CURRENT_ROUTE is already VALID -> ANSWER (no redundant compute).
           - If CURRENT_ROUTE is STALE or NOT_AVAILABLE:
             - If route prerequisites met -> REBUILD_ROUTE.
             - Else -> ASK for missing prerequisites.
        4. Explicit Discovery Requests:
           - Re-run discovery for places or food if requested.
        5. Lazy Replanning (Pure Mutation without explicit replan intent):
           - If dependencies became STALE, return WAIT with informative notice.
        6. Default: NO_ACTION.
        """
        flags = classify_replanning_intent(user_intent=user_intent, user_message=user_message)

        # ---------------------------------------------------------------------
        # Rule 1: Informational / Unrelated Query -> ANSWER
        # Stale state never forces route or itinerary recomputation for unrelated questions.
        # ---------------------------------------------------------------------
        if flags["is_unrelated"]:
            return ReplanningAction(
                action_type=ReplanningActionType.ANSWER,
                reason="unrelated_query_preserves_stale_state",
            )

        # ---------------------------------------------------------------------
        # Rule 2: Explicit Itinerary Request
        # ---------------------------------------------------------------------
        if flags["is_itinerary"]:
            # If already VALID and user asks for it, use current result without recomputing
            if state.is_derived_valid(DerivedResource.CURRENT_ITINERARY):
                return ReplanningAction(
                    action_type=ReplanningActionType.ANSWER,
                    reason="itinerary_already_valid",
                    target_resource=DerivedResource.CURRENT_ITINERARY,
                )

            # Check if upstream prerequisite (Route) is STALE or NOT_AVAILABLE
            if not state.is_derived_valid(DerivedResource.CURRENT_ROUTE):
                route_met, route_missing, route_prompt = check_route_prerequisites(state)
                if not route_met:
                    return ReplanningAction(
                        action_type=ReplanningActionType.ASK,
                        reason="itinerary_blocked_by_missing_route_prerequisites",
                        target_resource=DerivedResource.CURRENT_ROUTE,
                        prerequisites_met=False,
                        missing_prerequisites=route_missing,
                        prompt_message=route_prompt,
                    )

                # Route prerequisites met: Rebuild route first, then chain itinerary rebuild
                hotel = state.hotel_selection
                origin_payload = {
                    "name": hotel.name,
                    "latitude": hotel.latitude,
                    "longitude": hotel.longitude,
                }
                stops_payload = [
                    {"name": p.name, "latitude": p.latitude, "longitude": p.longitude, "stop_type": "attraction"}
                    for p in state.selected_places
                ] + [
                    {"name": r.name, "latitude": r.latitude, "longitude": r.longitude, "stop_type": "restaurant"}
                    for r in state.selected_restaurants
                ] + [
                    {"name": c.name, "latitude": c.latitude, "longitude": c.longitude, "stop_type": "cafe"}
                    for c in state.selected_cafes
                ]

                chained_itinerary_action = ReplanningAction(
                    action_type=ReplanningActionType.REBUILD_ITINERARY,
                    reason="itinerary_rebuild_after_route_refresh",
                    target_resource=DerivedResource.CURRENT_ITINERARY,
                    tool_name="generate_itinerary",
                    tool_args={
                        "trip_date": state.trip_start_date.isoformat() if state.trip_start_date else None,
                    },
                )

                return ReplanningAction(
                    action_type=ReplanningActionType.REBUILD_ROUTE,
                    reason="route_stale_rebuild_prerequisite_before_itinerary",
                    target_resource=DerivedResource.CURRENT_ROUTE,
                    tool_name="optimize_route",
                    tool_args={
                        "origin": origin_payload,
                        "destination": origin_payload,
                        "stops": stops_payload,
                        "travel_mode": state.travel_mode or "driving",
                    },
                    chained_action=chained_itinerary_action,
                )

            # Route is already VALID: Rebuild Itinerary directly
            itin_met, itin_missing, itin_prompt = check_itinerary_prerequisites(state)
            if not itin_met:
                return ReplanningAction(
                    action_type=ReplanningActionType.ASK,
                    reason="itinerary_missing_duration_prerequisites",
                    target_resource=DerivedResource.CURRENT_ITINERARY,
                    prerequisites_met=False,
                    missing_prerequisites=itin_missing,
                    prompt_message=itin_prompt,
                )

            return ReplanningAction(
                action_type=ReplanningActionType.REBUILD_ITINERARY,
                reason="explicit_itinerary_request_with_valid_route",
                target_resource=DerivedResource.CURRENT_ITINERARY,
                tool_name="generate_itinerary",
                tool_args={
                    "trip_date": state.trip_start_date.isoformat() if state.trip_start_date else None,
                },
            )

        # ---------------------------------------------------------------------
        # Rule 3: Explicit Route Request
        # ---------------------------------------------------------------------
        if flags["is_route"]:
            # If already VALID, do not recompute unnecessarily
            if state.is_derived_valid(DerivedResource.CURRENT_ROUTE):
                return ReplanningAction(
                    action_type=ReplanningActionType.ANSWER,
                    reason="route_already_valid",
                    target_resource=DerivedResource.CURRENT_ROUTE,
                )

            # Route is STALE or NOT_AVAILABLE: Check prerequisites
            route_met, route_missing, route_prompt = check_route_prerequisites(state)
            if not route_met:
                return ReplanningAction(
                    action_type=ReplanningActionType.ASK,
                    reason="route_missing_prerequisites",
                    target_resource=DerivedResource.CURRENT_ROUTE,
                    prerequisites_met=False,
                    missing_prerequisites=route_missing,
                    prompt_message=route_prompt,
                )

            hotel = state.hotel_selection
            origin_payload = {
                "name": hotel.name,
                "latitude": hotel.latitude,
                "longitude": hotel.longitude,
            }
            stops_payload = [
                {"name": p.name, "latitude": p.latitude, "longitude": p.longitude, "stop_type": "attraction"}
                for p in state.selected_places
            ] + [
                {"name": r.name, "latitude": r.latitude, "longitude": r.longitude, "stop_type": "restaurant"}
                for r in state.selected_restaurants
            ] + [
                {"name": c.name, "latitude": c.latitude, "longitude": c.longitude, "stop_type": "cafe"}
                for c in state.selected_cafes
            ]

            return ReplanningAction(
                action_type=ReplanningActionType.REBUILD_ROUTE,
                reason="explicit_route_rebuild_with_valid_prerequisites",
                target_resource=DerivedResource.CURRENT_ROUTE,
                tool_name="optimize_route",
                tool_args={
                    "origin": origin_payload,
                    "destination": origin_payload,
                    "stops": stops_payload,
                    "travel_mode": state.travel_mode or "driving",
                },
            )

        # ---------------------------------------------------------------------
        # Rule 4: Explicit Discovery Requests
        # ---------------------------------------------------------------------
        if flags["is_places"]:
            anchor_name = state.hotel_selection.name if state.hotel_selection else state.destination
            query = f"attractions near {anchor_name}, {state.destination}" if state.hotel_selection else f"top attractions in {state.destination}"
            tool_args = {
                "destination": state.destination,
                "query": query,
                "exclude_names": list(state.rejected_places),
            }
            if state.hotel_selection:
                tool_args["location_anchor"] = state.hotel_selection.name
                tool_args["latitude"] = state.hotel_selection.latitude
                tool_args["longitude"] = state.hotel_selection.longitude

            return ReplanningAction(
                action_type=ReplanningActionType.SEARCH_PLACES,
                reason="explicit_place_discovery_request",
                target_resource=DerivedResource.PLACE_DISCOVERY,
                tool_name="search_places",
                tool_args=tool_args,
            )

        if flags["is_food"]:
            tool_args = {
                "destination": state.destination,
                "query": "restaurants",
                "exclude_names": list(state.rejected_food),
            }
            if state.hotel_selection:
                tool_args["location_anchor"] = state.hotel_selection.name
                tool_args["latitude"] = state.hotel_selection.latitude
                tool_args["longitude"] = state.hotel_selection.longitude

            return ReplanningAction(
                action_type=ReplanningActionType.SEARCH_FOOD,
                reason="explicit_food_discovery_request",
                target_resource=DerivedResource.FOOD_DISCOVERY,
                tool_name="search_restaurants",
                tool_args=tool_args,
            )

        # ---------------------------------------------------------------------
        # Rule 5: Lazy Replanning (Pure Mutation without explicit rebuild intent)
        # When mutations invalidate route/itinerary to STALE, do NOT call tools.
        # Inform the user and wait for explicit confirmation.
        # ---------------------------------------------------------------------
        if change_set and change_set.has_changes():
            stale_resources = [
                res.value for res in DerivedResource
                if state.is_derived_stale(res)
            ]
            if stale_resources:
                return ReplanningAction(
                    action_type=ReplanningActionType.WAIT,
                    reason="lazy_replanning_mutation_without_explicit_replan_request",
                    prompt_message="State updated. Dependent route/itinerary need updating when you're ready.",
                )

        return ReplanningAction(
            action_type=ReplanningActionType.NO_ACTION,
            reason="no_replanning_action_required",
        )


# --- Deterministic Replanning Executor ---

def execute_replanning_cycle(
    state: TripState,
    action: ReplanningAction,
    max_steps: int = 3,
) -> Dict[str, Any]:
    """
    Executes replanning actions deterministically with strict loop protection.
    Handles single actions and chained executions (e.g. Route -> Itinerary).
    Ensures:
    - Successful recomputation marks resource VALID with active state_version.
    - Failed recomputation keeps resource STALE.
    - Execution terminates strictly within max_steps.
    """
    executed_tools: List[str] = []
    current_action: Optional[ReplanningAction] = action
    step = 0
    last_error: Optional[str] = None

    while current_action and step < max_steps:
        step += 1

        if current_action.action_type == ReplanningActionType.REBUILD_ROUTE:
            initial_version = state.state_version
            res = optimize_route_tool(trip_state=state, **current_action.tool_args)
            executed_tools.append("optimize_route")

            if res.get("success") and "route" in res:
                opt_route = OptimizedRoute(**res["route"])
                state.current_route = opt_route
                marked = state.mark_derived_valid(DerivedResource.CURRENT_ROUTE, result_state_version=initial_version)
                if not marked:
                    # Version race detected: state mutated during compute
                    last_error = "Route computed against outdated state version; rejected."
                    break
                state.planning_stage = PlanningStage.ROUTE_PLANNING
                # Advance to chained action if present (e.g. Itinerary rebuild)
                current_action = current_action.chained_action
            else:
                # Failure: route remains STALE
                last_error = res.get("error", "Route optimization failed")
                break

        elif current_action.action_type == ReplanningActionType.REBUILD_ITINERARY:
            # Enforce prerequisite check: route must be valid
            if not state.is_derived_valid(DerivedResource.CURRENT_ROUTE):
                last_error = "Cannot rebuild itinerary: route is not valid."
                break

            initial_version = state.state_version
            res = generate_itinerary_tool(trip_state=state, **current_action.tool_args)
            executed_tools.append("generate_itinerary")

            if res.get("success") and "itinerary" in res:
                itin_obj = ItineraryResponse(**res["itinerary"])
                state.current_itinerary = itin_obj
                marked = state.mark_derived_valid(DerivedResource.CURRENT_ITINERARY, result_state_version=initial_version)
                if not marked:
                    last_error = "Itinerary computed against outdated state version; rejected."
                    break
                state.planning_stage = PlanningStage.READY
                current_action = current_action.chained_action
            else:
                last_error = res.get("error", "Itinerary generation failed")
                break

        elif current_action.action_type == ReplanningActionType.SEARCH_PLACES:
            res = search_places_tool(trip_state=state, **current_action.tool_args)
            executed_tools.append("search_places")
            if res.get("success"):
                state.mark_derived_valid(DerivedResource.PLACE_DISCOVERY)
            else:
                last_error = res.get("error", "Search places failed")
            current_action = current_action.chained_action

        elif current_action.action_type == ReplanningActionType.SEARCH_FOOD:
            res = search_restaurants_tool(trip_state=state, **current_action.tool_args)
            executed_tools.append("search_restaurants")
            if res.get("success"):
                state.mark_derived_valid(DerivedResource.FOOD_DISCOVERY)
            else:
                last_error = res.get("error", "Search food failed")
            current_action = current_action.chained_action

        else:
            # ASK, WAIT, ANSWER, NO_ACTION -> Terminal
            break

    # Milestone 3 Batch 3: Evaluate schedule feasibility deterministically
    feasibility_res = None
    if state.current_route is not None:
        from app.agent.feasibility import FeasibilityEngine
        feasibility_res = FeasibilityEngine.evaluate(state, state.current_route)
        state.feasibility_result = feasibility_res

    return {
        "success": last_error is None,
        "executed_tools": executed_tools,
        "steps_taken": step,
        "error": last_error,
        "route_valid": state.is_derived_valid(DerivedResource.CURRENT_ROUTE),
        "itinerary_valid": state.is_derived_valid(DerivedResource.CURRENT_ITINERARY),
        "feasibility": feasibility_res.model_dump() if feasibility_res else None,
    }
