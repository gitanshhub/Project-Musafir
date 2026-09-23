import os
import sys
from datetime import date
import requests

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.schemas.route import LocationPoint, RouteStop, RouteSegment, OptimizedRoute
from app.schemas.itinerary import ItineraryRequest, ItineraryResponse
from app.services.itinerary_service import generate_itinerary
from main import app

print("=" * 60)
print("PROJECT MUSAFIR — STEP 9 ITINERARY ENGINE VERIFICATION SUITE")
print("=" * 60)

# Sample base route
mock_route = OptimizedRoute(
    ordered_stops=[
        RouteStop(
            id="hawa_mahal",
            name="Hawa Mahal",
            latitude=26.9239,
            longitude=75.8267,
            type="attraction",
            duration_minutes=60,
        ),
        RouteStop(
            id="amber_palace",
            name="Amber Palace",
            latitude=26.9855,
            longitude=75.8513,
            type="attraction",
            duration_minutes=120,
        ),
    ],
    segments=[
        RouteSegment(
            from_stop="The Hosteller Jaipur",
            to_stop="Hawa Mahal",
            distance_meters=4500,
            duration_seconds=1200,  # 20 min
            distance_text="4.5 km",
            duration_text="20 mins",
        ),
        RouteSegment(
            from_stop="Hawa Mahal",
            to_stop="Amber Palace",
            distance_meters=11000,
            duration_seconds=1800,  # 30 min
            distance_text="11.0 km",
            duration_text="30 mins",
        ),
    ],
    total_distance_meters=15500,
    total_duration_seconds=3000,
    score=3000.0,
    reason="Shortest driving duration",
)

# TEST 1: Basic Itinerary Scheduling
print("\n[Test 1] Testing Basic Itinerary Scheduling...")
req1 = ItineraryRequest(
    route=mock_route,
    trip_date=date(2026, 9, 20),
    start_time="09:00",
    day_start_time="09:00",
    day_end_time="22:00",
)
res1 = generate_itinerary(req1)

assert len(res1.days) == 1, "Expected 1 day for basic route"
day1 = res1.days[0]
assert len(day1.items) == 2, "Expected 2 scheduled items"
assert len(day1.segments) == 2, "Expected 2 travel segments"

# Check travel 1: 09:00 -> 09:20 (20 min)
assert day1.segments[0].departure_time == "09:00"
assert day1.segments[0].arrival_time == "09:20"
# Check stop 1 (Hawa Mahal): 09:20 -> 10:20 (60 min)
assert day1.items[0].name == "Hawa Mahal"
assert day1.items[0].arrival_time == "09:20"
assert day1.items[0].departure_time == "10:20"
assert day1.items[0].duration_minutes == 60

# Check travel 2: 10:20 -> 10:50 (30 min)
assert day1.segments[1].departure_time == "10:20"
assert day1.segments[1].arrival_time == "10:50"
# Check stop 2 (Amber Palace): 10:50 -> 12:50 (120 min)
assert day1.items[1].name == "Amber Palace"
assert day1.items[1].arrival_time == "10:50"
assert day1.items[1].departure_time == "12:50"
assert day1.items[1].duration_minutes == 120

print("  ✓ Segment 1: 09:00 -> 09:20 (20 mins)")
print("  ✓ Stop 1 (Hawa Mahal): Visit 09:20 -> 10:20 (60 mins)")
print("  ✓ Segment 2: 10:20 -> 10:50 (30 mins)")
print("  ✓ Stop 2 (Amber Palace): Visit 10:50 -> 12:50 (120 mins)")
print("  ✓ Test 1 Passed!")


# TEST 2: Custom Visit Duration & Defaults
print("\n[Test 2] Testing Default Durations (attraction=60, restaurant=60, cafe=45)...")
route_defaults = OptimizedRoute(
    ordered_stops=[
        RouteStop(id="cafe1", name="Tapri Central", latitude=26.90, longitude=75.80, type="cafe"),
        RouteStop(id="rest1", name="Spice Court", latitude=26.91, longitude=75.79, type="restaurant"),
    ],
    segments=[
        RouteSegment(from_stop="Start", to_stop="Tapri Central", duration_seconds=600),
        RouteSegment(from_stop="Tapri Central", to_stop="Spice Court", duration_seconds=600),
    ],
    total_distance_meters=5000,
    total_duration_seconds=1200,
    score=1200,
)
res2 = generate_itinerary(ItineraryRequest(route=route_defaults, trip_date=date(2026, 9, 20), start_time="14:00"))
assert res2.days[0].items[0].duration_minutes == 45, "Cafe default should be 45 min"
assert res2.days[0].items[1].duration_minutes == 60, "Restaurant default should be 60 min"
print("  ✓ Cafe default duration = 45 mins")
print("  ✓ Restaurant default duration = 60 mins")
print("  ✓ Test 2 Passed!")


# TEST 3: Earliest Visit Wait Time
print("\n[Test 3] Testing Earliest Visit Wait Time...")
route_earliest = OptimizedRoute(
    ordered_stops=[
        RouteStop(
            id="night_spot",
            name="Chokhi Dhani",
            latitude=26.76,
            longitude=75.83,
            type="restaurant",
            earliest_visit="18:00",
            duration_minutes=90,
        )
    ],
    segments=[
        RouteSegment(from_stop="Hotel", to_stop="Chokhi Dhani", duration_seconds=1800),  # 30 min
    ],
    total_distance_meters=15000,
    total_duration_seconds=1800,
    score=1800,
)
# Traveler leaves at 16:30 -> arrives at 17:00, but earliest_visit is 18:00
res3 = generate_itinerary(ItineraryRequest(route=route_earliest, trip_date=date(2026, 9, 20), start_time="16:30"))
assert res3.days[0].segments[0].arrival_time == "17:00"
assert res3.days[0].items[0].arrival_time == "18:00", "Visit start should wait until 18:00"
assert res3.days[0].items[0].departure_time == "19:30"
print("  ✓ Traveler arrives at 17:00, waits until earliest_visit 18:00")
print("  ✓ Visit scheduled 18:00 -> 19:30")
print("  ✓ Test 3 Passed!")


# TEST 4: Latest Visit Constraint Rejection (Infeasible Schedule)
print("\n[Test 4] Testing Latest Visit Deadline Violation...")
route_late = OptimizedRoute(
    ordered_stops=[
        RouteStop(
            id="museum",
            name="Albert Hall Museum",
            latitude=26.91,
            longitude=75.81,
            type="attraction",
            latest_visit="10:00",
        )
    ],
    segments=[
        RouteSegment(from_stop="Hotel", to_stop="Albert Hall", duration_seconds=7200),  # 2 hours
    ],
    total_distance_meters=20000,
    total_duration_seconds=7200,
    score=7200,
)
try:
    generate_itinerary(ItineraryRequest(route=route_late, trip_date=date(2026, 9, 20), start_time="09:00"))
    assert False, "Expected ValueError due to late arrival after latest_visit"
except ValueError as e:
    print(f"  ✓ Successfully rejected infeasible visit: {e}")
print("  ✓ Test 4 Passed!")


# TEST 5: Lunch Window Handling
print("\n[Test 5] Testing Meal Window Scheduling (Lunch 12:00–14:30)...")
route_lunch = OptimizedRoute(
    ordered_stops=[
        RouteStop(
            id="lunch_place",
            name="Laxmi Mishthan Bhandar",
            latitude=26.92,
            longitude=75.82,
            type="restaurant",
            meal_type="lunch",
            duration_minutes=60,
        )
    ],
    segments=[
        RouteSegment(from_stop="Start", to_stop="LMB", duration_seconds=1200),  # 20 min travel
    ],
    total_distance_meters=5000,
    total_duration_seconds=1200,
    score=1200,
)
# Leave at 11:00 -> arrives at 11:20. Lunch window starts at 12:00
res5 = generate_itinerary(ItineraryRequest(route=route_lunch, trip_date=date(2026, 9, 20), start_time="11:00"))
assert res5.days[0].items[0].arrival_time == "12:00", "Visit start should align with lunch window 12:00"
assert res5.days[0].items[0].departure_time == "13:00"
print("  ✓ Arrived at 11:20, lunch starts at 12:00 -> departs at 13:00")
print("  ✓ Test 5 Passed!")


# TEST 6: Strict Preservation of Step 8 Stop Sequence
print("\n[Test 6] Testing Preservation of Step 8 Stop Sequence...")
route_order = OptimizedRoute(
    ordered_stops=[
        RouteStop(id="s1", name="Stop Alpha", latitude=26.90, longitude=75.80),
        RouteStop(id="s2", name="Stop Beta", latitude=26.91, longitude=75.81),
        RouteStop(id="s3", name="Stop Gamma", latitude=26.92, longitude=75.82),
    ],
    segments=[
        RouteSegment(from_stop="Start", to_stop="Stop Alpha", duration_seconds=300),
        RouteSegment(from_stop="Stop Alpha", to_stop="Stop Beta", duration_seconds=300),
        RouteSegment(from_stop="Stop Beta", to_stop="Stop Gamma", duration_seconds=300),
    ],
    total_distance_meters=3000,
    total_duration_seconds=900,
    score=900,
)
res6 = generate_itinerary(ItineraryRequest(route=route_order, trip_date=date(2026, 9, 20), start_time="09:00"))
out_ids = [item.stop_id for item in res6.days[0].items]
assert out_ids == ["s1", "s2", "s3"], f"Stop order violated: {out_ids}"
print(f"  ✓ Output stop order strictly matches input: {out_ids}")
print("  ✓ Test 6 Passed!")


# TEST 7 & 8: Day Boundaries & Multi-Day Rollover
print("\n[Test 7 & 8] Testing Day Boundary & Multi-Day Rollover...")
# Create a 4-stop full itinerary where stops 3 and 4 cannot finish before 20:00 (day_end_time)
multi_day_route = OptimizedRoute(
    ordered_stops=[
        RouteStop(id="stop1", name="Morning Palace", latitude=26.90, longitude=75.80, duration_minutes=180),  # 3 hrs
        RouteStop(id="stop2", name="Afternoon Fort", latitude=26.92, longitude=75.82, duration_minutes=240),  # 4 hrs
        RouteStop(id="stop3", name="Sunset Viewpoint", latitude=26.95, longitude=75.85, duration_minutes=180), # 3 hrs
        RouteStop(id="stop4", name="Night Bazaar", latitude=26.91, longitude=75.81, duration_minutes=120),    # 2 hrs
    ],
    segments=[
        RouteSegment(from_stop="Hotel", to_stop="Morning Palace", duration_seconds=1800),      # 0.5 hr travel -> arr 09:30, dep 12:30
        RouteSegment(from_stop="Morning Palace", to_stop="Afternoon Fort", duration_seconds=1800), # 0.5 hr travel -> arr 13:00, dep 17:00
        RouteSegment(from_stop="Afternoon Fort", to_stop="Sunset Viewpoint", duration_seconds=1800), # 0.5 hr travel -> arr 17:30, dep 20:30 (Exceeds 20:00!)
        RouteSegment(from_stop="Sunset Viewpoint", to_stop="Night Bazaar", duration_seconds=1800),
    ],
    total_distance_meters=40000,
    total_duration_seconds=7200,
    score=7200,
)

# Run with split_days=True and day_end_time="20:00"
res_multi = generate_itinerary(
    ItineraryRequest(
        route=multi_day_route,
        trip_date=date(2026, 9, 20),
        start_time="09:00",
        day_start_time="09:00",
        day_end_time="20:00",
        split_days=True,
    )
)
assert res_multi.total_days == 2, f"Expected 2 days, got {res_multi.total_days}"
assert len(res_multi.days[0].items) == 2, f"Day 1 should have 2 items, got {len(res_multi.days[0].items)}"
assert len(res_multi.days[1].items) == 2, f"Day 2 should have 2 items, got {len(res_multi.days[1].items)}"
assert res_multi.days[0].date == date(2026, 9, 20)
assert res_multi.days[1].date == date(2026, 9, 21)
assert res_multi.days[1].items[0].name == "Sunset Viewpoint"
assert res_multi.days[1].items[1].name == "Night Bazaar"
assert res_multi.days[1].segments[0].departure_time == "09:00"

print(f"  ✓ Successfully split across {res_multi.total_days} days:")
print(f"    * Day 1 ({res_multi.days[0].date}): {[i.name for i in res_multi.days[0].items]} (End: {res_multi.days[0].end_time})")
print(f"    * Day 2 ({res_multi.days[1].date}): {[i.name for i in res_multi.days[1].items]} (End: {res_multi.days[1].end_time})")
print("  ✓ Test 7 & 8 Passed!")


# TEST 9: Live FastAPI POST /itinerary/generate Endpoint
print("\n[Test 9] Testing FastAPI POST /itinerary/generate...")
endpoint_payload = {
    "trip_date": "2026-09-20",
    "start_time": "09:00",
    "day_start_time": "09:00",
    "day_end_time": "22:00",
    "split_days": True,
    "route": mock_route.model_dump(),
}

from fastapi import HTTPException
from main import generate_travel_itinerary

valid_req = ItineraryRequest(
    trip_date=date(2026, 9, 20),
    start_time="09:00",
    day_start_time="09:00",
    day_end_time="22:00",
    split_days=True,
    route=mock_route,
)
res = generate_travel_itinerary(valid_req)
assert res.total_days == 1
assert len(res.days[0].items) == 2
print("  ✓ FastAPI endpoint handler generate_travel_itinerary returns valid ItineraryResponse")
print(f"  ✓ Total days: {res.total_days}, Total distance: {res.total_distance_meters}m")

# Test 422 on invalid day boundary
invalid_req = ItineraryRequest(
    trip_date=date(2026, 9, 20),
    start_time="09:00",
    day_start_time="22:00",
    day_end_time="09:00",
    split_days=True,
    route=mock_route,
)
try:
    generate_travel_itinerary(invalid_req)
    assert False, "Expected HTTPException(422)"
except HTTPException as exc:
    assert exc.status_code == 422
    print(f"  ✓ HTTPException(422) properly raised: detail={exc.detail}")
print("  ✓ Test 9 Passed!")

print("\n" + "=" * 60)
print("ALL STEP 9 ITINERARY VERIFICATION TESTS PASSED SUCCESSFULLY!")
print("=" * 60)
