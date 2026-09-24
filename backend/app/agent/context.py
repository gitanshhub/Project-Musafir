"""
Project Musafir — Conversation Context & Session State (Step 12.6)
Decouples transient dialogue memory (active question, visible screen cards,
recent references) from canonical TripState.
"""

import re
import uuid
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from app.agent.state import TripState


class ActiveQuestion(BaseModel):
    """
    Structured representation of the active question Musafir asked the user.
    Enables zero-guess deterministic parsing of short answers.
    """
    field: str = Field(..., description="Target field name (e.g. 'hotel_total_budget', 'number_of_days', 'travel_mode')")
    expected_type: str = Field(..., description="'number' | 'money' | 'string' | 'date' | 'mode' | 'boolean'")
    scope: str = Field(..., description="'accommodation' | 'trip' | 'destination' | 'logistics' | 'confirmation'")
    prompt_text: Optional[str] = Field(None, description="Verbatim text of the question asked to the user")
    reason: Optional[str] = Field(None, description="Why this question is being asked, e.g. 'required_for_hotel_search'")
    affirmation_field: Optional[str] = Field(None, description="Field to set if user responds 'yes'")
    affirmation_value: Optional[Any] = Field(None, description="Value to apply if user responds 'yes'")


from app.schemas.reference import EntityReference, VisibleItemReference


class ConversationContext(BaseModel):
    """
    Transient conversational memory: active questions, screen cards, and reference identity.
    Keeps canonical TripState lean and unpolluted.
    """
    active_question: Optional[ActiveQuestion] = None
    last_agent_action: Optional[str] = None
    last_intent: Optional[str] = None

    # Result-set identity and visible card tracking
    visible_result_set_id: Optional[str] = None
    visible_hotels: List[VisibleItemReference] = Field(default_factory=list)
    visible_places: List[VisibleItemReference] = Field(default_factory=list)
    visible_restaurants: List[VisibleItemReference] = Field(default_factory=list)
    last_mentioned_entities: Dict[str, Any] = Field(default_factory=dict)

    def set_active_question(
        self,
        field: str,
        expected_type: str,
        scope: str,
        prompt_text: Optional[str] = None,
        reason: Optional[str] = None,
        affirmation_field: Optional[str] = None,
        affirmation_value: Optional[Any] = None,
    ) -> ActiveQuestion:
        """Sets the currently active question Musafir has asked the user."""
        self.active_question = ActiveQuestion(
            field=field,
            expected_type=expected_type,
            scope=scope,
            prompt_text=prompt_text,
            reason=reason,
            affirmation_field=affirmation_field,
            affirmation_value=affirmation_value,
        )
        return self.active_question

    def clear_active_question(self) -> None:
        """Clears the active question once answered or superseded."""
        self.active_question = None

    def set_visible_items(
        self,
        entity_type: str,
        items: List[Dict[str, Any]],
        result_set_id: Optional[str] = None,
    ) -> None:
        """
        Updates the active visible cards of a given entity_type with fresh 1-based index ordering.
        Supports 'hotel', 'place', and 'restaurant'.
        """
        self.visible_result_set_id = result_set_id or str(uuid.uuid4())[:8]
        refs: List[VisibleItemReference] = []
        for idx, item in enumerate(items, start=1):
            ref = VisibleItemReference(
                index=idx,
                entity_type=entity_type,
                id=str(item.get("property_token") or item.get("data_id") or item.get("id") or item.get("name") or idx),
                name=str(item.get("name") or f"{entity_type.title()} #{idx}"),
                price_per_night=item.get("price_per_night"),
                rating=item.get("rating"),
                extra_data=dict(item),
            )
            refs.append(ref)

        if entity_type == "hotel":
            self.visible_hotels = refs
        elif entity_type == "place":
            self.visible_places = refs
        elif entity_type == "restaurant":
            self.visible_restaurants = refs

    def resolve_item_reference(
        self,
        query: Union[int, str],
        entity_type: Optional[str] = None,
    ) -> Optional[VisibleItemReference]:
        """
        Generic ordinal and name resolver across visible items.
        Supports 1-based integer index, ordinal strings ('first', 'second', '2nd', 'last'),
        or partial name substring matching.
        """
        candidates: List[VisibleItemReference] = []
        if entity_type == "hotel":
            candidates = self.visible_hotels
        elif entity_type == "place":
            candidates = self.visible_places
        elif entity_type == "restaurant":
            candidates = self.visible_restaurants
        else:
            # Search all visible sets in priority: hotels -> places -> restaurants
            candidates = self.visible_hotels + self.visible_places + self.visible_restaurants

        if not candidates:
            return None

        # 1. Integer index
        if isinstance(query, int):
            for item in candidates:
                if item.index == query:
                    return item
            return None

        clean = str(query).strip().lower()
        clean_stripped = re.sub(r"[^\w\s]", "", clean)

        # If contrastive or negation expression is detected ("not the second", "actually not ..."),
        # do not guess deterministically — return None so caller falls back to LLM.
        if re.search(r"\b(not|neither|never|except|instead of)\b", clean_stripped):
            return None

        # 2. Ordinal word / last one mapping
        ordinals = {
            "first": 1, "1st": 1, "one": 1,
            "second": 2, "2nd": 2, "two": 2,
            "third": 3, "3rd": 3, "three": 3,
            "fourth": 4, "4th": 4, "four": 4,
            "fifth": 5, "5th": 5, "five": 5,
        }
        if clean_stripped in {"last", "last one", "the last one", "last hotel", "last place", "last restaurant"}:
            return candidates[-1]

        for word, idx in ordinals.items():
            patterns = {
                word,
                f"the {word}",
                f"{word} one",
                f"the {word} one",
                f"{word} hotel",
                f"the {word} hotel",
                f"{word} place",
                f"the {word} place",
                f"{word} restaurant",
                f"the {word} restaurant",
            }
            if clean_stripped in patterns or any(p in clean_stripped for p in [f"{word} hotel", f"{word} place", f"{word} restaurant", f"{word} one"]):
                for item in candidates:
                    if item.index == idx:
                        return item

        # 3. Name substring (Pass 1)
        for item in candidates:
            item_name_lower = item.name.lower()
            if clean_stripped in item_name_lower or item_name_lower in clean_stripped:
                return item

        # 4. Distinctive keyword overlap (Pass 2) - pick candidate with highest overlap
        clean_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", clean_stripped)) - {
            "like", "choose", "select", "book", "pick", "want", "take", "this", "that",
            "hotel", "resort", "stay", "place", "restaurant", "the", "one", "please"
        }
        best_item = None
        best_overlap = 0
        for item in candidates:
            item_name_lower = item.name.lower()
            item_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", item_name_lower))
            overlap = len(clean_words & item_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best_item = item

        if best_item and best_overlap > 0:
            return best_item

        return None

    def resolve_multiple_item_references(
        self,
        query: str,
        entity_type: Optional[str] = None,
    ) -> List[VisibleItemReference]:
        """
        Resolves compound references (e.g. 'first and third', '1st and 3rd',
        'first, second and last', '1 and 2', 'take the caves and the waterfall').
        Returns list of matched VisibleItemReference objects without duplicates.
        """
        if not query or not str(query).strip():
            return []

        clean = str(query).strip().lower()

        # If contrastive or rejection expression, do not parse as multi-selection
        if re.search(r"\b(not|neither|never|except|instead of|skip|remove|drop)\b", clean):
            return []

        # Candidate pool
        if entity_type == "hotel":
            candidates = self.visible_hotels
        elif entity_type == "place":
            candidates = self.visible_places
        elif entity_type in ("restaurant", "cafe"):
            candidates = self.visible_restaurants
        else:
            candidates = self.visible_restaurants or self.visible_places or self.visible_hotels

        if not candidates:
            return []

        # Split on commas, 'and', '&', '+'
        raw_parts = re.split(r"(?:,|\band\b|&|\+)+", clean)
        cleaned_parts = []
        filler_words = {"take", "choose", "select", "pick", "both", "all", "the", "cards", "places", "options", "stops", "hotels", "restaurants", "cafes", "dining"}
        for p in raw_parts:
            # Strip filler words from tokens
            words = [w for w in re.findall(r"\b\w+\b", p) if w not in filler_words]
            if words:
                cleaned_parts.append(" ".join(words))

        if len(cleaned_parts) <= 1 and not re.search(r"\b\d+\s+and\s+\d+\b", clean):
            return []

        matched: List[VisibleItemReference] = []
        seen_ids = set()

        for part in cleaned_parts:
            # First try direct resolution
            ref = self.resolve_item_reference(part, entity_type=entity_type)
            if ref and ref.id not in seen_ids:
                seen_ids.add(ref.id)
                matched.append(ref)
                continue

            # Check digit directly
            digit_m = re.match(r"^(\d+)$", part)
            if digit_m:
                idx = int(digit_m.group(1))
                for c in candidates:
                    if c.index == idx and c.id not in seen_ids:
                        seen_ids.add(c.id)
                        matched.append(c)
                        break
                continue

            # Substring name matching
            part_clean = re.sub(r"[^\w\s]", "", part).strip()
            for c in candidates:
                if c.id not in seen_ids:
                    if part_clean in c.name.lower() or c.name.lower() in part_clean:
                        seen_ids.add(c.id)
                        matched.append(c)
                        break

        return matched

    def resolve_item_rejection(
        self,
        query: str,
        entity_type: Optional[str] = None,
    ) -> Optional[Union[VisibleItemReference, str]]:
        """
        Detects and resolves item rejection phrases (e.g. 'remove the waterfall',
        'skip the museum', 'remove the second one', 'skip 3rd').
        Returns the resolved VisibleItemReference or normalized place name string.
        """
        if not query or not str(query).strip():
            return None

        clean = str(query).strip().lower()

        non_item_keywords = {"driving", "walking", "flight", "budget", "money", "days", "nights", "too much", "more than"}
        if any(kw in clean for kw in non_item_keywords):
            return None

        rejection_pattern = (
            r"^(?:remove|skip|drop|delete|exclude|omit)\s+(?:the\s+)?(.+)$"
        )
        match = re.search(rejection_pattern, clean)
        if not match:
            # Trailing pattern, e.g. 'skip 2nd', 'drop place 1'
            match = re.search(r"\b(?:skip|remove|drop|exclude)\s+(?:the\s+)?([a-z0-9\s]+)$", clean)
        if not match:
            return None

        target = match.group(1).strip()
        target_clean = re.sub(r"\b(?:place|attraction|stop|hotel|restaurant|cafe|café|one)\b", "", target).strip()

        # 1. Try resolving through visible items
        ref = self.resolve_item_reference(target_clean or target, entity_type=entity_type)
        if ref:
            return ref

        # 2. Check candidate name substrings directly
        candidates = self.visible_places + self.visible_hotels + self.visible_restaurants
        for c in candidates:
            if target.lower() in c.name.lower() or c.name.lower() in target.lower():
                return c

        # 3. Return clean name string for state rejection tracking
        clean_name = re.sub(r"^(?:the|a|an)\s+", "", target).strip()
        if len(clean_name) >= 2:
            return clean_name

        return None


def format_conversation_context(context: Optional[ConversationContext]) -> str:
    """Formats a concise textual summary of transient conversation context for the model prompt."""
    if not context:
        return ""
    lines = []
    if context.active_question:
        aq = context.active_question
        lines.append("\n[Active Clarification Question Waiting for Traveler's Answer]")
        lines.append(f"- Field: {aq.field} (type: {aq.expected_type}, scope: {aq.scope})")
        if aq.reason:
            lines.append(f"- Reason asked: {aq.reason}")
        if aq.prompt_text:
            lines.append(f"- Prompt asked: \"{aq.prompt_text}\"")

    if context.visible_hotels:
        lines.append(f"\n[Currently Visible Hotel Cards ({len(context.visible_hotels)})]")
        for h in context.visible_hotels[:5]:
            rate_str = f"₹{h.price_per_night:.0f}/night" if h.price_per_night else "Price unavailable"
            lines.append(f"  #{h.index}: {h.name} ({rate_str})")

    if context.visible_places:
        lines.append(f"\n[Currently Visible Place Cards ({len(context.visible_places)})]")
        for p in context.visible_places[:5]:
            lines.append(f"  #{p.index}: {p.name}")

    if context.visible_restaurants:
        lines.append(f"\n[Currently Visible Restaurant Cards ({len(context.visible_restaurants)})]")
        for r in context.visible_restaurants[:5]:
            lines.append(f"  #{r.index}: {r.name}")

    return "\n".join(lines) if lines else ""


class SessionState(BaseModel):
    """
    Unified session container holding canonical TripState and transient ConversationContext.
    Preserves separation between durable trip facts and active conversation flow.
    """
    conversation_id: str = Field(..., description="Unique session identifier")
    trip_state: TripState = Field(default_factory=TripState)
    conversation_context: ConversationContext = Field(default_factory=ConversationContext)


# --- In-Memory Session Repository (Step 12.6) ---

_SESSION_STORE: Dict[str, SessionState] = {}


def get_or_create_session(conversation_id: Optional[str] = None) -> SessionState:
    """
    Retrieves or creates a SessionState for the conversation_id.
    Synchronizes with the canonical TripState repository for backwards compatibility.
    """
    from app.agent.state import get_or_create_state

    # 1. Check existing session
    if conversation_id and conversation_id in _SESSION_STORE:
        return _SESSION_STORE[conversation_id]

    # 2. Synchronize with trip state repository
    trip_st = get_or_create_state(conversation_id)
    cid = trip_st.conversation_id

    if cid in _SESSION_STORE:
        session = _SESSION_STORE[cid]
        session.trip_state = trip_st
        return session

    session = SessionState(
        conversation_id=cid,
        trip_state=trip_st,
        conversation_context=ConversationContext(),
    )
    _SESSION_STORE[cid] = session
    return session


def get_session(conversation_id: str) -> Optional[SessionState]:
    """Retrieves an existing SessionState, or None if not found."""
    if conversation_id in _SESSION_STORE:
        return _SESSION_STORE[conversation_id]

    from app.agent.state import get_state
    trip_st = get_state(conversation_id)
    if trip_st:
        session = SessionState(
            conversation_id=conversation_id,
            trip_state=trip_st,
            conversation_context=ConversationContext(),
        )
        _SESSION_STORE[conversation_id] = session
        return session
    return None


def save_session(session: SessionState) -> SessionState:
    """Saves the SessionState and synchronizes the canonical TripState."""
    from app.agent.state import save_state
    save_state(session.trip_state)
    _SESSION_STORE[session.conversation_id] = session
    return session


def reset_session(conversation_id: str) -> SessionState:
    """
    Resets both canonical TripState and transient ConversationContext.
    Preserves the conversation_id.
    """
    from app.agent.state import reset_state
    new_trip = reset_state(conversation_id)
    session = SessionState(
        conversation_id=conversation_id,
        trip_state=new_trip,
        conversation_context=ConversationContext(),
    )
    _SESSION_STORE[conversation_id] = session
    return session


def delete_session(conversation_id: str) -> bool:
    """
    Deletes the session and its canonical TripState.
    Returns True if found and deleted, False otherwise.
    """
    from app.agent.state import delete_state
    trip_deleted = delete_state(conversation_id)
    sess_deleted = _SESSION_STORE.pop(conversation_id, None) is not None
    return trip_deleted or sess_deleted


def clear_all_sessions() -> None:
    """Clears all session states and trip states (useful for test isolation)."""
    from app.agent.state import clear_all_states
    _SESSION_STORE.clear()
    clear_all_states()

