"""Whole-turn interpretation; provider output is data, never executable tool arguments."""

import re
from typing import Any, Dict, List, Literal, Optional, cast

from pydantic import BaseModel, Field

from app.agent.context import SessionState
from app.agent.extraction import extract_trip_slots
from app.agent.semantics import parse_date_range, parse_duration_days

ActionName = Literal[
    "PLAN_TRIP",
    "SEARCH_HOTELS",
    "SEARCH_PLACES",
    "SEARCH_FOOD",
    "ROUTE_REQUEST",
    "ITINERARY_REQUEST",
]


class ReferenceEdit(BaseModel):
    operation: Literal["select", "remove", "keep"]
    entity_type: Literal["hotel", "place", "restaurant", "cafe"]
    reference: str = Field(min_length=1, max_length=200)


class TurnSemantics(BaseModel):
    requested_actions: List[ActionName] = Field(default_factory=list, max_length=6)
    references: List[ReferenceEdit] = Field(default_factory=list, max_length=20)
    duration_delta: Optional[int] = None
    clarification: Optional[str] = Field(default=None, max_length=500)
    food_category: Optional[Literal["restaurant", "cafe"]] = None
    meal_type: Optional[Literal["breakfast", "lunch", "dinner"]] = None
    location_anchor: Optional[str] = Field(default=None, max_length=200)


class InterpretedTurn(TurnSemantics):
    updates: Dict[str, Any] = Field(default_factory=dict)
    unresolved_reference_index: Optional[int] = None
    cancelled: bool = False


def build_context_snapshot(session: SessionState) -> Dict[str, Any]:
    state = session.trip_state
    context = session.conversation_context
    fields = (
        "destination",
        "number_of_days",
        "number_of_nights",
        "trip_start_date",
        "trip_end_date",
        "trip_budget",
        "hotel_budget",
        "hotel_total_budget",
        "travel_mode",
        "interests",
        "dietary_preferences",
        "accommodation_required",
        "accommodation_booked",
        "hotel_search_required",
        "explicit_fields",
        "planning_stage",
    )

    def identities(items):
        return [
            {
                "name": item.name,
                "id": item.id,
                "index": item.index,
                "entity_type": item.entity_type,
            }
            for item in items[:20]
        ]

    return {
        "trip": {field: getattr(state, field) for field in fields},
        "selected": {
            "hotel": state.hotel_selection.name if state.hotel_selection else None,
            "places": [p.name for p in state.selected_places[:20]],
            "restaurants": [p.name for p in state.selected_restaurants[:20]],
            "cafes": [p.name for p in state.selected_cafes[:20]],
        },
        "active_question": context.active_question.model_dump()
        if context.active_question
        else None,
        "visible": {
            "hotels": identities(context.visible_hotels),
            "places": identities(context.visible_places),
            "restaurants": identities(context.visible_restaurants),
        },
        "result_set_id": context.visible_result_set_id,
        "pending_actions": context.pending_actions,
        "pending_search_preferences": context.pending_search_preferences,
        "pending_turn": context.pending_turn,
        "state_version": state.state_version,
        "has_route": state.current_route is not None,
        "has_itinerary": state.current_itinerary is not None,
        "derived_freshness": {
            key: value.status.value for key, value in state.derived_freshness.items()
        },
        "recent_turns": [
            {"role": m.get("role"), "content": str(m.get("content", ""))[:1000]}
            for m in state.messages[-8:]
            if m.get("role") in ("user", "assistant")
        ],
    }


def extract_actions(message: str) -> List[ActionName]:
    actions: List[ActionName] = []
    for match in re.finditer(
        r"\b(?:find|search|show|suggest|recommend|discover|look for|build|rebuild|generate|create|plan|update)\b"
        r"[^.;,]{0,45}?\b(hotels?|accommodation|attractions?|places|restaurants?|cafes?|route|itinerary|schedule)\b",
        message.lower(),
    ):
        targets = [match.group(1)]
        remainder = message.lower()[match.end() :]
        while extra := re.match(
            r"\s+and\s+(?:some\s+)?(hotels?|accommodation|places|attractions?|restaurants?|cafes?|route|itinerary|schedule)\b",
            remainder,
        ):
            targets.append(extra.group(1))
            remainder = remainder[extra.end() :]
        for target in targets:
            action: ActionName = (
                "SEARCH_HOTELS"
                if target in ("hotel", "hotels", "accommodation")
                else "SEARCH_PLACES"
                if target in ("attraction", "attractions", "places")
                else "ROUTE_REQUEST"
                if target == "route"
                else "ITINERARY_REQUEST"
                if target in ("itinerary", "schedule")
                else "SEARCH_FOOD"
            )
            if action not in actions:
                actions.append(action)
    return actions


def interpret_complete_turn(
    message: str, session: SessionState, *, slot_message: Optional[str] = None
) -> Optional[InterpretedTurn]:
    """Resolve explicit multi-part turns before any single-intent early return."""
    text = message.lower().strip()
    pending = session.conversation_context.pending_turn
    cancel_requested = text in ("cancel", "nevermind", "forget it") and (
        pending
        or session.conversation_context.pending_actions
        or session.conversation_context.active_question
    )
    rejected_pending_turn = text == "no" and (
        pending or session.conversation_context.pending_actions
    )
    if cancel_requested or rejected_pending_turn:
        session.conversation_context.pending_turn = None
        session.conversation_context.clear_active_question()
        return InterpretedTurn(cancelled=True)
    if pending and len(text.split()) <= 5:
        turn = InterpretedTurn(**pending)
        active = session.conversation_context.active_question
        if (
            text in ("yes", "yes please", "confirm")
            and active
            and active.affirmation_field == "destination"
        ):
            turn.references = []
            turn.clarification = None
            return turn
        visible_names = [
            item.name.lower()
            for item in session.conversation_context.visible_hotels
            + session.conversation_context.visible_places
            + session.conversation_context.visible_restaurants
            if item.name
        ]
        selected_names = [
            item.name.lower()
            for item in session.trip_state.selected_places
            + session.trip_state.selected_restaurants
            + session.trip_state.selected_cafes
        ]
        reference_answer = (
            re.fullmatch(
                r"(?:the )?(?:first|second|third|fourth|fifth|last|\d+(?:st|nd|rd|th)?)(?: (?:one|hotel|place|attraction|restaurant|cafe))?",
                text,
            )
            or text in visible_names + selected_names
        )
        if turn.references and reference_answer:
            index = (
                turn.unresolved_reference_index
                if turn.unresolved_reference_index is not None
                else len(turn.references) - 1
            )
            turn.references[index].reference = text
            turn.clarification = None
            return turn
        total_days = int(text) if text.isdigit() else parse_duration_days(text)
        active = session.conversation_context.active_question
        if total_days and (
            turn.duration_delta is not None
            or (active and active.field == "number_of_days")
        ):
            turn.duration_delta = None
            turn.clarification = None
            turn.updates["number_of_days"] = total_days
            return turn
    if (
        text in ("try again", "retry", "continue", "go ahead")
        and session.conversation_context.pending_actions
    ):
        return InterpretedTurn()
    if re.match(r"^(?:what|when|where|how|is|tell me)\b", text):
        return None
    if re.match(r"^(?:must visit|mark|designate|require)\b", text):
        return None
    if re.search(r"\b(?:more relaxed|slower pace|less crowded|same budget)\b", text):
        return None
    slots = extract_trip_slots(
        slot_message if slot_message is not None else message, session.trip_state
    )
    updates = slots.high_confidence_updates()
    date_range = parse_date_range(message)
    if date_range:
        start, end = date_range
        updates.update(
            trip_start_date=start,
            trip_end_date=end,
            number_of_days=(end - start).days + 1,
            number_of_nights=(end - start).days,
        )
    if any(k in updates for k in ("hotel_budget", "hotel_total_budget", "trip_budget")):
        question = session.conversation_context.active_question
        explicit_trip_scope = bool(
            re.search(r"\b(?:whole|entire|trip|overall)\b", text)
        )
        if (
            question
            and question.scope == "accommodation"
            and not explicit_trip_scope
            and "trip_budget" in updates
        ):
            updates["hotel_total_budget"] = updates.pop("trip_budget")
    actions = extract_actions(message)
    if "SEARCH_HOTELS" in actions:
        updates["accommodation_required"] = True
        updates["accommodation_booked"] = False
    delta_match = re.search(
        r"\b(add|extend by|remove|shorten by)\s+(\d+|one|two|three|four|five|a)\s+(?:more\s+)?days?\b",
        text,
    )
    delta = None
    if delta_match:
        raw = delta_match.group(2)
        delta = (
            int(raw)
            if raw.isdigit()
            else {"one": 1, "a": 1, "two": 2, "three": 3, "four": 4, "five": 5}[raw]
        )
        if delta_match.group(1) in ("remove", "shorten by"):
            delta = -delta
        updates.pop("number_of_days", None)

    references = []
    for match in re.finditer(
        r"\b(keep|remove|drop|select|choose|pick)\s+(?:the\s+)?(.*?)\b(hotel|attraction|place|restaurant|cafe)\b",
        text,
    ):
        op, ref, entity = match.groups()
        references.append(
            ReferenceEdit(
                operation="remove"
                if op in ("remove", "drop")
                else ("keep" if op == "keep" else "select"),
                entity_type=cast(
                    Literal["hotel", "place", "restaurant", "cafe"],
                    "place" if entity == "attraction" else entity,
                ),
                reference=ref.strip() or "selected",
            )
        )

    rich = (
        bool(updates)
        and set(updates) != {"travel_mode"}
        or (actions and updates)
        or delta is not None
        or (
            references
            and (updates or delta is not None or actions or len(references) > 1)
        )
    )
    if not rich and not actions:
        return None
    # Leave standalone legacy reference/route handling intact.
    if (
        not updates
        and delta is None
        and not references
        and actions in (["ROUTE_REQUEST"], ["ITINERARY_REQUEST"])
    ):
        return None
    if (
        updates.get("destination")
        and not actions
        and not session.trip_state.destination
    ):
        actions = ["PLAN_TRIP"]
    updates["explicit_fields"] = [
        field for field in slots.explicit_fields if field in updates
    ]
    if "SEARCH_HOTELS" in actions:
        updates["explicit_fields"].extend(
            ["accommodation_required", "accommodation_booked"]
        )
    updates["extracted_confidence"] = {
        k: v.value for k, v in slots.confidence.items() if k in updates
    }
    anchor = re.search(
        r"\b(?:near|around)\s+(?:my|the|our)?\s*(.+?)(?:[.;,]|$)",
        message,
        re.IGNORECASE,
    )
    meal = re.search(r"\b(breakfast|lunch|dinner)\b", text)
    return InterpretedTurn(
        updates=updates,
        requested_actions=actions,
        references=references,
        duration_delta=delta,
        food_category="cafe" if re.search(r"\bcafes?\b", text) else None,
        meal_type=cast(Literal["breakfast", "lunch", "dinner"], meal.group(1))
        if meal
        else None,
        location_anchor=anchor.group(1).strip() if anchor else None,
    )


def resolve_turn_references(
    turn: InterpretedTurn, session: SessionState
) -> InterpretedTurn:
    """Resolve identities against current cards or selected stops, never model-made entities."""
    resolved = turn.model_copy(deep=True)
    state = session.trip_state
    if (
        turn.references
        and turn.updates.get("destination")
        and state.destination
        and turn.updates["destination"].casefold() != state.destination.casefold()
    ):
        resolved.clarification = "Those choices belong to your previous destination. Should I switch destinations and clear them?"
        session.conversation_context.set_active_question(
            "destination",
            "confirmation",
            "trip",
            resolved.clarification,
            affirmation_field="destination",
            affirmation_value=turn.updates["destination"],
        )
        return resolved
    if resolved.duration_delta is not None:
        if (
            not state.number_of_days
            or state.number_of_days + resolved.duration_delta < 1
        ):
            resolved.clarification = "How many days should the trip be in total?"
        else:
            resolved.updates["number_of_days"] = (
                state.number_of_days + resolved.duration_delta
            )
            resolved.updates.setdefault("explicit_fields", []).append("number_of_days")
    for index, edit in enumerate(resolved.references):
        if edit.operation == "select":
            item = session.conversation_context.resolve_item_reference(
                edit.reference, edit.entity_type
            )
            if item is None:
                resolved.clarification = (
                    f"Which {edit.entity_type} from the current results do you mean?"
                )
                resolved.unresolved_reference_index = index
                break
            payload = item.extra_data
            if edit.entity_type == "hotel":
                resolved.updates["hotel_selection"] = payload
            else:
                field = {
                    "place": "selected_places",
                    "restaurant": "selected_restaurants",
                    "cafe": "selected_cafes",
                }[edit.entity_type]
                resolved.updates.setdefault(field, []).append(payload)
        else:
            selected = (
                ([state.hotel_selection] if state.hotel_selection else [])
                if edit.entity_type == "hotel"
                else getattr(
                    state,
                    {
                        "place": "selected_places",
                        "restaurant": "selected_restaurants",
                        "cafe": "selected_cafes",
                    }[edit.entity_type],
                )
            )
            if edit.reference == "selected" and len(selected) == 1:
                selected_item = selected[0]
            elif edit.reference in ("last", "last one") and selected:
                selected_item = selected[-1]
            else:
                candidates = [
                    p for p in selected if p.name.lower() == edit.reference.lower()
                ]
                selected_item = candidates[0] if len(candidates) == 1 else None
            if selected_item is None:
                resolved.clarification = (
                    f"Which selected {edit.entity_type} do you mean?"
                )
                resolved.unresolved_reference_index = index
                break
            if edit.operation == "remove":
                if edit.entity_type == "hotel":
                    resolved.clarification = "Would you like to replace the selected hotel or skip accommodation?"
                    break
                resolved.updates.setdefault(
                    {
                        "place": "remove_places",
                        "restaurant": "remove_restaurants",
                        "cafe": "remove_cafes",
                    }[edit.entity_type],
                    [],
                ).append(selected_item.name)
    return resolved
