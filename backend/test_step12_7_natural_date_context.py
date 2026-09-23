"""
Project Musafir — Step 12.7 Natural Date, Context & State Fix Test Suite
Verifies:
1. Duration retention across turns
2. Duration + check-in derives check-out & hotel nights automatically
3. Date range parsing ("24 Sept - 29 Sept" -> 2026-09-24 to 2026-09-29, 6 days, 5 nights)
4. Screenshot bug prevention: ActiveQuestion = hotel_budget with "24 sept - 29 sept" never sets budget to 24
5. Natural date formats: "24 September", "24th September", "24 Sep", "24 sept", "Sep 24", "24/09", "24-09"
6. Relative dates relative to Asia/Kolkata ("tomorrow", "next Friday", etc.)
7. One-day day trip produces 0 hotel nights and None nightly hotel budget
8. Context retention across turns: Kerala + 5 days + ₹3500 total + 24 Sept all preserved
9. Duration mutation ("make it 7 days") updates check-out and nights, preserving budget & destination
10. Destination pivot preserves compatible fields (duration, dates, budget) while invalidating destination results
"""

import unittest
import uuid
from datetime import date, timedelta
from fastapi.testclient import TestClient

from main import app
from app.agent.state import TripState
from app.agent.context import (
    SessionState,
    get_or_create_session,
    get_session,
    save_session,
    clear_all_sessions,
)
from app.agent.semantics import (
    get_current_date,
    calculate_nights,
    calculate_nightly_hotel_budget,
    parse_currency_amount,
    parse_duration_days,
    parse_duration_nights,
    resolve_relative_date,
    parse_date_range,
    derive_trip_dates,
)
from app.agent.state_update import apply_trip_state_update, get_next_active_question
from app.agent.fast_path import resolve_fast_path, FastPathResult


class TestStep12_7NaturalDateContext(unittest.TestCase):
    def setUp(self):
        clear_all_sessions()
        self.client = TestClient(app)

    def tearDown(self):
        clear_all_sessions()

    # -------------------------------------------------------------------------
    # Test 1: Duration Retention
    # -------------------------------------------------------------------------
    def test_01_duration_retention(self):
        """Input: '5 days' -> duration_days = 5, number_of_nights = 4."""
        days = parse_duration_days("5 days")
        self.assertEqual(days, 5)
        nights = calculate_nights(number_of_days=days)
        self.assertEqual(nights, 4)

        state = TripState(destination="Kerala")
        apply_trip_state_update(state, {"number_of_days": 5})
        self.assertEqual(state.number_of_days, 5)
        self.assertEqual(state.number_of_nights, 4)

    # -------------------------------------------------------------------------
    # Test 2: Duration + Check-In Derives Check-Out & Nights
    # -------------------------------------------------------------------------
    def test_02_duration_plus_check_in_derives_checkout(self):
        """
        User provides 5 days, then 24 Sept.
        Expected: check-in = 2026-09-24, check-out = 2026-09-28 (5 days, 4 nights).
        """
        ref_year = get_current_date().year
        d_start = resolve_relative_date("24 Sept")
        self.assertIsNotNone(d_start)
        self.assertEqual(d_start.month, 9)
        self.assertEqual(d_start.day, 24)

        state = TripState(destination="Kerala", number_of_days=5)
        apply_trip_state_update(state, {"trip_start_date": d_start})

        self.assertEqual(state.number_of_days, 5)
        self.assertEqual(state.trip_start_date, d_start)
        expected_checkout = d_start + timedelta(days=4)
        self.assertEqual(state.trip_end_date, expected_checkout)
        self.assertEqual(state.number_of_nights, 4)

    # -------------------------------------------------------------------------
    # Test 3: Date Range Parsing
    # -------------------------------------------------------------------------
    def test_03_date_range_parsing(self):
        """
        '24 Sept - 29 Sept' or '24 Sept to 29 Sept'
        check_in = 24 Sept, check_out = 29 Sept, duration = 6 days, nights = 5.
        """
        dr1 = parse_date_range("24 Sept - 29 Sept")
        self.assertIsNotNone(dr1)
        start1, end1 = dr1
        self.assertEqual(start1.day, 24)
        self.assertEqual(start1.month, 9)
        self.assertEqual(end1.day, 29)
        self.assertEqual(end1.month, 9)

        dr2 = parse_date_range("from 24 Sept to 29 Sept")
        self.assertIsNotNone(dr2)
        self.assertEqual(dr2, (start1, end1))

        state = TripState(destination="Kerala")
        apply_trip_state_update(state, {"trip_start_date": start1, "trip_end_date": end1})
        self.assertEqual(state.trip_start_date, start1)
        self.assertEqual(state.trip_end_date, end1)
        self.assertEqual(state.number_of_days, 6)
        self.assertEqual(state.number_of_nights, 5)

    # -------------------------------------------------------------------------
    # Test 4: Screenshot Bug Prevention (Date Never Overwrites Budget)
    # -------------------------------------------------------------------------
    def test_04_screenshot_bug_prevention_date_never_becomes_budget(self):
        """
        When ActiveQuestion = hotel_total_budget, and user says "24 sept - 29 sept"
        budget must remain unchanged and dates must be extracted!
        Never hotel_total_budget = 24!
        """
        # 1. parse_currency_amount must NOT extract 24 from "24 sept - 29 sept"
        amt = parse_currency_amount("24 sept - 29 sept")
        self.assertIsNone(amt, "Date range string must not be parsed as currency amount")

        amt2 = parse_currency_amount("24 sept")
        self.assertIsNone(amt2, "Single date string must not be parsed as currency amount")

        # 2. FastPath with active question hotel_total_budget
        cid = str(uuid.uuid4())
        session = get_or_create_session(cid)
        session.trip_state.destination = "Kerala"
        session.trip_state.number_of_days = 5
        session.conversation_context.set_active_question(
            field="hotel_total_budget",
            expected_type="money",
            scope="accommodation",
            prompt_text="What's your hotel budget?",
        )
        save_session(session)

        # Fast path evaluation
        fp_res = resolve_fast_path("24 sept - 29 sept", session)
        self.assertTrue(fp_res.matched)
        self.assertIn("trip_start_date", fp_res.state_updates)
        self.assertNotIn("hotel_total_budget", fp_res.state_updates)
        self.assertNotIn("hotel_budget", fp_res.state_updates)

    # -------------------------------------------------------------------------
    # Test 5: Budget Total vs Budget Per Night Semantics
    # -------------------------------------------------------------------------
    def test_05_budget_total_and_per_night_semantics(self):
        """₹3500 total vs ₹2000 per night."""
        state = TripState(destination="Kerala", number_of_days=5, number_of_nights=4)
        apply_trip_state_update(state, {"hotel_total_budget": 3500.0})
        self.assertEqual(state.hotel_total_budget, 3500.0)
        self.assertEqual(state.hotel_budget, 875.0)

        # Explicit per night
        apply_trip_state_update(state, {"hotel_budget": 2000.0})
        self.assertEqual(state.hotel_budget, 2000.0)

    # -------------------------------------------------------------------------
    # Test 6: Natural Date Formats
    # -------------------------------------------------------------------------
    def test_06_natural_date_formats(self):
        """All variations of 24 Sept must resolve to the same date."""
        expected_month = 9
        expected_day = 24

        formats = [
            "24 September",
            "24th September",
            "24 Sep",
            "24 sept",
            "Sep 24",
            "September 24th",
            "24/09",
            "24-09",
            "starting 24 Sept",
            "I'll start on 24th September",
        ]

        for fmt in formats:
            resolved = resolve_relative_date(fmt)
            self.assertIsNotNone(resolved, f"Failed to parse natural date: '{fmt}'")
            self.assertEqual(resolved.month, expected_month, f"Month mismatch for '{fmt}'")
            self.assertEqual(resolved.day, expected_day, f"Day mismatch for '{fmt}'")

    # -------------------------------------------------------------------------
    # Test 7: Relative Dates
    # -------------------------------------------------------------------------
    def test_07_relative_dates(self):
        """tomorrow, day after tomorrow, this weekend."""
        today = get_current_date()
        tmrw = resolve_relative_date("tomorrow")
        self.assertEqual(tmrw, today + timedelta(days=1))

        dat = resolve_relative_date("day after tomorrow")
        self.assertEqual(dat, today + timedelta(days=2))

    # -------------------------------------------------------------------------
    # Test 8: Context Retention Across 4-Turn Sequence
    # -------------------------------------------------------------------------
    def test_08_context_retention_across_sequence(self):
        """
        Turn 1: Kerala
        Turn 2: 5 days
        Turn 3: ₹3500 total
        Turn 4: 24 Sept
        Expected: All 4 facts present, check-out derived, 4 nights, ₹875/night.
        """
        cid = str(uuid.uuid4())
        session = get_or_create_session(cid)

        # 1. Kerala
        apply_trip_state_update(session.trip_state, {"destination": "Kerala"})
        # 2. 5 days
        apply_trip_state_update(session.trip_state, {"number_of_days": 5})
        # 3. ₹3500 total
        apply_trip_state_update(session.trip_state, {"hotel_total_budget": 3500.0})
        # 4. 24 Sept
        d_start = resolve_relative_date("24 Sept")
        apply_trip_state_update(session.trip_state, {"trip_start_date": d_start})

        st = session.trip_state
        self.assertEqual(st.destination, "Kerala")
        self.assertEqual(st.number_of_days, 5)
        self.assertEqual(st.number_of_nights, 4)
        self.assertEqual(st.hotel_total_budget, 3500.0)
        self.assertEqual(st.hotel_budget, 875.0)
        self.assertEqual(st.trip_start_date, d_start)
        self.assertEqual(st.trip_end_date, d_start + timedelta(days=4))

    # -------------------------------------------------------------------------
    # Test 9: Duration Mutation Preserves Check-in & Budget
    # -------------------------------------------------------------------------
    def test_09_duration_mutation_recalculates_checkout_preserves_other(self):
        """
        Existing: 5 days, 24 Sept -> 28 Sept (4 nights), budget 3500.
        User: 'make it 7 days'
        Expected: 7 days, 24 Sept -> 30 Sept (6 nights), budget 3500, nightly budget recalculated.
        """
        d_start = resolve_relative_date("24 Sept")
        state = TripState(
            destination="Kerala",
            number_of_days=5,
            trip_start_date=d_start,
            hotel_total_budget=3500.0,
        )
        apply_trip_state_update(state, {"trip_start_date": d_start})
        self.assertEqual(state.trip_end_date, d_start + timedelta(days=4))

        # Mutation
        apply_trip_state_update(state, {"number_of_days": 7})

        self.assertEqual(state.destination, "Kerala")
        self.assertEqual(state.number_of_days, 7)
        self.assertEqual(state.number_of_nights, 6)
        self.assertEqual(state.trip_start_date, d_start)
        self.assertEqual(state.trip_end_date, d_start + timedelta(days=6))
        self.assertEqual(state.hotel_total_budget, 3500.0)
        self.assertAlmostEqual(state.hotel_budget, 3500.0 / 6, places=2)

    # -------------------------------------------------------------------------
    # Test 10: One-Day Day Trip Semantics
    # -------------------------------------------------------------------------
    def test_10_one_day_trip_zero_nights(self):
        """duration = 1 day -> hotel_nights = 0, hotel_budget = None."""
        state = TripState(destination="Delhi", number_of_days=1, hotel_total_budget=2000.0)
        apply_trip_state_update(state, {"number_of_days": 1, "hotel_total_budget": 2000.0})
        self.assertEqual(state.number_of_nights, 0)
        self.assertIsNone(state.hotel_budget)

    # -------------------------------------------------------------------------
    # Test 11: Destination Pivot Preserves Compatible State
    # -------------------------------------------------------------------------
    def test_11_destination_pivot_preserves_duration_and_budget(self):
        """
        Kashmir, 5 days, 3500 budget, 24 Sept.
        Pivot to Kerala -> destination = Kerala, duration=5, budget=3500, dates preserved,
        hotel_selection & places cleared.
        """
        d_start = resolve_relative_date("24 Sept")
        state = TripState(
            destination="Kashmir",
            number_of_days=5,
            hotel_total_budget=3500.0,
            trip_start_date=d_start,
        )
        apply_trip_state_update(state, {"trip_start_date": d_start})
        state.hotel_selection = {"name": "Kashmir Resort"}

        # Pivot
        apply_trip_state_update(state, {"destination": "Kerala"})

        self.assertEqual(state.destination, "Kerala")
        self.assertEqual(state.number_of_days, 5)
        self.assertEqual(state.number_of_nights, 4)
        self.assertEqual(state.hotel_total_budget, 3500.0)
        self.assertEqual(state.hotel_budget, 875.0)
        self.assertEqual(state.trip_start_date, d_start)
        self.assertIsNone(state.hotel_selection)

    # -------------------------------------------------------------------------
    # Test 12: Explicit Dates Override Inferred Duration
    # -------------------------------------------------------------------------
    def test_12_explicit_dates_override_inferred_duration(self):
        """
        Previously: 5 days.
        User says: "24 Sept to 29 Sept" (6 days).
        Explicit dates must win -> duration becomes 6, nights become 5.
        """
        state = TripState(destination="Kerala", number_of_days=5)
        dr = parse_date_range("24 Sept to 29 Sept")
        self.assertIsNotNone(dr)
        s, e = dr
        apply_trip_state_update(state, {"trip_start_date": s, "trip_end_date": e})

        self.assertEqual(state.number_of_days, 6)
        self.assertEqual(state.number_of_nights, 5)
        self.assertEqual(state.trip_start_date, s)
    # -------------------------------------------------------------------------
    # Test 13: Full Multi-Turn /agent/chat Sequence 1 (Main Acceptance Flow)
    # -------------------------------------------------------------------------
    def test_13_full_multiturn_agent_chat_sequence(self):
        """
        End-to-end /agent/chat API verification:
        Turn 1: "Kerala" -> sets destination
        Turn 2: "5 days" -> sets duration=5, nights=4
        Turn 3: "₹3500 total" -> sets hotel_total_budget=3500, hotel_budget=875
        Turn 4: "24 Sept" -> sets check-in=24 Sept, check-out=28 Sept, nights=4
        Confirms no checkout question, no YYYY-MM-DD prompts, natural date display.
        """
        # Turn 1: Destination (omitting conversation_id initializes fresh session)
        r1 = self.client.post("/agent/chat", json={"message": "Kerala"})
        self.assertEqual(r1.status_code, 200)
        d1 = r1.json()
        cid = d1["conversation_id"]
        self.assertEqual(d1["state_summary"]["destination"], "Kerala")

        # Turn 2: Duration
        r2 = self.client.post("/agent/chat", json={"message": "5 days", "conversation_id": cid})
        self.assertEqual(r2.status_code, 200)
        d2 = r2.json()
        self.assertEqual(d2["state_summary"]["number_of_days"], 5)
        self.assertEqual(d2["state_summary"]["number_of_nights"], 4)

        # Turn 3: Budget
        r3 = self.client.post("/agent/chat", json={"message": "₹3500 total", "conversation_id": cid})
        self.assertEqual(r3.status_code, 200)
        d3 = r3.json()
        self.assertEqual(d3["state_summary"]["hotel_total_budget"], 3500.0)
        self.assertEqual(d3["state_summary"]["hotel_budget"], 875.0)

        # Turn 4: Natural date
        r4 = self.client.post("/agent/chat", json={"message": "24 Sept", "conversation_id": cid})
        self.assertEqual(r4.status_code, 200)
        d4 = r4.json()
        self.assertIn("24 September to 28 September (5 days, 4 nights)", d4["response"])
        self.assertNotIn("YYYY-MM-DD", d4["response"])
        self.assertNotIn("property_token", d4["response"])

        st = d4["state_summary"]
        self.assertEqual(st["destination"], "Kerala")
        self.assertEqual(st["number_of_days"], 5)
        self.assertEqual(st["number_of_nights"], 4)
        self.assertEqual(st["hotel_total_budget"], 3500.0)
        self.assertEqual(st["hotel_budget"], 875.0)

    # -------------------------------------------------------------------------
    # Test 14: Hotel Selection via Ordinal and Name FastPath
    # -------------------------------------------------------------------------
    def test_14_hotel_selection_ordinal_and_name(self):
        """
        User selects 'the second one' or 'I like Aloha' from visible hotels.
        Must select hotel deterministically and retain existing trip dates/budget without asking again.
        """
        cid = str(uuid.uuid4())
        session = get_or_create_session(cid)
        session.trip_state.destination = "Rishikesh"
        session.trip_state.number_of_days = 3
        session.trip_state.hotel_budget = 3500.0

        session.conversation_context.set_visible_items(
            entity_type="hotel",
            items=[
                {"name": "Ganga Kinare", "property_token": "tok_ganga_1", "price_per_night": 3000.0},
                {"name": "Aloha On The Ganges", "property_token": "tok_aloha_2", "price_per_night": 3500.0},
                {"name": "Ananda In The Himalayas", "property_token": "tok_ananda_3", "price_per_night": 9000.0},
            ],
        )
        save_session(session)

        # Case A: Ordinal "the second one"
        fp_ordinal = resolve_fast_path("the second one", session)
        self.assertTrue(fp_ordinal.matched)
        self.assertEqual(fp_ordinal.intent, "SELECT_ITEM")
        self.assertEqual(fp_ordinal.reference_resolution["name"], "Aloha On The Ganges")

        # Case B: Hotel name "I like Aloha"
        fp_name = resolve_fast_path("I like Aloha", session)
        self.assertTrue(fp_name.matched)
        self.assertEqual(fp_name.intent, "SELECT_ITEM")
        self.assertEqual(fp_name.reference_resolution["name"], "Aloha On The Ganges")

    # -------------------------------------------------------------------------
    # Test 15: Sanitization of Property Tokens & Raw Tool Tokens
    # -------------------------------------------------------------------------
    def test_15_sanitize_public_response_removes_internal_tokens(self):
        """Verify internal property tokens, raw argument tags, and credentials are eliminated."""
        from app.agent.loop import sanitize_public_response

        dirty = (
            "Here is the hotel (property token: tok_xyz_12345678901234567890). "
            "<tool_call>search_hotels</tool_call><arg_value>Kerala</arg_value> "
            "property_token='secret_token_123' token: abcdefghijklmnopqrstuvwxyz123"
        )
        clean = sanitize_public_response(dirty)
        self.assertNotIn("property token", clean.lower())
        self.assertNotIn("tok_xyz", clean)
        self.assertNotIn("<tool_call>", clean)
        self.assertNotIn("<arg_value>", clean)
        self.assertNotIn("secret_token_123", clean)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123", clean)

    # -------------------------------------------------------------------------
    # Test 16: Sequence 5 — Duration + Relative Date
    # -------------------------------------------------------------------------
    def test_16_duration_plus_relative_date(self):
        """5 days, starting tomorrow -> check-out = tomorrow + 4 days, 4 nights."""
        today = get_current_date()
        tmrw = today + timedelta(days=1)

        state = TripState(destination="Goa", number_of_days=5)
        apply_trip_state_update(state, {"trip_start_date": tmrw})

        self.assertEqual(state.trip_start_date, tmrw)
        self.assertEqual(state.trip_end_date, tmrw + timedelta(days=4))
        self.assertEqual(state.number_of_days, 5)
        self.assertEqual(state.number_of_nights, 4)

    # -------------------------------------------------------------------------
    # Test 17: Sequence 6 — Atomic Multiple Changes
    # -------------------------------------------------------------------------
    def test_17_atomic_multiple_changes_in_one_turn(self):
        """
        'Make it 7 days, increase the hotel budget to ₹5000 total, focus on nature and food, and I'll be driving.'
        Atomic update: duration=7, budget=5000, interests=['nature', 'food'], travel_mode='driving'
        """
        state = TripState(destination="Kerala", number_of_days=5, hotel_total_budget=3500.0)
        changes = {
            "number_of_days": 7,
            "hotel_total_budget": 5000.0,
            "interests": ["nature", "food"],
            "travel_mode": "driving",
        }
        change_set = apply_trip_state_update(state, changes)

        self.assertEqual(state.number_of_days, 7)
        self.assertEqual(state.number_of_nights, 6)
        self.assertEqual(state.hotel_total_budget, 5000.0)
        self.assertAlmostEqual(state.hotel_budget, 5000.0 / 6, places=2)
        self.assertEqual(state.travel_mode, "driving")
        self.assertIn("nature", state.interests)
        self.assertIn("food", state.interests)
        self.assertIn("number_of_days", change_set.changed_fields)
        self.assertIn("hotel_total_budget", change_set.changed_fields)


if __name__ == "__main__":
    unittest.main()
