"""
Project Musafir — Fast-Path Intent & Active-Question Resolver (Step 12.6)
Processes high-confidence inputs with negligible local latency (0 LLM roundtrips),
preventing rate limits and latency spikes for obvious short answers and ordinals.
Falls back conservatively to LLM whenever ambiguity exists.
"""

import re
from datetime import timedelta
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from app.agent.context import SessionState, VisibleItemReference
from app.agent.state import PlanningStage
from app.agent.semantics import (
    parse_currency_amount,
    parse_duration_days,
    resolve_relative_date,
    parse_date_range,
    calculate_nights,
    calculate_nightly_hotel_budget,
)
from app.agent.extraction import extract_trip_slots
from app.agent.turn import (
    InterpretedTurn,
    ReferenceEdit,
    extract_actions,
    interpret_complete_turn,
)
from app.agent.llm_extraction import detect_fallback_trigger, merge_fallback_slots, run_fallback
from app.llm.openrouter_client import OpenRouterClient
from app.agent.resolution import (
    resolve_entity_reference,
    resolve_budget_phrase,
    build_entity_replacement_batch,
    BudgetScope,
)


class FastPathResult(BaseModel):
    """
    Result of deterministic fast-path evaluation.
    """
    matched: bool = Field(False, description="True if input was resolved with high confidence deterministically")
    confidence: str = Field("low", description="'high' | 'medium' | 'low'")
    intent: str = Field("FALLBACK_TO_LLM", description="Resolved intent classification")
    state_updates: Dict[str, Any] = Field(default_factory=dict, description="Deterministic fields to update in TripState")
    reference_resolution: Optional[Dict[str, Any]] = Field(None, description="Resolved entity card reference if applicable")
    cleared_active_question: bool = Field(False, description="True if the active question has been satisfied")
    explanation: str = Field("", description="Reasoning or description of the deterministic resolution")
    fallback_trigger: Optional[str] = None
    fallback_succeeded: Optional[bool] = None
    process_as_trip: Optional[bool] = None
    turn: Optional[InterpretedTurn] = None


# Conservative regex patterns for pure informational travel questions (non-planning)
GENERAL_QUERY_PATTERNS = [
    r"^is\s+[a-z]+\s+(?:a\s+)?good\s+time\s+to\s+visit\b",
    r"^what\s+(?:is|are)\s+the\s+best\s+(?:time|months?|season)\s+to\s+visit\b",
    r"^(?:how\s+is\s+the\s+weather|what\s+is\s+the\s+weather)\s+in\b",
    r"^best\s+(?:time|months?|season)\s+to\s+visit\b",
    r"^(?:can\s+you\s+tell\s+me\s+about|tell\s+me\s+about)\s+[a-z\s]+history\b",
]


def resolve_fast_path(
    user_message: str,
    session: SessionState,
    llm_client: Optional[OpenRouterClient] = None,
) -> FastPathResult:
    if re.search(r"\b(?:more relaxed|slower pace|less crowded|same budget|best places)\b", user_message.lower()):
        return FastPathResult(matched=False, explanation="The complete request requires semantic interpretation.")
    entity_command = re.match(r"^(?:remove|drop|replace|swap|substitute|change|switch|mark|must visit|keep|pick|select)\b", user_message.lower())
    compound_change = re.search(
        r"[,;]|\b(?:days?|walking|driving|cycling|transit|budget)\b|\b(?:and|then)\b.*\b(?:find|search|show|build|create|route|itinerary|plan)\b",
        user_message.lower(),
    )
    entity_result = None
    entity_clause = re.split(
        r"[,;](?!\d)|\s+(?:and|then)\s+(?=(?:find|search|show|build|rebuild|create|generate|plan|increase|decrease|reduce|extend|change|switch|update|make|set|add|remove|select|pick|budget|hotel budget|trip budget|rs|inr|one|two|three|four|five|walking|driving|cycling|transit)\b|[₹$]|\d)",
        user_message,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    compound_change = compound_change or entity_clause != user_message
    if entity_command:
        entity_result = _resolve_single_intent(entity_clause, session, None)
        if entity_result.matched and not compound_change:
            return entity_result
    slot_message = (
        user_message[len(entity_clause):]
        if entity_command and entity_clause != user_message
        else user_message
    )
    turn = interpret_complete_turn(user_message, session, slot_message=slot_message)
    if not turn and entity_result and entity_result.matched and entity_clause != user_message:
        turn = InterpretedTurn(requested_actions=extract_actions(user_message))
    if turn:
        replacement = re.fullmatch(
            r"(?:replace|swap|substitute)\s+(.+?)\s+(?:with|for)\s+(.+)",
            entity_clause, re.IGNORECASE,
        )
        hotel_change = re.fullmatch(
            r"(?:change|switch|update|swap)\s+(?:my\s+|the\s+)?hotel\s+(?:to|for)\s+(.+)",
            entity_clause, re.IGNORECASE,
        )
        if entity_command and compound_change and replacement:
            target = (
                (entity_result.reference_resolution or {}).get("target", {})
                if entity_result else {}
            )
            entity_type = target.get("entity_type", "place")
            turn.references = [
                ReferenceEdit(operation="remove", entity_type=entity_type, reference=replacement.group(1).strip()),
                ReferenceEdit(operation="select", entity_type=entity_type, reference=replacement.group(2).strip()),
                *turn.references,
            ]
        elif entity_command and compound_change and hotel_change:
            turn.references = [
                ReferenceEdit(operation="select", entity_type="hotel", reference=hotel_change.group(1).strip()),
                *turn.references,
            ]
        elif entity_result and entity_result.matched and not turn.references:
            turn.updates = {**entity_result.state_updates, **turn.updates}
        extracted = extract_trip_slots(slot_message, session.trip_state)
        trigger = detect_fallback_trigger(user_message, extracted, session.trip_state)
        fallback = None
        short_destination = bool(extracted.destination and re.fullmatch(r"(?:to\s+|in\s+|actually\s+|actually\s+make\s+it\s+|make\s+it\s+|switch\s+to\s+|change\s+to\s+)?" + re.escape(extracted.destination.lower()) + r"(?:\s+instead)?", user_message.strip().lower()))
        destination_answer = session.conversation_context.active_question and session.conversation_context.active_question.field == "destination"
        implicit_duration = bool(trigger and extracted.number_of_days is None and re.search(r"\b(?:short getaway|free (?:sunday|saturday)|couple|several|longer|shorter)\b", user_message.lower()))
        if trigger and (trigger.suspicious_fields or implicit_duration) and not (trigger.checks == ["missing_trip_context"] and (short_destination or destination_answer)):
            if llm_client:
                fallback = run_fallback(user_message, extracted, trigger, llm_client, session=session)
            if fallback and fallback.succeeded:
                if fallback.process_as_trip is False:
                    return FastPathResult(matched=True, intent="NON_TRIP_QUERY", process_as_trip=False)
                merged = merge_fallback_slots(extracted, fallback.slots, trigger.suspicious_fields)
                corrected_updates = merged.high_confidence_updates()
                action_requirements = {
                    field: turn.updates[field]
                    for field in ("accommodation_required", "accommodation_booked")
                    if field in turn.updates
                }
                turn.updates = {**action_requirements, **corrected_updates}
                turn.updates["explicit_fields"] = list(turn.updates)
                for field in type(fallback.semantics).model_fields:
                    value = getattr(fallback.semantics, field)
                    if value:
                        setattr(turn, field, value)
            else:
                turn.clarification = "How many days should the trip be?" if implicit_duration else "Could you clarify the destination or accommodation change you want?"
                if implicit_duration:
                    session.conversation_context.set_active_question(
                        "number_of_days", "number", "trip", turn.clarification
                    )
        intent = "PLAN_TRIP" if ("destination" in turn.updates or "number_of_days" in turn.updates) else "UPDATE_FIELD"
        if session.trip_state.number_of_days and "number_of_days" in turn.updates and "destination" not in turn.updates:
            intent = "UPDATE_DURATION"
        if not (set(turn.updates) - {"explicit_fields", "extracted_confidence"}) and turn.requested_actions:
            intent = turn.requested_actions[0]
        if "number_of_days" in turn.updates and "number_of_nights" not in turn.updates:
            turn.updates["number_of_nights"] = max(0, turn.updates["number_of_days"] - 1)
        if (
            "hotel_total_budget" in turn.updates
            and "hotel_budget" not in turn.updates
            and "hotel_budget" not in session.trip_state.explicit_fields
        ):
            nights = turn.updates.get("number_of_nights", session.trip_state.number_of_nights)
            if nights:
                turn.updates["hotel_budget"] = round(turn.updates["hotel_total_budget"] / nights, 2)
        updates = dict(turn.updates)
        if turn.duration_delta is not None and session.trip_state.number_of_days:
            intent = "UPDATE_DURATION"
            updates["number_of_days"] = session.trip_state.number_of_days + turn.duration_delta
        reference = None
        if intent == "SEARCH_FOOD":
            reference = {"category": turn.food_category or "restaurant", "meal_type": turn.meal_type, "anchor": turn.location_anchor}
        active = session.conversation_context.active_question
        return FastPathResult(matched=True, confidence="high", intent=intent, state_updates=updates,
                              reference_resolution=reference,
                              cleared_active_question=bool(active and active.field in turn.updates),
                              turn=turn, fallback_trigger=trigger.category.value if fallback else None,
                              fallback_succeeded=fallback.succeeded if fallback else None)
    result = _resolve_single_intent(user_message, session, None)
    if not result.matched and llm_client is not None:
        result = _resolve_single_intent(user_message, session, llm_client)
    if result.matched and result.intent in {"PLAN_TRIP", "ANSWER_ACTIVE_QUESTION", "UPDATE_FIELD", "UPDATE_BUDGET"}:
        actions = ["PLAN_TRIP"] if "destination" in result.state_updates and not session.trip_state.destination else []
        if result.turn is None:
            result.turn = InterpretedTurn(updates=result.state_updates, requested_actions=actions)
    return result


def _resolve_single_intent(
    user_message: str,
    session: SessionState,
    llm_client: Optional[OpenRouterClient] = None,
) -> FastPathResult:
    """
    Evaluates user message against active session context using conservative deterministic rules.
    Returns FastPathResult with matched=True only when high confidence is established.
    """
    if not user_message or not str(user_message).strip():
        return FastPathResult(matched=False, confidence="low", intent="FALLBACK_TO_LLM")

    clean = re.sub(r"\(.*?\)", " ", user_message.strip()).strip()
    clean_lower = clean.lower()
    ctx = session.conversation_context
    trip = session.trip_state

    # -------------------------------------------------------------------------
    # -1. Explicit Route Request Trigger
    # -------------------------------------------------------------------------
    route_patterns = [
        r"^(?:build|rebuild|create|generate|plan|make|optimize|show|calculate|update|recalculate)\s+(?:the\s+|a\s+|our\s+)?(?:new\s+)?(?:route|trip\s+route)\b",
        r"^(?:build\s+route|optimize\s+route|plan\s+route|generate\s+route|rebuild\s+route|recalculate\s+route|update\s+route)\b",
        r"^route\s+please\b",
    ]
    if any(re.search(p, clean_lower) for p in route_patterns):
        return FastPathResult(
            matched=True,
            confidence="high",
            intent="ROUTE_REQUEST",
            state_updates={},
            explanation="User explicitly requested route construction",
        )

    # -------------------------------------------------------------------------
    # -0.9. Explicit Itinerary Request Trigger
    # -------------------------------------------------------------------------
    itinerary_patterns = [
        r"^(?:build|rebuild|create|generate|plan|make|show|calculate|update)\s+(?:the\s+|a\s+|our\s+)?(?:new\s+)?(?:itinerary|schedule|timetable|daily\s+plan)\b",
        r"^(?:build\s+itinerary|plan\s+itinerary|generate\s+itinerary|rebuild\s+itinerary|update\s+itinerary)\b",
        r"^itinerary\s+please\b",
    ]
    if any(re.search(p, clean_lower) for p in itinerary_patterns):
        return FastPathResult(
            matched=True,
            confidence="high",
            intent="ITINERARY_REQUEST",
            state_updates={},
            explanation="User explicitly requested itinerary synthesis",
        )

    # -------------------------------------------------------------------------
    # -0.88. Pending Clarification Answer Resolution
    # -------------------------------------------------------------------------
    if ctx.active_question and ctx.active_question.scope == "clarification":
        clar_field = ctx.active_question.field
        options = ctx.last_mentioned_entities.get("clarification_options", [])

        if clar_field == "hotel":
            resolved_hotel = None
            ord_map = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "last": -1}
            for w, idx in ord_map.items():
                if re.search(rf"\b{w}\b", clean_lower):
                    if options and (idx < len(options) or idx == -1):
                        resolved_hotel = options[idx]
                        break
                    elif ctx.visible_hotels:
                        target_card = ctx.visible_hotels[-1] if idx == -1 else next((h for h in ctx.visible_hotels if h.index == idx + 1), None)
                        if target_card:
                            resolved_hotel = target_card.name
                            break

            if not resolved_hotel and options:
                stopwords = {"change", "hotel", "hotels", "place", "places", "restaurant", "restaurants", "cafe", "cafes", "the", "one", "to", "with", "instead", "switch", "choose", "pick", "take", "want", "select", "near", "property"}
                query_tokens = [w for w in clean_lower.split() if w not in stopwords and len(w) >= 3]
                for opt in options:
                    opt_lower = opt.lower()
                    if clean_lower in opt_lower or opt_lower in clean_lower:
                        resolved_hotel = opt
                        break
                    if query_tokens and any(t in opt_lower for t in query_tokens):
                        resolved_hotel = opt
                        break

            if not resolved_hotel and ctx.visible_hotels:
                ref = ctx.resolve_item_reference(clean_lower, entity_type="hotel")
                if ref:
                    resolved_hotel = ref.name

            if resolved_hotel:
                hotel_payload = {"name": resolved_hotel}
                for vh in ctx.visible_hotels:
                    if vh.name.lower() == resolved_hotel.lower() and vh.extra_data:
                        hotel_payload = vh.extra_data
                        break
                return FastPathResult(
                    matched=True,
                    confidence="high",
                    intent="CHANGE_HOTEL",
                    state_updates={"hotel_selection": hotel_payload},
                    cleared_active_question=True,
                    explanation=f"Resolved hotel clarification to '{resolved_hotel}'",
                )

        elif clar_field in {"place", "attraction"}:
            resolved_place = None
            ord_map = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "last": -1}
            for w, idx in ord_map.items():
                if re.search(rf"\b{w}\b", clean_lower):
                    if options and (idx < len(options) or idx == -1):
                        resolved_place = options[idx]
                        break
                    elif ctx.visible_places:
                        target_card = ctx.visible_places[-1] if idx == -1 else next((p for p in ctx.visible_places if p.index == idx + 1), None)
                        if target_card:
                            resolved_place = target_card.name
                            break

            if not resolved_place and options:
                stopwords = {"change", "place", "places", "attraction", "attractions", "sight", "sights", "the", "one", "to", "with", "instead", "switch", "choose", "pick", "take", "want", "select", "visit"}
                query_tokens = [w for w in clean_lower.split() if w not in stopwords and len(w) >= 3]
                for opt in options:
                    opt_lower = opt.lower()
                    if clean_lower in opt_lower or opt_lower in clean_lower:
                        resolved_place = opt
                        break
                    if query_tokens and any(t in opt_lower for t in query_tokens):
                        resolved_place = opt
                        break

            if not resolved_place and ctx.visible_places:
                ref = ctx.resolve_item_reference(clean_lower, entity_type="place")
                if ref:
                    resolved_place = ref.name

            if resolved_place:
                place_payload = {"name": resolved_place}
                for vp in ctx.visible_places:
                    if vp.name.lower() == resolved_place.lower() and vp.extra_data:
                        place_payload = vp.extra_data
                        break
                return FastPathResult(
                    matched=True,
                    confidence="high",
                    intent="SELECT_ITEM",
                    state_updates={"selected_places": [place_payload]},
                    cleared_active_question=True,
                    explanation=f"Resolved place clarification to '{resolved_place}'",
                )

        elif clar_field in {"restaurant", "cafe"}:
            resolved_food = None
            ord_map = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "last": -1}
            for w, idx in ord_map.items():
                if re.search(rf"\b{w}\b", clean_lower):
                    if options and (idx < len(options) or idx == -1):
                        resolved_food = options[idx]
                        break
                    elif ctx.visible_restaurants:
                        target_card = ctx.visible_restaurants[-1] if idx == -1 else next((r for r in ctx.visible_restaurants if r.index == idx + 1), None)
                        if target_card:
                            resolved_food = target_card.name
                            break

            if not resolved_food and options:
                stopwords = {"change", "restaurant", "restaurants", "cafe", "cafes", "food", "eat", "dining", "the", "one", "to", "with", "instead", "switch", "choose", "pick", "take", "want", "select"}
                query_tokens = [w for w in clean_lower.split() if w not in stopwords and len(w) >= 3]
                for opt in options:
                    opt_lower = opt.lower()
                    if clean_lower in opt_lower or opt_lower in clean_lower:
                        resolved_food = opt
                        break
                    if query_tokens and any(t in opt_lower for t in query_tokens):
                        resolved_food = opt
                        break

            if not resolved_food and ctx.visible_restaurants:
                ref = ctx.resolve_item_reference(clean_lower, entity_type="restaurant")
                if ref:
                    resolved_food = ref.name

            if resolved_food:
                food_payload = {"name": resolved_food}
                is_cafe = clar_field == "cafe" or "cafe" in resolved_food.lower()
                for vr in ctx.visible_restaurants:
                    if vr.name.lower() == resolved_food.lower() and vr.extra_data:
                        food_payload = vr.extra_data
                        break
                field_key = "selected_cafes" if is_cafe else "selected_restaurants"
                return FastPathResult(
                    matched=True,
                    confidence="high",
                    intent="SELECT_ITEM",
                    state_updates={field_key: [food_payload]},
                    cleared_active_question=True,
                    explanation=f"Resolved food clarification to '{resolved_food}'",
                )

        elif clar_field == "budget":
            amount = ctx.last_mentioned_entities.get("clarification_amount")
            if amount is not None:
                if re.search(r"\b(nightly|per\s*night|night|each\s*night|/night)\b", clean_lower):
                    return FastPathResult(
                        matched=True,
                        confidence="high",
                        intent="UPDATE_BUDGET",
                        state_updates={"hotel_budget": float(amount)},
                        cleared_active_question=True,
                        explanation=f"Resolved nightly hotel budget of ₹{amount:,.0f}/night",
                    )
                elif re.search(r"\b(hotel|total\s*hotel|for\s*hotel|hotel\s*stay)\b", clean_lower):
                    updates = {"hotel_total_budget": float(amount)}
                    nights = trip.number_of_nights
                    if nights is None and trip.number_of_days and trip.number_of_days > 1:
                        nights = trip.number_of_days - 1
                    if nights and nights > 0:
                        updates["hotel_budget"] = float(amount) / nights
                    return FastPathResult(
                        matched=True,
                        confidence="high",
                        intent="UPDATE_BUDGET",
                        state_updates=updates,
                        cleared_active_question=True,
                        explanation=f"Resolved total hotel budget of ₹{amount:,.0f}",
                    )
                elif re.search(r"\b(trip|whole\s*trip|entire\s*trip|overall)\b", clean_lower):
                    return FastPathResult(
                        matched=True,
                        confidence="high",
                        intent="UPDATE_BUDGET",
                        state_updates={"trip_budget": float(amount)},
                        cleared_active_question=True,
                        explanation=f"Resolved entire trip budget of ₹{amount:,.0f}",
                    )

    # -------------------------------------------------------------------------
    # -0.87. Travel Mode Change (Stand-alone or with route rebuild)
    # -------------------------------------------------------------------------
    mode_match = re.search(
        r"^(?:switch|change|set|update|make\s+it)\s+(?:travel\s+mode\s+to\s+|mode\s+to\s+|to\s+)?(driving|walking|transit|bicycling|two_wheeler|car|cab|taxi|bike)\b",
        clean_lower
    )
    if mode_match:
        raw_m = mode_match.group(1)
        mode_map = {
            "driving": "driving", "car": "driving", "cab": "driving", "taxi": "driving",
            "walking": "walking",
            "transit": "transit",
            "bicycling": "bicycling", "bike": "two_wheeler", "two_wheeler": "two_wheeler",
        }
        target_mode = mode_map.get(raw_m, raw_m)
        has_route = trip.current_route is not None
        rebuild_route = has_route or bool(re.search(r"\b(?:rebuild|build|optimize|recalculate)\s+(?:the\s+)?route\b", clean_lower))
        if rebuild_route:
            return FastPathResult(
                matched=True,
                confidence="high",
                intent="UPDATE_FIELD_AND_REBUILD_ROUTE",
                state_updates={"travel_mode": target_mode},
                explanation=f"Changed travel mode to '{target_mode}' and rebuilding route",
            )
        else:
            return FastPathResult(
                matched=True,
                confidence="high",
                intent="UPDATE_TRAVEL_MODE",
                state_updates={"travel_mode": target_mode},
                explanation=f"Changed travel mode to '{target_mode}'",
            )

    # -------------------------------------------------------------------------
    # -0.86. Must-Visit / Required Stop Designator (Milestone 3, Batch 3)
    # -------------------------------------------------------------------------
    req_match = re.search(
        r"\b(?:must\s+visit|require|make\s+(?:it\s+)?required|designate\s+as\s+required|mark\s+as\s+required|keep)\s+(.+)",
        clean_lower
    )
    if req_match:
        target_stop_raw = req_match.group(1).strip()
        target_stop_raw = re.sub(r"\b(?:as\s+required|as\s+must-visit|please|the)\b", "", target_stop_raw).strip()
        matching_stop = None
        for p in trip.selected_places:
            if p.name.lower() == target_stop_raw.lower() or target_stop_raw.lower() in p.name.lower() or p.name.lower() in target_stop_raw.lower():
                matching_stop = p.name
                break
        if not matching_stop:
            for f in trip.selected_food:
                if f.name.lower() == target_stop_raw.lower() or target_stop_raw.lower() in f.name.lower() or f.name.lower() in target_stop_raw.lower():
                    matching_stop = f.name
                    break

        if matching_stop:
            return FastPathResult(
                matched=True,
                confidence="high",
                intent="MARK_STOP_REQUIRED",
                state_updates={"mark_required_stops": [matching_stop]},
                reference_resolution={"name": matching_stop, "priority": "REQUIRED"},
                explanation=f"Designated '{matching_stop}' as a REQUIRED / must-visit stop",
            )

    # -------------------------------------------------------------------------
    # -0.85. Compound Mode Change + Route Rebuild Trigger
    # -------------------------------------------------------------------------
    if re.search(r"\b(?:rebuild|build|optimize|recalculate)\s+(?:the\s+)?route\b", clean_lower):
        compound_mode = None
        for kw, target_mode in [
            ("driving", "driving"), ("drive", "driving"), ("car", "driving"), ("cab", "driving"), ("taxi", "driving"),
            ("walking", "walking"), ("walk", "walking"),
            ("transit", "transit"), ("train", "transit"), ("bus", "transit"), ("metro", "transit"),
            ("two_wheeler", "two_wheeler"), ("two wheeler", "two_wheeler"), ("bike", "two_wheeler"),
        ]:
            if re.search(rf"\b(?:to\s+|by\s+|with\s+)?{kw}\b", clean_lower):
                compound_mode = target_mode
                break

        if compound_mode:
            return FastPathResult(
                matched=True,
                confidence="high",
                intent="UPDATE_FIELD_AND_REBUILD_ROUTE",
                state_updates={"travel_mode": compound_mode},
                explanation=f"Changed travel mode to '{compound_mode}' and requested route rebuild",
            )


    # -------------------------------------------------------------------------
    # -0.8. Food Discovery Request Trigger
    # -------------------------------------------------------------------------
    food_search_patterns = [
        r"^(?:find|show|search|recommend|suggest|get|look\s+for)\s+(?:me\s+)?(?:some\s+)?(?:good\s+|cheap\s+|veg\s+|vegetarian\s+)?(?:restaurants|cafes|cafe|places\s+to\s+eat|food|lunch|dinner|breakfast)\b",
        r"^(?:restaurants|cafes|food|lunch|dinner|breakfast)\s+(?:near|around|along)\b",
        r"^(?:show|find)\s+(?:cafes|restaurants|food|lunch|dinner)\b",
        r"\b(?:find|show|search)\s+(?:lunch|dinner|breakfast|food|restaurants|cafes)\s+(?:near|along)\s+(?:my\s+|the\s+)?route\b",
        r"\b(?:lunch|dinner|food)\s+near\s+(?:my\s+|the\s+)?route\b",
    ]
    if any(re.search(p, clean_lower) for p in food_search_patterns):
        is_cafe = "cafe" in clean_lower or "coffee" in clean_lower
        meal = "lunch" if "lunch" in clean_lower else ("dinner" if "dinner" in clean_lower else ("breakfast" if "breakfast" in clean_lower else None))

        anchor = None
        if "near hotel" in clean_lower or "near my hotel" in clean_lower or "around hotel" in clean_lower:
            anchor = "hotel"
        elif "near route" in clean_lower or "along route" in clean_lower or "near my route" in clean_lower or "along the route" in clean_lower:
            anchor = "route"
        else:
            anchor_match = re.search(r"\b(?:near|around)\s+(?:the\s+)?([a-z0-9\s]+)$", clean_lower)
            if anchor_match:
                anchor = anchor_match.group(1).strip()

        return FastPathResult(
            matched=True,
            confidence="high",
            intent="SEARCH_FOOD",
            state_updates={"planning_stage": PlanningStage.FOOD_DISCOVERY},
            reference_resolution={"category": "cafe" if is_cafe else "restaurant", "meal_type": meal, "anchor": anchor},
            explanation="User requested food or café discovery",
        )

    # -------------------------------------------------------------------------
    # -0.5. Explicit Item Rejection Trigger
    # -------------------------------------------------------------------------
    rejection_entity = None
    if trip.planning_stage in {PlanningStage.FOOD_DISCOVERY, PlanningStage.FOOD_SELECTION} or ctx.visible_restaurants:
        rejection_entity = "restaurant"
    elif trip.hotel_selection:
        rejection_entity = "place"

    rejection = ctx.resolve_item_rejection(clean_lower, entity_type=rejection_entity)
    if rejection:
        name = rejection.name if isinstance(rejection, VisibleItemReference) else str(rejection)
        item_id = rejection.id if isinstance(rejection, VisibleItemReference) else None

        is_cafe = False
        is_restaurant = False
        if isinstance(rejection, VisibleItemReference):
            if rejection.entity_type == "cafe" or (rejection.extra_data and "cafe" in str(rejection.extra_data.get("category", "")).lower()):
                is_cafe = True
            elif rejection.entity_type == "restaurant":
                is_restaurant = True

        if not is_cafe and not is_restaurant:
            if any(c.name.lower() == name.lower() for c in trip.selected_cafes):
                is_cafe = True
            elif any(r.name.lower() == name.lower() for r in trip.selected_restaurants):
                is_restaurant = True
            elif "cafe" in clean_lower:
                is_cafe = True
            elif "restaurant" in clean_lower or "food" in clean_lower:
                is_restaurant = True
            elif trip.planning_stage in {PlanningStage.FOOD_DISCOVERY, PlanningStage.FOOD_SELECTION}:
                is_restaurant = True

        if is_cafe:
            state_key = "rejected_cafes"
            desc = f"User rejected cafe '{name}'"
        elif is_restaurant:
            state_key = "rejected_restaurants"
            desc = f"User rejected restaurant '{name}'"
        else:
            state_key = "rejected_places"
            desc = f"User rejected place '{name}'"

        ref_res = {"name": name, "id": item_id} if item_id else {"name": name}
        if re.search(r"\b(?:rebuild|build|optimize|recalculate)\s+(?:the\s+)?route\b", clean_lower):
            ref_res["rebuild_route"] = True

        return FastPathResult(
            matched=True,
            confidence="high",
            intent="REJECT_ITEM",
            reference_resolution=ref_res,
            state_updates={state_key: [name]},
            explanation=desc,
        )

    # -------------------------------------------------------------------------
    # -0.4. Change Hotel Trigger & Compound Hotel + Duration
    # -------------------------------------------------------------------------
    compound_hotel_dur = re.search(
        r"\b(?:change|switch|update)\s+(?:my\s+|the\s+)?hotel\s+to\s+([^,]+?)\s+and\s+(?:make\s+(?:it\s+)?|set\s+duration\s+to\s+)?(\d+)\s+days?\b",
        clean_lower,
    )
    if compound_hotel_dur:
        h_target = compound_hotel_dur.group(1).strip()
        new_days = int(compound_hotel_dur.group(2))
        h_res = resolve_entity_reference(h_target, trip, ctx, entity_type="hotel")
        if h_res.is_ambiguous and h_res.clarification:
            return FastPathResult(
                matched=True,
                confidence="high",
                intent="CLARIFICATION",
                reference_resolution={"clarification": h_res.clarification.model_dump()},
                explanation="Ambiguous hotel reference in compound change",
            )
        h_data = h_res.resolved_entity.data if (h_res.resolved_entity and h_res.resolved_entity.data) else {"name": h_target.title()}
        return FastPathResult(
            matched=True,
            confidence="high",
            intent="COMPOUND_MUTATION",
            state_updates={"hotel_selection": h_data, "number_of_days": new_days},
            explanation=f"Changed hotel to '{h_data.get('name')}' and duration to {new_days} days",
        )

    change_hotel_match = re.search(r"\b(?:change|switch|update|swap)\s+(?:my\s+|the\s+)?hotel(?:\s+to\s+|\s+for\s+)(.+)", clean_lower)
    if change_hotel_match:
        target_hotel_str = change_hotel_match.group(1).strip()
        rebuild_route = False
        if re.search(r"\b(?:and\s+)?(?:rebuild|build|optimize|recalculate)\s+(?:the\s+)?route\b", target_hotel_str):
            rebuild_route = True
            target_hotel_str = re.sub(r"\b(?:and\s+)?(?:rebuild|build|optimize|recalculate)\s+(?:the\s+)?route\b", "", target_hotel_str).strip()

        h_res = resolve_entity_reference(target_hotel_str, trip, ctx, entity_type="hotel")
        if h_res.is_ambiguous and h_res.clarification:
            return FastPathResult(
                matched=True,
                confidence="high",
                intent="CLARIFICATION",
                reference_resolution={"clarification": h_res.clarification.model_dump()},
                explanation="Ambiguous hotel reference requires user clarification",
            )

        h_data = h_res.resolved_entity.data if (h_res.resolved_entity and h_res.resolved_entity.data) else {"name": target_hotel_str.title()}
        return FastPathResult(
            matched=True,
            confidence="high",
            intent="CHANGE_HOTEL",
            state_updates={"hotel_selection": h_data},
            reference_resolution={"hotel": h_data, "rebuild_route": rebuild_route},
            explanation=f"Changed hotel to '{h_data.get('name')}' (rebuild_route={rebuild_route})",
        )

    # -------------------------------------------------------------------------
    # -0.3. Entity-Specific Replacement Trigger
    # -------------------------------------------------------------------------
    replace_match = re.search(r"\b(?:replace|swap|substitute)\s+(.+?)\s+(?:with|for)\s+(.+)", clean_lower)
    if replace_match:
        orig_str = replace_match.group(1).strip()
        new_str = replace_match.group(2).strip()
        rebuild_route = False
        if re.search(r"\b(?:and\s+)?(?:rebuild|build|optimize|recalculate)\s+(?:the\s+)?route\b", new_str):
            rebuild_route = True
            new_str = re.sub(r"\b(?:and\s+)?(?:rebuild|build|optimize|recalculate)\s+(?:the\s+)?route\b", "", new_str).strip()

        target_res = resolve_entity_reference(orig_str, trip, ctx)
        if target_res.is_ambiguous and target_res.clarification:
            return FastPathResult(
                matched=True,
                confidence="high",
                intent="CLARIFICATION",
                reference_resolution={"clarification": target_res.clarification.model_dump()},
                explanation="Ambiguous replacement target requires user clarification",
            )

        if target_res.resolved_entity:
            new_res = resolve_entity_reference(new_str, trip, ctx, entity_type=target_res.resolved_entity.entity_type)
            new_payload = new_res.resolved_entity.data if (new_res.resolved_entity and new_res.resolved_entity.data) else {"name": new_str.title()}
            batch = build_entity_replacement_batch(target_res.resolved_entity, new_payload, source_message=user_message)
            return FastPathResult(
                matched=True,
                confidence="high",
                intent="REPLACE_ITEM",
                reference_resolution={
                    "target": target_res.resolved_entity.model_dump(),
                    "replacement": new_payload,
                    "batch": batch.model_dump(),
                    "rebuild_route": rebuild_route,
                },
                explanation=f"Replaced {target_res.resolved_entity.name} with {new_payload.get('name')}",
            )

    # -------------------------------------------------------------------------
    # -0.2. Dynamic Duration Modification Trigger
    # -------------------------------------------------------------------------
    duration_mod_match = re.search(
        r"\b(?:make\s+(?:it\s+|the\s+trip\s+)?|reduce\s+(?:it\s+|the\s+trip\s+)?(?:to\s+|by\s+)?|add\s+|extend\s+(?:the\s+trip\s+by\s+)?|increase\s+duration\s+to\s+)(\d+|another|a|one)\s+days?\b",
        clean_lower,
    )
    if duration_mod_match and trip.number_of_days is not None:
        raw_num = duration_mod_match.group(1)
        num = 1 if raw_num in ("another", "a", "one") else int(raw_num)
        rebuild_itin = bool(re.search(r"\b(?:update|rebuild|generate)\s+(?:the\s+)?itinerary\b", clean_lower))

        if re.search(r"\badd\b|\bextend\b", clean_lower):
            new_days = trip.number_of_days + num
        elif re.search(r"\breduce\s+(?:it\s+|the\s+trip\s+)?by\b", clean_lower):
            new_days = max(1, trip.number_of_days - num)
        else:
            new_days = max(1, num)

        return FastPathResult(
            matched=True,
            confidence="high",
            intent="UPDATE_DURATION",
            state_updates={"number_of_days": new_days},
            reference_resolution={"rebuild_itinerary": rebuild_itin, "new_days": new_days},
            explanation=f"Updated trip duration from {trip.number_of_days} to {new_days} days",
        )

    # -------------------------------------------------------------------------
    # 0. Conservative Check: Open-ended, complex, or contrastive phrases delegate to LLM
    # -------------------------------------------------------------------------
    if re.search(r"\broute\b", clean_lower):
        # Allow compound rebuild route if mode or replacement matched earlier
        pass

    complex_nuance_patterns = [
        r"\b(best\s+places|suggest|recommend|somewhere|changed my mind|trip under|not the|not that)\b",
    ]
    if any(re.search(p, clean_lower) for p in complex_nuance_patterns):
        return FastPathResult(
            matched=False,
            confidence="low",
            intent="FALLBACK_TO_LLM",
            explanation="Complex, nuanced, or contrastive utterance delegates to LLM",
        )

    # -------------------------------------------------------------------------
    # -0.1. Explicit & Dynamic Budget Modification Trigger
    # -------------------------------------------------------------------------
    dest_indicators = [r"\b(?:go\s+to|trip\s+to|travel\s+to|visiting|planning\s+a\s+trip)\b"]
    if not any(re.search(d, clean_lower) for d in dest_indicators):
        budget_patterns = [
            r"\b(?:increase|decrease|change|set|make|update|reduce|raise|lower|my)\b.*\b(?:budget|hotel\s+budget|trip\s+budget|nightly|per\s+night)\b",
            r"^(?:hotel\s+budget|trip\s+budget|budget)\b",
            r"\b(?:budget\s+is|budget\s+of|budget\s+to)\b",
            r"\b(?:can\s+spend|spend)\s+(?:₹|rs\.?|inr)?\s*\d+",
            r"^(?:increase|decrease|raise|reduce|lower)\s+(?:my\s+)?budget\b",
        ]
        if any(re.search(bp, clean_lower) for bp in budget_patterns):
            b_res = resolve_budget_phrase(clean_lower, trip)
            if b_res.is_ambiguous and b_res.clarification:
                return FastPathResult(
                    matched=True,
                    confidence="high",
                    intent="CLARIFICATION",
                    reference_resolution={"clarification": b_res.clarification.model_dump()},
                    explanation="Ambiguous budget phrase requires clarification",
                )
            elif b_res.amount and b_res.scope:
                field_name = {
                    BudgetScope.NIGHTLY_HOTEL: "hotel_budget",
                    BudgetScope.TOTAL_HOTEL: "hotel_total_budget",
                    BudgetScope.TOTAL_TRIP: "trip_budget",
                }[b_res.scope]
                return FastPathResult(
                    matched=True,
                    confidence="high",
                    intent="UPDATE_BUDGET",
                    state_updates={field_name: b_res.amount},
                    reference_resolution={"amount": b_res.amount, "scope": b_res.scope.value},
                    explanation=f"Updated {field_name} to {b_res.amount}",
                )

    # -------------------------------------------------------------------------
    # -0.05. Pure Informational Query Detector (Fast-Path Escape Hatch)
    # -------------------------------------------------------------------------
    for pat in GENERAL_QUERY_PATTERNS:
        if re.search(pat, clean_lower):
            return FastPathResult(
                matched=True,
                confidence="high",
                intent="GENERAL_TRAVEL_QUERY",
                state_updates={},
                cleared_active_question=False,
                explanation="Resolved general informational query with zero state mutations",
            )

    # -------------------------------------------------------------------------
    # 0. Multi-Slot Trip Planning & Slot Extraction (Milestone 3, M3.2)
    # -------------------------------------------------------------------------
    pivot_markers = [r"\binstead\b", r"\bswitch\s+to\b", r"\bforget\b", r"\bchange\s+to\b", r"\brather\b"]
    is_pivot = any(re.search(p, clean_lower) for p in pivot_markers)
    is_tool_search = bool(re.search(r"^(?:find|search|show|list|look\s+for)\s+(?:hotels?|places?|attractions?|restaurants?|cafes?|food)\b", clean_lower))

    if is_pivot:
        return FastPathResult(
            matched=False,
            confidence="low",
            intent="FALLBACK_TO_LLM",
            explanation="Pivot expression delegates to LLM",
        )

    if not is_tool_search:
        extracted = extract_trip_slots(clean, trip)
        fallback_trigger = detect_fallback_trigger(clean, extracted, trip)
        fallback_result = None
        if fallback_trigger and llm_client is not None:
            fallback_result = run_fallback(clean, extracted, fallback_trigger, llm_client, session=session)
            if fallback_result.succeeded and fallback_result.slots:
                extracted = merge_fallback_slots(
                    extracted,
                    fallback_result.slots,
                    fallback_result.suspicious_fields,
                )
                if fallback_result.process_as_trip is False:
                    return FastPathResult(
                        matched=True,
                        confidence="high",
                        intent="NON_TRIP_QUERY",
                        fallback_trigger=fallback_trigger.category.value,
                        fallback_succeeded=True,
                        process_as_trip=False,
                        explanation="Fallback classified the message as outside trip planning.",
                    )
                if fallback_result.semantics.model_dump(exclude_defaults=True):
                    turn = InterpretedTurn(updates=extracted.high_confidence_updates(), **fallback_result.semantics.model_dump())
                    turn.updates["explicit_fields"] = list(turn.updates)
                    return FastPathResult(matched=True, confidence="high", intent="UPDATE_FIELD", state_updates=turn.updates,
                                          turn=turn, fallback_trigger=fallback_trigger.category.value, fallback_succeeded=True)
        high_updates = extracted.high_confidence_updates()

        # Check for destination corrections e.g. "Actually make it Pune, not Goa" or "Actually make it Jaipur"
        is_correction = bool(re.search(r"\b(?:actually\s+(?:make\s+it\s+)?|make\s+it\s+|switch\s+to\s+|change\s+to\s+)[a-zA-Z]+", clean_lower))

        # Check if duration correction e.g. "actually 4 days"
        is_dur_correction = bool(re.search(r"\bactually\s+\d+", clean_lower) and "number_of_days" in high_updates)

        is_multi_slot = len(extracted.explicit_fields) >= 2
        is_slot_update = bool(
            is_correction
            or is_dur_correction
            or ("dietary_preferences" in high_updates and not trip.dietary_preferences)
            or (is_multi_slot and (not trip.destination or len(trip.messages) == 0 or is_correction))
            or (is_multi_slot and high_updates)
            or (fallback_result and fallback_result.succeeded and high_updates)
        )

        if is_slot_update and high_updates:
            if "number_of_days" in high_updates and "number_of_nights" not in high_updates:
                high_updates["number_of_nights"] = max(0, high_updates["number_of_days"] - 1)
                if "hotel_required" not in high_updates:
                    high_updates["hotel_required"] = (high_updates["number_of_nights"] > 0)

            high_updates["explicit_fields"] = list(extracted.explicit_fields)
            high_updates["extracted_confidence"] = {k: v.value for k, v in extracted.confidence.items()}

            cleared_q = (
                ctx.active_question is not None
                and ctx.active_question.field in high_updates
            )

            explanation_parts = [f"{k}={v}" for k, v in high_updates.items() if k not in ("explicit_fields", "extracted_confidence")]
            return FastPathResult(
                matched=True,
                confidence="high",
                intent="PLAN_TRIP" if ("destination" in high_updates or "number_of_days" in high_updates) else ("ANSWER_ACTIVE_QUESTION" if cleared_q else "UPDATE_FIELD"),
                state_updates=high_updates,
                cleared_active_question=cleared_q,
                explanation=f"Slot extraction resolved: {', '.join(explanation_parts)}",
                fallback_trigger=(fallback_trigger.category.value if fallback_trigger else None),
                fallback_succeeded=(fallback_result.succeeded if fallback_result else None),
                turn=InterpretedTurn(updates=high_updates, **fallback_result.semantics.model_dump()) if fallback_result and fallback_result.succeeded else None,
            )

    # -------------------------------------------------------------------------
    # 1. High-Confidence Date Range Detection (Priority 1)
    # -------------------------------------------------------------------------
    date_range = parse_date_range(clean_lower)
    if date_range is not None:
        start_d, end_d = date_range
        nights = (end_d - start_d).days
        days = nights + 1 if nights > 0 else 1
        updates = {
            "trip_start_date": start_d,
            "trip_end_date": end_d,
            "number_of_days": days,
            "number_of_nights": nights,
        }
        if trip.hotel_total_budget and nights > 0:
            rate = calculate_nightly_hotel_budget(
                total_hotel_budget=trip.hotel_total_budget,
                explicit_nights=nights,
            )
            if rate:
                updates["hotel_budget"] = rate

        cleared_q = (
            ctx.active_question is not None
            and ctx.active_question.field in {"trip_start_date", "dates", "trip_end_date", "number_of_days"}
        )
        return FastPathResult(
            matched=True,
            confidence="high",
            intent="ANSWER_ACTIVE_QUESTION" if cleared_q else "UPDATE_FIELD",
            state_updates=updates,
            cleared_active_question=cleared_q,
            explanation=f"Resolved date range {start_d.isoformat()} to {end_d.isoformat()} ({days} days, {nights} nights)",
        )

    # -------------------------------------------------------------------------
    # 2. High-Confidence Single Date / Relative Date Detection (Priority 2)
    # -------------------------------------------------------------------------
    resolved_date = resolve_relative_date(clean_lower)
    if resolved_date is not None:
        updates = {"trip_start_date": resolved_date}
        if trip.number_of_days:
            nights = max(0, trip.number_of_days - 1)
            updates["trip_end_date"] = resolved_date + timedelta(days=nights)
            updates["number_of_nights"] = nights
            if trip.hotel_total_budget and nights > 0:
                rate = calculate_nightly_hotel_budget(
                    total_hotel_budget=trip.hotel_total_budget,
                    explicit_nights=nights,
                )
                if rate:
                    updates["hotel_budget"] = rate

        cleared_q = (
            ctx.active_question is not None
            and ctx.active_question.field in {"trip_start_date", "dates", "trip_end_date"}
        )
        return FastPathResult(
            matched=True,
            confidence="high",
            intent="ANSWER_ACTIVE_QUESTION" if cleared_q else "UPDATE_FIELD",
            state_updates=updates,
            cleared_active_question=cleared_q,
            explanation=f"Resolved start date {resolved_date.isoformat()}",
        )

    # -------------------------------------------------------------------------
    # 3. High-Confidence Trip Duration / Days (Priority 3)
    # -------------------------------------------------------------------------
    if ctx.active_question is not None and (
        ctx.active_question.field in {"number_of_days", "duration"}
        or (ctx.active_question.scope == "trip" and ctx.active_question.expected_type == "number")
    ):
        days = parse_duration_days(clean_lower)
        if days is not None:
            nights = calculate_nights(number_of_days=days)
            updates = {
                "number_of_days": days,
                "number_of_nights": nights,
            }
            if trip.trip_start_date:
                updates["trip_end_date"] = trip.trip_start_date + timedelta(days=nights)
            if trip.hotel_total_budget and nights > 0:
                rate = calculate_nightly_hotel_budget(
                    total_hotel_budget=trip.hotel_total_budget,
                    explicit_nights=nights,
                )
                if rate:
                    updates["hotel_budget"] = rate

            return FastPathResult(
                matched=True,
                confidence="high",
                intent="ANSWER_ACTIVE_QUESTION",
                state_updates=updates,
                cleared_active_question=True,
                explanation=f"Resolved trip duration of {days} days ({nights} nights) from active question '{ctx.active_question.field}'",
            )

    # -------------------------------------------------------------------------
    # 4. High-Confidence Hotel Budget Semantics (Priority 4)
    # -------------------------------------------------------------------------
    is_total_budget_context = (
        (ctx.active_question and ctx.active_question.field in {"hotel_total_budget", "total_hotel_budget"})
        or bool(re.search(r"\b(total|overall|complete|full)\b", clean_lower))
    )
    is_per_night_context = (
        (ctx.active_question and ctx.active_question.field in {"hotel_budget", "hotel_budget_per_night"})
        or bool(re.search(r"\b(per night|/night|a night|nightly)\b", clean_lower))
    )

    amount = parse_currency_amount(clean_lower)
    if amount is not None:
        if is_per_night_context and not is_total_budget_context:
            cleared_q = (
                ctx.active_question is not None
                and ctx.active_question.field in {"hotel_budget", "hotel_budget_per_night"}
            )
            return FastPathResult(
                matched=True,
                confidence="high",
                intent="ANSWER_ACTIVE_QUESTION" if cleared_q else "UPDATE_FIELD",
                state_updates={"hotel_budget": amount},
                cleared_active_question=cleared_q,
                explanation=f"Resolved nightly hotel budget ceiling of ₹{amount:,.0f}/night",
            )
        elif is_total_budget_context or (ctx.active_question and ctx.active_question.scope == "accommodation"):
            updates = {"hotel_total_budget": amount}
            nights = trip.number_of_nights
            if nights is None and trip.number_of_days:
                nights = calculate_nights(number_of_days=trip.number_of_days)
            elif nights is None and trip.trip_start_date and trip.trip_end_date:
                nights = calculate_nights(start_date=trip.trip_start_date, end_date=trip.trip_end_date)

            if nights and nights > 0:
                nightly_rate = calculate_nightly_hotel_budget(
                    total_hotel_budget=amount,
                    explicit_nights=nights,
                )
                if nightly_rate:
                    updates["hotel_budget"] = nightly_rate

            cleared_q = (
                ctx.active_question is not None
                and ctx.active_question.field in {"hotel_total_budget", "total_hotel_budget", "hotel_budget"}
            )
            return FastPathResult(
                matched=True,
                confidence="high",
                intent="ANSWER_ACTIVE_QUESTION" if cleared_q else "UPDATE_FIELD",
                state_updates=updates,
                cleared_active_question=cleared_q,
                explanation=f"Resolved hotel total budget of ₹{amount:,.0f}",
            )

    # -------------------------------------------------------------------------
    # 5. Travel Mode (Priority 5)
    # -------------------------------------------------------------------------
    mode_map = {
        "car": "driving", "drive": "driving", "driving": "driving", "cab": "driving", "taxi": "driving",
        "train": "transit", "transit": "transit", "metro": "transit", "bus": "transit",
        "walk": "walking", "walking": "walking",
        "bike": "two_wheeler", "motorcycle": "two_wheeler", "two wheeler": "two_wheeler",
    }
    is_mode_question = (
        ctx.active_question is not None
        and (ctx.active_question.field == "travel_mode" or ctx.active_question.expected_type == "mode")
    )
    is_pure_mode_phrase = bool(re.match(r"^(?:switch\s+to\s+|change\s+to\s+|use\s+|by\s+|in\s+)?(?:" + "|".join(mode_map.keys()) + r")\b", clean_lower))

    if is_mode_question or is_pure_mode_phrase:
        for kw, target_mode in mode_map.items():
            if re.search(rf"\b{kw}\b", clean_lower):
                return FastPathResult(
                    matched=True,
                    confidence="high",
                    intent="ANSWER_ACTIVE_QUESTION" if is_mode_question else "UPDATE_FIELD",
                    state_updates={"travel_mode": target_mode},
                    cleared_active_question=is_mode_question,
                    explanation=f"Resolved travel mode '{target_mode}' from '{kw}'",
                )

    # -------------------------------------------------------------------------
    # 6. Confirmation (Yes / No)
    # -------------------------------------------------------------------------
    if ctx.active_question is not None:
        aq = ctx.active_question
        if aq.expected_type == "boolean" or aq.scope == "confirmation":
            if re.match(r"^(?:yes|yeah|yep|sure|ok|correct|right|do it)\b", clean_lower):
                updates = {}
                if aq.affirmation_field and aq.affirmation_value is not None:
                    updates[aq.affirmation_field] = aq.affirmation_value
                return FastPathResult(
                    matched=True,
                    confidence="high",
                    intent="CONFIRMATION_YES",
                    state_updates=updates,
                    cleared_active_question=True,
                    explanation="Confirmed with affirmation",
                )
            if re.match(r"^(?:no|nah|nope|cancel|don't|dont|never mind)\b", clean_lower):
                return FastPathResult(
                    matched=True,
                    confidence="high",
                    intent="CONFIRMATION_NO",
                    state_updates={},
                    cleared_active_question=True,
                    explanation="Declined confirmation",
                )

    # -------------------------------------------------------------------------
    # 7. Standalone Item Reference Matching (Ordinals / Card Selection)
    # -------------------------------------------------------------------------
    if ctx.visible_hotels or ctx.visible_places or ctx.visible_restaurants:
        active_entity_type = None
        if trip.planning_stage in {PlanningStage.FOOD_DISCOVERY, PlanningStage.FOOD_SELECTION} and ctx.visible_restaurants:
            active_entity_type = "restaurant"
        elif trip.hotel_selection and ctx.visible_places:
            active_entity_type = "place"
        elif ctx.visible_hotels and not trip.hotel_selection:
            active_entity_type = "hotel"
        elif ctx.visible_places:
            active_entity_type = "place"
        elif ctx.visible_restaurants:
            active_entity_type = "restaurant"

        # Check compound multi-item selection first
        multi_refs = ctx.resolve_multiple_item_references(clean_lower, entity_type=active_entity_type)
        if multi_refs and len(multi_refs) > 1:
            place_payloads = [
                r.extra_data or {"name": r.name, "data_id": r.id}
                for r in multi_refs if r.entity_type == "place"
            ]
            hotel_payload = next((r.extra_data or {"name": r.name} for r in multi_refs if r.entity_type == "hotel"), None)
            restaurant_payloads = [
                r.extra_data or {"name": r.name, "data_id": r.id}
                for r in multi_refs if r.entity_type in {"restaurant", "cafe"}
            ]
            updates: Dict[str, Any] = {}
            if place_payloads:
                updates["selected_places"] = place_payloads
            if hotel_payload:
                updates["hotel_selection"] = hotel_payload
            if restaurant_payloads:
                cafes = [p for p in restaurant_payloads if "cafe" in str(p.get("category", "")).lower()]
                rests = [p for p in restaurant_payloads if "cafe" not in str(p.get("category", "")).lower()]
                if rests:
                    updates["selected_restaurants"] = rests
                if cafes:
                    updates["selected_cafes"] = cafes

            return FastPathResult(
                matched=True,
                confidence="high",
                intent="SELECT_ITEMS",
                reference_resolution={"items": [r.model_dump() for r in multi_refs]},
                state_updates=updates,
                cleared_active_question=False,
                explanation=f"Selected {len(multi_refs)} items: {[r.name for r in multi_refs]}",
            )

        # If user explicitly specifies restaurant or cafe, check visible restaurants first
        if ("restaurant" in clean_lower or "cafe" in clean_lower or "food" in clean_lower) and ctx.visible_restaurants:
            matched_ref = ctx.resolve_item_reference(clean_lower, entity_type="restaurant")
            if matched_ref:
                is_cafe = (
                    matched_ref.entity_type == "cafe"
                    or "cafe" in matched_ref.name.lower()
                    or (matched_ref.extra_data and "cafe" in str(matched_ref.extra_data.get("category", "")).lower())
                    or "cafe" in clean_lower
                )
                field_key = "selected_cafes" if is_cafe else "selected_restaurants"
                payload = matched_ref.extra_data or {"name": matched_ref.name, "data_id": matched_ref.id}
                return FastPathResult(
                    matched=True,
                    confidence="high",
                    intent="SELECT_ITEM",
                    reference_resolution=matched_ref.model_dump(),
                    state_updates={field_key: [payload]},
                    cleared_active_question=False,
                    explanation=f"Referenced {'cafe' if is_cafe else 'restaurant'} '{matched_ref.name}' (position #{matched_ref.index})",
                )

        # Check visible hotels if hotel not yet selected, or user explicitly mentions 'hotel'
        if ctx.visible_hotels and (not trip.hotel_selection or "hotel" in clean_lower):
            matched_ref = ctx.resolve_item_reference(clean_lower, entity_type="hotel")
            if matched_ref:
                return FastPathResult(
                    matched=True,
                    confidence="high",
                    intent="SELECT_ITEM",
                    reference_resolution=matched_ref.model_dump(),
                    state_updates={"hotel_selection": matched_ref.extra_data or {"name": matched_ref.name}},
                    cleared_active_question=False,
                    explanation=f"Selected hotel '{matched_ref.name}' (position #{matched_ref.index})",
                )

        if ctx.visible_places and trip.planning_stage not in {PlanningStage.FOOD_DISCOVERY, PlanningStage.FOOD_SELECTION} and not ("restaurant" in clean_lower or "cafe" in clean_lower):
            matched_ref = ctx.resolve_item_reference(clean_lower, entity_type="place")
            if matched_ref:
                return FastPathResult(
                    matched=True,
                    confidence="high",
                    intent="SELECT_ITEM",
                    reference_resolution=matched_ref.model_dump(),
                    state_updates={"selected_places": [matched_ref.extra_data or {"name": matched_ref.name, "data_id": matched_ref.id}]},
                    cleared_active_question=False,
                    explanation=f"Referenced place '{matched_ref.name}' (position #{matched_ref.index})",
                )

        if ctx.visible_restaurants:
            matched_ref = ctx.resolve_item_reference(clean_lower, entity_type="restaurant")
            if matched_ref:
                is_cafe = (
                    matched_ref.entity_type == "cafe"
                    or (matched_ref.extra_data and "cafe" in str(matched_ref.extra_data.get("category", "")).lower())
                    or "cafe" in clean_lower
                )
                field_key = "selected_cafes" if is_cafe else "selected_restaurants"
                payload = matched_ref.extra_data or {"name": matched_ref.name, "data_id": matched_ref.id}
                return FastPathResult(
                    matched=True,
                    confidence="high",
                    intent="SELECT_ITEM",
                    reference_resolution=matched_ref.model_dump(),
                    state_updates={field_key: [payload]},
                    cleared_active_question=False,
                    explanation=f"Referenced {'cafe' if is_cafe else 'restaurant'} '{matched_ref.name}' (position #{matched_ref.index})",
                )

    # -------------------------------------------------------------------------
    # 8. High-Confidence Destination Resolution
    # -------------------------------------------------------------------------
    from app.agent.extraction import CANONICAL_DESTINATIONS
    dest_phrase_match = re.search(
        r"^(?:to\s+|i(?:'m|\s+am)?\s+(?:going|traveling|travelling|planning\s+to\s+go)\s+to|i\s+(?:would\s+like|want)\s+to\s+(?:visit|go\s+to|travel\s+to)|plan\s+(?:a\s+)?trip\s+to|trip\s+to|heading\s+to|visiting)\s+([a-zA-Z\s]{2,30})$",
        clean,
        re.IGNORECASE,
    )
    if dest_phrase_match:
        raw_d = dest_phrase_match.group(1).strip()
        extracted_dest = CANONICAL_DESTINATIONS.get(raw_d.lower(), raw_d.title())
        cleared_q = ctx.active_question is not None and ctx.active_question.field == "destination"
        return FastPathResult(
            matched=True,
            confidence="high",
            intent="ANSWER_ACTIVE_QUESTION" if cleared_q else "UPDATE_FIELD",
            state_updates={"destination": extracted_dest},
            cleared_active_question=cleared_q,
            explanation=f"Resolved destination '{extracted_dest}' from phrase",
        )

    if (ctx.active_question and ctx.active_question.field == "destination") or (not trip.destination and len(clean.split()) <= 3):
        non_destination_keywords = {
            "hotel", "hotels", "resort", "resorts", "place", "places", "restaurant", "restaurants",
            "second", "2nd", "first", "1st", "third", "3rd", "last", "option", "card", "check",
            "find", "search", "show", "tell", "give", "book", "what", "is", "where", "how", "when", "can",
            "hi", "hello", "hey", "help", "start", "reset", "info", "test", "thanks", "thank"
        }
        words_in_msg = set(re.findall(r"\b[a-zA-Z]+\b", clean_lower))
        if not (words_in_msg & non_destination_keywords):
            if not parse_currency_amount(clean_lower) and not resolve_relative_date(clean_lower) and not parse_duration_days(clean_lower):
                if re.match(r"^[a-zA-Z\s]{2,30}$", clean):
                    clean_d = re.sub(r"^(?:to|in|at)\s+", "", clean.strip(), flags=re.IGNORECASE).strip()
                    extracted_dest = CANONICAL_DESTINATIONS.get(clean_d.lower(), clean_d.title())
                    cleared_q = ctx.active_question is not None and ctx.active_question.field == "destination"
                    return FastPathResult(
                        matched=True,
                        confidence="high",
                        intent="ANSWER_ACTIVE_QUESTION" if cleared_q else "UPDATE_FIELD",
                        state_updates={"destination": extracted_dest},
                        cleared_active_question=cleared_q,
                        explanation=f"Resolved destination '{extracted_dest}'",
                    )

    # -------------------------------------------------------------------------
    # 3. Pure Informational Query Detector (Fast-Path Escape Hatch)
    # -------------------------------------------------------------------------
    for pat in GENERAL_QUERY_PATTERNS:
        if re.search(pat, clean_lower):
            return FastPathResult(
                matched=True,
                confidence="high",
                intent="GENERAL_TRAVEL_QUERY",
                state_updates={},
                cleared_active_question=False,
                explanation="Detected informational travel query; bypasses trip planning mutation",
            )

    # -------------------------------------------------------------------------
    # 4. Conservative Fallback to Single LLM Agent Turn
    # -------------------------------------------------------------------------
    return FastPathResult(
        matched=False,
        confidence="low",
        intent="FALLBACK_TO_LLM",
        explanation="Utterance requires semantic/language reasoning; delegating to main agent loop",
    )
