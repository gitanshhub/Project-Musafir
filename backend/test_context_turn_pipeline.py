"""Offline whole-turn and conversational continuation regressions."""

import json
import unittest
from contextlib import ExitStack
from datetime import date, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from app.api.agent import get_agent_loop
from app.agent.context import clear_all_sessions, get_or_create_session, get_session
from app.agent.fast_path import resolve_fast_path
from app.agent.feasibility import FeasibilityEngine
from app.agent.llm_extraction import _parse_response
from app.agent.loop import AgentLoop
from app.agent.replanner import ReplanningAction, ReplanningActionType
from app.agent.state_update import apply_trip_state_update
from app.agent.turn import InterpretedTurn, ReferenceEdit, interpret_complete_turn
from app.agent.turn_executor import execute_turn
from app.llm.openrouter_client import LLMProviderError, LLMResponse
from app.schemas.itinerary import ItineraryResponse
from app.schemas.hotel import Hotel
from app.schemas.route import OptimizedRoute

HOTEL = {
    "name": "City Hotel",
    "price_per_night": 2000,
    "latitude": 26.9,
    "longitude": 75.8,
}
PLACE = {
    "name": "Amber Fort",
    "data_id": "amber",
    "latitude": 26.98,
    "longitude": 75.85,
}
SECOND_PLACE = {
    "name": "Jal Mahal",
    "data_id": "jal",
    "latitude": 26.95,
    "longitude": 75.84,
}
RESTAURANT = {
    "name": "Old Town Cafe",
    "data_id": "old-town-cafe",
    "latitude": 26.9,
    "longitude": 75.8,
}


class SemanticProvider:
    def __init__(self):
        self.calls = []
        self.payload = {"process_as_trip": True, "slots": {}, "confidence": {}}
        self.error = None

    def send_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return LLMResponse(
            content=json.dumps(self.payload), model="offline-semantic-provider"
        )


class ContextTurnTests(unittest.TestCase):
    def setUp(self):
        clear_all_sessions()
        self.provider = SemanticProvider()
        app.dependency_overrides[get_agent_loop] = lambda: AgentLoop(
            client=self.provider
        )
        self.client = TestClient(app)
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.hotels = stack.enter_context(
            patch(
                "app.agent.tools.search_hotels_tool",
                return_value={"success": True, "hotels": [HOTEL]},
            )
        )
        self.places = stack.enter_context(
            patch(
                "app.agent.tools.search_places_tool",
                return_value={"success": True, "places": [PLACE, SECOND_PLACE]},
            )
        )
        self.food = stack.enter_context(
            patch(
                "app.agent.tools.search_restaurants_tool",
                return_value={"success": True, "restaurants": []},
            )
        )

    def tearDown(self):
        app.dependency_overrides.clear()
        clear_all_sessions()

    def chat(self, message, cid=None):
        body = {"message": message}
        if cid:
            body["conversation_id"] = cid
        response = self.client.post("/agent/chat", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def ready_session(self):
        session = get_or_create_session()
        apply_trip_state_update(
            session.trip_state,
            {
                "destination": "Jaipur",
                "number_of_days": 4,
                "trip_start_date": "2026-11-24",
                "hotel_total_budget": 9000,
            },
        )
        return session

    def test_rich_request_keeps_all_facts_and_correct_money(self):
        reply = self.chat(
            "Plan Jaipur for 4 days starting 24 November. My total trip budget is ₹30000. I like history and local food."
        )
        state = get_session(reply["conversation_id"]).trip_state
        self.assertEqual(
            (state.destination, state.number_of_days, state.trip_budget),
            ("Jaipur", 4, 30000),
        )
        self.assertEqual(state.trip_start_date.month, 11)
        self.assertEqual(state.trip_start_date.day, 24)
        self.assertEqual(state.interests, ["history", "local food"])
        self.assertIn("accommodation", reply["response"])
        self.assertEqual(self.provider.calls, [])

    def test_search_resumes_after_final_clarification(self):
        reply = self.chat("Jaipur for 4 days")
        cid = reply["conversation_id"]
        self.chat("₹6000 total hotel budget", cid)
        reply = self.chat("24 November", cid)
        self.hotels.assert_called_once()
        args = self.hotels.call_args.kwargs
        self.assertEqual(args["max_price"], 2000)
        self.assertEqual(
            (
                date.fromisoformat(args["check_out"])
                - date.fromisoformat(args["check_in"])
            ).days,
            3,
        )
        self.assertEqual(reply["tool_calls"], ["search_hotels"])
        self.assertEqual(get_session(cid).conversation_context.pending_actions, [])

    def test_complete_hotel_request_executes_without_reasking(self):
        reply = self.chat(
            "Find a hotel in Jaipur for 4 days starting 24 November with ₹6000 total accommodation budget"
        )
        self.assertEqual(reply["tool_calls"], ["search_hotels"])
        self.assertEqual(self.hotels.call_args.kwargs["max_price"], 2000)
        self.assertIsNone(
            get_session(reply["conversation_id"]).conversation_context.active_question
        )

    def test_duration_plus_search_preserves_both(self):
        session = self.ready_session()
        reply = self.chat(
            "Make it 5 days and find cheaper hotels", session.conversation_id
        )
        self.assertEqual(session.trip_state.number_of_days, 5)
        self.assertEqual(reply["tool_calls"], ["search_hotels"])

    def test_cancel_clears_pending_search(self):
        reply = self.chat("Find hotels in Jaipur")
        session = get_session(reply["conversation_id"])
        self.assertEqual(
            session.conversation_context.pending_actions, ["SEARCH_HOTELS"]
        )
        reply = self.chat("cancel", session.conversation_id)
        self.assertEqual(reply["response"], "Canceled.")
        self.assertEqual(session.conversation_context.pending_actions, [])
        self.hotels.assert_not_called()

    def test_coordinated_selection_keeps_route_request(self):
        session = self.ready_session()
        session.conversation_context.set_visible_items("place", [PLACE, SECOND_PLACE])
        result = resolve_fast_path("select second place and build route", session)
        self.assertEqual(result.turn.requested_actions, ["ROUTE_REQUEST"])
        self.assertEqual(result.turn.references[0].reference, "second")

    def test_multiple_hotel_budgets_keep_distinct_scopes(self):
        turn = interpret_complete_turn(
            "₹3000 per night, ₹9000 total hotel", self.ready_session()
        )
        self.assertEqual(turn.updates["hotel_budget"], 3000)
        self.assertEqual(turn.updates["hotel_total_budget"], 9000)

    def test_explicit_nightly_cap_survives_total_budget_search_and_later_edits(self):
        session = self.ready_session()
        self.chat(
            "₹2000 per night, ₹9000 total hotel, find hotels", session.conversation_id
        )
        self.assertEqual(session.trip_state.hotel_budget, 2000)
        self.assertEqual(session.trip_state.hotel_total_budget, 9000)
        self.assertEqual(self.hotels.call_args.kwargs["max_price"], 2000)
        self.chat("make it 5 days", session.conversation_id)
        self.assertEqual(session.trip_state.hotel_budget, 2000)
        self.assertEqual(session.trip_state.hotel_total_budget, 9000)
        self.chat("find hotels", session.conversation_id)
        self.assertEqual(self.hotels.call_args.kwargs["max_price"], 2000)

    def test_nightly_change_set_and_selected_hotel_use_explicit_cap(self):
        session = self.ready_session()
        apply_trip_state_update(
            session.trip_state, {"hotel_selection": {**HOTEL, "price_per_night": 1500}}
        )
        change = apply_trip_state_update(
            session.trip_state, {"hotel_budget": 2000, "hotel_total_budget": 12000}
        )
        self.assertEqual(change.new_values["hotel_budget"], 2000)
        self.assertEqual(session.trip_state.hotel_budget, 2000)
        self.assertEqual(session.trip_state.hotel_selection.name, HOTEL["name"])

    def test_total_hotel_budget_limits_search_without_overwriting_nightly_cap(self):
        session = self.ready_session()
        self.chat(
            "5 days, ₹3000 per night, ₹6000 total hotel, find hotels",
            session.conversation_id,
        )
        self.assertEqual(session.trip_state.hotel_budget, 3000)
        self.assertEqual(session.trip_state.hotel_total_budget, 6000)
        self.assertEqual(self.hotels.call_args.kwargs["max_price"], 1500)

    def test_hotel_feasibility_enforces_total_stay_budget(self):
        session = self.ready_session()
        apply_trip_state_update(
            session.trip_state,
            {
                "number_of_days": 5,
                "hotel_budget": 3000,
                "hotel_total_budget": 6000,
                "hotel_selection": {**HOTEL, "price_per_night": 2500},
            },
        )
        violations = []
        FeasibilityEngine._evaluate_budget(session.trip_state, violations)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].required_value, 1500)
        self.assertIn("6,000 total over 4 nights", violations[0].explanation)
        self.assertEqual(session.trip_state.hotel_budget, 3000)

    def test_direct_hotel_tool_applies_total_budget_to_requested_stay(self):
        from app.agent.tools import search_hotels_tool

        session = self.ready_session()
        apply_trip_state_update(
            session.trip_state, {"hotel_budget": 3000, "hotel_total_budget": 6000}
        )
        with (
            patch("app.agent.tools._get_api_key", return_value="offline-test"),
            patch("app.agent.tools.fetch_hotels_from_serpapi", return_value={}),
            patch(
                "app.agent.tools.normalize_hotels_response",
                return_value=[
                    Hotel(**{**HOTEL, "price_per_night": 1500}),
                    Hotel(
                        **{**HOTEL, "name": "Too Expensive", "price_per_night": 2500}
                    ),
                ],
            ),
        ):
            result = search_hotels_tool(
                destination="Jaipur",
                check_in="2026-11-24",
                check_out="2026-11-28",
                max_price=3000,
                trip_state=session.trip_state,
            )
        self.assertTrue(result["success"], result)
        self.assertEqual([h["name"] for h in result["hotels"]], [HOTEL["name"]])

    def test_replacement_preserves_coordinated_budget_change(self):
        session = self.ready_session()
        apply_trip_state_update(session.trip_state, {"selected_places": [PLACE]})
        session.conversation_context.set_visible_items("place", [PLACE, SECOND_PLACE])
        self.chat(
            "replace Amber Fort with Jal Mahal and increase budget to ₹10000",
            session.conversation_id,
        )
        self.assertEqual(session.trip_state.trip_budget, 10000)
        self.assertEqual(
            [p.name for p in session.trip_state.selected_places], ["Jal Mahal"]
        )
        self.assertEqual(
            session.trip_state.selected_places[0].latitude, SECOND_PLACE["latitude"]
        )

    def test_replacement_preserves_coordinated_duration_change(self):
        session = self.ready_session()
        apply_trip_state_update(session.trip_state, {"selected_places": [PLACE]})
        session.conversation_context.set_visible_items("place", [PLACE, SECOND_PLACE])
        self.chat(
            "replace Amber Fort with Jal Mahal and make it 3 days",
            session.conversation_id,
        )
        self.assertEqual(session.trip_state.number_of_days, 3)
        self.assertEqual(
            [p.name for p in session.trip_state.selected_places], ["Jal Mahal"]
        )

    def test_unknown_replacement_clarifies_before_applying_budget(self):
        session = self.ready_session()
        apply_trip_state_update(session.trip_state, {"selected_places": [PLACE]})
        session.conversation_context.set_visible_items("place", [PLACE, SECOND_PLACE])
        self.chat(
            "replace Amber Fort with Unknown Palace and increase budget to ₹10000",
            session.conversation_id,
        )
        self.assertIsNone(session.trip_state.trip_budget)
        self.assertEqual(
            [p.name for p in session.trip_state.selected_places], ["Amber Fort"]
        )
        self.chat("second", session.conversation_id)
        self.assertEqual(session.trip_state.trip_budget, 10000)
        self.assertEqual(
            [p.name for p in session.trip_state.selected_places], ["Jal Mahal"]
        )

    def test_coordinated_hotel_selection_uses_visible_identity(self):
        session = self.ready_session()
        session.conversation_context.set_visible_items("hotel", [HOTEL])
        self.chat("change hotel to City Hotel, find places", session.conversation_id)
        self.assertEqual(session.trip_state.hotel_selection.name, "City Hotel")
        self.assertEqual(session.trip_state.hotel_selection.latitude, HOTEL["latitude"])
        self.places.assert_called_once()

    def test_hotel_budget_between_entity_and_search_clauses_is_retained(self):
        for message in (
            "change hotel to City Hotel, ₹2000 per night, find hotels",
            "change hotel to City Hotel and ₹2000 per night and find hotels",
        ):
            with self.subTest(message=message):
                session = self.ready_session()
                session.conversation_context.set_visible_items("hotel", [HOTEL])
                self.chat(message, session.conversation_id)
                self.assertEqual(session.trip_state.hotel_selection.name, HOTEL["name"])
                self.assertEqual(session.trip_state.destination, "Jaipur")
                self.assertEqual(session.trip_state.hotel_budget, 2000)
                self.assertEqual(self.hotels.call_args.kwargs["max_price"], 2000)

    def test_fallback_preserves_explicit_hotel_requirement(self):
        session = get_or_create_session()
        apply_trip_state_update(
            session.trip_state,
            {
                "destination": "Jaipur",
                "number_of_days": 1,
                "accommodation_required": False,
                "explicit_fields": ["accommodation_required"],
            },
        )
        self.provider.payload = {
            "process_as_trip": True,
            "slots": {"destination": "Goa"},
            "confidence": {"destination": "HIGH"},
        }
        result = resolve_fast_path(
            "find hotels in Goa, but not Jaipur", session, self.provider
        )
        self.assertIn("SEARCH_HOTELS", result.turn.requested_actions)
        self.assertTrue(result.turn.updates["accommodation_required"])
        self.assertFalse(result.turn.updates["accommodation_booked"])

    def test_implicit_duration_answer_resumes_pending_plan(self):
        self.provider.error = LLMProviderError("provider unavailable")
        reply = self.chat("Plan a short getaway in Jaipur")
        session = get_session(reply["conversation_id"])
        self.assertEqual(
            session.conversation_context.active_question.field, "number_of_days"
        )
        self.chat("3 days", session.conversation_id)
        self.assertEqual(session.trip_state.number_of_days, 3)
        self.assertNotEqual(
            getattr(session.conversation_context.active_question, "field", None),
            "number_of_days",
        )

    def test_replanning_actions_return_structured_results(self):
        session = self.ready_session()
        route = OptimizedRoute(
            ordered_stops=[],
            segments=[],
            total_distance_meters=0,
            total_duration_seconds=0,
            score=1,
        )
        itinerary = ItineraryResponse(
            days=[],
            total_days=0,
            total_distance_meters=0,
            total_travel_seconds=0,
            total_visit_minutes=0,
        )
        for action, attribute, key, value in (
            ("ROUTE_REQUEST", "current_route", "route", route),
            ("ITINERARY_REQUEST", "current_itinerary", "itinerary", itinerary),
        ):
            with self.subTest(action=action):
                setattr(session.trip_state, attribute, value)
                decision = ReplanningAction(
                    action_type=ReplanningActionType.ANSWER,
                    reason="Current plan is valid.",
                )
                with patch(
                    "app.agent.turn_executor.ReplanningController.decide",
                    return_value=decision,
                ):
                    outcome = execute_turn(
                        InterpretedTurn(requested_actions=[action]), session
                    )
                self.assertIn(key, outcome["results"])

    def test_relative_duration_is_contextual_and_deterministic(self):
        session = self.ready_session()
        self.chat("add two more days", session.conversation_id)
        self.assertEqual(session.trip_state.number_of_days, 6)
        self.assertEqual(self.provider.calls, [])

    def test_semantic_recovery_sees_full_context(self):
        session = self.ready_session()
        session.conversation_context.set_active_question(
            "trip_start_date", "date", "trip", "When are you starting?"
        )
        session.conversation_context.set_visible_items("hotel", [HOTEL])
        session.trip_state.messages = [
            {"role": "user", "content": "I like historical sights"}
        ]
        self.provider.payload = {
            "process_as_trip": True,
            "slots": {"number_of_days": 6},
            "confidence": {"number_of_days": "HIGH"},
        }
        result = resolve_fast_path("a little longer please", session, self.provider)
        self.assertEqual(result.state_updates["number_of_days"], 6)
        prompt = self.provider.calls[0]["messages"][1]["content"]
        for fact in (
            "Jaipur",
            "9000",
            "When are you starting?",
            "City Hotel",
            "historical sights",
            "state_version",
        ):
            self.assertIn(fact, prompt)

    def test_implicit_single_day_uses_semantic_recovery(self):
        self.provider.payload = {
            "process_as_trip": True,
            "slots": {"destination": "Jaipur", "number_of_days": 1},
            "confidence": {"destination": "HIGH", "number_of_days": "HIGH"},
        }
        reply = self.chat("I have a free Sunday in Jaipur")
        self.assertEqual(reply["state_summary"]["number_of_days"], 1)
        self.assertEqual(reply["tool_calls"], ["search_places"])

    def test_zero_night_trip_skips_hotels(self):
        reply = self.chat("I want a 1-day walking trip to Kochi under ₹10k")
        self.assertEqual(reply["state_summary"]["trip_budget"], 10000)
        self.assertFalse(reply["state_summary"]["hotel_search_required"])
        self.assertEqual(reply["tool_calls"], ["search_places"])
        self.hotels.assert_not_called()

    def test_duration_followed_by_start_date_derives_dates(self):
        reply = self.chat("Plan Jaipur for 5 days")
        reply = self.chat("starting 24 Sept", reply["conversation_id"])
        state = get_session(reply["conversation_id"]).trip_state
        self.assertEqual(state.trip_end_date, state.trip_start_date + timedelta(days=4))
        self.assertEqual(state.number_of_nights, 4)

    def test_dates_are_not_currency(self):
        session = self.ready_session()
        self.chat("24 Sept - 29 Sept", session.conversation_id)
        self.assertEqual(session.trip_state.number_of_days, 6)
        self.assertEqual(session.trip_state.hotel_total_budget, 9000)
        self.assertIsNone(session.trip_state.trip_budget)

    def test_explicit_trip_budget_overrides_active_hotel_question(self):
        reply = self.chat("Jaipur for 4 days")
        reply = self.chat("My whole trip budget is ₹30000", reply["conversation_id"])
        self.assertEqual(reply["state_summary"]["trip_budget"], 30000)
        self.assertIsNone(reply["state_summary"]["hotel_total_budget"])

    def test_unscoped_amount_uses_active_accommodation_question(self):
        reply = self.chat("Jaipur for 4 days")
        reply = self.chat("around 6k", reply["conversation_id"])
        self.assertEqual(reply["state_summary"]["hotel_total_budget"], 6000)
        self.assertEqual(reply["state_summary"]["hotel_budget"], 2000)

    def test_compound_changes_preserve_hotel_and_remove_stop(self):
        session = self.ready_session()
        apply_trip_state_update(
            session.trip_state,
            {"hotel_selection": HOTEL, "selected_places": [PLACE, SECOND_PLACE]},
        )
        self.chat(
            "Keep the hotel, remove the last attraction, add one day, change to walking",
            session.conversation_id,
        )
        self.assertEqual(session.trip_state.hotel_selection.name, "City Hotel")
        self.assertEqual(
            [p.name for p in session.trip_state.selected_places], ["Amber Fort"]
        )
        self.assertEqual(session.trip_state.number_of_days, 5)
        self.assertEqual(session.trip_state.travel_mode, "walking")
        self.assertEqual(session.trip_state.destination, "Jaipur")
        self.hotels.assert_not_called()

    def test_destination_correction_preserves_compatible_preferences(self):
        session = self.ready_session()
        apply_trip_state_update(
            session.trip_state,
            {
                "hotel_selection": HOTEL,
                "selected_places": [PLACE],
                "travel_mode": "walking",
                "interests": ["history"],
            },
        )
        session.conversation_context.set_visible_items("place", [PLACE])
        self.chat("Actually make it Kerala", session.conversation_id)
        self.assertEqual(session.trip_state.destination, "Kerala")
        self.assertEqual(session.trip_state.number_of_days, 4)
        self.assertEqual(session.trip_state.hotel_total_budget, 9000)
        self.assertEqual(session.trip_state.travel_mode, "walking")
        self.assertIsNone(session.trip_state.hotel_selection)
        self.assertEqual(session.conversation_context.visible_places, [])

    def test_search_places_does_not_require_hotel_budget(self):
        reply = self.chat("Find attractions in Jaipur for 3 days")
        self.assertEqual(reply["tool_calls"], ["search_places"])
        self.hotels.assert_not_called()

    def test_search_food_preserves_category_and_selected_anchor(self):
        session = self.ready_session()
        apply_trip_state_update(session.trip_state, {"hotel_selection": HOTEL})
        self.chat("Find cafes near my hotel", session.conversation_id)
        self.assertEqual(self.food.call_args.kwargs["category"], "cafe")
        self.assertEqual(self.food.call_args.kwargs["location_anchor"], "City Hotel")
        self.assertEqual(self.food.call_args.kwargs["latitude"], 26.9)

    def test_multiple_explicit_searches_are_executed(self):
        session = self.ready_session()
        reply = self.chat(
            "Find attractions and find restaurants in Jaipur", session.conversation_id
        )
        self.assertEqual(reply["tool_calls"], ["search_places", "search_restaurants"])

    def test_search_failure_is_honest_and_retryable(self):
        self.hotels.return_value = {
            "success": False,
            "error": "provider down with private token",
        }
        reply = self.chat(
            "Find hotels in Jaipur for 4 days starting 24 November with ₹6000 accommodation budget"
        )
        cid = reply["conversation_id"]
        self.assertIn("didn't complete", reply["response"])
        self.assertNotIn("private token", reply["response"])
        self.assertEqual(
            get_session(cid).conversation_context.pending_actions, ["SEARCH_HOTELS"]
        )
        self.hotels.return_value = {"success": True, "hotels": [HOTEL]}
        reply = self.chat("try again", cid)
        self.assertEqual(reply["tool_calls"], ["search_hotels"])

    def test_empty_results_do_not_claim_recommendations(self):
        self.hotels.return_value = {"success": True, "hotels": []}
        reply = self.chat(
            "Find hotels in Jaipur for 4 days starting 24 November with ₹6000 accommodation budget"
        )
        self.assertIn("No matching hotels", reply["response"])
        self.assertEqual(
            get_session(reply["conversation_id"]).conversation_context.visible_hotels,
            [],
        )

    def test_unresolved_reference_does_not_partially_apply_compound_change(self):
        session = self.ready_session()
        session.conversation_context.set_visible_items("place", [PLACE, SECOND_PLACE])
        turn = InterpretedTurn(
            updates={"travel_mode": "walking"},
            references=[
                ReferenceEdit(
                    operation="select", entity_type="place", reference="that one"
                )
            ],
        )
        outcome = execute_turn(turn, session)
        self.assertIn("Which", outcome["response"])
        self.assertEqual(session.trip_state.travel_mode, "driving")
        self.chat("second", session.conversation_id)
        self.assertEqual(session.trip_state.selected_places[0].name, "Jal Mahal")
        self.assertEqual(session.trip_state.travel_mode, "walking")

    def test_provider_failure_does_not_lose_existing_context(self):
        session = self.ready_session()
        self.provider.error = LLMProviderError("provider unavailable")
        resolve_fast_path("a little longer please", session, self.provider)
        self.assertEqual(session.trip_state.destination, "Jaipur")
        self.assertEqual(session.trip_state.number_of_days, 4)

    def test_pure_preference_edit_does_not_start_discovery(self):
        session = self.ready_session()
        self.chat("My whole trip budget is ₹25000", session.conversation_id)
        self.hotels.assert_not_called()
        self.places.assert_not_called()

    def test_model_payload_cannot_inject_selected_entities(self):
        slots, _ = _parse_response(
            '{"slots":{"hotel_selection":{"name":"Invented"},"destination":"Jaipur"},"confidence":{"destination":"HIGH"}}'
        )
        self.assertEqual(slots.high_confidence_updates(), {"destination": "Jaipur"})

    def test_model_invalid_values_are_rejected(self):
        for values in (
            {"number_of_days": -3},
            {"trip_budget": -1},
            {"travel_mode": "teleport"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                _parse_response(json.dumps({"slots": values}))

    def test_search_returns_no_stale_cards_after_concurrent_edit(self):
        session = self.ready_session()

        def change_while_searching(**kwargs):
            apply_trip_state_update(session.trip_state, {"destination": "Kochi"})
            return {"success": True, "hotels": [HOTEL]}

        self.hotels.side_effect = change_while_searching
        reply = self.chat("Find hotels", session.conversation_id)
        self.assertIsNone(reply["results"])
        self.assertEqual(session.conversation_context.visible_hotels, [])
        self.assertEqual(session.trip_state.destination, "Kochi")

    def test_pending_reference_tracks_the_actual_ambiguous_index(self):
        session = self.ready_session()
        apply_trip_state_update(
            session.trip_state, {"selected_places": [PLACE, SECOND_PLACE]}
        )
        session.conversation_context.set_visible_items("hotel", [HOTEL])
        turn = InterpretedTurn(
            references=[
                ReferenceEdit(
                    operation="select", entity_type="hotel", reference="unknown"
                ),
                ReferenceEdit(
                    operation="remove", entity_type="place", reference="last"
                ),
            ]
        )
        execute_turn(turn, session)
        self.assertEqual(
            session.conversation_context.pending_turn["unresolved_reference_index"], 0
        )
        self.chat("first", session.conversation_id)
        self.assertEqual(session.trip_state.hotel_selection.name, "City Hotel")
        self.assertEqual(
            [p.name for p in session.trip_state.selected_places], ["Amber Fort"]
        )

    def test_destination_change_cannot_select_an_old_city_hotel(self):
        session = self.ready_session()
        session.conversation_context.set_visible_items("hotel", [HOTEL])
        turn = InterpretedTurn(
            updates={"destination": "Kochi"},
            references=[
                ReferenceEdit(
                    operation="select", entity_type="hotel", reference="first"
                )
            ],
        )
        outcome = execute_turn(turn, session)
        self.assertIn("previous destination", outcome["response"])
        self.assertIsNone(session.trip_state.hotel_selection)
        self.chat("yes", session.conversation_id)
        self.assertEqual(session.trip_state.destination, "Kochi")
        self.assertIsNone(session.trip_state.hotel_selection)

    def test_contrastive_destination_correction_is_not_a_competing_choice(self):
        session = self.ready_session()
        apply_trip_state_update(session.trip_state, {"destination": "Kashmir"})
        self.chat("Actually not Kashmir, Kerala", session.conversation_id)
        self.assertEqual(session.trip_state.destination, "Kerala")

    def test_explicit_accommodation_on_day_trip_asks_nights(self):
        reply = self.chat(
            "Find hotels in Jaipur for 1 day starting 24 November with ₹6000 hotel budget"
        )
        self.assertEqual(
            get_session(
                reply["conversation_id"]
            ).conversation_context.active_question.field,
            "number_of_nights",
        )
        self.hotels.assert_not_called()

    def test_duration_reduction_releases_inferred_hotel_questions(self):
        reply = self.chat("Jaipur for 4 days")
        reply = self.chat("Make it 1 day", reply["conversation_id"])
        self.assertFalse(reply["state_summary"]["hotel_search_required"])
        self.assertEqual(reply["tool_calls"], ["search_places"])

    def test_coordinated_searches_both_execute(self):
        session = self.ready_session()
        self.food.return_value = {"success": True, "restaurants": [RESTAURANT]}
        reply = self.chat(
            "Find places and restaurants in Jaipur", session.conversation_id
        )
        self.assertEqual(reply["tool_calls"], ["search_places", "search_restaurants"])
        self.assertTrue(reply["results"]["places"])
        self.assertTrue(reply["results"]["restaurants"])
        self.hotels.assert_not_called()
        self.assertEqual(self.provider.calls, [])

    def test_explicit_go_request_preserves_city_and_whole_trip_budget(self):
        reply = self.chat("I want to go to Jaipur with 4000 budget")
        self.assertEqual(reply["state_summary"]["destination"], "Jaipur")
        self.assertEqual(reply["state_summary"]["trip_budget"], 4000)
        self.assertEqual(self.provider.calls, [])

    def test_search_isolated_from_provider_side_state_mutation(self):
        session = self.ready_session()

        def provider(**kwargs):
            kwargs["trip_state"].destination = "Kochi"
            return {"success": True, "hotels": [HOTEL]}

        self.hotels.side_effect = provider
        self.chat("Find hotels", session.conversation_id)
        self.assertEqual(session.trip_state.destination, "Jaipur")
        self.assertEqual(
            session.trip_state.get_derived_status("hotel_discovery").value, "valid"
        )

    def test_reference_question_does_not_swallow_new_destination(self):
        session = self.ready_session()
        turn = InterpretedTurn(
            references=[
                ReferenceEdit(
                    operation="select", entity_type="hotel", reference="unknown"
                )
            ]
        )
        execute_turn(turn, session)
        self.chat("Actually change to Kerala", session.conversation_id)
        self.assertEqual(session.trip_state.destination, "Kerala")

    def test_later_search_cannot_return_earlier_stale_results(self):
        session = self.ready_session()

        def change_while_searching(**kwargs):
            apply_trip_state_update(session.trip_state, {"destination": "Kochi"})
            return {"success": True, "restaurants": [RESTAURANT]}

        self.food.side_effect = change_while_searching
        reply = self.chat("Find places and restaurants", session.conversation_id)
        self.assertIsNone(reply["results"])
        self.assertNotIn("Here are", reply["response"])
        self.assertEqual(session.conversation_context.visible_places, [])
        self.assertEqual(
            session.trip_state.get_derived_status("place_discovery").value, "stale"
        )

    def test_new_start_date_moves_derived_end_date(self):
        session = self.ready_session()
        self.chat("starting 28 November", session.conversation_id)
        self.assertEqual(
            session.trip_state.trip_end_date,
            session.trip_state.trip_start_date + timedelta(days=3),
        )

    def test_day_trip_to_overnight_recomputes_inferred_accommodation(self):
        session = get_or_create_session()
        apply_trip_state_update(
            session.trip_state, {"destination": "Jaipur", "number_of_days": 1}
        )
        self.chat("Make it 3 days", session.conversation_id)
        self.assertTrue(session.trip_state.hotel_search_required)


if __name__ == "__main__":
    unittest.main()
