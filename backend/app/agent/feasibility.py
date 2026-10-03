"""
Project Musafir — Deterministic Feasibility & Conflict Engine (Milestone 3, Batch 3)
Evaluates whether a requested trip is feasible under all active constraints:
- Budget ceilings (nightly hotel vs. rate)
- Travel mode limits (walking distance / transit times)
- Day time capacity (available waking window vs. visit + transit durations)
- Meal windows (lunch 12:00-14:30, dinner 19:00-21:30)
- Stop priority (REQUIRED / must-visit vs. PREFERRED vs. OPTIONAL)

Core Principles:
1. 100% Deterministic: Pure Python arithmetic, zero LLM calls, zero network operations.
2. Hard Constraints are Never Silently Relaxed.
3. Soft Constraints / Preferences may be reported as violated but never treated as hard.
4. No Silent Deletions: Stops that do not fit are surfaced as unscheduled_items with clear conflicts.
5. Structured Output: Produces FeasibilityResult with ConstraintViolations and DayScheduleMetrics.
"""

from typing import Any, Dict, List, Optional, Set, Tuple, Union
import math

from app.schemas.constraint import (
    Constraint,
    ConstraintPriority,
    ConstraintScope,
    ConstraintType,
    ConstraintStatus,
    ConstraintSource,
    FeasibilityStatus,
    ConstraintViolationType,
    ConstraintViolation,
    DayScheduleMetrics,
    FeasibilityResult,
)
from app.agent.state import TripState, Place, Restaurant
from app.schemas.route import OptimizedRoute, RouteSegment


# Standard operational assumptions (SYSTEM_DERIVED)
DEFAULT_DAY_START_HOUR = 9.0    # 09:00 AM
DEFAULT_DAY_END_HOUR = 20.0     # 08:00 PM (11 waking hours)
DEFAULT_LUNCH_WINDOW = (12.0, 14.5)   # 12:00 PM to 02:30 PM
DEFAULT_DINNER_WINDOW = (19.0, 21.5)  # 07:00 PM to 09:30 PM

DEFAULT_ATTRACTION_MINUTES = 60.0     # 1 hour per attraction stop
DEFAULT_CAFE_MINUTES = 45.0           # 45 minutes for a cafe stop
DEFAULT_RESTAURANT_MINUTES = 75.0     # 1h 15m for full restaurant dining

MAX_DAILY_WALKING_KM = 12.0           # Warning/conflict threshold for single-day walking
MAX_SINGLE_WALKING_LEG_KM = 5.0       # Max realistic walking leg between sequential stops


def _parse_time_to_hours(time_str: Optional[str], default: float) -> float:
    """Parses 'HH:MM' string to decimal hours (e.g. '09:30' -> 9.5)."""
    if not time_str or ":" not in time_str:
        return default
    try:
        parts = time_str.strip().split(":")
        h = float(parts[0])
        m = float(parts[1]) if len(parts) > 1 else 0.0
        return h + (m / 60.0)
    except Exception:
        return default


def _format_hours_minutes(hours: float) -> str:
    """Formats decimal hours into human-readable 'Xh Ym' (e.g. 1.75 -> '1h 45m')."""
    total_minutes = int(round(hours * 60))
    h = total_minutes // 60
    m = total_minutes % 60
    if h > 0 and m > 0:
        return f"{h}h {m}m"
    elif h > 0:
        return f"{h}h"
    else:
        return f"{m}m"


class FeasibilityEngine:
    """
    Deterministic Feasibility Evaluation Engine.
    Analyzes current TripState against constraints, spatial route, and timing rules.
    """

    @classmethod
    def evaluate(
        cls,
        state: TripState,
        route: Optional[OptimizedRoute] = None,
    ) -> FeasibilityResult:
        """
        Main deterministic evaluation entry point.
        Evaluates budget, travel mode, daily time capacity, meal windows, and stop priorities.
        """
        violations: List[ConstraintViolation] = []
        warnings: List[str] = []

        # ---------------------------------------------------------------------
        # 1. Budget Constraint Evaluation
        # ---------------------------------------------------------------------
        cls._evaluate_budget(state, violations)

        # ---------------------------------------------------------------------
        # 2. Travel Mode & Distance Evaluation
        # ---------------------------------------------------------------------
        active_route = route or state.current_route
        cls._evaluate_travel_mode(state, active_route, violations, warnings)

        # ---------------------------------------------------------------------
        # 3. Schedule Feasibility & Day Capacity Evaluation
        # ---------------------------------------------------------------------
        scheduled_items, unscheduled_items, day_metrics, total_avail_h, total_req_h, excess_h = (
            cls._evaluate_schedule_capacity(state, active_route, violations, warnings)
        )

        # ---------------------------------------------------------------------
        # 4. Status Determination
        # ---------------------------------------------------------------------
        has_critical_or_required_violation = any(
            v.severity == "CRITICAL"
            or v.violation_type in (
                ConstraintViolationType.REQUIRED_STOP_CONFLICT,
                ConstraintViolationType.BUDGET_CONFLICT,
            )
            for v in violations
        )

        if has_critical_or_required_violation:
            status = FeasibilityStatus.INFEASIBLE
        elif violations:
            status = FeasibilityStatus.PARTIALLY_FEASIBLE
        else:
            status = FeasibilityStatus.FEASIBLE

        result = FeasibilityResult(
            status=status,
            scheduled_items=scheduled_items,
            unscheduled_items=unscheduled_items,
            violations=violations,
            warnings=warnings,
            day_metrics=day_metrics,
            total_available_hours=round(total_avail_h, 2),
            total_required_hours=round(total_req_h, 2),
            excess_hours=round(excess_h, 2),
        )

        # Cache on state
        state.feasibility_result = result
        return result

    # -------------------------------------------------------------------------
    # Evaluation Sub-routines
    # -------------------------------------------------------------------------

    @classmethod
    def _evaluate_budget(
        cls,
        state: TripState,
        violations: List[ConstraintViolation],
    ) -> None:
        """Verifies accommodation rate against budget ceiling."""
        hotel = state.hotel_selection
        if not hotel:
            return

        hotel_price = getattr(hotel, "price_per_night", None)
        if hotel_price is None or hotel_price <= 0:
            return

        budget = state.effective_hotel_budget
        if budget and hotel_price > budget:
            excess = hotel_price - budget
            total_budget_note = (
                f" (₹{state.hotel_total_budget:,.0f} total over {state.number_of_nights} nights)"
                if state.hotel_total_budget and state.number_of_nights
                and (not state.hotel_budget or budget < state.hotel_budget)
                else ""
            )
            violations.append(
                ConstraintViolation(
                    violation_type=ConstraintViolationType.BUDGET_CONFLICT,
                    affected_constraint=Constraint(
                        constraint_type=ConstraintType.HOTEL_BUDGET_CEILING,
                        scope=ConstraintScope.HOTEL,
                        priority=ConstraintPriority.REQUIRED,
                        source=ConstraintSource.USER_EXPLICIT,
                        value=budget,
                        status=ConstraintStatus.VIOLATED,
                    ),
                    affected_items=[hotel.name],
                    actual_value=hotel_price,
                    required_value=budget,
                    excess_or_deficit=excess,
                    severity="CRITICAL",
                    explanation=(
                        f"Selected hotel '{hotel.name}' costs ₹{hotel_price:,.0f}/night, "
                        f"which exceeds your effective nightly budget ceiling of ₹{budget:,.0f}{total_budget_note} "
                        f"(over by ₹{excess:,.0f}/night)."
                    ),
                    metadata={"hotel_name": hotel.name, "price_per_night": hotel_price, "budget": budget, "hotel_total_budget": state.hotel_total_budget},
                )
            )

    @classmethod
    def _evaluate_travel_mode(
        cls,
        state: TripState,
        route: Optional[OptimizedRoute],
        violations: List[ConstraintViolation],
        warnings: List[str],
    ) -> None:
        """Evaluates feasibility of selected travel mode over required route distances."""
        mode = (state.travel_mode or "driving").strip().lower()
        if not route or not route.segments:
            return

        total_distance_km = (route.total_distance_meters or 0.0) / 1000.0
        total_duration_h = (route.total_duration_seconds or 0.0) / 3600.0

        if mode == "walking":
            days = state.number_of_days or 1
            daily_walking_km = total_distance_km / max(1, days)

            # Check single longest leg
            longest_seg = max(route.segments, key=lambda s: s.distance_meters, default=None)
            longest_km = (longest_seg.distance_meters / 1000.0) if longest_seg else 0.0

            if longest_km > MAX_SINGLE_WALKING_LEG_KM or daily_walking_km > MAX_DAILY_WALKING_KM:
                advisory = (
                    f"Walking note: This itinerary covers {total_distance_km:.1f} km of walking "
                    f"(~{_format_hours_minutes(total_duration_h)} transit time), with individual legs up to {longest_km:.1f} km. "
                    f"Consider switching to driving or transit if you prefer shorter walks."
                )
                warnings.append(advisory)
                if hasattr(state, "feasibility_notes") and advisory not in state.feasibility_notes:
                    state.feasibility_notes.append(advisory)

                violations.append(
                    ConstraintViolation(
                        violation_type=ConstraintViolationType.MODE_CONFLICT,
                        affected_constraint=Constraint(
                            constraint_type=ConstraintType.TRAVEL_MODE,
                            scope=ConstraintScope.ROUTE,
                            priority=ConstraintPriority.REQUIRED,
                            source=ConstraintSource.USER_EXPLICIT,
                            value="walking",
                            status=ConstraintStatus.VIOLATED,
                        ),
                        affected_items=[seg.to_stop for seg in route.segments if seg.distance_meters > (MAX_SINGLE_WALKING_LEG_KM * 1000)],
                        actual_value=f"{total_distance_km:.1f} km walking ({_format_hours_minutes(total_duration_h)})",
                        required_value=f"<= {MAX_DAILY_WALKING_KM:.1f} km/day",
                        excess_or_deficit=f"+{total_distance_km - MAX_DAILY_WALKING_KM:.1f} km",
                        severity="HIGH",
                        explanation=(
                            f"Walking mode is impractical for this itinerary: requires {total_distance_km:.1f} km "
                            f"of total walking (~{_format_hours_minutes(total_duration_h)} transit time), "
                            f"with individual legs up to {longest_km:.1f} km."
                        ),
                        metadata={"total_distance_km": total_distance_km, "total_duration_hours": total_duration_h, "mode": mode},
                    )
                )

    @classmethod
    def _evaluate_schedule_capacity(
        cls,
        state: TripState,
        route: Optional[OptimizedRoute],
        violations: List[ConstraintViolation],
        warnings: List[str],
    ) -> Tuple[List[str], List[str], List[DayScheduleMetrics], float, float, float]:
        """
        Determines daily scheduling capacity and identifies unscheduled / conflicted stops.
        """
        days = max(1, state.number_of_days or 1)
        start_h = _parse_time_to_hours(state.start_time or state.day_start_time, DEFAULT_DAY_START_HOUR)
        end_h = _parse_time_to_hours(state.day_end_time, DEFAULT_DAY_END_HOUR)
        # Dedicated available sightseeing window (accounting for lunch reservation)
        daily_available_hours = max(2.0, (end_h - start_h) - 1.5)
        total_available_hours = daily_available_hours * days

        # Collect all items requiring schedule time
        items_to_schedule: List[Dict[str, Any]] = []

        # Attractions
        for p in state.selected_places:
            pri = "REQUIRED" if (state.is_stop_required(p.name) or getattr(p, "priority", "") == "REQUIRED") else getattr(p, "priority", "PREFERRED")
            dur_min = getattr(p, "visit_duration_minutes", None) or DEFAULT_ATTRACTION_MINUTES
            items_to_schedule.append({
                "name": p.name,
                "type": "place",
                "priority": pri,
                "duration_hours": dur_min / 60.0,
            })

        # Restaurants
        for r in state.selected_restaurants:
            pri = "REQUIRED" if (state.is_stop_required(r.name) or getattr(r, "priority", "") == "REQUIRED") else "PREFERRED"
            items_to_schedule.append({
                "name": r.name,
                "type": "restaurant",
                "priority": pri,
                "duration_hours": DEFAULT_RESTAURANT_MINUTES / 60.0,
            })

        # Cafes
        for c in state.selected_cafes:
            pri = "REQUIRED" if (state.is_stop_required(c.name) or getattr(c, "priority", "") == "REQUIRED") else "PREFERRED"
            items_to_schedule.append({
                "name": c.name,
                "type": "cafe",
                "priority": pri,
                "duration_hours": DEFAULT_CAFE_MINUTES / 60.0,
            })

        # Transit estimates per stop
        if (state.travel_mode or "").lower() == "walking":
            if route and route.total_duration_seconds and route.segments:
                transit_per_stop_h = max(0.5, (route.total_duration_seconds / len(route.segments)) / 3600.0)
            else:
                transit_per_stop_h = 0.75  # ~45 minutes walking transit per stop
        elif route and route.segments and len(route.segments) > 0:
            avg_sec = (route.total_duration_seconds or 0.0) / max(1, len(route.segments))
            transit_per_stop_h = max(0.35, avg_sec / 3600.0)
        else:
            transit_per_stop_h = 0.35  # default ~21 minutes transit between stops

        # Sort items: REQUIRED first, then PREFERRED, then OPTIONAL
        pri_order = {"REQUIRED": 0, "PREFERRED": 1, "OPTIONAL": 2}
        items_sorted = sorted(items_to_schedule, key=lambda x: pri_order.get(x["priority"], 1))


        scheduled_names: List[str] = []
        unscheduled_names: List[str] = []
        day_metrics: List[DayScheduleMetrics] = []

        total_req_hours = sum(it["duration_hours"] + transit_per_stop_h for it in items_sorted)

        # Allocate greedily across available days
        curr_day = 1
        curr_day_used_h = 0.0
        curr_day_sightseeing_h = 0.0
        curr_day_travel_h = 0.0
        curr_day_meal_h = 0.0
        curr_day_stops: List[str] = []

        for item in items_sorted:
            item_h = item["duration_hours"]
            transit_h = transit_per_stop_h
            needed_h = item_h + transit_h

            if curr_day_used_h + needed_h <= daily_available_hours:
                # Fits in current day
                curr_day_used_h += needed_h
                if item["type"] == "place":
                    curr_day_sightseeing_h += item_h
                else:
                    curr_day_meal_h += item_h
                curr_day_travel_h += transit_h
                curr_day_stops.append(item["name"])
                scheduled_names.append(item["name"])
            else:
                # Try next day if available
                if curr_day < days:
                    # Finalize current day
                    day_metrics.append(DayScheduleMetrics(
                        day_number=curr_day,
                        available_hours=daily_available_hours,
                        required_sightseeing_hours=round(curr_day_sightseeing_h, 2),
                        required_travel_hours=round(curr_day_travel_h, 2),
                        required_meal_hours=round(curr_day_meal_h, 2),
                        total_required_hours=round(curr_day_used_h, 2),
                        excess_hours=0.0,
                        stops=list(curr_day_stops),
                    ))
                    # Advance to next day
                    curr_day += 1
                    curr_day_used_h = needed_h
                    curr_day_sightseeing_h = item_h if item["type"] == "place" else 0.0
                    curr_day_meal_h = item_h if item["type"] != "place" else 0.0
                    curr_day_travel_h = transit_h
                    curr_day_stops = [item["name"]]
                    scheduled_names.append(item["name"])
                else:
                    # Capacity fully exceeded! Item cannot fit.
                    unscheduled_names.append(item["name"])

        # Finalize last day
        if curr_day <= days:
            day_metrics.append(DayScheduleMetrics(
                day_number=curr_day,
                available_hours=daily_available_hours,
                required_sightseeing_hours=round(curr_day_sightseeing_h, 2),
                required_travel_hours=round(curr_day_travel_h, 2),
                required_meal_hours=round(curr_day_meal_h, 2),
                total_required_hours=round(curr_day_used_h, 2),
                excess_hours=0.0,
                stops=list(curr_day_stops),
            ))

        # Fill remaining days if any
        while len(day_metrics) < days:
            day_metrics.append(DayScheduleMetrics(
                day_number=len(day_metrics) + 1,
                available_hours=daily_available_hours,
                required_sightseeing_hours=0.0,
                required_travel_hours=0.0,
                required_meal_hours=0.0,
                total_required_hours=0.0,
                excess_hours=0.0,
                stops=[],
            ))

        excess_hours = max(0.0, total_req_hours - total_available_hours)

        # ---------------------------------------------------------------------
        # Conflict Generation
        # ---------------------------------------------------------------------
        if unscheduled_names:
            # Check if any unscheduled items were REQUIRED
            unscheduled_required = [
                name for name in unscheduled_names if state.is_stop_required(name)
            ]

            if unscheduled_required:
                violations.append(
                    ConstraintViolation(
                        violation_type=ConstraintViolationType.REQUIRED_STOP_CONFLICT,
                        affected_constraint=Constraint(
                            constraint_type=ConstraintType.MUST_VISIT_STOP,
                            scope=ConstraintScope.PLACE,
                            priority=ConstraintPriority.REQUIRED,
                            source=ConstraintSource.USER_EXPLICIT,
                            value=unscheduled_required,
                            status=ConstraintStatus.VIOLATED,
                        ),
                        affected_items=unscheduled_required,
                        actual_value=f"{len(scheduled_names)}/{len(items_sorted)} scheduled",
                        required_value=f"All {len(items_sorted)} stops scheduled",
                        severity="CRITICAL",
                        explanation=(
                            f"Must-visit stop(s) '{', '.join(unscheduled_required)}' cannot fit into "
                            f"your {days}-day itinerary. To include them, you can extend the trip duration, "
                            f"switch to faster transit, or remove optional stops."
                        ),
                        metadata={"unscheduled_required": unscheduled_required},
                    )
                )

            # Day capacity exceeded violation
            violations.append(
                ConstraintViolation(
                    violation_type=ConstraintViolationType.DAY_CAPACITY_EXCEEDED,
                    affected_constraint=Constraint(
                        constraint_type=ConstraintType.DAILY_TIME_LIMIT,
                        scope=ConstraintScope.TIME,
                        priority=ConstraintPriority.REQUIRED,
                        source=ConstraintSource.SYSTEM_DERIVED,
                        value=total_available_hours,
                        status=ConstraintStatus.VIOLATED,
                    ),
                    affected_items=unscheduled_names,
                    actual_value=f"{total_req_hours:.1f}h required",
                    required_value=f"{total_available_hours:.1f}h available",
                    excess_or_deficit=f"+{excess_hours:.1f}h",
                    severity="HIGH",
                    explanation=(
                        f"Your planned stops require {_format_hours_minutes(total_req_hours)} of sightseeing and travel, "
                        f"exceeding the available schedule of {_format_hours_minutes(total_available_hours)} across {days} day(s) "
                        f"by {_format_hours_minutes(excess_hours)}. "
                        f"Unscheduled stops: {', '.join(unscheduled_names)}."
                    ),
                    metadata={
                        "total_required_hours": total_req_hours,
                        "total_available_hours": total_available_hours,
                        "excess_hours": excess_hours,
                        "unscheduled_stops": unscheduled_names,
                    },
                )
            )

        return (
            scheduled_names,
            unscheduled_names,
            day_metrics,
            total_available_hours,
            total_req_hours,
            excess_hours,
        )


# =============================================================================
# CONFLICT EXPLANATION FORMATTER
# =============================================================================

def format_feasibility_explanation(result: FeasibilityResult, state: TripState) -> str:
    """
    Transforms deterministic feasibility violations into clear, traveler-friendly explanations.
    Guarantees:
    - Never silently removes stops or compromises hard constraints.
    - Accurately reports duration excesses and specific conflicting entities.
    - Outlines user-driven resolution choices (add days, change mode, remove optional stops).
    """
    if result.is_feasible:
        stops_count = len(result.scheduled_items)
        days = max(1, state.number_of_days or 1)
        return f"Your {days}-day plan is feasible! All {stops_count} stops fit comfortably within daily schedules."

    lines = []
    # 1. Headline summary
    if result.status == FeasibilityStatus.INFEASIBLE:
        lines.append("I evaluated your itinerary, but found a scheduling conflict with your requirements:")
    else:
        lines.append("I evaluated your itinerary and fit most of your stops, but some cannot fit within the available time:")

    # 2. Detailed violations
    for v in result.violations:
        lines.append(f"• {v.explanation}")

    # 3. Actionable resolution options
    days = max(1, state.number_of_days or 1)
    options = []
    if any(v.violation_type in (ConstraintViolationType.DAY_CAPACITY_EXCEEDED, ConstraintViolationType.REQUIRED_STOP_CONFLICT) for v in result.violations):
        options.append(f"1. Add an extra day (e.g. \"make it {days + 1} days\")")
        if state.travel_mode == "walking":
            options.append("2. Switch travel mode to driving to reduce transit time (e.g. \"switch to driving\")")
        if result.unscheduled_items:
            options.append(f"3. Remove one or more optional stops (e.g. \"remove {result.unscheduled_items[0]}\")")
    elif any(v.violation_type == ConstraintViolationType.BUDGET_CONFLICT for v in result.violations):
        options.append("1. Increase the applicable nightly or total hotel budget ceiling (e.g. \"increase hotel budget to ...\")")
        options.append("2. Switch to an accommodation under your budget (e.g. \"change hotel to ...\")")
    elif any(v.violation_type == ConstraintViolationType.MODE_CONFLICT for v in result.violations):
        options.append("1. Switch travel mode to driving or transit (e.g. \"switch to driving\")")
        options.append("2. Remove distant attractions to keep stops tightly clustered")

    if options:
        lines.append("\nHere are a few ways we can resolve this:")
        for opt in options:
            lines.append(f"  {opt}")
        lines.append("\nWhat would you prefer?")

    return "\n".join(lines)
