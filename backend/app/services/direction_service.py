import re
from typing import Any, Dict, Optional, Tuple
import requests
from app.schemas.direction import DirectionResponse

SUPPORTED_TRAVEL_MODES = {
    "driving": 0,
    "walking": 2,
    "bicycling": 1,
    "transit": 3,
    "two_wheeler": 9,
}


def is_coordinates(value: str) -> bool:
    """Check whether a string represents 'latitude,longitude' coordinates."""
    parts = value.strip().split(",")
    if len(parts) == 2:
        try:
            lat = float(parts[0].strip())
            lng = float(parts[1].strip())
            return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0
        except ValueError:
            return False
    return False


def build_direction_params(
    origin: str,
    destination: str,
    mode: str,
    api_key: str,
) -> Dict[str, Any]:
    """Translate origin, destination, and mode into SerpApi Google Maps Directions parameters."""
    mode_lower = mode.strip().lower()
    numeric_mode = SUPPORTED_TRAVEL_MODES.get(mode_lower, 0)

    params: Dict[str, Any] = {
        "engine": "google_maps_directions",
        "travel_mode": numeric_mode,
        "api_key": api_key,
    }

    clean_origin = origin.strip()
    if is_coordinates(clean_origin):
        params["start_coords"] = clean_origin
    else:
        params["start_addr"] = clean_origin

    clean_destination = destination.strip()
    if is_coordinates(clean_destination):
        params["end_coords"] = clean_destination
    else:
        params["end_addr"] = clean_destination

    return params


def fetch_directions_from_serpapi(params: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    """Execute HTTP request against SerpApi Google Maps Directions."""
    response = requests.get(
        "https://serpapi.com/search",
        params=params,
        timeout=timeout,
    )
    if response.status_code != 200:
        error_detail = "Failed to fetch directions from SerpApi"
        try:
            err_data = response.json()
            if "error" in err_data:
                error_detail = err_data["error"]
        except Exception:
            pass
        raise RuntimeError(f"SerpApi error ({response.status_code}): {error_detail}")

    data = response.json()
    if "error" in data:
        raise ValueError(data["error"])

    return data


def extract_metric(raw_metric: Any) -> Tuple[Optional[float], Optional[str]]:
    """Extract numeric value and string text from distance or duration data."""
    if raw_metric is None:
        return None, None

    if isinstance(raw_metric, (int, float)):
        return float(raw_metric), None

    if isinstance(raw_metric, dict):
        val = raw_metric.get("value")
        text = raw_metric.get("text")
        num_val = float(val) if val is not None else None
        return num_val, str(text) if text else None

    if isinstance(raw_metric, str):
        nums = re.findall(r"\d+(?:\.\d+)?", raw_metric.replace(",", ""))
        num_val = float(nums[0]) if nums else None
        return num_val, raw_metric.strip()

    return None, None


def normalize_direction_response(
    raw_response: Dict[str, Any],
    origin: str,
    destination: str,
    mode: str,
) -> Optional[DirectionResponse]:
    """Normalize raw SerpApi directions response into a clean DirectionResponse."""
    if not isinstance(raw_response, dict):
        return None

    routes = raw_response.get("directions") or raw_response.get("routes")
    if not isinstance(routes, list) or len(routes) == 0:
        return None

    best_route = routes[0]
    if not isinstance(best_route, dict):
        return None

    # Check legs if present
    legs = best_route.get("legs")
    leg_data = legs[0] if isinstance(legs, list) and len(legs) > 0 and isinstance(legs[0], dict) else best_route

    # Distance extraction
    dist_val, dist_text = extract_metric(leg_data.get("distance"))
    formatted_dist = leg_data.get("formatted_distance") or best_route.get("formatted_distance")
    if formatted_dist and isinstance(formatted_dist, str):
        dist_text = formatted_dist.strip()

    # Duration extraction
    dur_val, dur_text = extract_metric(leg_data.get("duration"))
    formatted_dur = leg_data.get("formatted_duration") or best_route.get("formatted_duration")
    if formatted_dur and isinstance(formatted_dur, str):
        dur_text = formatted_dur.strip()

    # Fallback string formatting if missing but numeric is present
    if dist_val is not None and not dist_text:
        if dist_val >= 1000:
            dist_text = f"{dist_val / 1000:.1f} km"
        else:
            dist_text = f"{int(dist_val)} m"

    if dur_val is not None and not dur_text:
        mins = round(dur_val / 60)
        if mins >= 60:
            hours = mins // 60
            rem_mins = mins % 60
            dur_text = f"{hours} hr {rem_mins} min" if rem_mins else f"{hours} hr"
        else:
            dur_text = f"{mins} min"

    return DirectionResponse(
        origin=origin.strip(),
        destination=destination.strip(),
        mode=mode.strip().lower(),
        distance_text=dist_text,
        distance_meters=dist_val,
        duration_text=dur_text,
        duration_seconds=dur_val,
    )
