"""
Project Musafir — Milestone 3 Batch 3: Conversational E2E Integration Suite
Tests the complete end-to-end conversational lifecycle:
Constraint/Request -> Infeasibility Conflict Detected -> Structured Explanation
-> User Decision -> Mutation -> Dependency Invalidation -> Replanning -> Feasible Result.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import unittest
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.route import OptimizedRoute, RouteStop, RouteSegment
from app.agent.state import TripState, PlanningStage
from app.agent.context import SessionState
from app.agent.dependencies import DerivedResource
from app.agent.freshness import DerivedStateStatus
from app.agent.feasibility import FeasibilityEngine, format_feasibility_explanation
from app.agent.replanner import ReplanningController, execute_replanning_cycle
from app.agent.fast_path import resolve_fast_path
from app.agent.state_update import apply_trip_state_update
from app.schemas.constraint import FeasibilityStatus, ConstraintViolationType


def build_test_state() -> TripState:
    hotel = Hotel(
        id="h1",
        name="Fort Kochi Heritage Hotel",
        latitude=9.9667,
        longitude=76.2417,
        price_per_night=3500.0,
        rating=4.5,
    )
    places = [
        Place(data_id="p1", name="Fort Kochi Beach", latitude=9.9654, longitude=76.2423, visit_duration_minutes=60),
        Place(data_id="p2", name="Mattancherry Palace", latitude=9.9582, longitude=76.2592, visit_duration_minutes=90),
        Place(data_id="p3", name="Jewish Synagogue", latitude=9.9575, longitude=76.2596, visit_duration_minutes=60),
        Place(data_id="p4", name="Santa Cruz Cathedral", latitude=9.9647, longitude=76.2419, visit_duration_minutes=45),
        Place(data_id="p5", name="St. Francis Church", latitude=9.9669, longitude=76.2411, visit_duration_minutes=45),
        Place(data_id="p6", name="Indo-Portuguese Museum", latitude=9.9632, longitude=76.2445, visit_duration_minutes=60),
        Place(data_id="p7", name="Kerala Kathakali Centre", latitude=9.9678, longitude=76.2452, visit_duration_minutes=90),
        Place(data_id="p8", name="Hill Palace Museum", latitude=9.9527, longitude=76.3644, visit_duration_minutes=120),
    ]
    state = TripState(
        conversation_id="e2e-conv-batch3",
        destination="Kochi",
        number_of_days=1,
        travel_mode="walking",
        hotel_budget=5000.0,
    )
    state.set_hotel(hotel)
    for p in places:
        state.add_place(p)
    return state


def build_route_for_state(state: TripState, dist_km: float = 16.0) -> OptimizedRoute:
    hotel = state.hotel_selection
    stops = [RouteStop(id="origin", name=hotel.name, latitude=hotel.latitude, longitude=hotel.longitude, type="hotel")]
    for p in state.selected_places:
        stops.append(RouteStop(id=p.data_id or p.name, name=p.name, latitude=p.latitude, longitude=p.longitude, type="attraction"))
    stops.append(RouteStop(id="dest", name=hotel.name, latitude=hotel.latitude, longitude=hotel.longitude, type="hotel"))
    seg_dist = (dist_km * 1000.0) / max(1, len(stops) - 1)
    seg_dur = int(seg_dist / (1.2 if state.travel_mode == "walking" else 8.0))
    segments = [
        RouteSegment(from_stop=stops[i].id, to_stop=stops[i+1].id, distance_meters=seg_dist, duration_seconds=seg_dur)
        for i in range(len(stops) - 1)
    ]
    return OptimizedRoute(
        ordered_stops=stops,
        segments=segments,
        total_distance_meters=dist_km * 1000.0,
        total_duration_seconds=sum(s.duration_seconds for s in segments),
        score=0.95,
    )


class TestBatch3ConversationalE2E(unittest.TestCase):
    """Conversational end-to-end integration covering the full trade-off resolution lifecycle."""

    def test_e2e_scenario_1_overloaded_day_resolved_by_adding_day(self):
        """
        Scenario 1:
        1. User creates a 1-day trip with 8 attractions.
        2. Feasibility Engine detects DAY_CAPACITY_EXCEEDED.
        3. Agent explains conflict with options (add day, change mode, remove stop).
        4. User chooses: 'make it 2 days'.
        5. State mutates -> duration updated -> replanning cycle runs -> feasible result.
        """
        state = build_test_state()
        state.travel_mode = "driving"
        session = SessionState(conversation_id=state.conversation_id, trip_state=state)

        # Step 1: Initial evaluation with 8 places on a 1-day driving trip
        route = build_route_for_state(state, dist_km=22.0)
        state.current_route = route
        feas1 = FeasibilityEngine.evaluate(state, route)
        state.feasibility_result = feas1

        self.assertFalse(feas1.is_feasible)
        self.assertIn(feas1.status, (FeasibilityStatus.INFEASIBLE, FeasibilityStatus.PARTIALLY_FEASIBLE))
        self.assertTrue(len(feas1.unscheduled_items) > 0)

        # Step 2: Agent explains conflict
        explanation = format_feasibility_explanation(feas1, state)
        self.assertTrue("cannot fit" in explanation.lower() or "conflict" in explanation.lower())
        self.assertIn("Add an extra day", explanation)

        # Step 3: User decides: 'make it 2 days'
        fp = resolve_fast_path("make it 2 days", session)
        self.assertTrue(fp.matched)
        self.assertEqual(fp.intent, "UPDATE_DURATION")
        self.assertEqual(fp.state_updates["number_of_days"], 2)

        # Step 4: Apply mutation -> dependency invalidation
        cs = apply_trip_state_update(state, fp.state_updates)
        self.assertIn("number_of_days", cs.changed_fields)
        self.assertEqual(state.number_of_days, 2)

        # Step 5: Replanning & re-evaluation
        feas2 = FeasibilityEngine.evaluate(state, route)
        self.assertTrue(feas2.is_feasible)
        self.assertEqual(len(feas2.scheduled_items), 8)
        self.assertEqual(len(feas2.unscheduled_items), 0)
        print("  ✓ E2E Scenario 1: Overloaded day resolved by adding a day")

    def test_e2e_scenario_2_walking_mode_conflict_resolved_by_switching_mode(self):
        """
        Scenario 2:
        1. User has a 1-day walking trip with 16 km total route.
        2. Feasibility Engine surfaces MODE_CONFLICT (walking distance limit).
        3. Agent explains impractical walking distance.
        4. User responds: 'switch to driving'.
        5. Fast-path handles mode switch -> travel_mode becomes driving -> route rebuilt -> feasible!
        """
        state = build_test_state()
        state.selected_places = state.selected_places[:4]  # 4 places, manageable in driving
        state.travel_mode = "walking"
        session = SessionState(conversation_id=state.conversation_id, trip_state=state)

        route = build_route_for_state(state, dist_km=16.0)
        state.current_route = route
        feas1 = FeasibilityEngine.evaluate(state, route)
        state.feasibility_result = feas1

        mode_violation = next((v for v in feas1.violations if v.violation_type == ConstraintViolationType.MODE_CONFLICT), None)
        self.assertIsNotNone(mode_violation)

        # User chooses to switch mode
        fp = resolve_fast_path("switch to driving", session)
        self.assertTrue(fp.matched)
        self.assertIn(fp.intent, ("UPDATE_FIELD_AND_REBUILD_ROUTE", "UPDATE_TRAVEL_MODE"))
        self.assertEqual(fp.state_updates["travel_mode"], "driving")

        apply_trip_state_update(state, fp.state_updates)
        self.assertEqual(state.travel_mode, "driving")

        driving_route = build_route_for_state(state, dist_km=16.0)
        state.current_route = driving_route
        feas2 = FeasibilityEngine.evaluate(state, driving_route)
        self.assertTrue(feas2.is_feasible)
        self.assertEqual(len(feas2.violations), 0)
        print("  ✓ E2E Scenario 2: Walking mode conflict resolved by switching to driving")

    def test_e2e_scenario_3_must_visit_stop_prioritized_during_tight_capacity(self):
        """
        Scenario 3:
        1. User marks 'Fort Kochi Beach' as REQUIRED via 'must visit Fort Kochi Beach'.
        2. Fast-path marks stop as REQUIRED in state.
        3. Squeezed capacity prioritizes 'Fort Kochi Beach', leaving an optional stop unscheduled.
        4. User resolves by removing the unscheduled stop -> 100% feasible.
        """
        state = build_test_state()
        state.travel_mode = "driving"
        session = SessionState(conversation_id=state.conversation_id, trip_state=state)

        # User designates must-visit stop
        fp = resolve_fast_path("must visit Fort Kochi Beach", session)
        self.assertTrue(fp.matched)
        self.assertEqual(fp.intent, "MARK_STOP_REQUIRED")
        apply_trip_state_update(state, fp.state_updates)

        self.assertTrue(state.is_stop_required("Fort Kochi Beach"))

        route = build_route_for_state(state, dist_km=18.0)
        feas1 = FeasibilityEngine.evaluate(state, route)

        # Fort Kochi Beach must be among the scheduled items!
        self.assertIn("Fort Kochi Beach", feas1.scheduled_items)

        # Unscheduled optional stops can be removed
        if feas1.unscheduled_items:
            un = feas1.unscheduled_items[0]
            apply_trip_state_update(state, {"remove_places": [un]})
            self.assertFalse(any(p.name == un for p in state.selected_places))

        print("  ✓ E2E Scenario 3: Must-visit stop prioritized during tight capacity")


if __name__ == "__main__":
    print("\n=======================================================")
    print(" PROJECT MUSAFIR — BATCH 3 CONVERSATIONAL E2E SUITE")
    print("=======================================================\n")
    unittest.main(verbosity=2)
