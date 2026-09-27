"""Targeted LLM fallback for cases the deterministic extractor cannot trust."""

import json
import logging
import re
import time
from enum import Enum
from typing import Any, Dict, Optional, Set

from pydantic import BaseModel, Field

from app.agent.extraction import ExtractedTripSlots, SlotConfidence
from app.agent.state import TripState
from app.llm.openrouter_client import LLMError, OpenRouterClient


logger = logging.getLogger("musafir.agent.llm_extraction")


class FallbackCategory(str, Enum):
    INCOMPLETENESS = "A_INCOMPLETENESS"
    SUSPICION = "B_SUSPICION"


class FallbackTrigger(BaseModel):
    category: FallbackCategory
    checks: list[str] = Field(default_factory=list)
    suspicious_fields: Set[str] = Field(default_factory=set)


class FallbackResult(BaseModel):
    succeeded: bool = False
    slots: Optional[ExtractedTripSlots] = None
    process_as_trip: Optional[bool] = None
    suspicious_fields: Set[str] = Field(default_factory=set)
    trigger: Optional[FallbackTrigger] = None
    latency_ms: int = 0
    error: Optional[str] = None


def detect_fallback_trigger(
    message: str,
    extracted: ExtractedTripSlots,
    state: TripState,
) -> Optional[FallbackTrigger]:
    """Return only evidence-backed Category A/B triggers."""
    text = message.lower()
    checks: list[str] = []
    suspicious_fields: Set[str] = set()

    if re.search(r"\b(?:hotel|accommodation|stay)\b", text) and re.search(
        r"\b(?:first|last|remaining|still\s+need|already\s+sorted|partially)\b", text
    ):
        checks.append("ambiguous_accommodation_numbers")
        suspicious_fields.update({"accommodation_required", "accommodation_booked", "hotel_search_required", "hotel_budget"})

    destinations = re.findall(r"\b(?:goa|kerala|jaipur|kochi|udaipur|mumbai|manali|agra)\b", text)
    if len(set(destinations)) > 1:
        checks.append("competing_destination_candidates")
        suspicious_fields.add("destination")

    has_trip_context = bool(re.search(
        r"\b(?:trip|travel|tour|vacation|holiday|plan|planning|going|visit|stay|days?|nights?|weekend|itinerary)\b",
        text,
    ))
    if extracted.destination and not has_trip_context:
        checks.append("missing_trip_context")
        suspicious_fields.add("destination")

    if checks:
        return FallbackTrigger(
            category=FallbackCategory.SUSPICION,
            checks=checks,
            suspicious_fields=suspicious_fields,
        )

    missing = []
    for field in ("destination", "number_of_days", "travel_mode"):
        if getattr(extracted, field) is None or extracted.confidence.get(field) == SlotConfidence.LOW:
            missing.append(field)
    if missing:
        return FallbackTrigger(
            category=FallbackCategory.INCOMPLETENESS,
            checks=[f"missing_or_low:{field}" for field in missing],
        )
    return None


def _prompt(
    message: str,
    extracted: ExtractedTripSlots,
    trigger: FallbackTrigger,
) -> str:
    known = extracted.high_confidence_updates()
    fields = ["destination", "number_of_days", "number_of_nights", "travel_mode",
              "accommodation_required", "accommodation_booked", "hotel_search_required",
              "hotel_budget", "dietary_preferences", "interests"]
    if trigger.category == FallbackCategory.INCOMPLETENESS:
        task = "Resolve only the missing or low-confidence fields. Do not guess; use null when unresolved."
    else:
        task = (
            "Independently verify the suspicious interpretation. Decide whether this is a trip-planning "
            "request. Set process_as_trip=false for a general/non-trip message."
        )
    return f"""You are a strict trip-slot extraction component.
{task}
User message: {message!r}
Deterministic high-confidence values (do not override unless clearly contradicted): {json.dumps(known, default=str)}
Trigger: {trigger.category.value}; checks: {trigger.checks}
Return JSON only with this shape:
{{"process_as_trip": true, "slots": {{"field": value}}, "confidence": {{"field": "HIGH"|"LOW"}}}}
Allowed fields: {fields}
Use null or omit fields you cannot resolve. Never invent a city, duration, mode, budget, or hotel state."""


def _parse_response(content: str) -> tuple[ExtractedTripSlots, Optional[bool]]:
    raw = content.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("fallback response must be a JSON object")
    values = payload.get("slots") or {}
    if not isinstance(values, dict):
        raise ValueError("fallback slots must be an object")
    confidence = payload.get("confidence") or {}
    allowed = set(ExtractedTripSlots.model_fields) - {"confidence", "explicit_fields", "raw_message"}
    clean: Dict[str, Any] = {k: v for k, v in values.items() if k in allowed and v is not None}
    slots = ExtractedTripSlots(**clean, raw_message="")
    for field, value in clean.items():
        slots.confidence[field] = SlotConfidence(str(confidence.get(field, "LOW")).upper())
        slots.explicit_fields.append(field)
    process_as_trip = payload.get("process_as_trip")
    if process_as_trip is not None and not isinstance(process_as_trip, bool):
        raise ValueError("process_as_trip must be boolean")
    return slots, process_as_trip


def run_fallback(
    message: str,
    extracted: ExtractedTripSlots,
    trigger: FallbackTrigger,
    client: OpenRouterClient,
) -> FallbackResult:
    started = time.perf_counter()
    try:
        response = client.send_message(
            messages=[
                {"role": "system", "content": "Return only valid JSON. Do not include markdown."},
                {"role": "user", "content": _prompt(message, extracted, trigger)},
            ],
            temperature=0.0,
        )
        slots, process_as_trip = _parse_response(response.content or "")
        result = FallbackResult(
            succeeded=True,
            slots=slots,
            process_as_trip=process_as_trip,
            suspicious_fields=trigger.suspicious_fields,
            trigger=trigger,
        )
    except (LLMError, ValueError, TypeError, json.JSONDecodeError) as exc:
        result = FallbackResult(
            trigger=trigger,
            suspicious_fields=trigger.suspicious_fields,
            error=str(exc),
        )
        logger.warning("LLM extraction fallback failed: %s", exc)
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "[LLMFallback] category=%s checks=%s succeeded=%s latency_ms=%s",
        trigger.category.value,
        ",".join(trigger.checks),
        result.succeeded,
        result.latency_ms,
    )
    return result


def merge_fallback_slots(
    deterministic: ExtractedTripSlots,
    fallback: ExtractedTripSlots,
    override_fields: Set[str],
) -> ExtractedTripSlots:
    merged = deterministic.model_copy(deep=True)
    for field in fallback.explicit_fields:
        if field not in override_fields and field in deterministic.explicit_fields:
            continue
        setattr(merged, field, getattr(fallback, field))
        merged.confidence[field] = fallback.confidence.get(field, SlotConfidence.LOW)
        if field not in merged.explicit_fields:
            merged.explicit_fields.append(field)
    return merged
