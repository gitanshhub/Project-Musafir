"""
Project Musafir — Agent Chat API Endpoint (Step 10.5)
FastAPI controller exposing POST /agent/chat for conversational trip planning.
"""

import re
import time
import logging
from uuid import UUID
from typing import Optional, Callable
from fastapi import APIRouter, HTTPException, Depends

from app.schemas.agent import AgentChatRequest, AgentChatResponse
from app.schemas.route import OptimizedRoute
from app.agent.state import get_state, get_or_create_state, save_state, PlanningStage
from app.agent.context import get_or_create_session, get_session, save_session
from app.agent.fast_path import resolve_fast_path, FastPathResult
from app.agent.state_update import apply_trip_state_update, get_next_active_question
from app.agent.dependencies import DerivedResource
from app.agent.replanner import (
    ReplanningController,
    ReplanningActionType,
    execute_replanning_cycle,
)
from app.agent.planner import decide_next_planning_action, PlanningActionType
from app.agent.tools import optimize_route_tool, search_restaurants_tool
from app.agent.loop import AgentLoop, AgentLoopError, sanitize_public_response
from app.llm.openrouter_client import (
    LLMAuthError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMProviderError,
)

logger = logging.getLogger("musafir.api.agent")

router = APIRouter(prefix="/agent", tags=["Agent"])


def _sanitize_error(msg: str) -> str:
    """Strips API keys, tokens, and authorization headers from error messages."""
    redacted = re.sub(r"sk-[a-zA-Z0-9_\-]+", "[REDACTED]", msg)
    redacted = re.sub(r"(?i)bearer\s+[a-zA-Z0-9_\.\-]+", "Bearer [REDACTED]", redacted)
    redacted = re.sub(r"(?i)key[=:\s]+[a-zA-Z0-9_\-]+", "key=[REDACTED]", redacted)
    return redacted


def get_agent_loop() -> AgentLoop:
    """Dependency provider returning an AgentLoop instance."""
    return AgentLoop()


@router.post(
    "/chat",
    response_model=AgentChatResponse,
    summary="Chat with the Musafir travel planning agent",
    description=(
        "Sends a user message to the Musafir agent. Manages conversation state, "
        "orchestrates tool calling with Gemma 4, and returns sanitized response metadata."
    ),
    responses={
        200: {"description": "Successful conversational turn."},
        404: {"description": "Conversation ID not found."},
        422: {"description": "Validation error (e.g. empty message or invalid UUID)."},
        429: {"description": "LLM rate limit reached."},
        502: {"description": "LLM provider or authentication error."},
        504: {"description": "LLM request timed out."},
        500: {"description": "Internal agent or server error."},
    },
)
def agent_chat(
    request: AgentChatRequest,
    loop: AgentLoop = Depends(get_agent_loop),
) -> AgentChatResponse:
    """
    Executes a turn of conversation with the Musafir travel agent.
    Checks deterministic FastPath first; falls back to single Gemma 4 LLM AgentLoop.
    """
    # 1. Retrieve or create session state
    if request.conversation_id is None:
        session = get_or_create_session()
        logger.info(f"Created new conversation session: {session.conversation_id}")
    else:
        conv_id_str = str(request.conversation_id)
        session = get_session(conv_id_str)
        if not session:
            logger.warning(f"Conversation not found: {conv_id_str}")
            raise HTTPException(
                status_code=404,
                detail=f"Conversation '{conv_id_str}' not found.",
            )
        logger.info(f"Loaded existing conversation session: {conv_id_str}")

    state = session.trip_state

    # 2. FastPath Evaluation (Deterministic Local Python Resolution)
    t_fp_start = time.perf_counter()
    fp_result: FastPathResult = resolve_fast_path(
        request.message,
        session,
        llm_client=getattr(loop, "client", None),
    )
    fp_duration_ms = int((time.perf_counter() - t_fp_start) * 1000)
    logger.info(f"[FastPath] Evaluated in {fp_duration_ms}ms: matched={fp_result.matched}, intent={fp_result.intent}")

    if fp_result.intent == "NON_TRIP_QUERY":
        response_text = "I can help plan a trip when you're ready. This message doesn't contain a trip-planning request."
        state.messages.append({"role": "user", "content": request.message.strip()})
        state.messages.append({"role": "assistant", "content": response_text})
        save_session(session)
        return AgentChatResponse(
            conversation_id=UUID(state.conversation_id),
            response=response_text,
            tool_calls=[],
            iterations=0,
            state_summary=state.summary(),
            results=None,
            metrics={
                "total_turn_ms": fp_duration_ms,
                "fast_path_ms": fp_duration_ms,
                "llm_iterations": 0,
                "total_llm_ms": 0,
                "tool_count": 0,
                "fallback_trigger": fp_result.fallback_trigger,
                "fallback_succeeded": fp_result.fallback_succeeded,
            },
        )

    if fp_result.matched:
        if fp_result.cleared_active_question:
            session.conversation_context.clear_active_question()
        elif fp_result.intent in {"CHANGE_HOTEL", "REPLACE_ITEM", "SELECT_ITEM", "SELECT_ITEMS", "REJECT_ITEM", "UPDATE_DURATION"}:
            if session.conversation_context.active_question and session.conversation_context.active_question.scope == "clarification":
                session.conversation_context.clear_active_question()

        if fp_result.intent in {"ANSWER_ACTIVE_QUESTION", "UPDATE_FIELD", "PLAN_TRIP"}:
            # Single source of truth for mutation: apply_trip_state_update
            apply_trip_state_update(state, fp_result.state_updates)

            # Deterministic readiness determines next active question
            next_q = get_next_active_question(state)
            if next_q:
                session.conversation_context.active_question = next_q
            else:
                session.conversation_context.clear_active_question()

            # Concise contextual confirmation
            confirmation = "Got it."
            if "destination" in fp_result.state_updates and "number_of_days" in fp_result.state_updates:
                days = state.number_of_days
                mode_str = " walking" if state.travel_mode == "walking" else (f" by {state.travel_mode}" if state.travel_mode and state.travel_mode != "driving" else "")
                if state.number_of_nights and state.number_of_nights > 0:
                    confirmation = f"Got it — {days} days ({state.number_of_nights} nights){mode_str} trip to {state.destination}."
                else:
                    confirmation = f"Got it — {days}-day{mode_str} trip to {state.destination}."
            elif "trip_start_date" in fp_result.state_updates or "trip_end_date" in fp_result.state_updates:
                if state.trip_start_date and state.trip_end_date and state.number_of_days:
                    s_str = state.trip_start_date.strftime("%d %B")
                    e_str = state.trip_end_date.strftime("%d %B")
                    days = state.number_of_days
                    nights = state.number_of_nights or 0
                    confirmation = f"Got it — {s_str} to {e_str} ({days} days, {nights} nights)."
                elif state.trip_start_date:
                    d_str = state.trip_start_date.strftime("%d %b")
                    confirmation = f"Got it — starting {d_str}."
            elif "number_of_days" in fp_result.state_updates:
                days = state.number_of_days
                nights = state.number_of_nights or 0
                if state.trip_start_date and state.trip_end_date:
                    s_str = state.trip_start_date.strftime("%d %B")
                    e_str = state.trip_end_date.strftime("%d %B")
                    confirmation = f"Got it — {s_str} to {e_str} ({days} days, {nights} nights)."
                else:
                    confirmation = f"Got it — {days} days ({nights} nights)."
            elif "hotel_total_budget" in fp_result.state_updates:
                tot = int(state.hotel_total_budget or fp_result.state_updates["hotel_total_budget"])
                if state.hotel_budget and state.number_of_nights:
                    rate = int(state.hotel_budget)
                    n = state.number_of_nights
                    confirmation = f"Got it — ₹{tot:,} total accommodation budget (₹{rate:,}/night for {n} nights)."
                else:
                    confirmation = f"Got it — ₹{tot:,} total accommodation budget."
            elif "hotel_budget" in fp_result.state_updates:
                b = int(state.hotel_budget)
                confirmation = f"Got it — ₹{b:,}/night accommodation budget."
            elif "destination" in fp_result.state_updates:
                confirmation = f"Got it — {state.destination}."
            elif "travel_mode" in fp_result.state_updates:
                confirmation = f"Got it — traveling by {state.travel_mode}."
            elif fp_result.intent == "CONFIRMATION_YES":
                confirmation = "Got it, confirmed."
            elif fp_result.intent == "CONFIRMATION_NO":
                confirmation = "Understood."

            executed_tools: List[str] = []
            results = None

            if next_q is None and state.destination and len(state.selected_places) == 0:
                if state.hotel_search_required is False or state.hotel_required is False or (state.number_of_nights is not None and state.number_of_nights == 0):
                    # Day trip / no hotel: immediately trigger place discovery for destination
                    from app.agent.tools import search_places_tool
                    place_res = search_places_tool(destination=state.destination, trip_state=state)
                    if place_res.get("success"):
                        places = place_res.get("places", [])
                        results = {"places": places}
                        session.conversation_context.set_visible_items("place", places)
                        state.planning_stage = PlanningStage.PLACE_SELECTION
                        executed_tools = ["search_places"]
                        final_text = f"{confirmation} Here are top attractions in {state.destination} to explore:"
                    else:
                        final_text = confirmation
                else:
                    final_text = confirmation
            elif next_q and next_q.prompt_text:
                final_text = f"{confirmation} {next_q.prompt_text}"
            else:
                final_text = confirmation

            final_text = sanitize_public_response(final_text)

            # Update conversation history
            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)

            fp_metrics = {
                "total_turn_ms": fp_duration_ms,
                "fast_path_ms": fp_duration_ms,
                "llm_iterations": 0,
                "total_llm_ms": 0,
                "tool_count": len(executed_tools),
            }

            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=executed_tools,
                iterations=0,
                state_summary=state.summary(),
                results=results,
                metrics=fp_metrics,
            )

        elif fp_result.intent in {"SELECT_ITEM", "SELECT_ITEMS"}:
            apply_trip_state_update(state, fp_result.state_updates)
            if fp_result.intent == "SELECT_ITEMS":
                items = (fp_result.reference_resolution or {}).get("items", [])
                item_names = [it.get("name") for it in items if it.get("name")]
                if item_names:
                    final_text = f"Got it — added {', '.join(item_names)} to your trip."
                else:
                    final_text = "Got it — added selections to your trip."
            else:
                ref_name = (fp_result.reference_resolution or {}).get("name", "item")
                final_text = f"Got it — selected {ref_name}."

            # Prompt traveler regarding additional place/food selections or route readiness
            has_food = bool(state.selected_restaurants or state.selected_cafes)
            if state.hotel_selection and (len(state.selected_places) >= 1 or has_food):
                final_text += " You can select more stops, or let me know when you're ready to build the route."

            next_q = get_next_active_question(state)
            if next_q and not state.hotel_selection:
                session.conversation_context.active_question = next_q
                if next_q.prompt_text:
                    final_text = f"{final_text} {next_q.prompt_text}"

            final_text = sanitize_public_response(final_text)

            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)

            fp_metrics = {
                "total_turn_ms": fp_duration_ms,
                "fast_path_ms": fp_duration_ms,
                "llm_iterations": 0,
                "total_llm_ms": 0,
                "tool_count": 0,
            }

            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=[],
                iterations=0,
                state_summary=state.summary(),
                results=None,
                metrics=fp_metrics,
            )

        elif fp_result.intent == "SEARCH_FOOD":
            apply_trip_state_update(state, fp_result.state_updates)
            plan_action = decide_next_planning_action(state, context=session.conversation_context, user_intent="SEARCH_FOOD")
            executed_tools = []
            results = None
            if plan_action.action_type == PlanningActionType.SEARCH_FOOD:
                tool_args = dict(plan_action.tool_args)
                if fp_result.reference_resolution:
                    cat = fp_result.reference_resolution.get("category")
                    meal = fp_result.reference_resolution.get("meal_type")
                    anc = fp_result.reference_resolution.get("anchor")
                    if cat:
                        tool_args["category"] = cat
                    if meal:
                        tool_args["meal_type"] = meal
                    if anc:
                        tool_args["location_anchor"] = anc

                food_res = search_restaurants_tool(trip_state=state, **tool_args)
                if food_res.get("success"):
                    rests = food_res.get("restaurants", [])
                    results = {"restaurants": rests}
                    session.conversation_context.set_visible_items("restaurant", rests)
                    state.planning_stage = PlanningStage.FOOD_SELECTION
                    cat_name = "cafés" if tool_args.get("category") == "cafe" else "restaurants"
                    anc_text = f" near {tool_args['location_anchor']}" if tool_args.get("location_anchor") else ""
                    meal_text = f" for {tool_args['meal_type']}" if tool_args.get("meal_type") else ""
                    final_text = f"Here are {len(rests)} {cat_name}{meal_text}{anc_text}."
                    executed_tools = ["search_restaurants"]
                else:
                    final_text = f"Could not find restaurants: {food_res.get('error', 'unknown error')}"
            else:
                final_text = plan_action.prompt_message or "Please tell me what dining options you're looking for."

            final_text = sanitize_public_response(final_text)

            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)

            fp_metrics = {
                "total_turn_ms": fp_duration_ms,
                "fast_path_ms": fp_duration_ms,
                "llm_iterations": 0,
                "total_llm_ms": 0,
                "tool_count": len(executed_tools),
            }

            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=executed_tools,
                iterations=0,
                state_summary=state.summary(),
                results=results,
                metrics=fp_metrics,
            )

        elif fp_result.intent == "REJECT_ITEM":
            change_set = apply_trip_state_update(state, fp_result.state_updates)
            ref_name = (fp_result.reference_resolution or {}).get("name", "item")
            executed_tools = []
            results = None

            # Check if user explicitly asked to rebuild route in the same utterance
            should_rebuild_route = bool((fp_result.reference_resolution or {}).get("rebuild_route"))
            if should_rebuild_route:
                replan_action = ReplanningController.decide(
                    state=state,
                    user_intent="ROUTE_REQUEST",
                    user_message=request.message,
                    change_set=change_set,
                )
                replan_res = execute_replanning_cycle(state, replan_action)
                executed_tools = replan_res.get("executed_tools", [])
                if replan_res.get("success") and state.current_route:
                    dist_km = state.current_route.total_distance_meters / 1000.0
                    stops_n = len(state.selected_places) + len(state.selected_restaurants) + len(state.selected_cafes)
                    stops_word = "stops" if (state.selected_restaurants or state.selected_cafes) else "places"
                    final_text = f"Removed {ref_name} and rebuilt your route with {stops_n} {stops_word} ({dist_km:.1f} km)."
                    results = {"route": state.current_route.model_dump()}
                else:
                    final_text = f"Removed {ref_name}, but route rebuild failed: {replan_res.get('error')}"
            else:
                # Lazy replanning: dependencies became stale, but no tool is called immediately
                final_text = f"Understood — removed {ref_name} from your selections."
                if state.is_derived_stale(DerivedResource.CURRENT_ROUTE):
                    final_text += " Your route and itinerary need updating when you're ready."

            final_text = sanitize_public_response(final_text)

            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)

            fp_metrics = {
                "total_turn_ms": fp_duration_ms,
                "fast_path_ms": fp_duration_ms,
                "llm_iterations": 0,
                "total_llm_ms": 0,
                "tool_count": len(executed_tools),
            }

            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=executed_tools,
                iterations=0,
                state_summary=state.summary(),
                results=results,
                metrics=fp_metrics,
            )

        elif fp_result.intent == "UPDATE_FIELD_AND_REBUILD_ROUTE":
            change_set = apply_trip_state_update(state, fp_result.state_updates)
            replan_action = ReplanningController.decide(
                state=state,
                user_intent="ROUTE_REQUEST",
                user_message=request.message,
                change_set=change_set,
            )
            replan_res = execute_replanning_cycle(state, replan_action)
            executed_tools = replan_res.get("executed_tools", [])
            results = None
            if replan_res.get("success") and state.current_route:
                dist_km = state.current_route.total_distance_meters / 1000.0
                feas = replan_res.get("feasibility")
                if state.feasibility_result and not state.feasibility_result.is_feasible:
                    from app.agent.feasibility import format_feasibility_explanation
                    feas_msg = format_feasibility_explanation(state.feasibility_result, state)
                    final_text = f"Switched travel mode to {state.travel_mode} and rebuilt your route ({dist_km:.1f} km), but detected scheduling conflicts:\n\n{feas_msg}"
                else:
                    final_text = f"Switched travel mode to {state.travel_mode} and rebuilt your route ({dist_km:.1f} km)."
                results = {"route": state.current_route.model_dump(), "feasibility": feas}
            else:
                final_text = f"Updated travel mode to {state.travel_mode}. Could not rebuild route: {replan_res.get('error')}"

            final_text = sanitize_public_response(final_text)

            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)

            fp_metrics = {
                "total_turn_ms": fp_duration_ms,
                "fast_path_ms": fp_duration_ms,
                "llm_iterations": 0,
                "total_llm_ms": 0,
                "tool_count": len(executed_tools),
            }

            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=executed_tools,
                iterations=0,
                state_summary=state.summary(),
                results=results,
                metrics=fp_metrics,
            )

        elif fp_result.intent == "UPDATE_TRAVEL_MODE":
            change_set = apply_trip_state_update(state, fp_result.state_updates)
            final_text = f"Updated travel mode to {state.travel_mode}."
            if state.is_derived_stale(DerivedResource.CURRENT_ROUTE):
                final_text += " Your route and itinerary need updating when you're ready."
            final_text = sanitize_public_response(final_text)
            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)
            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=[],
                iterations=0,
                state_summary=state.summary(),
                results=None,
                metrics={"total_turn_ms": fp_duration_ms, "fast_path_ms": fp_duration_ms, "llm_iterations": 0, "total_llm_ms": 0, "tool_count": 0},
            )

        elif fp_result.intent == "MARK_STOP_REQUIRED":
            change_set = apply_trip_state_update(state, fp_result.state_updates)
            ref_name = (fp_result.reference_resolution or {}).get("name", "Stop")
            final_text = f"Marked '{ref_name}' as a REQUIRED (must-visit) stop."
            if state.is_derived_stale(DerivedResource.CURRENT_ITINERARY):
                final_text += " Your daily schedule will prioritize it when generating the itinerary."
            final_text = sanitize_public_response(final_text)
            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)
            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=[],
                iterations=0,
                state_summary=state.summary(),
                results={"required_stops": state.required_stops},
                metrics={"total_turn_ms": fp_duration_ms, "fast_path_ms": fp_duration_ms, "llm_iterations": 0, "total_llm_ms": 0, "tool_count": 0},
            )

        elif fp_result.intent == "CLARIFICATION":
            clar_dict = (fp_result.reference_resolution or {}).get("clarification", {})
            question = clar_dict.get("question", "Could you please clarify your request?")
            final_text = sanitize_public_response(question)

            # Persist clarification as active question in conversation context across turns
            field_name = clar_dict.get("field", "clarification")
            session.conversation_context.set_active_question(
                field=field_name,
                expected_type="clarification_answer",
                scope="clarification",
                prompt_text=question,
                reason=clar_dict.get("reason"),
            )
            if clar_dict.get("options"):
                session.conversation_context.last_mentioned_entities["clarification_options"] = clar_dict.get("options")
            if (fp_result.reference_resolution or {}).get("amount"):
                session.conversation_context.last_mentioned_entities["clarification_amount"] = (fp_result.reference_resolution or {}).get("amount")
            elif clar_dict.get("metadata", {}).get("amount"):
                session.conversation_context.last_mentioned_entities["clarification_amount"] = clar_dict.get("metadata", {}).get("amount")

            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)
            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=[],
                iterations=0,
                state_summary=state.summary(),
                results={"clarification": clar_dict},
                metrics={
                    "total_turn_ms": fp_duration_ms,
                    "fast_path_ms": fp_duration_ms,
                    "llm_iterations": 0,
                    "total_llm_ms": 0,
                    "tool_count": 0,
                },
            )

        elif fp_result.intent == "CHANGE_HOTEL":
            change_set = apply_trip_state_update(state, fp_result.state_updates)
            hotel_name = state.hotel_selection.name if state.hotel_selection else "new hotel"
            rebuild_route = (fp_result.reference_resolution or {}).get("rebuild_route", False)
            executed_tools = []
            results = None

            if rebuild_route:
                replan_action = ReplanningController.decide(
                    state=state,
                    user_intent="ROUTE_REQUEST",
                    user_message=request.message,
                    change_set=change_set,
                )
                replan_res = execute_replanning_cycle(state, replan_action)
                executed_tools = replan_res.get("executed_tools", [])
                if replan_res.get("success") and state.current_route:
                    dist_km = state.current_route.total_distance_meters / 1000.0
                    final_text = f"Changed hotel to {hotel_name} and rebuilt your route ({dist_km:.1f} km)."
                    results = {"route": state.current_route.model_dump()}
                else:
                    final_text = f"Changed hotel to {hotel_name}. Could not rebuild route: {replan_res.get('error')}"
            else:
                final_text = f"Changed your hotel to {hotel_name}."
                if state.is_derived_stale(DerivedResource.CURRENT_ROUTE):
                    final_text += " Your route and itinerary need updating when you're ready."

            final_text = sanitize_public_response(final_text)
            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)

            fp_metrics = {
                "total_turn_ms": fp_duration_ms,
                "fast_path_ms": fp_duration_ms,
                "llm_iterations": 0,
                "total_llm_ms": 0,
                "tool_count": len(executed_tools),
            }

            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=executed_tools,
                iterations=0,
                state_summary=state.summary(),
                results=results,
                metrics=fp_metrics,
            )

        elif fp_result.intent == "REPLACE_ITEM":
            ref = fp_result.reference_resolution or {}
            batch_data = ref.get("batch")
            from app.agent.mutation import apply_mutation_batch
            if batch_data:
                change_set = apply_mutation_batch(state, batch_data)
            else:
                change_set = apply_trip_state_update(state, fp_result.state_updates)

            t_name = ref.get("target", {}).get("name", "item")
            r_name = ref.get("replacement", {}).get("name", "replacement") if isinstance(ref.get("replacement"), dict) else str(ref.get("replacement", "replacement"))
            rebuild_route = ref.get("rebuild_route", False)
            executed_tools = []
            results = None

            if rebuild_route:
                replan_action = ReplanningController.decide(
                    state=state,
                    user_intent="ROUTE_REQUEST",
                    user_message=request.message,
                    change_set=change_set,
                )
                replan_res = execute_replanning_cycle(state, replan_action)
                executed_tools = replan_res.get("executed_tools", [])
                if replan_res.get("success") and state.current_route:
                    dist_km = state.current_route.total_distance_meters / 1000.0
                    final_text = f"Replaced {t_name} with {r_name} and rebuilt your route ({dist_km:.1f} km)."
                    results = {"route": state.current_route.model_dump()}
                else:
                    final_text = f"Replaced {t_name} with {r_name}. Could not rebuild route: {replan_res.get('error')}"
            else:
                final_text = f"Replaced {t_name} with {r_name}."
                if state.is_derived_stale(DerivedResource.CURRENT_ROUTE):
                    final_text += " Your route and itinerary need updating when you're ready."

            final_text = sanitize_public_response(final_text)
            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)

            fp_metrics = {
                "total_turn_ms": fp_duration_ms,
                "fast_path_ms": fp_duration_ms,
                "llm_iterations": 0,
                "total_llm_ms": 0,
                "tool_count": len(executed_tools),
            }

            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=executed_tools,
                iterations=0,
                state_summary=state.summary(),
                results=results,
                metrics=fp_metrics,
            )

        elif fp_result.intent == "UPDATE_DURATION":
            change_set = apply_trip_state_update(state, fp_result.state_updates)
            rebuild_itin = (fp_result.reference_resolution or {}).get("rebuild_itinerary", False)
            executed_tools = []
            results = None
            if rebuild_itin:
                replan_action = ReplanningController.decide(
                    state=state,
                    user_intent="ITINERARY_REQUEST",
                    user_message=request.message,
                    change_set=change_set,
                )
                replan_res = execute_replanning_cycle(state, replan_action)
                executed_tools = replan_res.get("executed_tools", [])
                if replan_res.get("success") and state.current_itinerary:
                    final_text = f"Updated trip to {state.number_of_days} days and regenerated your itinerary."
                    results = {"itinerary": state.current_itinerary.model_dump()}
                else:
                    final_text = f"Updated duration to {state.number_of_days} days. Itinerary update: {replan_res.get('error')}"
            else:
                final_text = f"Updated trip duration to {state.number_of_days} days."
                if state.is_derived_stale(DerivedResource.CURRENT_ITINERARY):
                    final_text += " Your daily itinerary needs updating when you're ready."

            final_text = sanitize_public_response(final_text)
            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)

            fp_metrics = {
                "total_turn_ms": fp_duration_ms,
                "fast_path_ms": fp_duration_ms,
                "llm_iterations": 0,
                "total_llm_ms": 0,
                "tool_count": len(executed_tools),
            }

            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=executed_tools,
                iterations=0,
                state_summary=state.summary(),
                results=results,
                metrics=fp_metrics,
            )

        elif fp_result.intent == "UPDATE_BUDGET":
            change_set = apply_trip_state_update(state, fp_result.state_updates)
            scope = (fp_result.reference_resolution or {}).get("scope", "budget")
            amount = (fp_result.reference_resolution or {}).get("amount", 0)
            if "selected_hotel" in change_set.invalidated_fields:
                final_text = f"Updated your {scope.replace('_', ' ')} to ₹{int(amount):,}. Your previous hotel selection exceeded this new budget and was removed. Your route and itinerary need updating."
            else:
                final_text = f"Updated your {scope.replace('_', ' ')} to ₹{int(amount):,}."

            final_text = sanitize_public_response(final_text)
            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)

            fp_metrics = {
                "total_turn_ms": fp_duration_ms,
                "fast_path_ms": fp_duration_ms,
                "llm_iterations": 0,
                "total_llm_ms": 0,
                "tool_count": 0,
            }

            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=[],
                iterations=0,
                state_summary=state.summary(),
                results=None,
                metrics=fp_metrics,
            )

        elif fp_result.intent == "COMPOUND_MUTATION":
            change_set = apply_trip_state_update(state, fp_result.state_updates)
            final_text = f"Updated your trip preferences: {change_set.summary()}."
            if state.is_derived_stale(DerivedResource.CURRENT_ROUTE):
                final_text += " Your route and itinerary need updating when you're ready."

            final_text = sanitize_public_response(final_text)
            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)

            fp_metrics = {
                "total_turn_ms": fp_duration_ms,
                "fast_path_ms": fp_duration_ms,
                "llm_iterations": 0,
                "total_llm_ms": 0,
                "tool_count": 0,
            }

            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=[],
                iterations=0,
                state_summary=state.summary(),
                results=None,
                metrics=fp_metrics,
            )

        elif fp_result.intent == "ROUTE_REQUEST":
            replan_action = ReplanningController.decide(
                state=state,
                user_intent="ROUTE_REQUEST",
                user_message=request.message,
            )
            executed_tools = []
            results = None

            if replan_action.action_type == ReplanningActionType.ANSWER:
                dist_km = (state.current_route.total_distance_meters / 1000.0) if state.current_route else 0.0
                final_text = f"Your route is already current ({dist_km:.1f} km)."
                if state.current_route:
                    results = {"route": state.current_route.model_dump()}
            elif replan_action.action_type == ReplanningActionType.ASK:
                final_text = replan_action.prompt_message or "Please select your starting hotel and attractions first."
            else:
                replan_res = execute_replanning_cycle(state, replan_action)
                executed_tools = replan_res.get("executed_tools", [])
                if replan_res.get("success") and state.current_route:
                    hotel_name = state.hotel_selection.name if state.hotel_selection else "hotel"
                    dist_km = state.current_route.total_distance_meters / 1000.0
                    stops_n = len(state.selected_places) + len(state.selected_restaurants) + len(state.selected_cafes)
                    stops_word = "stops" if (state.selected_restaurants or state.selected_cafes) else "places"
                    feas = replan_res.get("feasibility")
                    if state.feasibility_result and not state.feasibility_result.is_feasible:
                        from app.agent.feasibility import format_feasibility_explanation
                        feas_msg = format_feasibility_explanation(state.feasibility_result, state)
                        final_text = f"I've built the route covering {stops_n} {stops_word} ({dist_km:.1f} km), but detected scheduling conflicts:\n\n{feas_msg}"
                    else:
                        final_text = f"I've built the optimal route for your trip from {hotel_name} covering {stops_n} {stops_word}! Total travel distance is {dist_km:.1f} km."
                    results = {"route": state.current_route.model_dump(), "feasibility": feas}
                else:
                    final_text = f"Could not compute route: {replan_res.get('error', 'unknown error')}"

            final_text = sanitize_public_response(final_text)

            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)

            fp_metrics = {
                "total_turn_ms": fp_duration_ms,
                "fast_path_ms": fp_duration_ms,
                "llm_iterations": 0,
                "total_llm_ms": 0,
                "tool_count": len(executed_tools),
            }

            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=executed_tools,
                iterations=0,
                state_summary=state.summary(),
                results=results,
                metrics=fp_metrics,
            )

        elif fp_result.intent == "ITINERARY_REQUEST":
            replan_action = ReplanningController.decide(
                state=state,
                user_intent="ITINERARY_REQUEST",
                user_message=request.message,
            )
            executed_tools = []
            results = None

            if replan_action.action_type == ReplanningActionType.ANSWER:
                days = state.current_itinerary.total_days if state.current_itinerary else 1
                final_text = f"Your daily itinerary is already current across {days} days."
                if state.current_itinerary:
                    results = {"itinerary": state.current_itinerary.model_dump()}
            elif replan_action.action_type == ReplanningActionType.ASK:
                final_text = replan_action.prompt_message or "A valid route and duration are required before generating the itinerary."
            else:
                replan_res = execute_replanning_cycle(state, replan_action)
                executed_tools = replan_res.get("executed_tools", [])
                if replan_res.get("success") and state.current_itinerary:
                    days = state.current_itinerary.total_days
                    feas = replan_res.get("feasibility")
                    if state.feasibility_result and not state.feasibility_result.is_feasible:
                        from app.agent.feasibility import format_feasibility_explanation
                        feas_msg = format_feasibility_explanation(state.feasibility_result, state)
                        final_text = f"I've updated your daily schedule, but detected feasibility conflicts:\n\n{feas_msg}"
                    else:
                        final_text = f"I've updated your daily itinerary across {days} days with arrival, visit, and departure times."
                    results = {"itinerary": state.current_itinerary.model_dump(), "feasibility": feas}
                else:
                    final_text = f"Could not generate itinerary: {replan_res.get('error', 'unknown error')}"


            final_text = sanitize_public_response(final_text)

            state.messages.append({"role": "user", "content": request.message.strip()})
            state.messages.append({"role": "assistant", "content": final_text})
            save_session(session)

            fp_metrics = {
                "total_turn_ms": fp_duration_ms,
                "fast_path_ms": fp_duration_ms,
                "llm_iterations": 0,
                "total_llm_ms": 0,
                "tool_count": len(executed_tools),
            }

            return AgentChatResponse(
                conversation_id=UUID(state.conversation_id),
                response=final_text,
                tool_calls=executed_tools,
                iterations=0,
                state_summary=state.summary(),
                results=results,
                metrics=fp_metrics,
            )

    # 3. Main AgentLoop Fallback (Nuanced / Complex Language Turns)
    try:
        try:
            result = loop.run(
                messages=state.messages,
                user_message=request.message,
                trip_state=state,
                session=session,
            )
        except TypeError:
            result = loop.run(
                messages=state.messages,
                user_message=request.message,
                trip_state=state,
            )
    except LLMAuthError as exc:
        logger.error(f"LLM Authentication error: {_sanitize_error(str(exc))}")
        raise HTTPException(
            status_code=502,
            detail="LLM authentication failed. Please check provider credentials.",
        ) from exc
    except LLMRateLimitError as exc:
        logger.warning(f"LLM Rate limit: {_sanitize_error(str(exc))}")
        raise HTTPException(
            status_code=429,
            detail="LLM provider rate limit exceeded. Please try again shortly.",
        ) from exc
    except LLMTimeoutError as exc:
        logger.error(f"LLM Timeout error: {_sanitize_error(str(exc))}")
        raise HTTPException(
            status_code=504,
            detail="LLM provider request timed out.",
        ) from exc
    except LLMProviderError as exc:
        clean_msg = _sanitize_error(str(exc))
        logger.error(f"LLM Provider error: {clean_msg}")
        raise HTTPException(
            status_code=502,
            detail=f"LLM provider error: {clean_msg}",
        ) from exc
    except AgentLoopError as exc:
        clean_msg = _sanitize_error(str(exc))
        logger.error(f"Agent Loop error: {clean_msg}")
        raise HTTPException(
            status_code=500,
            detail=f"Agent loop error: {clean_msg}",
        ) from exc
    except Exception as exc:
        clean_msg = _sanitize_error(str(exc))
        logger.error(f"Unexpected agent error: {clean_msg}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred while processing the agent conversation.",
        ) from exc

    # 4. Persist updated conversation history and session
    state.messages = result.messages
    save_session(session)

    # 5. Return sanitized API response
    clean_response = sanitize_public_response(result.response)
    return AgentChatResponse(
        conversation_id=UUID(state.conversation_id),
        response=clean_response,
        tool_calls=result.tool_calls,
        iterations=result.iterations,
        state_summary=result.state_summary or state.summary(),
        results=result.results,
        metrics=getattr(result, "metrics", None),
    )
