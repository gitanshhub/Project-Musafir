"""Focused tests for the M3.2 deterministic-to-LLM extraction fallback."""

import sys
import unittest

sys.path.insert(0, ".")

from app.agent.context import SessionState
from app.agent.extraction import extract_trip_slots
from app.agent.fast_path import resolve_fast_path
from app.agent.llm_extraction import (
    FallbackCategory,
    _parse_response,
    detect_fallback_trigger,
)
from app.agent.state import TripState
from app.llm.openrouter_client import LLMProviderError, LLMResponse


class FakeClient:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls = 0

    def send_message(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return LLMResponse(content=self.content, model="fake")


class TestM32LLMFallback(unittest.TestCase):
    def test_h37_is_suspicion_trigger(self):
        message = "What's the weather like in Goa this time of year?"
        trigger = detect_fallback_trigger(message, extract_trip_slots(message), TripState())
        self.assertIsNotNone(trigger)
        self.assertEqual(trigger.category, FallbackCategory.SUSPICION)
        self.assertIn("missing_trip_context", trigger.checks)

    def test_h34_is_suspicion_trigger(self):
        message = "5 nights in Kerala, hotel's already sorted for the first 3, still need something for the last 2."
        trigger = detect_fallback_trigger(message, extract_trip_slots(message), TripState())
        self.assertIsNotNone(trigger)
        self.assertEqual(trigger.category, FallbackCategory.SUSPICION)
        self.assertIn("ambiguous_accommodation_numbers", trigger.checks)

    def test_fallback_payload_parses_transient_non_trip_decision(self):
        slots, process_as_trip = _parse_response(
            '{"process_as_trip": false, "slots": {}, "confidence": {}}'
        )
        self.assertFalse(process_as_trip)
        self.assertFalse(slots.explicit_fields)

    def test_fallback_fills_missing_duration(self):
        session = SessionState(conversation_id="test")
        client = FakeClient(
            '{"process_as_trip": true, "slots": {"number_of_days": 2}, '
            '"confidence": {"number_of_days": "HIGH"}}'
        )
        result = resolve_fast_path("Weekend getaway in Goa", session, llm_client=client)
        self.assertTrue(result.matched)
        self.assertEqual(result.state_updates["number_of_days"], 2)
        self.assertEqual(client.calls, 1)

    def test_fallback_failure_does_not_raise(self):
        session = SessionState(conversation_id="test")
        client = FakeClient(error=LLMProviderError("provider unavailable"))
        result = resolve_fast_path("Quick trip to Kochi", session, llm_client=client)
        self.assertIsNotNone(result)
        self.assertEqual(client.calls, 1)


if __name__ == "__main__":
    unittest.main()
