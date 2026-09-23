import os
import re
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional
import requests

from app.agent.state import TripState
from app.agent.state_update import (
    apply_trip_state_update,
    check_trip_readiness,
    get_next_missing_requirement,
)
from app.schemas.hotel import Hotel, HotelDetail, HotelResponse
from app.schemas.place import Place, PlaceResponse
from app.schemas.restaurant import Restaurant, RestaurantResponse
from app.schemas.route import LocationPoint, RouteStop, RouteRequest, OptimizedRoute
from app.schemas.itinerary import ItineraryRequest, ItineraryResponse
from app.services.hotel_service import (
    build_hotel_search_params,
    build_hotel_detail_params,
    fetch_hotels_from_serpapi,
    normalize_hotels_response,
    normalize_hotel_detail,
    filter_hotels_by_budget,
)
from app.services.place_service import (
    build_place_search_params,
    fetch_places_from_serpapi,
    normalize_places_response,
)
from app.services.restaurant_service import (
    build_restaurant_search_params,
    fetch_restaurants_from_serpapi,
    normalize_restaurants_response,
)
from app.services.route_service import optimize_route as service_optimize_route
from app.services.itinerary_service import generate_itinerary as service_generate_itinerary


def _get_api_key(api_key: Optional[str] = None) -> str:
    key = api_key or os.getenv("SERPAPI_API_KEY")
    if not key:
        raise ValueError("SERPAPI_API_KEY is not configured in environment or provided to tool.")
    return key


# --- Tool Implementations ---


def update_trip_state_tool(
    trip_state: Optional[TripState] = None,
    destination: Optional[str] = None,
    trip_start_date: Optional[str] = None,
    trip_end_date: Optional[str] = None,
    number_of_days: Optional[int] = None,
    number_of_nights: Optional[int] = None,
    hotel_budget: Optional[float] = None,
    hotel_total_budget: Optional[float] = None,
    trip_budget: Optional[float] = None,
    travel_mode: Optional[str] = None,
    interests: Optional[List[str]] = None,
    dietary_preferences: Optional[List[str]] = None,
    meal_preferences: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Deterministically updates trip state with extracted user preferences and constraints.
    Returns the change set (changed_fields, invalidated_fields), new state version,
    readiness evaluations, and the next missing requirement.
    """
    state_to_update = trip_state if trip_state is not None else TripState()
    raw_updates = {
        "destination": destination,
        "trip_start_date": trip_start_date,
        "trip_end_date": trip_end_date,
        "number_of_days": number_of_days,
        "number_of_nights": number_of_nights,
        "hotel_budget": hotel_budget,
        "hotel_total_budget": hotel_total_budget,
        "trip_budget": trip_budget,
        "travel_mode": travel_mode,
        "interests": interests,
        "dietary_preferences": dietary_preferences,
        "meal_preferences": meal_preferences,
    }
    clean_updates = {k: v for k, v in raw_updates.items() if v is not None}

    change_set = apply_trip_state_update(state_to_update, clean_updates)
    readiness = check_trip_readiness(state_to_update)
    next_missing = get_next_missing_requirement(state_to_update)

    return {
        "success": True,
        "change_set": change_set.model_dump(),
        "state_version": state_to_update.state_version,
        "readiness": readiness,
        "next_missing": next_missing,
        "state_summary": state_to_update.summary(),
    }


def search_hotels_tool(
    destination: str,
    check_in: Optional[str] = None,
    check_out: Optional[str] = None,
    adults: int = 1,
    children: int = 0,
    max_price: Optional[float] = None,
    limit: int = 5,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Search for hotels in a destination matching budget and guest counts."""
    if not destination or not destination.strip():
        return {"success": False, "error": "destination is required"}

    try:
        key = _get_api_key(api_key)

        # Parse or default dates
        if check_in:
            check_in_dt = date.fromisoformat(check_in)
        else:
            check_in_dt = date.today() + timedelta(days=1)

        if check_out:
            check_out_dt = date.fromisoformat(check_out)
        else:
            check_out_dt = check_in_dt + timedelta(days=2)

        if check_out_dt <= check_in_dt:
            return {"success": False, "error": "check_out must be after check_in"}

        params = build_hotel_search_params(
            destination=destination.strip(),
            check_in=check_in_dt,
            check_out=check_out_dt,
            adults=max(1, int(adults)),
            children=max(0, int(children)),
            api_key=key,
            currency="INR",
        )
        t0 = time.perf_counter()
        raw = fetch_hotels_from_serpapi(params)
        serpapi_time_ms = int((time.perf_counter() - t0) * 1000)

        hotels = normalize_hotels_response(raw, default_currency="INR")
        filtered = filter_hotels_by_budget(hotels, max_price=max_price)
        limited = filtered[:limit]

        return {
            "success": True,
            "count": len(limited),
            "total_found": len(filtered),
            "hotels": [h.model_dump() for h in limited],
            "serpapi_time_ms": serpapi_time_ms,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def get_hotel_details_tool(
    property_token: str,
    check_in: Optional[str] = None,
    check_out: Optional[str] = None,
    adults: int = 2,
    children: int = 0,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve detailed information, amenities, and photos for a specific hotel property."""
    if not property_token or not property_token.strip():
        return {"success": False, "error": "property_token is required"}

    try:
        key = _get_api_key(api_key)

        check_in_dt = date.fromisoformat(check_in) if check_in else None
        check_out_dt = date.fromisoformat(check_out) if check_out else None

        params = build_hotel_detail_params(
            property_token=property_token.strip(),
            api_key=key,
            check_in=check_in_dt,
            check_out=check_out_dt,
            adults=adults,
            children=children,
            currency="INR",
        )
        t0 = time.perf_counter()
        raw = fetch_hotels_from_serpapi(params)
        serpapi_time_ms = int((time.perf_counter() - t0) * 1000)

        detail = normalize_hotel_detail(raw, property_token=property_token.strip(), default_currency="INR")

        if not detail:
            return {"success": False, "error": f"Hotel details not found for token '{property_token}'"}

        return {
            "success": True,
            "hotel": detail.model_dump(),
            "serpapi_time_ms": serpapi_time_ms,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def search_places_tool(
    destination: Optional[str] = None,
    query: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 5,
    api_key: Optional[str] = None,
    location_anchor: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    exclude_names: Optional[List[str]] = None,
    trip_state: Optional[TripState] = None,
) -> Dict[str, Any]:
    """Search for tourist attractions, landmarks, and points of interest."""
    if not destination or not destination.strip():
        if trip_state and trip_state.destination:
            destination = trip_state.destination
        else:
            return {"success": False, "error": "destination is required"}

    try:
        key = _get_api_key(api_key)
        effective_query = query.strip() if query and query.strip() else None
        effective_category = category.strip() if category and category.strip() else None
        effective_anchor = location_anchor.strip() if location_anchor and location_anchor.strip() else None
        effective_lat = latitude
        effective_lng = longitude

        # Inherit anchor from trip_state hotel_selection if user mentions "near hotel" or if hotel is selected
        if trip_state and trip_state.hotel_selection:
            hotel = trip_state.hotel_selection
            is_hotel_query = False
            if effective_query:
                # Check for "near hotel", "around hotel", etc.
                pattern = r'\b(?:near|around)\s+(?:the\s+|my\s+)?hotel\b'
                if re.search(pattern, effective_query, flags=re.IGNORECASE):
                    is_hotel_query = True
                    effective_query = re.sub(pattern, f"near {hotel.name}", effective_query, flags=re.IGNORECASE)

            if not effective_anchor and (is_hotel_query or effective_query is None or "near" in (effective_query or "").lower()):
                effective_anchor = hotel.name
                if effective_lat is None:
                    effective_lat = hotel.latitude
                if effective_lng is None:
                    effective_lng = hotel.longitude

        # Consolidate exclusions from argument and state
        combined_excludes = set()
        if exclude_names:
            combined_excludes.update(e.strip().lower() for e in exclude_names if e and e.strip())
        if trip_state and trip_state.rejected_places:
            combined_excludes.update(r.strip().lower() for r in trip_state.rejected_places if r and r.strip())

        if not effective_query and not effective_category and not effective_anchor:
            effective_query = "tourist attractions"

        params = build_place_search_params(
            destination=destination.strip(),
            category=effective_category,
            query=effective_query,
            api_key=key,
            location_anchor=effective_anchor,
            latitude=effective_lat,
            longitude=effective_lng,
        )
        t0 = time.perf_counter()
        raw = fetch_places_from_serpapi(params)
        serpapi_time_ms = int((time.perf_counter() - t0) * 1000)

        places = normalize_places_response(raw, limit=max(1, limit + len(combined_excludes)))

        # Filter out rejected places
        if combined_excludes:
            places = [
                p for p in places
                if p.name.strip().lower() not in combined_excludes
                and (not p.data_id or p.data_id.lower() not in combined_excludes)
            ]

        limited = places[:limit]
        return {
            "success": True,
            "count": len(limited),
            "places": [p.model_dump() for p in limited],
            "serpapi_time_ms": serpapi_time_ms,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def search_restaurants_tool(
    destination: str,
    query: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 5,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Search for restaurants, local dining, and cafés."""
    if not destination or not destination.strip():
        return {"success": False, "error": "destination is required"}

    try:
        key = _get_api_key(api_key)
        effective_query = query.strip() if query and query.strip() else None
        effective_category = category.strip() if category and category.strip() else None
        if not effective_query and not effective_category:
            effective_query = "restaurants"

        params = build_restaurant_search_params(
            destination=destination.strip(),
            category=effective_category,
            query=effective_query,
            api_key=key,
        )
        t0 = time.perf_counter()
        raw = fetch_restaurants_from_serpapi(params)
        serpapi_time_ms = int((time.perf_counter() - t0) * 1000)

        restaurants = normalize_restaurants_response(raw, limit=max(1, limit))
        return {
            "success": True,
            "count": len(restaurants),
            "restaurants": [r.model_dump() for r in restaurants],
            "serpapi_time_ms": serpapi_time_ms,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def optimize_route_tool(
    start_location: Optional[Dict[str, Any]] = None,
    stops: Optional[List[Dict[str, Any]]] = None,
    end_location: Optional[Dict[str, Any]] = None,
    origin: Optional[Dict[str, Any]] = None,
    destination: Optional[Dict[str, Any]] = None,
    mode: str = "driving",
    respect_user_order: bool = False,
    api_key: Optional[str] = None,
    trip_state: Optional[TripState] = None,
) -> Dict[str, Any]:
    """Compute the mathematically shortest and most optimal sequence to visit a list of stops."""
    try:
        key = _get_api_key(api_key)

        effective_start = start_location or origin
        effective_end = end_location or destination

        # Auto-hydrate from trip_state if start or stops missing
        if trip_state:
            if not effective_start and trip_state.hotel_selection:
                hotel = trip_state.hotel_selection
                effective_start = {
                    "name": hotel.name,
                    "latitude": hotel.latitude,
                    "longitude": hotel.longitude,
                }
                if not effective_end:
                    effective_end = dict(effective_start)

            if not stops or len(stops) == 0:
                raw_stops = []
                for idx, p in enumerate(trip_state.selected_places):
                    raw_stops.append({
                        "id": p.data_id or f"place_{idx+1}",
                        "name": p.name,
                        "latitude": p.latitude,
                        "longitude": p.longitude,
                        "type": "attraction",
                    })
                for idx, r in enumerate(trip_state.selected_restaurants):
                    raw_stops.append({
                        "id": r.data_id or f"rest_{idx+1}",
                        "name": r.name,
                        "latitude": r.latitude,
                        "longitude": r.longitude,
                        "type": "restaurant",
                    })
                stops = raw_stops

            if mode == "driving" and trip_state.travel_mode:
                mode = trip_state.travel_mode

        if not effective_start:
            return {"success": False, "error": "start_location (or hotel_selection in TripState) is required"}

        if not stops or len(stops) == 0:
            return {"success": False, "error": "At least 1 stop (or selected_places in TripState) is required"}

        # Format stops into RouteStop instances with fallback id and type
        formatted_stops = []
        for idx, s in enumerate(stops):
            s_dict = dict(s)
            if "id" not in s_dict or not s_dict["id"]:
                s_dict["id"] = s_dict.get("data_id") or f"stop_{idx+1}"
            if "type" not in s_dict and "stop_type" in s_dict:
                s_dict["type"] = s_dict["stop_type"]
            formatted_stops.append(RouteStop(**s_dict))

        route_req = RouteRequest(
            start_location=LocationPoint(**effective_start),
            end_location=LocationPoint(**effective_end) if effective_end else None,
            stops=formatted_stops,
            mode=mode,
            respect_user_order=respect_user_order,
        )
        opt_route = service_optimize_route(route_req, api_key=key)
        return {"success": True, "route": opt_route.model_dump()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def generate_itinerary_tool(
    route: Dict[str, Any],
    trip_date: str,
    start_time: str = "09:00",
    day_start_time: str = "09:00",
    day_end_time: str = "22:00",
    split_days: bool = True,
) -> Dict[str, Any]:
    """Convert an optimized route into a day-by-day timetable with arrival, visit, and departure times."""
    try:
        itin_req = ItineraryRequest(
            route=OptimizedRoute(**route),
            trip_date=date.fromisoformat(trip_date),
            start_time=start_time,
            day_start_time=day_start_time,
            day_end_time=day_end_time,
            split_days=split_days,
        )
        itin_res = service_generate_itinerary(itin_req)
        return {"success": True, "itinerary": itin_res.model_dump()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# --- Tool Dispatcher ---

TOOL_REGISTRY = {
    "update_trip_state": update_trip_state_tool,
    "search_hotels": search_hotels_tool,
    "get_hotel_details": get_hotel_details_tool,
    "search_places": search_places_tool,
    "search_restaurants": search_restaurants_tool,
    "optimize_route": optimize_route_tool,
    "generate_itinerary": generate_itinerary_tool,
}


def execute_tool(
    name: str,
    arguments: Dict[str, Any],
    api_key: Optional[str] = None,
    trip_state: Optional[TripState] = None,
) -> Dict[str, Any]:
    """
    Executes a requested tool by name with arguments.
    Injects api_key or trip_state if the tool supports it.
    Returns structured dict with success and data or error.
    """
    func = TOOL_REGISTRY.get(name)
    if not func:
        return {"success": False, "error": f"Tool '{name}' is not recognized"}

    args = dict(arguments)
    # Inject api_key if tool accepts it and not explicitly provided in arguments
    if "api_key" not in args and name in {"search_hotels", "get_hotel_details", "search_places", "search_restaurants", "optimize_route"}:
        args["api_key"] = api_key

    # Inject trip_state if tool accepts it
    if "trip_state" not in args and name in {"update_trip_state", "search_places", "optimize_route"}:
        args["trip_state"] = trip_state

    # Inject destination from trip_state if not explicitly provided
    if name == "search_places" and "destination" not in args and trip_state and trip_state.destination:
        args["destination"] = trip_state.destination

    try:
        return func(**args)
    except TypeError as te:
        return {"success": False, "error": f"Invalid arguments for tool '{name}': {str(te)}"}
    except Exception as exc:
        return {"success": False, "error": f"Tool '{name}' execution failed: {str(exc)}"}


# --- OpenRouter / OpenAI-Compatible Tool Specifications ---

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "update_trip_state",
            "description": "Record or update trip requirements and traveler preferences (destination, duration in days, nightly hotel budget, travel mode, interests, dates). Call this immediately whenever the user specifies or changes their destination, budget, or trip parameters. Do NOT run external search tools on state changes unless the trip is ready and specific recommendations are needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {
                        "type": "string",
                        "description": "The destination city or region (e.g. 'Jaipur', 'Kashmir', 'Goa')",
                    },
                    "number_of_days": {
                        "type": "integer",
                        "description": "Total duration of the trip in days (e.g. 3, 5, 7)",
                    },
                    "number_of_nights": {
                        "type": "integer",
                        "description": "Total nights for accommodation (e.g. 4)",
                    },
                    "hotel_budget": {
                        "type": "number",
                        "description": "Nightly hotel accommodation budget ceiling in INR (e.g. 1000).",
                    },
                    "hotel_total_budget": {
                        "type": "number",
                        "description": "Total accommodation budget across all nights in INR (e.g. 3500).",
                    },
                    "trip_budget": {
                        "type": "number",
                        "description": "Overall total trip budget in INR (not limited to accommodation).",
                    },
                    "travel_mode": {
                        "type": "string",
                        "description": "Preferred transit mode: 'driving', 'walking', 'transit', 'two_wheeler'",
                    },
                    "interests": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of traveler interests or themes (e.g. ['nature', 'adventure', 'heritage'])",
                    },
                    "trip_start_date": {
                        "type": "string",
                        "description": "Trip start date in YYYY-MM-DD format",
                    },
                    "trip_end_date": {
                        "type": "string",
                        "description": "Trip end date in YYYY-MM-DD format",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_hotels",
            "description": "Search for real hotel accommodations in a destination with prices in INR, ratings, and locations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {
                        "type": "string",
                        "description": "The destination city to search hotels in (e.g. 'Jaipur', 'Manali')",
                    },
                    "check_in": {
                        "type": "string",
                        "description": "Check-in date in YYYY-MM-DD format (defaults to tomorrow if omitted)",
                    },
                    "check_out": {
                        "type": "string",
                        "description": "Check-out date in YYYY-MM-DD format (defaults to 2 days after check-in)",
                    },
                    "adults": {
                        "type": "integer",
                        "description": "Number of adults (default 1)",
                        "default": 1,
                    },
                    "children": {
                        "type": "integer",
                        "description": "Number of children (default 0)",
                        "default": 0,
                    },
                    "max_price": {
                        "type": "number",
                        "description": "Maximum price per night in INR (e.g. 3500)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of hotels to return (default 5)",
                        "default": 5,
                    },
                },
                "required": ["destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hotel_details",
            "description": "Get in-depth details, amenities, description, and photos for a specific hotel using its property_token.",
            "parameters": {
                "type": "object",
                "properties": {
                    "property_token": {
                        "type": "string",
                        "description": "Unique property token of the hotel obtained from search_hotels",
                    },
                    "check_in": {
                        "type": "string",
                        "description": "Check-in date in YYYY-MM-DD format",
                    },
                    "check_out": {
                        "type": "string",
                        "description": "Check-out date in YYYY-MM-DD format",
                    },
                    "adults": {
                        "type": "integer",
                        "description": "Number of adults (default 2)",
                        "default": 2,
                    },
                    "children": {
                        "type": "integer",
                        "description": "Number of children (default 0)",
                        "default": 0,
                    },
                },
                "required": ["property_token"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_places",
            "description": "Discover attractions, heritage forts, museums, monuments, and places of interest with coordinates and ratings. Supports anchoring searches near a hotel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {
                        "type": "string",
                        "description": "The destination city (e.g. 'Jaipur')",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search keyword (e.g. 'forts', 'palaces', 'sunset viewpoints')",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category filter (e.g. 'historical', 'cultural', 'nature', 'museum')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max places to return (default 5)",
                        "default": 5,
                    },
                    "location_anchor": {
                        "type": "string",
                        "description": "Optional place or hotel name to anchor the search around (e.g. 'Fairmont Jaipur')",
                    },
                    "latitude": {
                        "type": "number",
                        "description": "Optional center latitude for proximity search",
                    },
                    "longitude": {
                        "type": "number",
                        "description": "Optional center longitude for proximity search",
                    },
                    "exclude_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of place names to exclude/skip",
                    },
                },
                "required": ["destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_restaurants",
            "description": "Find top restaurants, traditional food joints, and cafés with coordinates, ratings, and cuisines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {
                        "type": "string",
                        "description": "The destination city (e.g. 'Jaipur')",
                    },
                    "query": {
                        "type": "string",
                        "description": "Food preference or keyword (e.g. 'Rajasthani thali', 'vegetarian', 'rooftop cafe')",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category filter (e.g. 'cafe', 'fine dining', 'street food')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max restaurants to return (default 5)",
                        "default": 5,
                    },
                },
                "required": ["destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "optimize_route",
            "description": "Calculate the most efficient, shortest travel route between a starting hotel and selected stops.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_location": {
                        "type": "object",
                        "description": "Starting location point (typically the hotel) with name, latitude, longitude",
                        "properties": {
                            "name": {"type": "string"},
                            "latitude": {"type": "number"},
                            "longitude": {"type": "number"},
                        },
                        "required": ["latitude", "longitude"],
                    },
                    "stops": {
                        "type": "array",
                        "description": "List of 1 to 6 stops to visit with id, name, latitude, longitude, and optional type, duration_minutes, earliest_visit, latest_visit, meal_type",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "latitude": {"type": "number"},
                                "longitude": {"type": "number"},
                                "type": {"type": "string", "description": "attraction, restaurant, cafe"},
                                "duration_minutes": {"type": "integer"},
                                "meal_type": {"type": "string", "description": "breakfast, lunch, dinner"},
                                "earliest_visit": {"type": "string", "description": "HH:MM format"},
                                "latest_visit": {"type": "string", "description": "HH:MM format"},
                            },
                            "required": ["id", "name", "latitude", "longitude"],
                        },
                    },
                    "end_location": {
                        "type": "object",
                        "description": "Optional ending location point (e.g. return to hotel)",
                        "properties": {
                            "name": {"type": "string"},
                            "latitude": {"type": "number"},
                            "longitude": {"type": "number"},
                        },
                    },
                    "mode": {
                        "type": "string",
                        "description": "Travel mode: 'driving', 'walking', 'bicycling', 'transit', 'two_wheeler'",
                        "default": "driving",
                    },
                    "respect_user_order": {
                        "type": "boolean",
                        "description": "If true, keep the exact order of stops supplied without reordering",
                        "default": False,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_itinerary",
            "description": "Generate a complete day-by-day timetable with exact arrival, visit, and departure times from an optimized route.",
            "parameters": {
                "type": "object",
                "properties": {
                    "route": {
                        "type": "object",
                        "description": "The OptimizedRoute object produced by optimize_route",
                    },
                    "trip_date": {
                        "type": "string",
                        "description": "Starting date in YYYY-MM-DD format",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "Day 1 departure time from hotel (HH:MM, default '09:00')",
                        "default": "09:00",
                    },
                    "day_start_time": {
                        "type": "string",
                        "description": "Standard daily start time (HH:MM, default '09:00')",
                        "default": "09:00",
                    },
                    "day_end_time": {
                        "type": "string",
                        "description": "Standard daily cutoff time (HH:MM, default '22:00')",
                        "default": "22:00",
                    },
                    "split_days": {
                        "type": "boolean",
                        "description": "Whether to rollover stops exceeding day_end_time to subsequent days",
                        "default": True,
                    },
                },
                "required": ["route", "trip_date"],
            },
        },
    },
]
