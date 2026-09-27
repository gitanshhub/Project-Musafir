"""
Musafir M3.2 — Phases B to E: End-to-End & Realistic Conversation Validation Suite
Tests the complete conversational pipeline (/agent/chat and SessionState) across:
- Phase B: High-frequency real-world phrasing (voice-to-text fillers, run-ons, multi-message accumulation, Hinglish, typos, WhatsApp forwards)
- Phase C: Common Indian-context ambiguities (historical city aliases, budget shorthand)
- Phase D: State integrity (corrections after the fact, sequential corrections, non-answer replies to pending questions, negative overrides, explicit < derived)
- Phase E: Duplicate and out-of-order delivery (verbatim retry deduplication, near-simultaneous conflict resolution, expired/missing session)
"""

import sys
import os
import unittest
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from main import app
from app.agent.state import clear_all_states, get_state, TripState
from app.agent.context import clear_all_sessions, get_session
from app.agent.state_update import apply_trip_state_update
from app.agent.semantics import derive_trip_inferences
from app.agent.extraction import extract_trip_slots


class TestPhasesBtoE(unittest.TestCase):
    def setUp(self):
        clear_all_states()
        clear_all_sessions()
        self.client = TestClient(app)

    # =========================================================================
    # Phase B — High-Frequency Real-World Phrasing
    # =========================================================================

    def test_b1_voice_to_text_fillers_and_run_ons(self):
        """B1: Voice-to-text with fillers ('um, basically, like') and run-ons with 'and'."""
        msg = "So um basically like I want to plan a 1-day walking trip to Kochi and see historical places"
        resp = self.client.post("/agent/chat", json={"message": msg})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        summary = data.get("state_summary") or {}

        self.assertEqual(summary.get("destination"), "Kochi")
        self.assertEqual(summary.get("number_of_days"), 1)
        self.assertEqual(summary.get("travel_mode"), "walking")
        # Ensure filler words were not misfiled into dietary or interests
        self.assertNotIn("like", summary.get("interests", []))
        self.assertNotIn("basically", summary.get("interests", []))

    def test_b2_multi_message_accumulation(self):
        """B2: Multi-message accumulation across small turns: destination, duration, mode."""
        # Turn 1: Destination
        r1 = self.client.post("/agent/chat", json={"message": "trip to Kochi"})
        self.assertEqual(r1.status_code, 200)
        cid = r1.json().get("conversation_id")
        s1 = r1.json().get("state_summary") or {}
        self.assertEqual(s1.get("destination"), "Kochi")

        # Turn 2: Duration
        r2 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "just for a day"})
        self.assertEqual(r2.status_code, 200)
        s2 = r2.json().get("state_summary") or {}
        self.assertEqual(s2.get("destination"), "Kochi")
        self.assertEqual(s2.get("number_of_days"), 1)

        # Turn 3: Mode
        r3 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "walking"})
        self.assertEqual(r3.status_code, 200)
        s3 = r3.json().get("state_summary") or {}
        self.assertEqual(s3.get("destination"), "Kochi")
        self.assertEqual(s3.get("number_of_days"), 1)
        self.assertEqual(s3.get("travel_mode"), "walking")
        self.assertFalse(s3.get("hotel_required"))

    def test_b3_hinglish_code_switching(self):
        """B3: Hinglish phrasing extracts destination, duration, and walking mode."""
        msg = "Kochi jaana hai ek din ke liye, walking pe"
        resp = self.client.post("/agent/chat", json={"message": msg})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        summary = data.get("state_summary") or {}

        self.assertEqual(summary.get("destination"), "Kochi")
        self.assertEqual(summary.get("number_of_days"), 1)
        self.assertEqual(summary.get("travel_mode"), "walking")

    def test_b4_mobile_compressed_text(self):
        """B4: Mobile compressed text without space between digit and day."""
        msg = "Plan a 1day walking trip to Kochi"
        resp = self.client.post("/agent/chat", json={"message": msg})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        summary = data.get("state_summary") or {}

        self.assertEqual(summary.get("destination"), "Kochi")
        self.assertEqual(summary.get("number_of_days"), 1)
        self.assertEqual(summary.get("travel_mode"), "walking")

    def test_b5_copy_pasted_noisy_text(self):
        """B5: WhatsApp forward with extraneous parentheticals."""
        msg = "Kochi trip plan - 1 day - walking (sent from someone's itinerary)"
        resp = self.client.post("/agent/chat", json={"message": msg})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        summary = data.get("state_summary") or {}

        self.assertEqual(summary.get("destination"), "Kochi")
        self.assertEqual(summary.get("number_of_days"), 1)
        self.assertEqual(summary.get("travel_mode"), "walking")

    # =========================================================================
    # Phase C — Common Indian-Context Ambiguities
    # =========================================================================

    def test_c1_canonical_destination_aliases(self):
        """C1: Historical and colonial Indian city aliases normalize to canonical names."""
        aliases = [
            ("Cochin", "Kochi"),
            ("Bangalore", "Bengaluru"),
            ("Bombay", "Mumbai"),
            ("Pondicherry", "Puducherry"),
            ("Madras", "Chennai"),
            ("Benaras", "Varanasi"),
            ("Calcutta", "Kolkata"),
        ]
        for alias, canonical in aliases:
            msg = f"Plan a 2-day driving trip to {alias}"
            resp = self.client.post("/agent/chat", json={"message": msg})
            self.assertEqual(resp.status_code, 200, f"Failed for alias {alias}")
            summary = resp.json().get("state_summary") or {}
            self.assertEqual(summary.get("destination"), canonical, f"Alias {alias} did not resolve to {canonical}")

    def test_c3_budget_shorthand_formats(self):
        """C3: Budget shorthand: '20k', '1.5L', '20000rs', '₹20,000'."""
        cases = [
            ("Hotel budget is 20k per night", 20000.0),
            ("Hotel budget 1.5L total", 150000.0),
            ("Can spend 20000rs per night on hotel", 20000.0),
            ("Accommodation budget ₹5,000 per night", 5000.0),
        ]
        for msg, expected_amt in cases:
            slots = extract_trip_slots(msg)
            amt = slots.hotel_total_budget if "total" in msg else slots.hotel_budget
            self.assertEqual(amt, expected_amt, f"Failed parsing amount from '{msg}', got {amt}")

    # =========================================================================
    # Phase D — State Integrity Under Realistic Conversation Flow
    # =========================================================================

    def test_d1_correction_after_the_fact(self):
        """D1: User changes destination after turn 1; previous destination is completely overwritten."""
        r1 = self.client.post("/agent/chat", json={"message": "Trip to Goa for 3 days"})
        self.assertEqual(r1.status_code, 200)
        cid = r1.json().get("conversation_id")
        self.assertEqual(r1.json().get("state_summary", {}).get("destination"), "Goa")

        r2 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "Actually make it Pune, not Goa"})
        self.assertEqual(r2.status_code, 200)
        s2 = r2.json().get("state_summary") or {}
        self.assertEqual(s2.get("destination"), "Pune")
        self.assertNotEqual(s2.get("destination"), "Goa")

    def test_d2_multiple_corrections_in_sequence(self):
        """D2: Multiple sequential corrections leave only the latest stated destination."""
        r1 = self.client.post("/agent/chat", json={"message": "Goa for 2 days"})
        cid = r1.json().get("conversation_id")

        r2 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "Actually make it Jaipur"})
        self.assertEqual(r2.json().get("state_summary", {}).get("destination"), "Jaipur")

        r3 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "Actually make it Udaipur"})
        s3 = r3.json().get("state_summary") or {}
        self.assertEqual(s3.get("destination"), "Udaipur")

    def test_d3_unrelated_reply_to_pending_question(self):
        """D3: When agent asks for travel mode, user provides diet; diet is recorded, mode remains pending."""
        # Turn 1: destination & duration provided, mode not provided
        r1 = self.client.post("/agent/chat", json={"message": "Trip to Jaipur for 3 days"})
        cid = r1.json().get("conversation_id")
        s1 = r1.json().get("state_summary") or {}
        self.assertEqual(s1.get("destination"), "Jaipur")
        self.assertEqual(s1.get("number_of_days"), 3)

        # Turn 2: User says "we are vegetarian"
        r2 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "We are pure vegetarian"})
        s2 = r2.json().get("state_summary") or {}
        # Dietary preferences correctly captured
        self.assertIn("vegetarian", s2.get("dietary_preferences", []))
        # Destination was not corrupted
        self.assertEqual(s2.get("destination"), "Jaipur")

    def test_d4_explicit_negative_override(self):
        """D4: Multi-day trip with explicit 'no need for a hotel' sets hotel_required=False."""
        msg = "2 days in Kochi, no need for a hotel, walking"
        resp = self.client.post("/agent/chat", json={"message": msg})
        self.assertEqual(resp.status_code, 200)
        summary = resp.json().get("state_summary") or {}

        self.assertEqual(summary.get("destination"), "Kochi")
        self.assertEqual(summary.get("number_of_days"), 2)
        self.assertEqual(summary.get("travel_mode"), "walking")
        # Explicit negative override guarantees hotel_required=False despite days=2
        self.assertFalse(summary.get("hotel_required"))

    def test_d5_explicit_nights_lower_than_derived(self):
        """D5: Explicit nights lower than days - 1 is retained and not overwritten upward."""
        msg = "Plan a 5 days 3 nights trip to Manali by car"
        resp = self.client.post("/agent/chat", json={"message": msg})
        self.assertEqual(resp.status_code, 200)
        summary = resp.json().get("state_summary") or {}

        self.assertEqual(summary.get("destination"), "Manali")
        self.assertEqual(summary.get("number_of_days"), 5)
        self.assertEqual(summary.get("number_of_nights"), 3)  # Explicit 3, not 5-1=4!
        self.assertTrue(summary.get("hotel_required"))

    # =========================================================================
    # Phase E — Duplicate and Out-of-Order Delivery
    # =========================================================================

    def test_e1_verbatim_duplicate_message_no_list_duplication(self):
        """E1: Verbatim identical message processed twice does not duplicate entries in list fields."""
        msg = "Plan a 3-day walking trip to Jaipur, pure vegetarian, interested in forts"
        r1 = self.client.post("/agent/chat", json={"message": msg})
        cid = r1.json().get("conversation_id")

        # Retry exact same message on same conversation
        r2 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": msg})
        self.assertEqual(r2.status_code, 200)
        summary = r2.json().get("state_summary") or {}

        # Lists must be deduplicated
        diets = summary.get("dietary_preferences", [])
        interests = summary.get("interests", [])
        self.assertEqual(diets.count("vegetarian"), 1, f"Dietary preference duplicated: {diets}")
        self.assertEqual(interests.count("forts"), 1, f"Interest duplicated: {interests}")

    def test_e2_conflicting_messages_latest_wins(self):
        """E2: Near-simultaneous conflicting updates: latest message deterministically wins."""
        r1 = self.client.post("/agent/chat", json={"message": "Trip to Jaipur for 3 days"})
        cid = r1.json().get("conversation_id")
        self.assertEqual(r1.json().get("state_summary", {}).get("number_of_days"), 3)

        r2 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "actually 4 days"})
        self.assertEqual(r2.status_code, 200)
        summary = r2.json().get("state_summary") or {}
        self.assertEqual(summary.get("number_of_days"), 4)

    def test_e3_missing_or_expired_session(self):
        """E3: Client sends unknown or expired conversation_id: returns 404 cleanly, never 500."""
        random_cid = str(uuid4())
        resp = self.client.post("/agent/chat", json={"conversation_id": random_cid, "message": "I am vegetarian"})
        self.assertEqual(resp.status_code, 404)
        self.assertIn("not found", resp.text.lower())


if __name__ == "__main__":
    unittest.main()
