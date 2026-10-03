"""
Project Musafir — Agent Tool-Calling Loop (Step 10.4)
Orchestrates multi-turn conversation and tool calling between Gemma 4 (via OpenRouter)
and Musafir deterministic tools (SerpApi hotels/places/restaurants, route optimization, itinerary).
"""

import json
import logging
import re
import time
import uuid
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field

from app.llm.openrouter_client import OpenRouterClient, LLMResponse
from app.llm.prompts import SYSTEM_PROMPT
from app.agent.tools import TOOL_DEFINITIONS, execute_tool
from app.agent.state import TripState
from app.agent.dependencies import DerivedResource
from app.agent.context import SessionState, ConversationContext, format_conversation_context
from app.agent.state_update import get_next_active_question


logger = logging.getLogger("musafir.agent.loop")


# --- JSON Serialization & Sanitization Helpers ---

def to_json_safe(obj: Any) -> Any:
    """Recursively converts any object (models, dates, UUIDs, enums, sets) to JSON-serializable primitives."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, BaseModel):
        if hasattr(obj, "model_dump"):
            return to_json_safe(obj.model_dump())
        elif hasattr(obj, "dict"):
            return to_json_safe(obj.dict())
    if isinstance(obj, dict):
        return {str(k): to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_json_safe(x) for x in obj]
    return str(obj)


def json_dumps_safe(obj: Any, **kwargs) -> str:
    """Safe wrapper around json.dumps ensuring all nested structures are JSON primitives."""
    return json.dumps(to_json_safe(obj), **kwargs)


def sanitize_public_response(text: Optional[str]) -> str:
    """
    Strips internal model markers (<arg_value>, <tool_call>, XML tags),
    raw tool call artifacts, credentials, and stack traces from public-facing responses.
    """
    if not text:
        return ""

    cleaned = text
    # Remove XML-like tool call / argument wrappers and contents if purely structural
    cleaned = re.sub(
        r"<(?:tool_call|arg_value|arg_name|tool_response)[^>]*>[\s\S]*?</(?:tool_call|arg_value|arg_name|tool_response)>",
        "",
        cleaned,
    )
    # Remove standalone or unclosed markers
    cleaned = re.sub(
        r"</?(?:tool_call|arg_value|arg_name|tool_response|call:[^>]+)[^>]*>",
        "",
        cleaned,
    )
    # Remove raw property token leakage from assistant prose
    cleaned = re.sub(r"(?i)\bproperty_token['\":\s=]+[a-zA-Z0-9_\-]+", "", cleaned)
    cleaned = re.sub(r"(?i)\(property token:[^)]+\)", "", cleaned)
    cleaned = re.sub(r"(?i)\btoken:\s*[a-zA-Z0-9_\-]{20,}", "", cleaned)
    # Remove credentials / tokens
    cleaned = re.sub(r"sk-[a-zA-Z0-9_\-]+", "[REDACTED]", cleaned)
    cleaned = re.sub(r"(?i)bearer\s+[a-zA-Z0-9_\.\-]+", "Bearer [REDACTED]", cleaned)
    cleaned = re.sub(r"(?i)key[=:\s]+[a-zA-Z0-9_\-]+", "key=[REDACTED]", cleaned)
    # Remove Python traceback blocks
    cleaned = re.sub(r"Traceback \(most recent call last\):[\s\S]*?(?=\n\n|\Z)", "", cleaned)
    # Remove internal derived freshness and state version leakage
    cleaned = re.sub(
        r"(?i)\b(?:CURRENT_ROUTE|CURRENT_ITINERARY|HOTEL_DISCOVERY|PLACE_DISCOVERY|FOOD_DISCOVERY)\s*[:=]\s*(?:VALID|STALE|NOT_AVAILABLE)\b",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(?i)\bderived_freshness\b", "", cleaned)
    cleaned = re.sub(r"(?i)\bstate_version\s*[:=]\s*\d+\b", "", cleaned)

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


# --- Exceptions ---

class AgentLoopError(Exception):
    """Raised when the agent loop encounters an unrecoverable failure or exceeds max iterations."""
    pass


# --- Agent Loop Result Model ---

class AgentResult(BaseModel):
    """Normalized output produced by the agent tool-calling loop."""
    response: str = Field(..., description="Final natural-language response generated for the user")
    tool_calls: List[str] = Field(default_factory=list, description="Ordered list of tool names executed")
    iterations: int = Field(..., description="Total LLM roundtrips/iterations taken")
    state_summary: Optional[Dict[str, Any]] = Field(None, description="Snapshot of the trip state summary")
    messages: List[Dict[str, Any]] = Field(default_factory=list, description="Complete conversation history")
    results: Optional[Dict[str, Any]] = Field(None, description="Structured tool execution results (e.g. hotel cards)")
    metrics: Optional[Dict[str, Any]] = Field(None, description="Timing and performance metrics breakdown")


# --- State Context Helper ---

def format_state_context(state: TripState) -> str:
    """Formats a concise textual summary of the current TripState for the model prompt."""
    s = state.summary()
    lines = ["\n[Current Trip State]"]
    if s.get("destination"):
        lines.append(f"- Destination: {s['destination']}")
    if s.get("dates"):
        lines.append(f"- Dates: {s['dates']}")
    if s.get("number_of_days"):
        lines.append(f"- Duration: {s['number_of_days']} days")
    if s.get("hotel"):
        lines.append(f"- Selected Hotel: {s['hotel']}")
    if s.get("hotel_budget"):
        lines.append(f"- Hotel Budget: ₹{s['hotel_budget']}/night")
    if s.get("hotel_total_budget"):
        lines.append(f"- Total Accommodation Budget: ₹{s['hotel_total_budget']}")
    if s.get("trip_budget"):
        lines.append(f"- Whole-Trip Budget: ₹{s['trip_budget']}")
    for field in ("interests", "dietary_preferences", "explicit_fields", "derived_freshness"):
        lines.append(f"- {field}: {getattr(state, field, s.get(field))}")
    if s.get("travel_mode"):
        lines.append(f"- Travel Mode: {s['travel_mode']}")
    if s.get("places"):
        lines.append(f"- Selected Places ({s['places_count']}): {', '.join(s['places'])}")
    if s.get("restaurants"):
        lines.append(f"- Selected Restaurants ({s['restaurants_count']}): {', '.join(s['restaurants'])}")
    if s.get("has_route"):
        lines.append("- Route: Generated")
    if s.get("has_itinerary"):
        lines.append("- Itinerary: Generated")
    return "\n".join(lines)


# --- Tool Argument Parsing Helper ---

def parse_tool_arguments(raw_args: Any) -> Tuple[bool, Union[Dict[str, Any], str]]:
    """
    Safely parses tool call arguments into a Python dictionary.
    Handles JSON strings, dicts, empty payloads, and malformed JSON.
    Returns (is_valid, parsed_dict_or_error_str).
    """
    if isinstance(raw_args, dict):
        return True, raw_args

    if isinstance(raw_args, str):
        cleaned = raw_args.strip()
        if not cleaned:
            return True, {}
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return True, parsed
            return False, "Tool arguments must be a JSON object (key-value dictionary)."
        except Exception as exc:
            return False, f"Malformed JSON in tool arguments: {str(exc)}"

    return False, f"Unsupported tool arguments type: {type(raw_args).__name__}"


# --- Agent Loop Orchestrator ---

class AgentLoop:
    """
    Multi-turn agent loop that manages the conversation flow with Gemma 4,
    detects tool calls, executes deterministic tools via execute_tool,
    feeds results back to the LLM, and terminates safely with a final response.
    """

    MAX_TOOL_ITERATIONS: int = 8

    def __init__(
        self,
        client: Optional[OpenRouterClient] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_executor: Optional[Callable[..., Dict[str, Any]]] = None,
    ):
        self.client = client or OpenRouterClient()
        self.tools = tools if tools is not None else TOOL_DEFINITIONS
        self.tool_executor = tool_executor or execute_tool

    def run(
        self,
        messages: Optional[List[Dict[str, Any]]] = None,
        user_message: Optional[str] = None,
        trip_state: Optional[TripState] = None,
        session: Optional[SessionState] = None,
        serpapi_key: Optional[str] = None,
        max_iterations: Optional[int] = None,
    ) -> AgentResult:
        """
        Executes the agent tool-calling loop until a final text response is produced
        or max_iterations is reached.

        Args:
            messages: Prior conversation messages list.
            user_message: New user message string to append.
            trip_state: Optional TripState instance to inject context into the system prompt.
            session: Optional SessionState instance containing both TripState and ConversationContext.
            serpapi_key: Optional override for SerpApi key passed to tools.
            max_iterations: Maximum loop iterations (defaults to MAX_TOOL_ITERATIONS = 8).

        Returns:
            AgentResult containing final text, list of executed tool names, iteration count,
            and updated message history.
        """
        if session is not None:
            active_trip_state: TripState = session.trip_state
            conv_ctx: Optional[ConversationContext] = session.conversation_context
        else:
            active_trip_state = trip_state if trip_state is not None else TripState()
            conv_ctx = None

        active_messages: List[Dict[str, Any]] = [dict(m) for m in (messages or [])]

        # 1. Ensure system prompt is present with state context and conversation context
        system_content = (
            SYSTEM_PROMPT
            + format_state_context(active_trip_state)
            + format_conversation_context(conv_ctx)
        )

        if not active_messages or active_messages[0].get("role") != "system":
            active_messages.insert(0, {"role": "system", "content": system_content})
        else:
            active_messages[0] = {"role": "system", "content": system_content}

        # 2. Append new user message if supplied
        if user_message and user_message.strip():
            active_messages.append({"role": "user", "content": user_message.strip()})

        # Performance timing tracking
        t_turn_start = time.perf_counter()
        llm_durations_ms: List[int] = []
        tool_durations_ms: List[int] = []
        serpapi_durations_ms: List[int] = []
        state_update_durations_ms: List[int] = []

        executed_tool_names: List[str] = []
        collected_results: Optional[Dict[str, Any]] = None
        iteration = 0
        limit = max_iterations or self.MAX_TOOL_ITERATIONS

        # 3. Multi-turn execution loop
        while iteration < limit:
            iteration += 1
            logger.info(f"[Agent] Iteration {iteration}/{limit}")

            # Send prompt + conversation history + available tools to Gemma
            t_llm_start = time.perf_counter()
            llm_response: LLMResponse = self.client.send_message(
                messages=active_messages,
                tools=self.tools,
            )
            llm_durations_ms.append(int((time.perf_counter() - t_llm_start) * 1000))

            # Check if model requested any tool calls
            tool_calls = llm_response.tool_calls

            # Case A: Model produced final natural-language response without tool calls
            if not tool_calls:
                raw_text = (llm_response.content or "").strip()
                final_text = sanitize_public_response(raw_text)
                logger.info("[Agent] Final response generated.")

                active_messages.append({
                    "role": "assistant",
                    "content": final_text,
                })

                total_turn_ms = int((time.perf_counter() - t_turn_start) * 1000)
                metrics = {
                    "total_turn_ms": total_turn_ms,
                    "llm_iterations": iteration,
                    "total_llm_ms": sum(llm_durations_ms),
                    "llm_durations_ms": llm_durations_ms,
                    "total_tool_ms": sum(tool_durations_ms),
                    "tool_durations_ms": tool_durations_ms,
                    "total_serpapi_ms": sum(serpapi_durations_ms),
                    "total_state_update_ms": sum(state_update_durations_ms),
                }
                logger.info(
                    f"[Agent Perf] Total: {total_turn_ms}ms | State: {sum(state_update_durations_ms)}ms | "
                    f"LLM ({iteration} iter): {sum(llm_durations_ms)}ms | "
                    f"Tools ({len(executed_tool_names)}): {sum(tool_durations_ms)}ms | "
                    f"SerpApi: {sum(serpapi_durations_ms)}ms"
                )

                if conv_ctx and conv_ctx.active_question is None:
                    next_q = get_next_active_question(active_trip_state)
                    if next_q:
                        conv_ctx.active_question = next_q

                return AgentResult(
                    response=final_text,
                    tool_calls=executed_tool_names,
                    iterations=iteration,
                    state_summary=active_trip_state.summary(),
                    messages=active_messages,
                    results=collected_results,
                    metrics=metrics,
                )

            # Case B: Model requested one or more tool calls
            # Append assistant's tool-call message to conversation history
            active_messages.append({
                "role": "assistant",
                "content": llm_response.content or "",
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                call_id = call.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                func_obj = call.get("function") or {}
                tool_name = func_obj.get("name") or call.get("name", "unknown_tool")
                raw_args = func_obj.get("arguments", call.get("arguments", {}))

                executed_tool_names.append(tool_name)
                logger.info(f"[Agent] Tool call: {tool_name}")

                # Capture request state version for stale-result race protection
                request_state_version = active_trip_state.state_version

                # Safely parse JSON arguments
                is_valid, parsed_args_or_err = parse_tool_arguments(raw_args)

                t_tool_start = time.perf_counter()
                tool_result: Dict[str, Any] = {}

                if not is_valid:
                    tool_result = {
                        "success": False,
                        "error": parsed_args_or_err,
                    }
                    logger.warning(f"[Agent] Tool {tool_name} argument parsing failed: {parsed_args_or_err}")
                elif tool_name == "update_trip_state":
                    # Deterministic state update
                    t_state_start = time.perf_counter()
                    try:
                        try:
                            tool_result = self.tool_executor(
                                tool_name,
                                parsed_args_or_err,
                                trip_state=active_trip_state,
                            )
                        except TypeError:
                            tool_result = self.tool_executor(
                                tool_name,
                                parsed_args_or_err,
                            )
                    except Exception as exc:
                        tool_result = {"success": False, "error": f"State update error: {str(exc)}"}
                    state_duration = int((time.perf_counter() - t_state_start) * 1000)
                    state_update_durations_ms.append(state_duration)

                    # Refresh system prompt state context immediately
                    active_messages[0] = {
                        "role": "system",
                        "content": SYSTEM_PROMPT + format_state_context(active_trip_state) + format_conversation_context(conv_ctx),
                    }
                    if conv_ctx and conv_ctx.active_question:
                        changes = tool_result.get("change_set", {}).get("changed_fields", [])
                        if conv_ctx.active_question.field in changes:
                            conv_ctx.clear_active_question()
                else:
                    # Guard 1: Destination context consistency guard
                    # If tool requests a specific destination that conflicts with current active state, discard it!
                    tool_dest = (parsed_args_or_err.get("destination") or "").strip().lower() if isinstance(parsed_args_or_err, dict) else ""
                    curr_dest = (active_trip_state.destination or "").strip().lower()

                    if tool_name in {"search_hotels", "search_places", "search_restaurants"} and curr_dest and tool_dest and tool_dest != curr_dest:
                        tool_result = {
                            "success": False,
                            "discarded": True,
                            "error": f"Search destination '{tool_dest}' does not match current trip destination '{curr_dest}'. Stale search discarded.",
                        }
                        logger.warning(f"[Agent] Discarding tool call for stale destination '{tool_dest}' (current: '{curr_dest}')")
                    else:
                        try:
                            tool_result = self.tool_executor(
                                tool_name,
                                parsed_args_or_err,
                                api_key=serpapi_key,
                            )
                        except Exception as exc:
                            tool_result = {
                                "success": False,
                                "error": f"Tool execution error: {str(exc)}",
                            }
                            logger.error(f"[Agent] Tool {tool_name} execution raised exception: {exc}")

                        # Guard 2: Stale version protection (version race guard)
                        # If state was updated concurrently or version changed, discard tool results
                        if tool_name in {"search_hotels", "search_places", "search_restaurants", "optimize_route", "generate_itinerary"}:
                            if active_trip_state.state_version != request_state_version:
                                tool_result = {
                                    "success": False,
                                    "discarded": True,
                                    "error": f"Stale tool result discarded: state version changed from {request_state_version} to {active_trip_state.state_version}.",
                                }
                                logger.warning(
                                    f"[Agent] Discarding stale tool result ({tool_name}): "
                                    f"request version {request_state_version} != current {active_trip_state.state_version}"
                                )

                tool_duration_ms = int((time.perf_counter() - t_tool_start) * 1000)
                tool_durations_ms.append(tool_duration_ms)

                if isinstance(tool_result, dict) and tool_result.get("serpapi_time_ms"):
                    serpapi_durations_ms.append(tool_result["serpapi_time_ms"])

                logger.info(f"[Agent] Tool {tool_name} result: success={tool_result.get('success', False)} in {tool_duration_ms}ms")

                # Capture structured results: latest valid search_hotels call
                if tool_name == "search_hotels" and tool_result.get("success") and not tool_result.get("discarded"):
                    raw_hotels = tool_result.get("hotels", [])
                    sanitized_hotels = []
                    for h in raw_hotels:
                        sanitized_hotels.append({
                            "name": h.get("name"),
                            "rating": h.get("rating"),
                            "review_count": h.get("review_count"),
                            "price_per_night": h.get("price_per_night"),
                            "currency": h.get("currency", "INR"),
                            "latitude": h.get("latitude"),
                            "longitude": h.get("longitude"),
                            "thumbnail": h.get("thumbnail"),
                            "amenities": h.get("amenities", []) or [],
                            "property_token": h.get("property_token"),
                        })
                    collected_results = {
                        "hotels": sanitized_hotels
                    }
                    if conv_ctx:
                        conv_ctx.set_visible_items("hotel", sanitized_hotels)
                    if active_trip_state:
                        active_trip_state.mark_derived_valid(DerivedResource.HOTEL_DISCOVERY)

                # Capture structured results: latest valid search_places call
                if tool_name == "search_places" and tool_result.get("success") and not tool_result.get("discarded"):
                    raw_places = tool_result.get("places", [])
                    collected_results = {
                        "places": raw_places
                    }
                    if conv_ctx:
                        conv_ctx.set_visible_items("place", raw_places)
                    if active_trip_state:
                        active_trip_state.mark_derived_valid(DerivedResource.PLACE_DISCOVERY)

                # Capture structured results: latest valid search_restaurants call
                if tool_name == "search_restaurants" and tool_result.get("success") and not tool_result.get("discarded"):
                    raw_restaurants = tool_result.get("restaurants", [])
                    collected_results = {
                        "restaurants": raw_restaurants
                    }
                    if conv_ctx:
                        conv_ctx.set_visible_items("restaurant", raw_restaurants)
                    if active_trip_state:
                        active_trip_state.mark_derived_valid(DerivedResource.FOOD_DISCOVERY)

                # Append tool result message to conversation history
                active_messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": tool_name,
                    "content": json_dumps_safe(tool_result),
                })

        # If loop reached max iterations without producing a final text response:
        raise AgentLoopError(
            f"Maximum tool-call iterations exceeded ({limit})."
        )
