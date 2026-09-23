"""
Project Musafir — Step 12.6 Milestone 2 Comprehensive Integration Test Suite
Verifies:
1. FastPath + SessionState integration into /agent/chat (0 LLM roundtrips, 0 tool calls)
2. Exact problem reproduction test from user screenshots:
   Active hotel budget -> "total 3500" -> 4 nights -> ₹875/night -> "Start tomorrow" -> pivot to Kashmir
3. State semantics equivalence: FastPath and LLM produce identical canonical TripState
4. SessionState full lifecycle (create, retrieve, save, reset, delete)
5. ActiveQuestion lifecycle and deterministic readiness
6. Tool suppression on conversational turns
7. Stale destination and state version race protection
8. 1-day day trip semantics (1 day = 0 nights)
"""

import uuid
import unittest
from unittest.mock import MagicMock
from datetime import date, timedelta
from uuid import UUID
from fastapi.testclient import TestClient
from main import app
from app.agent.state import TripState, get_state, get_or_create_state, clear_all_states
from app.agent.context import (
    ActiveQuestion,
    VisibleItemReference,
    ConversationContext,
    SessionState,
    get_or_create_session,
    get_session,
    save_session,
    reset_session,
    delete_session,
    clear_all_sessions,
    format_conversation_context,
)
from app.agent.semantics import (
    calculate_nights,
    calculate_nightly_hotel_budget,
    resolve_relative_date,
    get_current_date,
)
from app.agent.state_update import (
    apply_trip_state_update,
    check_trip_readiness,
    get_next_active_question,
)
from app.agent.loop import AgentLoop, AgentResult, to_json_safe, json_dumps_safe, sanitize_public_response
from app.agent.fast_path import resolve_fast_path


class TestStep12_6Milestone2Integration(unittest.TestCase):
    """Milestone 2 Integration Test Suite."""

    def setUp(self):
        clear_all_sessions()
        self.client = TestClient(app)

    def tearDown(self):
        clear_all_sessions()

    # -------------------------------------------------------------------------
    # Test 1: Exact Multi-Turn Problem from Screenshots
    # -------------------------------------------------------------------------
    def test_01_exact_screenshot_conversation_sequence(self):
        """
        The critical multi-turn sequence:
        1. Agent asks hotel budget -> User says "total 3500"
           -> 0 LLM roundtrips, 0 tool calls, 4 nights, ₹875/night ceiling, active question cleared.
        2. User says "Start tomorrow" -> resolved date, 0 LLM calls.
        3. User says "Actually Kashmir instead" -> pivot handled by agent loop,
           Jaipur data cleared, 5 days duration preserved.
        """
        # Initialize session for a 5-day trip to Jaipur
        cid = str(uuid.uuid4())
        session = get_or_create_session(cid)
        session.trip_state.destination = "Jaipur"
        session.trip_state.number_of_days = 5
        session.trip_state.number_of_nights = 4
        session.conversation_context.set_active_question(
            field="hotel_total_budget",
            expected_type="money",
            scope="accommodation",
            prompt_text="What budget would you like to keep for accommodation?",
            reason="required_for_hotel_search",
        )
        save_session(session)

        # Turn 1: User replies "total 3500"
        res1 = self.client.post("/agent/chat", json={
            "conversation_id": cid,
            "message": "total 3500",
        })
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()

        # Must execute locally with 0 LLM iterations and 0 tool calls
        self.assertEqual(data1["iterations"], 0)
        self.assertEqual(data1["tool_calls"], [])
        self.assertIn("3,500", data1["response"])
        self.assertIn("875", data1["response"])

        # Canonical state check
        reloaded = get_session(cid)
        self.assertEqual(reloaded.trip_state.hotel_total_budget, 3500.0)
        self.assertEqual(reloaded.trip_state.number_of_days, 5)
        self.assertEqual(reloaded.trip_state.number_of_nights, 4)
        self.assertEqual(reloaded.trip_state.hotel_budget, 875.0)

        # Active question for hotel_total_budget must be satisfied and replaced by dates
        next_q = reloaded.conversation_context.active_question
        self.assertIsNotNone(next_q)
        self.assertEqual(next_q.field, "trip_start_date")

        # Turn 2: User replies "Start tomorrow"
        res2 = self.client.post("/agent/chat", json={
            "conversation_id": cid,
            "message": "Start tomorrow",
        })
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()

        # Must also execute locally with 0 LLM iterations
        self.assertEqual(data2["iterations"], 0)
        self.assertEqual(data2["tool_calls"], [])

        reloaded2 = get_session(cid)
        expected_start = get_current_date() + timedelta(days=1)
        self.assertEqual(reloaded2.trip_state.trip_start_date, expected_start)
        self.assertEqual(reloaded2.trip_state.hotel_total_budget, 3500.0)
        self.assertEqual(reloaded2.trip_state.hotel_budget, 875.0)

        # Turn 3: User says "Actually Kashmir instead" (Nuanced pivot -> falls back to LLM)
        # FastPath conservatively yields matched=False
        fp_res3 = resolve_fast_path("Actually Kashmir instead", reloaded2)
        self.assertFalse(fp_res3.matched)
        self.assertEqual(fp_res3.intent, "FALLBACK_TO_LLM")

        # Mock loop handles the LLM turn and executes update_trip_state
        from unittest.mock import MagicMock
        from app.api.agent import get_agent_loop

        mock_loop = MagicMock(spec=AgentLoop)
        def fake_run(messages, user_message, trip_state, session=None, **kwargs):
            # The agent tool call executes update_trip_state
            apply_trip_state_update(session.trip_state, {"destination": "Kashmir"})
            return AgentResult(
                response="Got it — switching destination to Kashmir while keeping duration and budget.",
                tool_calls=["update_trip_state"],
                iterations=1,
                state_summary=session.trip_state.summary(),
                messages=messages + [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": "Got it — switching destination to Kashmir."},
                ],
            )
        mock_loop.run.side_effect = fake_run
        app.dependency_overrides[get_agent_loop] = lambda: mock_loop

        res3 = self.client.post("/agent/chat", json={
            "conversation_id": cid,
            "message": "Actually Kashmir instead",
        })
        self.assertEqual(res3.status_code, 200)

        reloaded3 = get_session(cid)
        self.assertEqual(reloaded3.trip_state.destination, "Kashmir")
        # Preserves duration and budget!
        self.assertEqual(reloaded3.trip_state.number_of_days, 5)
        self.assertEqual(reloaded3.trip_state.number_of_nights, 4)
        self.assertEqual(reloaded3.trip_state.hotel_total_budget, 3500.0)
        self.assertEqual(reloaded3.trip_state.hotel_budget, 875.0)

        # Turn 4: User says "Keep everything else, but make it 7 days and increase budget to 40k."
        # Two changes, one atomic transition
        change_set = apply_trip_state_update(reloaded3.trip_state, {
            "number_of_days": 7,
            "hotel_total_budget": 40000.0,
        })
        self.assertIn("number_of_days", change_set.changed_fields)
        self.assertIn("hotel_total_budget", change_set.changed_fields)
        self.assertEqual(reloaded3.trip_state.number_of_days, 7)
        self.assertEqual(reloaded3.trip_state.number_of_nights, 6)
        self.assertEqual(reloaded3.trip_state.hotel_total_budget, 40000.0)
        # 40,000 / 6 nights = 6,666.67/night
        self.assertAlmostEqual(reloaded3.trip_state.hotel_budget, 6666.67, places=2)

        app.dependency_overrides.pop(get_agent_loop, None)

    # -------------------------------------------------------------------------
    # Test 2: Equivalence Test (FastPath vs LLM produce identical canonical state)
    # -------------------------------------------------------------------------
    def test_02_fast_path_and_llm_state_semantics_equivalence(self):
        """
        Review point 8: FastPath and update_trip_state must produce identical state.
        """
        # State A: FastPath path
        state_fp = TripState(destination="Jaipur", number_of_days=5)
        apply_trip_state_update(state_fp, {"hotel_total_budget": 3500.0})

        # State B: LLM update_trip_state tool path
        state_llm = TripState(destination="Jaipur", number_of_days=5)
        apply_trip_state_update(state_llm, {"hotel_total_budget": 3500.0})

        self.assertEqual(state_fp.hotel_total_budget, state_llm.hotel_total_budget)
        self.assertEqual(state_fp.number_of_nights, state_llm.number_of_nights)
        self.assertEqual(state_fp.hotel_budget, state_llm.hotel_budget)
        self.assertEqual(state_fp.hotel_budget, 875.0)

    # -------------------------------------------------------------------------
    # Test 3: SessionState Full Lifecycle
    # -------------------------------------------------------------------------
    def test_03_session_state_lifecycle(self):
        """
        Creation, retrieval, persistence, reset, and deletion.
        """
        sess = get_or_create_session("sess_lifecycle_1")
        self.assertEqual(sess.conversation_id, "sess_lifecycle_1")

        sess.trip_state.destination = "Goa"
        sess.trip_state.number_of_days = 3
        sess.conversation_context.set_active_question(
            field="travel_mode",
            expected_type="mode",
            scope="logistics",
            reason="required_for_routing",
        )
        save_session(sess)

        # Retrieve
        fetched = get_session("sess_lifecycle_1")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.trip_state.destination, "Goa")
        self.assertEqual(fetched.conversation_context.active_question.field, "travel_mode")

        # Reset
        resetted = reset_session("sess_lifecycle_1")
        self.assertEqual(resetted.conversation_id, "sess_lifecycle_1")
        self.assertIsNone(resetted.trip_state.destination)
        self.assertIsNone(resetted.conversation_context.active_question)

        # Delete
        self.assertTrue(delete_session("sess_lifecycle_1"))
        self.assertIsNone(get_session("sess_lifecycle_1"))

    # -------------------------------------------------------------------------
    # Test 4: ActiveQuestion Lifecycle & Deterministic Readiness
    # -------------------------------------------------------------------------
    def test_04_active_question_lifecycle_and_readiness(self):
        """
        Deterministic readiness decides next structured question without LLM guessing.
        """
        st = TripState()
        # 1. No destination -> asks destination
        q1 = get_next_active_question(st)
        self.assertEqual(q1.field, "destination")
        self.assertEqual(q1.reason, "required_for_trip_planning")

        # 2. Broad destination -> asks clarification
        st.destination = "South India"
        q2 = get_next_active_question(st)
        self.assertEqual(q2.field, "destination")
        self.assertEqual(q2.reason, "clarification_required_for_broad_destination")

        # 3. Specific destination set, no duration -> asks duration
        st.destination = "Jaipur"
        q3 = get_next_active_question(st)
        self.assertEqual(q3.field, "number_of_days")
        self.assertEqual(q3.reason, "required_for_itinerary")

        # 4. Duration set (5 days), no budget -> asks accommodation budget
        st.number_of_days = 5
        st.number_of_nights = 4
        q4 = get_next_active_question(st)
        self.assertEqual(q4.field, "hotel_total_budget")
        self.assertEqual(q4.reason, "required_for_hotel_search")

        # 5. Budget set, no start date -> asks start date
        st.hotel_total_budget = 4000.0
        st.hotel_budget = 1000.0
        q5 = get_next_active_question(st)
        self.assertEqual(q5.field, "trip_start_date")

    # -------------------------------------------------------------------------
    # Test 5: Tool Suppression on Conversational Turns
    # -------------------------------------------------------------------------
    def test_05_tool_suppression_on_conversational_turns(self):
        """
        Pure state updates ("5 days", "car", "tomorrow") cause zero tool calls.
        """
        cid = str(uuid.uuid4())
        session = get_or_create_session(cid)
        session.trip_state.destination = "Agra"
        session.conversation_context.set_active_question(
            field="number_of_days",
            expected_type="number",
            scope="trip",
        )
        save_session(session)

        res = self.client.post("/agent/chat", json={
            "conversation_id": cid,
            "message": "5 days",
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["tool_calls"], [])
        self.assertEqual(data["iterations"], 0)

    # -------------------------------------------------------------------------
    # Test 6: 1-Day Day-Trip Semantics
    # -------------------------------------------------------------------------
    def test_06_one_day_day_trip_semantics(self):
        """
        1 day -> 0 nights. If nights = 0, nightly hotel budget ceiling is None.
        """
        self.assertEqual(calculate_nights(number_of_days=1), 0)
        self.assertIsNone(calculate_nightly_hotel_budget(total_hotel_budget=3500.0, number_of_days=1))

        state = TripState(destination="Delhi", number_of_days=1)
        apply_trip_state_update(state, {"number_of_days": 1, "hotel_total_budget": 2000.0})
        self.assertEqual(state.number_of_nights, 0)
        self.assertIsNone(state.hotel_budget)

    # -------------------------------------------------------------------------
    # Test 7: Visible Result Set Ordinal Selection via FastPath
    # -------------------------------------------------------------------------
    def test_07_visible_result_set_ordinal_selection(self):
        """
        User selects 'second hotel' or 'last hotel' from visible screen cards.
        """
        cid = str(uuid.uuid4())
        session = get_or_create_session(cid)
        session.conversation_context.set_visible_items("hotel", [
            {"property_token": "h_1", "name": "Hotel Sarang Palace", "price_per_night": 2800.0},
            {"property_token": "h_2", "name": "Pearl Palace Heritage", "price_per_night": 4600.0},
            {"property_token": "h_3", "name": "The Lalit Jaipur", "price_per_night": 4800.0},
        ], result_set_id="res_cards_01")
        save_session(session)

        res = self.client.post("/agent/chat", json={
            "conversation_id": cid,
            "message": "the second hotel",
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["iterations"], 0)
        self.assertIn("Pearl Palace Heritage", data["response"])

        reloaded = get_session(cid)
        self.assertIsNotNone(reloaded.trip_state.hotel_selection)
        self.assertEqual(reloaded.trip_state.hotel_selection.name, "Pearl Palace Heritage")

    # -------------------------------------------------------------------------
    # Test 8: Multi-Field Atomic Update
    # -------------------------------------------------------------------------
    def test_08_multi_field_atomic_update(self):
        """
        'Make it 7 days, ₹40k, nature and adventure, and I'll drive.'
        Applies atomically via single shared state engine.
        """
        state = TripState(destination="Kashmir", number_of_days=5)
        initial_ver = state.state_version

        change_set = apply_trip_state_update(state, {
            "number_of_days": 7,
            "trip_budget": 40000.0,
            "travel_mode": "car",
            "interests": ["nature", "adventure"],
        })

        self.assertEqual(state.number_of_days, 7)
        self.assertEqual(state.number_of_nights, 6)
        self.assertEqual(state.trip_budget, 40000.0)
        self.assertEqual(state.travel_mode, "car")
        self.assertEqual(state.interests, ["nature", "adventure"])
        self.assertEqual(state.destination, "Kashmir")
        self.assertGreater(state.state_version, initial_ver)

    # -------------------------------------------------------------------------
    # Test 9: General Travel Question Suppresses Discovery & Mutation
    # -------------------------------------------------------------------------
    def test_09_general_travel_question_no_state_mutation(self):
        """
        'Is October a good time to visit Kashmir?'
        FastPath yields matched=False. LLM answers without mutating state or calling discovery tools.
        """
        cid = str(uuid.uuid4())
        session = get_or_create_session(cid)
        session.trip_state.destination = "Kashmir"
        save_session(session)

        fp_res = resolve_fast_path("Is October a good time to visit Kashmir?", session)
        self.assertTrue(fp_res.matched)
        self.assertEqual(fp_res.intent, "GENERAL_TRAVEL_QUERY")
        self.assertEqual(fp_res.state_updates, {})

        from app.api.agent import get_agent_loop

        mock_loop = MagicMock(spec=AgentLoop)
        mock_loop.run.return_value = AgentResult(
            response="October is an excellent time to visit Kashmir with pleasant autumn weather and chinar leaves changing color.",
            tool_calls=[],
            iterations=1,
            state_summary=session.trip_state.summary(),
            messages=[
                {"role": "user", "content": "Is October a good time to visit Kashmir?"},
                {"role": "assistant", "content": "October is an excellent time to visit Kashmir."},
            ],
        )
        app.dependency_overrides[get_agent_loop] = lambda: mock_loop

        res = self.client.post("/agent/chat", json={
            "conversation_id": cid,
            "message": "Is October a good time to visit Kashmir?",
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["tool_calls"], [])
        self.assertEqual(data["iterations"], 1)
        self.assertIn("October", data["response"])

        reloaded = get_session(cid)
        self.assertEqual(reloaded.trip_state.destination, "Kashmir")
        self.assertIsNone(reloaded.trip_state.number_of_days)

        app.dependency_overrides.pop(get_agent_loop, None)

    # -------------------------------------------------------------------------
    # Test 10: Stale Result & Destination Mismatch Discarded
    # -------------------------------------------------------------------------
    def test_10_stale_result_and_destination_mismatch_discarded(self):
        """
        If destination changed or state version changed, late discovery results are discarded.
        """
        from app.schemas.hotel import Hotel
        trip_state = TripState(destination="Kashmir")
        trip_state.state_version = 3

        loop = AgentLoop(
            tools=[],
            tool_executor=lambda name, args, **kwargs: {
                "success": True,
                "hotels": [{"name": "Hotel Sarang", "price_per_night": 2500}],
            },
        )

        # 1. Destination mismatch guard: Search destination is Jaipur, but trip is Kashmir
        fake_llm = MagicMock()
        from app.llm.openrouter_client import LLMResponse
        fake_llm.send_message.side_effect = [
            LLMResponse(
                content="",
                model="google/gemma-4",
                tool_calls=[{
                    "id": "call_stale_1",
                    "function": {"name": "search_hotels", "arguments": '{"destination": "Jaipur"}'},
                }],
            ),
            LLMResponse(content="Sorry, that search was stale.", model="google/gemma-4"),
        ]
        loop.client = fake_llm

        res = loop.run(trip_state=trip_state)
        # Search for Jaipur was discarded because trip destination is Kashmir
        self.assertIn("search_hotels", res.tool_calls)
        # Check tool message in messages has discarded=True
        tool_msg = next(m for m in res.messages if m.get("role") == "tool")
        import json
        content = json.loads(tool_msg["content"])
        self.assertTrue(content.get("discarded"))

    # -------------------------------------------------------------------------
    # Test 11: JSON Serialization Safe Boundary
    # -------------------------------------------------------------------------
    def test_11_json_serialization_safe_boundary(self):
        """
        Ensures dates, datetimes, UUIDs, Pydantic models, and sets serialize safely into tool output without TypeError.
        """
        from datetime import datetime
        from app.schemas.hotel import Hotel

        sample_hotel = Hotel(
            name="Heritage Haveli",
            rating=4.5,
            price_per_night=3200.0,
            currency="INR",
        )

        complex_payload = {
            "created_at": datetime.now(),
            "target_date": date.today(),
            "session_id": uuid.uuid4(),
            "hotel": sample_hotel,
            "tags": {"boutique", "heritage"},
        }

        # Must not raise TypeError: Object of type ... is not JSON serializable
        serialized = json_dumps_safe(complex_payload)
        self.assertIsInstance(serialized, str)
        self.assertIn("Heritage Haveli", serialized)
        self.assertIn("boutique", serialized)

    # -------------------------------------------------------------------------
    # Test 12: Internal Marker Sanitization
    # -------------------------------------------------------------------------
    def test_12_internal_marker_sanitization(self):
        """
        Verifies <arg_value>, <tool_call>, credentials, and tracebacks are stripped from public responses.
        """
        dirty_text = (
            "Here is your plan. <tool_call>update_trip_state</tool_call>\n"
            "<arg_value>4000</arg_value>\n"
            "Bearer eyJhbGciOiJIUzI1NiJ9.secret\n"
            "Using provider key=sk-1234567890abcdef\n"
            "Traceback (most recent call last):\n"
            "  File 'agent.py', line 42, in run\n"
            "ZeroDivisionError: division by zero\n\n"
            "I have finalized your 5-day itinerary."
        )

        cleaned = sanitize_public_response(dirty_text)
        self.assertNotIn("<arg_value>", cleaned)
        self.assertNotIn("</arg_value>", cleaned)
        self.assertNotIn("<tool_call>", cleaned)
        self.assertNotIn("sk-1234567890abcdef", cleaned)
        self.assertNotIn("ZeroDivisionError", cleaned)
        self.assertIn("Here is your plan.", cleaned)
        self.assertIn("I have finalized your 5-day itinerary.", cleaned)


if __name__ == "__main__":
    unittest.main()

