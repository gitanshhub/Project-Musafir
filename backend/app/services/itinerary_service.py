from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from app.schemas.route import RouteStop, RouteSegment, OptimizedRoute
from app.schemas.itinerary import (
    ItineraryItem,
    ItinerarySegment,
    DailyItinerary,
    ItineraryRequest,
    ItineraryResponse,
)

# Default durations in minutes by stop type
DEFAULT_DURATIONS: Dict[str, int] = {
    "attraction": 60,
    "restaurant": 60,
    "cafe": 45,
    "hotel": 30,
    "default": 60,
}

# Meal time windows (start_time, end_time)
MEAL_WINDOWS: Dict[str, Tuple[time, time]] = {
    "breakfast": (time(8, 0), time(10, 0)),
    "lunch": (time(12, 0), time(14, 30)),
    "dinner": (time(19, 0), time(22, 0)),
}

# Named time aliases
TIME_ALIASES: Dict[str, str] = {
    "morning": "09:00",
    "afternoon": "13:00",
    "evening": "18:00",
    "night": "20:00",
}


def parse_time_string(time_str: str) -> time:
    """
    Parses a time string in 'HH:MM' or alias ('morning', 'night') into a datetime.time object.
    Raises ValueError if invalid.
    """
    clean_str = time_str.strip().lower()
    if clean_str in TIME_ALIASES:
        clean_str = TIME_ALIASES[clean_str]

    parts = clean_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format '{time_str}'. Expected 'HH:MM' (e.g. '09:30').")

    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except ValueError:
        raise ValueError(f"Invalid numbers in time string '{time_str}'.")

    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"Time '{time_str}' is out of bounds (hours: 0-23, minutes: 0-59).")

    return time(hour=hours, minute=minutes)


def get_default_duration(stop_type: Optional[str]) -> int:
    """
    Returns deterministic default duration in minutes based on stop type.
    """
    if not stop_type:
        return DEFAULT_DURATIONS["default"]
    clean_type = stop_type.strip().lower()
    return DEFAULT_DURATIONS.get(clean_type, DEFAULT_DURATIONS["default"])


def get_meal_window(meal_type: Optional[str]) -> Optional[Tuple[time, time]]:
    """
    Returns (start_time, end_time) for a recognized meal type, or None.
    """
    if not meal_type:
        return None
    return MEAL_WINDOWS.get(meal_type.strip().lower())


def generate_itinerary(request: ItineraryRequest) -> ItineraryResponse:
    """
    Deterministically transforms an OptimizedRoute into a day-by-day ItineraryResponse.
    Preserves stop ordering, calculates arrival and departure times, enforces meal and
    earliest/latest visit constraints, and handles day boundaries/multi-day rollovers.
    """
    # 1. Parse and validate boundary times
    day_start_t = parse_time_string(request.day_start_time)
    day_end_t = parse_time_string(request.day_end_time)

    if day_start_t >= day_end_t:
        raise ValueError(
            f"day_start_time ({request.day_start_time}) must be earlier than day_end_time ({request.day_end_time})"
        )

    start_t = parse_time_string(request.start_time)

    ordered_stops: List[RouteStop] = request.route.ordered_stops
    segments: List[RouteSegment] = request.route.segments

    if not ordered_stops:
        # Edge case: no stops
        empty_day = DailyItinerary(
            day_number=1,
            date=request.trip_date,
            start_time=request.start_time,
            end_time=request.start_time,
            items=[],
            segments=[],
            total_travel_seconds=0.0,
            total_visit_minutes=0,
        )
        return ItineraryResponse(
            days=[empty_day],
            total_days=1,
            total_distance_meters=0.0,
            total_travel_seconds=0.0,
            total_visit_minutes=0,
        )

    # 2. Prepare tracking state
    current_day_number = 1
    current_date = request.trip_date

    daily_plans: List[DailyItinerary] = []
    current_day_items: List[ItineraryItem] = []
    current_day_segments: List[ItinerarySegment] = []
    current_day_start_str = request.start_time
    current_time_dt = datetime.combine(current_date, start_t)

    total_distance_meters = 0.0
    total_travel_seconds = 0.0
    total_visit_minutes = 0.0

    stop_index = 0
    total_stops = len(ordered_stops)

    while stop_index < total_stops:
        stop = ordered_stops[stop_index]

        # Determine visit duration
        duration_minutes = stop.duration_minutes
        if duration_minutes is None or duration_minutes <= 0:
            duration_minutes = get_default_duration(stop.type)

        # Retrieve associated route segment to this stop
        route_seg: Optional[RouteSegment] = segments[stop_index] if stop_index < len(segments) else None
        travel_sec = (route_seg.duration_seconds or 0.0) if route_seg else 0.0
        distance_m = (route_seg.distance_meters or 0.0) if route_seg else 0.0

        # Departure for travel
        dep_time_dt = current_time_dt
        arr_time_dt = dep_time_dt + timedelta(seconds=travel_sec)

        # Check earliest visit constraint
        earliest_t: Optional[time] = None
        if stop.earliest_visit:
            earliest_t = parse_time_string(stop.earliest_visit)

        # Check meal window constraints
        meal_win = get_meal_window(stop.meal_type)
        if meal_win:
            meal_start_t, meal_end_t = meal_win
            if earliest_t is None or meal_start_t > earliest_t:
                earliest_t = meal_start_t

        # Apply earliest visit wait time
        visit_start_dt = arr_time_dt
        if earliest_t:
            earliest_dt = datetime.combine(current_date, earliest_t)
            if visit_start_dt < earliest_dt:
                visit_start_dt = earliest_dt

        # Check latest visit constraint & meal window deadline
        latest_t: Optional[time] = None
        if stop.latest_visit:
            latest_t = parse_time_string(stop.latest_visit)

        if meal_win:
            _, meal_end_t = meal_win
            if latest_t is None or meal_end_t < latest_t:
                latest_t = meal_end_t

        if latest_t:
            # Validate earliest <= latest constraint
            if earliest_t and earliest_t > latest_t:
                raise ValueError(
                    f"Stop '{stop.name}' ({stop.id}) has earliest visit/window ({earliest_t.strftime('%H:%M')}) "
                    f"after latest visit/window ({latest_t.strftime('%H:%M')})"
                )
            latest_dt = datetime.combine(current_date, latest_t)
            if visit_start_dt > latest_dt:
                raise ValueError(
                    f"Stop '{stop.name}' ({stop.id}) cannot start before latest visit deadline "
                    f"{latest_t.strftime('%H:%M')}. Scheduled arrival/start is {visit_start_dt.strftime('%H:%M')}."
                )

        visit_end_dt = visit_start_dt + timedelta(minutes=duration_minutes)
        day_limit_dt = datetime.combine(current_date, day_end_t)

        # Day Boundary & Multi-Day Rollover Check
        if visit_end_dt > day_limit_dt:
            if not request.split_days:
                raise ValueError(
                    f"Stop '{stop.name}' ({stop.id}) visit finishes at {visit_end_dt.strftime('%H:%M')}, "
                    f"which exceeds day_end_time ({request.day_end_time})."
                )

            # If current day already has activities, wrap up this day and push this stop to next day
            if current_day_items:
                last_departure = current_day_items[-1].departure_time
                daily_plans.append(
                    DailyItinerary(
                        day_number=current_day_number,
                        date=current_date,
                        start_time=current_day_start_str,
                        end_time=last_departure,
                        items=current_day_items,
                        segments=current_day_segments,
                        total_travel_seconds=sum(s.duration_seconds or 0.0 for s in current_day_segments),
                        total_visit_minutes=sum(i.duration_minutes for i in current_day_items),
                    )
                )

                # Reset state for next day
                current_day_number += 1
                current_date = current_date + timedelta(days=1)
                current_day_items = []
                current_day_segments = []
                current_day_start_str = request.day_start_time
                current_time_dt = datetime.combine(current_date, day_start_t)
                # Retry scheduling this stop on the new day
                continue
            else:
                # Even starting from the beginning of the day, this single stop cannot fit
                raise ValueError(
                    f"Stop '{stop.name}' ({stop.id}) duration and travel ({travel_sec/60:.0f}m + {duration_minutes}m) "
                    f"cannot fit within day operating hours ({request.day_start_time} - {request.day_end_time})."
                )

        # Stop fits within the day! Record itinerary segment and item
        itin_segment = ItinerarySegment(
            from_stop=route_seg.from_stop if route_seg else (ordered_stops[stop_index - 1].name if stop_index > 0 else "Start Location"),
            to_stop=route_seg.to_stop if route_seg else stop.name,
            departure_time=dep_time_dt.strftime("%H:%M"),
            arrival_time=arr_time_dt.strftime("%H:%M"),
            duration_seconds=route_seg.duration_seconds if route_seg else 0.0,
            distance_meters=route_seg.distance_meters if route_seg else 0.0,
            duration_text=route_seg.duration_text if route_seg else None,
            distance_text=route_seg.distance_text if route_seg else None,
        )
        current_day_segments.append(itin_segment)

        itin_item = ItineraryItem(
            stop_id=stop.id,
            name=stop.name,
            type=stop.type,
            meal_type=stop.meal_type,
            arrival_time=visit_start_dt.strftime("%H:%M"),
            departure_time=visit_end_dt.strftime("%H:%M"),
            duration_minutes=duration_minutes,
            latitude=stop.latitude,
            longitude=stop.longitude,
        )
        current_day_items.append(itin_item)

        total_distance_meters += distance_m
        total_travel_seconds += travel_sec
        total_visit_minutes += duration_minutes

        current_time_dt = visit_end_dt
        stop_index += 1

    # Check if there is a trailing segment (e.g. return to end_location/hotel)
    if len(segments) > total_stops:
        final_seg = segments[total_stops]
        return_travel_sec = final_seg.duration_seconds or 0.0
        return_dist_m = final_seg.distance_meters or 0.0
        return_dep_dt = current_time_dt
        return_arr_dt = return_dep_dt + timedelta(seconds=return_travel_sec)

        itin_final_segment = ItinerarySegment(
            from_stop=final_seg.from_stop,
            to_stop=final_seg.to_stop,
            departure_time=return_dep_dt.strftime("%H:%M"),
            arrival_time=return_arr_dt.strftime("%H:%M"),
            duration_seconds=final_seg.duration_seconds,
            distance_meters=final_seg.distance_meters,
            duration_text=final_seg.duration_text,
            distance_text=final_seg.distance_text,
        )
        current_day_segments.append(itin_final_segment)
        total_distance_meters += return_dist_m
        total_travel_seconds += return_travel_sec
        current_time_dt = return_arr_dt

    # Wrap up final active day
    if current_day_items or current_day_segments:
        final_end_str = current_time_dt.strftime("%H:%M")
        daily_plans.append(
            DailyItinerary(
                day_number=current_day_number,
                date=current_date,
                start_time=current_day_start_str,
                end_time=final_end_str,
                items=current_day_items,
                segments=current_day_segments,
                total_travel_seconds=sum(s.duration_seconds or 0.0 for s in current_day_segments),
                total_visit_minutes=sum(i.duration_minutes for i in current_day_items),
            )
        )

    return ItineraryResponse(
        days=daily_plans,
        total_days=len(daily_plans),
        total_distance_meters=total_distance_meters,
        total_travel_seconds=total_travel_seconds,
        total_visit_minutes=int(total_visit_minutes),
    )
