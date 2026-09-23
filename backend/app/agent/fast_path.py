"""
Project Musafir — Fast-Path Intent & Active-Question Resolver (Step 12.6)
Processes high-confidence inputs with negligible local latency (0 LLM roundtrips),
preventing rate limits and latency spikes for obvious short answers and ordinals.
Falls back conservatively to LLM whenever ambiguity exists.
"""

import re
from datetime import timedelta
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.agent.context import SessionState, ActiveQuestion, VisibleItemReference
from app.agent.semantics import (
    parse_currency_amount,
    parse_duration_days,
    parse_duration_nights,
    resolve_relative_date,
    parse_date_range,
    calculate_nights,
    calculate_nightly_hotel_budget,
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


# Conservative regex patterns for pure informational travel questions (non-planning)
GENERAL_QUERY_PATTERNS = [
    r"^is\s+[a-z]+\s+(?:a\s+)?good\s+time\s+to\s+visit\b",
    r"^what\s+(?:is|are)\s+the\s+best\s+(?:time|months?|season)\s+to\s+visit\b",
    r"^(?:how\s+is\s+the\s+weather|what\s+is\s+the\s+weather)\s+in\b",
    r"^best\s+(?:time|months?|season)\s+to\s+visit\b",
    r"^(?:can\s+you\s+tell\s+me\s+about|tell\s+me\s+about)\s+[a-z\s]+history\b",
]


def resolve_fast_path(user_message: str, session: SessionState) -> FastPathResult:
    """
    Evaluates user message against active session context using conservative deterministic rules.
    Returns FastPathResult with matched=True only when high confidence is established.
    """
    if not user_message or not str(user_message).strip():
        return FastPathResult(matched=False, confidence="low", intent="FALLBACK_TO_LLM")

    clean = user_message.strip()
    clean_lower = clean.lower()
    ctx = session.conversation_context
    trip = session.trip_state

    # -------------------------------------------------------------------------
    # -1. Explicit Route Request Trigger
    # -------------------------------------------------------------------------
    route_patterns = [
        r"^(?:build|create|generate|plan|make|optimize|show|calculate)\s+(?:the\s+|a\s+|our\s+)?(?:route|trip\s+route)\b",
        r"^(?:build\s+route|optimize\s+route|plan\s+route|generate\s+route)\b",
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
    # -0.5. Explicit Item Rejection Trigger
    # -------------------------------------------------------------------------
    rejection_entity = "place" if trip.hotel_selection else None
    rejection = ctx.resolve_item_rejection(clean_lower, entity_type=rejection_entity)
    if rejection:
        name = rejection.name if isinstance(rejection, VisibleItemReference) else str(rejection)
        item_id = rejection.id if isinstance(rejection, VisibleItemReference) else None
        return FastPathResult(
            matched=True,
            confidence="high",
            intent="REJECT_ITEM",
            reference_resolution={"name": name, "id": item_id} if item_id else {"name": name},
            state_updates={"rejected_places": [name]},
            explanation=f"User rejected place '{name}'",
        )

    # -------------------------------------------------------------------------
    # 0. Conservative Check: Open-ended, complex, or contrastive phrases delegate to LLM
    # -------------------------------------------------------------------------
    if re.search(r"\broute\b", clean_lower):
        return FastPathResult(
            matched=False,
            confidence="low",
            intent="FALLBACK_TO_LLM",
            explanation="Complex or nuanced route request delegates to LLM",
        )

    complex_nuance_patterns = [
        r"\b(best\s+places|suggest|recommend|itinerary|somewhere|changed my mind|forget|trip under|not the|not that)\b",
        r"\b(don't|dont|never|neither|without|except)\b",
    ]
    if any(re.search(p, clean_lower) for p in complex_nuance_patterns):
        return FastPathResult(
            matched=False,
            confidence="low",
            intent="FALLBACK_TO_LLM",
            explanation="Complex, nuanced, or contrastive utterance delegates to LLM",
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
    for kw, target_mode in mode_map.items():
        if re.search(rf"\b{kw}\b", clean_lower):
            cleared_q = (
                ctx.active_question is not None
                and (ctx.active_question.field == "travel_mode" or ctx.active_question.expected_type == "mode")
            )
            return FastPathResult(
                matched=True,
                confidence="high",
                intent="ANSWER_ACTIVE_QUESTION" if cleared_q else "UPDATE_FIELD",
                state_updates={"travel_mode": target_mode},
                cleared_active_question=cleared_q,
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
        if trip.hotel_selection and ctx.visible_places:
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
            updates: Dict[str, Any] = {}
            if place_payloads:
                updates["selected_places"] = place_payloads
            if hotel_payload:
                updates["hotel_selection"] = hotel_payload

            return FastPathResult(
                matched=True,
                confidence="high",
                intent="SELECT_ITEMS",
                reference_resolution={"items": [r.model_dump() for r in multi_refs]},
                state_updates=updates,
                cleared_active_question=False,
                explanation=f"Selected {len(multi_refs)} items: {[r.name for r in multi_refs]}",
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

        if ctx.visible_places:
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
                return FastPathResult(
                    matched=True,
                    confidence="high",
                    intent="SELECT_ITEM",
                    reference_resolution=matched_ref.model_dump(),
                    state_updates={"selected_restaurants": [matched_ref.extra_data or {"name": matched_ref.name, "data_id": matched_ref.id}]},
                    cleared_active_question=False,
                    explanation=f"Referenced restaurant '{matched_ref.name}' (position #{matched_ref.index})",
                )

    # -------------------------------------------------------------------------
    # 8. High-Confidence Destination Resolution
    # -------------------------------------------------------------------------
    dest_phrase_match = re.search(
        r"^(?:i(?:'m|\s+am)?\s+(?:going|traveling|travelling|planning\s+to\s+go)\s+to|trip\s+to|heading\s+to|visiting)\s+([a-zA-Z\s]{3,30})$",
        clean,
        re.IGNORECASE,
    )
    if dest_phrase_match:
        extracted_dest = dest_phrase_match.group(1).strip().title()
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
                if re.match(r"^[a-zA-Z\s]{3,30}$", clean):
                    extracted_dest = clean.strip().title()
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
