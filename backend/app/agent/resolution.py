"""
Project Musafir — Deterministic Reference Resolution & Clarification Engine (Milestone 3, Batch 2)
Implements:
1. 5-stage reference resolution precedence:
   Exact Name -> Stable Selected Entity -> Visible Ordinal/Card -> Explicit Entity ID -> Contextual
2. Ambiguity detection & structured ClarificationRequest (clarification does NOT mutate state)
3. Explicit budget scope resolution & clarification (never silently convert total <-> nightly)
4. Date & duration resolution precedence (explicit nights > explicit dates > inferred duration)
5. Entity replacement helper producing atomic [REMOVE, SELECT] MutationBatches
"""

import re
from datetime import date as dt_date, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field

from app.schemas.mutation import (
    MutationType,
    MutationCommand,
    MutationBatch,
    BudgetScope,
    ResolutionConfidence,
    SUPPORTED_TRAVEL_MODES,
)
from app.schemas.reference import EntityReference, VisibleItemReference
from app.agent.state import TripState
from app.agent.context import SessionState, ConversationContext, get_session
from app.agent.semantics import calculate_nights, derive_trip_dates


class ResolutionPrecedence(str, Enum):
    EXACT_NAME = "exact_name"
    SELECTED_ENTITY = "selected_entity"
    VISIBLE_ORDINAL = "visible_ordinal"
    ENTITY_ID = "entity_id"
    CONTEXTUAL = "contextual"


class ResolvedEntity(BaseModel):
    """Normalized resolved entity representation."""
    entity_type: str = Field(..., description="'hotel' | 'place' | 'restaurant' | 'cafe'")
    id: str = Field(..., description="Unique entity ID or slug")
    name: str = Field(..., description="Human-readable entity name")
    precedence: ResolutionPrecedence = Field(..., description="Precedence rule that matched this entity")
    confidence: ResolutionConfidence = Field(default=ResolutionConfidence.HIGH)
    data: Dict[str, Any] = Field(default_factory=dict, description="Full entity payload dictionary")


class ClarificationRequest(BaseModel):
    """
    Structured clarification request when user intent or reference is ambiguous.
    CRITICAL INVARIANT: Clarification requests do NOT mutate TripState.
    """
    field: str = Field(..., description="Field requiring clarification (e.g. 'hotel', 'place', 'food', 'budget', 'dates')")
    question: str = Field(..., description="Single, user-facing clarification question")
    options: List[str] = Field(default_factory=list, description="Candidate options presented to user if applicable")
    reason: str = Field(..., description="Architectural reason for clarification")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EntityResolutionResult(BaseModel):
    """Result of entity resolution."""
    resolved_entity: Optional[ResolvedEntity] = None
    clarification: Optional[ClarificationRequest] = None
    is_ambiguous: bool = False


class BudgetResolutionResult(BaseModel):
    """Result of budget expression parsing."""
    amount: Optional[float] = None
    scope: Optional[BudgetScope] = None
    clarification: Optional[ClarificationRequest] = None
    is_ambiguous: bool = False


# -----------------------------------------------------------------------------
# Ordinal Word Mappings
# -----------------------------------------------------------------------------

ORDINAL_WORDS = {
    "first": 1, "1st": 1,
    "second": 2, "2nd": 2,
    "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5,
    "sixth": 6, "6th": 6,
    "seventh": 7, "7th": 7,
    "eighth": 8, "8th": 8,
    "ninth": 9, "9th": 9,
    "tenth": 10, "10th": 10,
}

CARDINAL_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

ORDINAL_MAP = {**ORDINAL_WORDS, **CARDINAL_WORDS}


def clean_query(text: str) -> str:
    """Strips punctuation and normalizes whitespace."""
    if not text:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(cleaned.split())


# -----------------------------------------------------------------------------
# Entity Reference Resolver
# -----------------------------------------------------------------------------

def resolve_entity_reference(
    query: Union[str, int],
    state: TripState,
    session: Optional[Union[SessionState, ConversationContext]] = None,
    entity_type: Optional[str] = None,
) -> EntityResolutionResult:
    """
    Deterministically resolves an entity reference from user utterance against:
    - Selected trip state entities (hotel, places, food)
    - Visible screen cards in active session (hotels, places, restaurants)
    
    Resolution follows strict 5-stage precedence:
    1. Exact Name Match
    2. Stable Selected Entity Match
    3. Visible Result Ordinal / List Reference
    4. Explicit Entity ID Match
    5. Contextual Reference ("that restaurant", "the hotel")
    
    If multiple candidates are equally plausible at the same precedence level,
    returns is_ambiguous=True and a structured ClarificationRequest (no state mutation).
    """
    if query is None:
        return EntityResolutionResult()

    # Normalize integer query into ordinal lookup
    if isinstance(query, int):
        clean_str = str(query)
    else:
        clean_str = str(query).strip()

    norm = clean_query(clean_str)
    if not norm:
        return EntityResolutionResult()

    # Build entity pools
    # Pool A: Active selected entities in TripState
    selected_items: List[Tuple[str, str, str, Dict[str, Any]]] = []
    # format: (entity_type, id, name, data_dict)
    if state.hotel_selection and (entity_type is None or entity_type == "hotel"):
        h = state.hotel_selection
        h_dict = h.model_dump() if hasattr(h, "model_dump") else dict(h)
        selected_items.append(("hotel", str(getattr(h, "data_id", "") or getattr(h, "id", "") or h.name), h.name, h_dict))

    if entity_type is None or entity_type == "place":
        for p in state.selected_places:
            p_dict = p.model_dump() if hasattr(p, "model_dump") else dict(p)
            selected_items.append(("place", str(getattr(p, "data_id", "") or getattr(p, "id", "") or p.name), p.name, p_dict))

    if entity_type is None or entity_type in ("restaurant", "food"):
        for r in state.selected_restaurants:
            r_dict = r.model_dump() if hasattr(r, "model_dump") else dict(r)
            selected_items.append(("restaurant", str(getattr(r, "data_id", "") or getattr(r, "id", "") or r.name), r.name, r_dict))

    if entity_type is None or entity_type in ("cafe", "food"):
        for c in state.selected_cafes:
            c_dict = c.model_dump() if hasattr(c, "model_dump") else dict(c)
            selected_items.append(("cafe", str(getattr(c, "data_id", "") or getattr(c, "id", "") or c.name), c.name, c_dict))

    # Pool B: Visible cards in Session / Context
    visible_items: List[VisibleItemReference] = []
    if session:
        ctx = session.conversation_context if hasattr(session, "conversation_context") else session
        if entity_type == "hotel":
            visible_items = ctx.visible_hotels
        elif entity_type == "place":
            visible_items = ctx.visible_places
        elif entity_type in ("restaurant", "food"):
            visible_items = ctx.visible_restaurants
        elif entity_type == "cafe":
            visible_items = [v for v in ctx.visible_restaurants if "cafe" in v.name.lower()]
        else:
            visible_items = ctx.visible_hotels + ctx.visible_places + ctx.visible_restaurants

    # =========================================================================
    # PRECEDENCE 1: Exact Name Match
    # =========================================================================
    exact_matches: List[ResolvedEntity] = []

    # Check selected items
    for etype, eid, ename, edata in selected_items:
        if clean_query(ename) == norm:
            exact_matches.append(ResolvedEntity(
                entity_type=etype,
                id=eid,
                name=ename,
                precedence=ResolutionPrecedence.EXACT_NAME,
                confidence=ResolutionConfidence.HIGH,
                data=edata,
            ))

    # Check visible items
    for v in visible_items:
        if clean_query(v.name) == norm:
            # avoid duplicating if already matched in selected
            if not any(m.id == v.id and m.entity_type == v.entity_type for m in exact_matches):
                exact_matches.append(ResolvedEntity(
                    entity_type=v.entity_type,
                    id=v.id,
                    name=v.name,
                    precedence=ResolutionPrecedence.EXACT_NAME,
                    confidence=ResolutionConfidence.HIGH,
                    data=v.extra_data,
                ))

    if len(exact_matches) == 1:
        return EntityResolutionResult(resolved_entity=exact_matches[0])
    elif len(exact_matches) > 1:
        return EntityResolutionResult(
            is_ambiguous=True,
            clarification=ClarificationRequest(
                field=exact_matches[0].entity_type,
                question=f"I found multiple options matching '{clean_str}'. Which one did you mean?",
                options=[m.name for m in exact_matches],
                reason="multiple_exact_matches",
            ),
        )

    # =========================================================================
    # PRECEDENCE 2: Stable Selected Entity Substring / Keyword Match
    # =========================================================================
    selected_matches: List[ResolvedEntity] = []
    for etype, eid, ename, edata in selected_items:
        ename_norm = clean_query(ename)
        if norm in ename_norm or ename_norm in norm:
            selected_matches.append(ResolvedEntity(
                entity_type=etype,
                id=eid,
                name=ename,
                precedence=ResolutionPrecedence.SELECTED_ENTITY,
                confidence=ResolutionConfidence.HIGH,
                data=edata,
            ))

    if len(selected_matches) == 1:
        return EntityResolutionResult(resolved_entity=selected_matches[0])
    elif len(selected_matches) > 1:
        return EntityResolutionResult(
            is_ambiguous=True,
            clarification=ClarificationRequest(
                field=selected_matches[0].entity_type,
                question=f"Which selected {selected_matches[0].entity_type} did you mean?",
                options=[m.name for m in selected_matches],
                reason="multiple_selected_matches",
            ),
        )

    # =========================================================================
    # PRECEDENCE 3: Visible Result Ordinal / Card Reference
    # =========================================================================
    # Matches "first", "second", "3rd", "last", "the second hotel", "first restaurant", etc.
    ordinal_target: Optional[int] = None

    # Direct integer
    if isinstance(query, int):
        ordinal_target = query
    elif norm in ORDINAL_MAP:
        ordinal_target = ORDINAL_MAP[norm]
    elif norm in ("last", "last one", "the last", "the last one"):
        if visible_items:
            ordinal_target = len(visible_items)
    else:
        # Pattern match: check explicit ordinals first (e.g. "second one", "the first hotel", "2nd place")
        for word, idx in ORDINAL_WORDS.items():
            if re.search(rf"\b{word}\b", norm):
                ordinal_target = idx
                break
        if ordinal_target is None:
            # Check cardinals only if not used as a dummy pronoun ("that one", "second one")
            for word, idx in CARDINAL_WORDS.items():
                if word == "one" and not re.search(r"\b(?:option|number|choice|card)\s+one\b|^one$", norm):
                    continue
                if re.search(rf"\b{word}\b", norm):
                    ordinal_target = idx
                    break

    if ordinal_target is not None and visible_items:
        matching_cards = [v for v in visible_items if v.index == ordinal_target]
        # Filter by category if user explicitly mentioned category in query
        if not entity_type:
            if "hotel" in norm:
                matching_cards = [v for v in matching_cards if v.entity_type == "hotel"]
            elif "place" in norm or "attraction" in norm:
                matching_cards = [v for v in matching_cards if v.entity_type == "place"]
            elif "restaurant" in norm:
                matching_cards = [v for v in matching_cards if v.entity_type in ("restaurant", "food")]
            elif "cafe" in norm:
                matching_cards = [v for v in matching_cards if v.entity_type == "cafe" or "cafe" in v.name.lower()]

        if len(matching_cards) == 1:
            matching_card = matching_cards[0]
            return EntityResolutionResult(
                resolved_entity=ResolvedEntity(
                    entity_type=matching_card.entity_type,
                    id=matching_card.id,
                    name=matching_card.name,
                    precedence=ResolutionPrecedence.VISIBLE_ORDINAL,
                    confidence=ResolutionConfidence.HIGH,
                    data=matching_card.extra_data,
                )
            )
        elif len(matching_cards) > 1:
            return EntityResolutionResult(
                is_ambiguous=True,
                clarification=ClarificationRequest(
                    field="mixed_ordinal",
                    question=f"I found multiple options for position #{ordinal_target}: {', '.join([f'{m.entity_type.title()} ({m.name})' for m in matching_cards])}. Which one did you mean?",
                    options=[f"{m.entity_type.title()}: {m.name}" for m in matching_cards],
                    reason="mixed_entity_ordinal_ambiguity",
                ),
            )

    # Substring in visible items (e.g. "Taj" matching "Taj Malabar" on screen)
    visible_matches: List[ResolvedEntity] = []
    for v in visible_items:
        v_norm = clean_query(v.name)
        if norm in v_norm:
            visible_matches.append(ResolvedEntity(
                entity_type=v.entity_type,
                id=v.id,
                name=v.name,
                precedence=ResolutionPrecedence.VISIBLE_ORDINAL,
                confidence=ResolutionConfidence.MEDIUM,
                data=v.extra_data,
            ))

    if len(visible_matches) == 1:
        return EntityResolutionResult(resolved_entity=visible_matches[0])
    elif len(visible_matches) > 1:
        return EntityResolutionResult(
            is_ambiguous=True,
            clarification=ClarificationRequest(
                field=visible_matches[0].entity_type,
                question=f"I found {len(visible_matches)} options matching '{clean_str}'. Which one would you prefer?",
                options=[m.name for m in visible_matches],
                reason="multiple_visible_matches",
            ),
        )

    # =========================================================================
    # PRECEDENCE 4: Explicit Entity ID Match
    # =========================================================================
    for etype, eid, ename, edata in selected_items:
        if eid.lower() == norm.lower():
            return EntityResolutionResult(
                resolved_entity=ResolvedEntity(
                    entity_type=etype,
                    id=eid,
                    name=ename,
                    precedence=ResolutionPrecedence.ENTITY_ID,
                    confidence=ResolutionConfidence.HIGH,
                    data=edata,
                )
            )
    for v in visible_items:
        if v.id.lower() == norm.lower():
            return EntityResolutionResult(
                resolved_entity=ResolvedEntity(
                    entity_type=v.entity_type,
                    id=v.id,
                    name=v.name,
                    precedence=ResolutionPrecedence.ENTITY_ID,
                    confidence=ResolutionConfidence.HIGH,
                    data=v.extra_data,
                )
            )

    # =========================================================================
    # PRECEDENCE 5: Contextual Reference ("the hotel", "that restaurant", etc.)
    # =========================================================================
    if norm in ("the hotel", "hotel", "my hotel", "our hotel") and state.hotel_selection:
        h = state.hotel_selection
        h_dict = h.model_dump() if hasattr(h, "model_dump") else dict(h)
        return EntityResolutionResult(
            resolved_entity=ResolvedEntity(
                entity_type="hotel",
                id=str(getattr(h, "data_id", "") or getattr(h, "id", "") or h.name),
                name=h.name,
                precedence=ResolutionPrecedence.CONTEXTUAL,
                confidence=ResolutionConfidence.HIGH,
                data=h_dict,
            )
        )

    if norm in ("the restaurant", "that restaurant", "the cafe", "the place", "the attraction"):
        # If user asks for "the restaurant" and exactly 1 is selected
        if "restaurant" in norm and len(state.selected_restaurants) == 1:
            r = state.selected_restaurants[0]
            r_dict = r.model_dump() if hasattr(r, "model_dump") else dict(r)
            return EntityResolutionResult(
                resolved_entity=ResolvedEntity(
                    entity_type="restaurant",
                    id=str(getattr(r, "data_id", "") or getattr(r, "id", "") or r.name),
                    name=r.name,
                    precedence=ResolutionPrecedence.CONTEXTUAL,
                    confidence=ResolutionConfidence.HIGH,
                    data=r_dict,
                )
            )
        elif "restaurant" in norm and len(state.selected_restaurants) > 1:
            return EntityResolutionResult(
                is_ambiguous=True,
                clarification=ClarificationRequest(
                    field="restaurant",
                    question="Which selected restaurant would you like to update?",
                    options=[r.name for r in state.selected_restaurants],
                    reason="multiple_selected_restaurants",
                ),
            )

        if "cafe" in norm and len(state.selected_cafes) == 1:
            c = state.selected_cafes[0]
            c_dict = c.model_dump() if hasattr(c, "model_dump") else dict(c)
            return EntityResolutionResult(
                resolved_entity=ResolvedEntity(
                    entity_type="cafe",
                    id=str(getattr(c, "data_id", "") or getattr(c, "id", "") or c.name),
                    name=c.name,
                    precedence=ResolutionPrecedence.CONTEXTUAL,
                    confidence=ResolutionConfidence.HIGH,
                    data=c_dict,
                )
            )
        elif "cafe" in norm and len(state.selected_cafes) > 1:
            return EntityResolutionResult(
                is_ambiguous=True,
                clarification=ClarificationRequest(
                    field="cafe",
                    question="Which selected cafe would you like to update?",
                    options=[c.name for c in state.selected_cafes],
                    reason="multiple_selected_cafes",
                ),
            )

        if "place" in norm or "attraction" in norm:
            if len(state.selected_places) == 1:
                p = state.selected_places[0]
                p_dict = p.model_dump() if hasattr(p, "model_dump") else dict(p)
                return EntityResolutionResult(
                    resolved_entity=ResolvedEntity(
                        entity_type="place",
                        id=str(getattr(p, "data_id", "") or getattr(p, "id", "") or p.name),
                        name=p.name,
                        precedence=ResolutionPrecedence.CONTEXTUAL,
                        confidence=ResolutionConfidence.HIGH,
                        data=p_dict,
                    )
                )
            elif len(state.selected_places) > 1:
                return EntityResolutionResult(
                    is_ambiguous=True,
                    clarification=ClarificationRequest(
                        field="place",
                        question="Which selected place would you like to modify?",
                        options=[p.name for p in state.selected_places],
                        reason="multiple_selected_places",
                    ),
                )

    # Could not resolve
    return EntityResolutionResult()


# -----------------------------------------------------------------------------
# Budget Scope & Value Resolver
# -----------------------------------------------------------------------------

def resolve_budget_phrase(text: str, state: TripState) -> BudgetResolutionResult:
    """
    Parses a user budget phrase into an amount and an explicit BudgetScope.
    Enforces the architectural invariant:
    - Never silently convert total hotel budget <-> nightly hotel budget or trip budget.
    - If scope is ambiguous, returns a structured ClarificationRequest (no state mutation).
    """
    if not text or not str(text).strip():
        return BudgetResolutionResult()

    raw = str(text).strip().lower()

    # Extract numeric amount (e.g. 25000, 25k, ₹25,000, 1500)
    match_k = re.search(r"(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*k\b", raw)
    match_num = re.search(r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*)(?:\.\d+)?\b", raw)

    amount: Optional[float] = None
    if match_k:
        amount = float(match_k.group(1)) * 1000.0
    elif match_num:
        clean_num = match_num.group(1).replace(",", "")
        try:
            val = float(clean_num)
            if val > 0:
                amount = val
        except ValueError:
            pass

    if amount is None:
        # User said "increase my budget" or "change budget" without amount
        return BudgetResolutionResult(
            is_ambiguous=True,
            clarification=ClarificationRequest(
                field="budget",
                question="What amount would you like to set for your budget?",
                options=["₹3,000 per night", "₹15,000 total hotel", "₹30,000 whole trip"],
                reason="missing_budget_amount",
            ),
        )

    # Determine explicit scope
    is_nightly = bool(re.search(r"\b(per\s*night|nightly|a\s*night|each\s*night|/night)\b", raw))
    is_hotel_total = bool(re.search(r"\b(hotel\s*total|total\s*hotel|for\s*hotel|hotel\s*budget|for\s*the\s*hotel)\b", raw)) and not is_nightly
    is_trip_total = bool(re.search(r"\b(trip\s*total|total\s*trip|whole\s*trip|entire\s*trip|overall|for\s*the\s*trip|total\s*budget)\b", raw))

    if is_nightly:
        return BudgetResolutionResult(amount=amount, scope=BudgetScope.NIGHTLY_HOTEL)
    elif is_hotel_total:
        return BudgetResolutionResult(amount=amount, scope=BudgetScope.TOTAL_HOTEL)
    elif is_trip_total:
        return BudgetResolutionResult(amount=amount, scope=BudgetScope.TOTAL_TRIP)

    # Contextual inference if in HOTEL_SELECTION stage and amount is reasonable for hotel
    if "hotel" in raw:
        # Default to total_hotel if "hotel" mentioned without per night
        return BudgetResolutionResult(amount=amount, scope=BudgetScope.TOTAL_HOTEL)

    # Ambiguous scope: e.g. "budget 25000" or "make it 20k"
    return BudgetResolutionResult(
        amount=amount,
        is_ambiguous=True,
        clarification=ClarificationRequest(
            field="budget",
            question=f"Should the ₹{int(amount):,} budget be for your nightly hotel stay, total hotel stay, or the entire trip?",
            options=["Per night hotel budget", "Total hotel stay budget", "Entire trip budget"],
            reason="ambiguous_budget_scope",
            metadata={"amount": amount},
        ),
    )


# -----------------------------------------------------------------------------
# Entity Replacement Helper
# -----------------------------------------------------------------------------

def build_entity_replacement_batch(
    target_entity: ResolvedEntity,
    replacement_name_or_data: Union[str, Dict[str, Any]],
    source_message: Optional[str] = None,
) -> MutationBatch:
    """
    Constructs an atomic [REMOVE, SELECT] MutationBatch for in-place entity substitution.
    Preserves existing constraints, hotel anchor, and other trip selections.
    """
    m_type_remove = {
        "place": MutationType.REMOVE_PLACE,
        "restaurant": MutationType.REMOVE_RESTAURANT,
        "cafe": MutationType.REMOVE_CAFE,
    }.get(target_entity.entity_type, MutationType.REMOVE_PLACE)

    m_type_select = {
        "place": MutationType.SELECT_PLACE,
        "restaurant": MutationType.SELECT_RESTAURANT,
        "cafe": MutationType.SELECT_CAFE,
    }.get(target_entity.entity_type, MutationType.SELECT_PLACE)

    remove_cmd = MutationCommand(
        mutation_type=m_type_remove,
        target_id=target_entity.id,
        target_name=target_entity.name,
        confidence=ResolutionConfidence.HIGH,
    )

    if isinstance(replacement_name_or_data, dict):
        select_cmd = MutationCommand(
            mutation_type=m_type_select,
            target_id=str(replacement_name_or_data.get("data_id") or replacement_name_or_data.get("id") or replacement_name_or_data.get("name")),
            target_name=str(replacement_name_or_data.get("name", "")),
            value=replacement_name_or_data,
            confidence=ResolutionConfidence.HIGH,
        )
    else:
        select_cmd = MutationCommand(
            mutation_type=m_type_select,
            target_name=str(replacement_name_or_data).strip(),
            confidence=ResolutionConfidence.HIGH,
        )

    return MutationBatch(
        mutations=[remove_cmd, select_cmd],
        source_message=source_message,
    )
