import itertools
import re
from typing import Any, Dict, List, Optional, Tuple
from app.schemas.route import (
    LocationPoint,
    RouteStop,
    RouteRequest,
    RouteSegment,
    OptimizedRoute,
)
from app.services.direction_service import (
    build_direction_params,
    fetch_directions_from_serpapi,
    normalize_direction_response,
)


def coords_str(lat: float, lng: float) -> str:
    """Format coordinates as 'latitude,longitude'."""
    return f"{lat:.6f},{lng:.6f}"


def parse_time_to_minutes(time_str: str) -> Optional[int]:
    """Parse time string like '14:00', '2:00 PM', or 'evening' into minutes from midnight."""
    clean = time_str.strip().lower()
    if clean in ("morning", "breakfast"):
        return 9 * 60
    if clean in ("noon", "lunch"):
        return 13 * 60
    if clean in ("afternoon", "sunset"):
        return 17 * 60
    if clean in ("night", "dinner"):
        return 20 * 60

    match = re.search(r"(\d{1,2}):(\d{2})(?:\s*(am|pm))?", clean)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        meridiem = match.group(3)
        if meridiem == "pm" and hours < 12:
            hours += 12
        elif meridiem == "am" and hours == 12:
            hours = 0
        return hours * 60 + minutes

    return None


def get_route_segment_with_cache(
    origin_coords: str,
    dest_coords: str,
    from_name: str,
    to_name: str,
    mode: str,
    api_key: str,
    cache: Dict[Tuple[str, str, str], RouteSegment],
) -> RouteSegment:
    """Retrieve segment travel data from Directions engine, using mode-specific in-memory cache."""
    # Cache key includes mode to preserve mode-dependent driving vs walking transitions
    cache_key = (origin_coords, dest_coords, mode.lower().strip())
    if cache_key in cache:
        cached = cache[cache_key]
        return RouteSegment(
            from_stop=from_name,
            to_stop=to_name,
            distance_meters=cached.distance_meters,
            duration_seconds=cached.duration_seconds,
            distance_text=cached.distance_text,
            duration_text=cached.duration_text,
        )

    params = build_direction_params(
        origin=origin_coords,
        destination=dest_coords,
        mode=mode,
        api_key=api_key,
    )

    raw_data = fetch_directions_from_serpapi(params)
    direction = normalize_direction_response(
        raw_data,
        origin=origin_coords,
        destination=dest_coords,
        mode=mode,
    )

    if not direction:
        raise ValueError(f"No route found between '{from_name}' and '{to_name}'")

    segment = RouteSegment(
        from_stop=from_name,
        to_stop=to_name,
        distance_meters=direction.distance_meters,
        duration_seconds=direction.duration_seconds,
        distance_text=direction.distance_text,
        duration_text=direction.duration_text,
    )

    cache[cache_key] = segment
    return segment


def check_hard_constraints(
    candidate_stops: List[RouteStop],
    segments: List[RouteSegment],
) -> bool:
    """Validate whether candidate stop sequence satisfies earliest/latest visit constraints."""
    # Start clock at 09:00 AM (540 minutes from midnight)
    current_time_minutes = 9 * 60

    # First segment: start_location -> candidate_stops[0]
    first_seg = segments[0]
    current_time_minutes += int((first_seg.duration_seconds or 0) / 60)

    for i, stop in enumerate(candidate_stops):
        # If not the first stop, travel duration from previous stop was already added
        if i > 0:
            seg = segments[i]
            current_time_minutes += int((seg.duration_seconds or 0) / 60)

        # Earliest visit check
        if stop.earliest_visit:
            earliest = parse_time_to_minutes(stop.earliest_visit)
            if earliest is not None:
                if current_time_minutes < earliest:
                    current_time_minutes = earliest  # Wait until place opens

        # Latest visit check
        if stop.latest_visit:
            latest = parse_time_to_minutes(stop.latest_visit)
            if latest is not None:
                if current_time_minutes > latest:
                    return False  # Violated latest visit hard constraint

        # Add time spent at stop
        stay_duration = stop.duration_minutes if stop.duration_minutes is not None else 60
        current_time_minutes += stay_duration

    return True


def optimize_route(request: RouteRequest, api_key: str) -> OptimizedRoute:
    """Evaluate candidate stop sequences and choose the optimal feasible route."""
    stops = request.stops
    start_loc = request.start_location
    end_loc = request.end_location
    mode = request.mode.strip().lower()

    # In-memory segment cache for this request
    segment_cache: Dict[Tuple[str, str, str], RouteSegment] = {}

    # Candidate orders
    if request.respect_user_order:
        candidate_orders = [list(stops)]
    else:
        candidate_orders = [list(p) for p in itertools.permutations(stops)]

    best_order: Optional[List[RouteStop]] = None
    best_segments: Optional[List[RouteSegment]] = None
    best_score = float("inf")
    best_total_distance = 0.0
    best_total_duration = 0.0

    start_coords = coords_str(start_loc.latitude, start_loc.longitude)
    start_name = start_loc.name or "Start Location"

    for candidate in candidate_orders:
        segments: List[RouteSegment] = []
        total_duration = 0.0
        total_distance = 0.0
        route_valid = True

        # Segment 1: Start Location -> First Stop
        first_stop = candidate[0]
        first_coords = coords_str(first_stop.latitude, first_stop.longitude)
        try:
            seg = get_route_segment_with_cache(
                origin_coords=start_coords,
                dest_coords=first_coords,
                from_name=start_name,
                to_name=first_stop.name,
                mode=mode,
                api_key=api_key,
                cache=segment_cache,
            )
            segments.append(seg)
            total_duration += seg.duration_seconds or 0.0
            total_distance += seg.distance_meters or 0.0
        except Exception:
            continue

        # Inter-stop segments: candidate[i] -> candidate[i+1]
        for i in range(len(candidate) - 1):
            curr_stop = candidate[i]
            next_stop = candidate[i + 1]
            curr_coords = coords_str(curr_stop.latitude, curr_stop.longitude)
            next_coords = coords_str(next_stop.latitude, next_stop.longitude)
            try:
                seg = get_route_segment_with_cache(
                    origin_coords=curr_coords,
                    dest_coords=next_coords,
                    from_name=curr_stop.name,
                    to_name=next_stop.name,
                    mode=mode,
                    api_key=api_key,
                    cache=segment_cache,
                )
                segments.append(seg)
                total_duration += seg.duration_seconds or 0.0
                total_distance += seg.distance_meters or 0.0
            except Exception:
                route_valid = False
                break

        if not route_valid:
            continue

        # Optional final segment: Last Stop -> End Location
        if end_loc:
            last_stop = candidate[-1]
            last_coords = coords_str(last_stop.latitude, last_stop.longitude)
            end_coords = coords_str(end_loc.latitude, end_loc.longitude)
            end_name = end_loc.name or "End Location"
            try:
                seg = get_route_segment_with_cache(
                    origin_coords=last_coords,
                    dest_coords=end_coords,
                    from_name=last_stop.name,
                    to_name=end_name,
                    mode=mode,
                    api_key=api_key,
                    cache=segment_cache,
                )
                segments.append(seg)
                total_duration += seg.duration_seconds or 0.0
                total_distance += seg.distance_meters or 0.0
            except Exception:
                continue

        # Check hard constraints
        if not check_hard_constraints(candidate, segments):
            continue

        # Score is total travel duration in seconds (lower is better)
        score = total_duration
        if score < best_score:
            best_score = score
            best_order = candidate
            best_segments = segments
            best_total_distance = total_distance
            best_total_duration = total_duration

    if best_order is None or best_segments is None:
        raise ValueError("No feasible route found satisfying constraints and directions")

    reason = (
        "Preserved user-specified order of stops"
        if request.respect_user_order
        else "Optimized for minimum travel time"
    )

    return OptimizedRoute(
        ordered_stops=best_order,
        segments=best_segments,
        total_distance_meters=best_total_distance,
        total_duration_seconds=best_total_duration,
        score=best_score,
        reason=reason,
    )
