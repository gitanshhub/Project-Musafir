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
from app.agent.planner import decide_next_planning_action, PlanningActionType
from app.agent.tools import optimize_route_tool
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
    fp_result: FastPathResult = resolve_fast_path(request.message, session)
    fp_duration_ms = int((time.perf_counter() - t_fp_start) * 1000)
    logger.info(f"[FastPath] Evaluated in {fp_duration_ms}ms: matched={fp_result.matched}, intent={fp_result.intent}")

    if fp_result.matched:
        if fp_result.intent in {"ANSWER_ACTIVE_QUESTION", "UPDATE_FIELD"}:
            # Single source of truth for mutation: apply_trip_state_update
            apply_trip_state_update(state, fp_result.state_updates)

            if fp_result.cleared_active_question:
                session.conversation_context.clear_active_question()

            # Deterministic readiness determines next active question
            next_q = get_next_active_question(state)
            if next_q:
                session.conversation_context.active_question = next_q

            # Concise contextual confirmation
            confirmation = "Got it."
            if "trip_start_date" in fp_result.state_updates or "trip_end_date" in fp_result.state_updates:
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

            if next_q and next_q.prompt_text:
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

        elif fp_result.intent in {"SELECT_ITEM", "SELECT_ITEMS"}:
            apply_trip_state_update(state, fp_result.state_updates)
            if fp_result.intent == "SELECT_ITEMS":
                items = (fp_result.reference_resolution or {}).get("items", [])
                item_names = [it.get("name") for it in items if it.get("name")]
                if item_names:
                    final_text = f"Got it — added {', '.join(item_names)} to your trip."
                else:
                    final_text = "Got it — added selected places to your trip."
            else:
                ref_name = (fp_result.reference_resolution or {}).get("name", "item")
                final_text = f"Got it — selected {ref_name}."

            # Prompt traveler regarding additional place selections or route readiness
            if state.hotel_selection and len(state.selected_places) >= 1:
                final_text += " You can select more places, or let me know when you're ready to build the route."

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

        elif fp_result.intent == "REJECT_ITEM":
            apply_trip_state_update(state, fp_result.state_updates)
            ref_name = (fp_result.reference_resolution or {}).get("name", "item")
            final_text = f"Understood — removed {ref_name} from your selections."

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
            plan_action = decide_next_planning_action(state, context=session.conversation_context, user_intent="ROUTE_REQUEST")
            executed_tools = []
            if plan_action.action_type == PlanningActionType.OPTIMIZE_ROUTE:
                route_res = optimize_route_tool(trip_state=state)
                if route_res.get("success"):
                    state.current_route = OptimizedRoute(**route_res["route"])
                    state.planning_stage = PlanningStage.ROUTE_PLANNING
                    hotel_name = state.hotel_selection.name if state.hotel_selection else "hotel"
                    dist_km = state.current_route.total_distance_meters / 1000.0
                    stops_n = len(state.selected_places)
                    final_text = f"I've built the optimal route for your trip from {hotel_name} covering {stops_n} places! Total travel distance is {dist_km:.1f} km."
                    executed_tools = ["optimize_route"]
                else:
                    final_text = f"Could not compute route: {route_res.get('error', 'unknown error')}"
            else:
                final_text = plan_action.prompt_message or "Please select your starting hotel and attractions first."

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
                results=None,
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
