from datetime import date
import os
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from fastapi.responses import HTMLResponse
import requests

from app.schemas.hotel import HotelResponse, HotelDetail
from app.schemas.place import PlaceResponse
from app.schemas.restaurant import RestaurantResponse
from app.schemas.direction import DirectionResponse
from app.schemas.route import RouteRequest, OptimizedRoute
from app.schemas.itinerary import ItineraryRequest, ItineraryResponse
from pydantic import BaseModel, Field
from app.llm import (
    OpenRouterClient,
    SYSTEM_PROMPT,
    LLMError,
    LLMConfigError,
    LLMAuthError,
    LLMRateLimitError,
    LLMTimeoutError,
)
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
from app.services.direction_service import (
    SUPPORTED_TRAVEL_MODES,
    build_direction_params,
    fetch_directions_from_serpapi,
    normalize_direction_response,
)
from app.services.route_service import optimize_route
from app.services.itinerary_service import generate_itinerary

from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

app = FastAPI(
    title="Project Musafir API",
    description="Backend API for Project Musafir Travel & Local Discovery",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")


@app.get("/")
def home():
    return {"message": "Travel Agent API is running"}


@app.get("/smoke-ui", response_class=HTMLResponse, tags=["development-harness"], include_in_schema=False)
def smoke_ui():
    """
    Internal development and automated smoke testing harness.
    NOTE: The canonical product user interface is the Next.js web application (port 3000).
    """
    html_path = os.path.join(os.path.dirname(__file__), "app", "static", "smoke_ui.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# --- HOTELS ---


@app.get("/hotels", response_model=HotelResponse)
def search_hotels(
    destination: str = Query(..., min_length=1, description="Hotel destination (e.g., Manali)"),
    check_in: date = Query(..., description="Check-in date (YYYY-MM-DD)"),
    check_out: date = Query(..., description="Check-out date (YYYY-MM-DD)"),
    adults: int = Query(..., ge=1, description="Number of adults (>= 1)"),
    children: int = Query(0, ge=0, description="Number of children (>= 0)"),
    max_price: Optional[float] = Query(None, gt=0, description="Maximum acceptable nightly price"),
):
    if not destination.strip():
        raise HTTPException(
            status_code=422,
            detail="destination cannot be empty or whitespace"
        )

    if check_out <= check_in:
        raise HTTPException(
            status_code=422,
            detail="check_out must be after check_in"
        )

    if not SERPAPI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="SERPAPI_API_KEY is not configured in .env"
        )

    params = build_hotel_search_params(
        destination=destination,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        children=children,
        api_key=SERPAPI_API_KEY,
        currency="INR",
    )

    try:
        raw_data = fetch_hotels_from_serpapi(params)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to communicate with SerpApi: {str(exc)}"
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )

    hotels = normalize_hotels_response(raw_data, default_currency="INR")
    filtered_hotels = filter_hotels_by_budget(hotels, max_price=max_price)

    return HotelResponse(hotels=filtered_hotels)


@app.get("/hotels/{property_token}", response_model=HotelDetail)
def get_hotel_details(
    property_token: str = Path(..., min_length=1, description="Unique SerpApi property token for the hotel"),
    check_in: Optional[date] = Query(None, description="Check-in date (YYYY-MM-DD)"),
    check_out: Optional[date] = Query(None, description="Check-out date (YYYY-MM-DD)"),
    adults: Optional[int] = Query(2, ge=1, description="Number of adults (>= 1)"),
    children: Optional[int] = Query(0, ge=0, description="Number of children (>= 0)"),
):
    if not property_token.strip():
        raise HTTPException(
            status_code=400,
            detail="property_token cannot be empty or whitespace"
        )

    if check_in and check_out and check_out <= check_in:
        raise HTTPException(
            status_code=422,
            detail="check_out must be after check_in"
        )

    if not SERPAPI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="SERPAPI_API_KEY is not configured in .env"
        )

    params = build_hotel_detail_params(
        property_token=property_token,
        api_key=SERPAPI_API_KEY,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        children=children,
        currency="INR",
    )

    try:
        raw_data = fetch_hotels_from_serpapi(params)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to communicate with SerpApi: {str(exc)}"
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )

    detail = normalize_hotel_detail(raw_data, property_token=property_token, default_currency="INR")
    if not detail:
        raise HTTPException(
            status_code=404,
            detail=f"Hotel with property_token '{property_token}' not found or detail data unavailable"
        )

    return detail


# --- PLACES & ATTRACTIONS ---


@app.get("/places", response_model=PlaceResponse)
def search_places(
    destination: str = Query(..., min_length=1, description="Destination or city to search (e.g., Jaipur)"),
    category: Optional[str] = Query(None, description="Category of places (e.g., historical, museums, viewpoints)"),
    query: Optional[str] = Query(None, description="Free-form search query (e.g., forts and palaces, traditional markets)"),
    limit: int = Query(10, ge=1, le=20, description="Maximum number of places to return (1-20)"),
):
    if not destination.strip():
        raise HTTPException(
            status_code=422,
            detail="destination cannot be empty or whitespace"
        )

    has_category = bool(category and category.strip())
    has_query = bool(query and query.strip())
    if not has_category and not has_query:
        raise HTTPException(
            status_code=422,
            detail="At least one search intent ('category' or 'query') must be provided"
        )

    if not SERPAPI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="SERPAPI_API_KEY is not configured in .env"
        )

    params = build_place_search_params(
        destination=destination,
        category=category,
        query=query,
        api_key=SERPAPI_API_KEY,
    )

    try:
        raw_data = fetch_places_from_serpapi(params)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to communicate with SerpApi: {str(exc)}"
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )

    places = normalize_places_response(raw_data, limit=limit)
    return PlaceResponse(places=places)


# --- RESTAURANTS & CAFES ---


@app.get("/restaurants", response_model=RestaurantResponse)
def search_restaurants(
    destination: str = Query(..., min_length=1, description="Destination or city to search (e.g., Jaipur)"),
    category: Optional[str] = Query(None, description="Category of restaurant/food (e.g., cafes, bakeries)"),
    query: Optional[str] = Query(None, description="Free-form food search query (e.g., Rajasthani food, vegetarian restaurants)"),
    limit: int = Query(10, ge=1, le=20, description="Maximum number of restaurants to return (1-20)"),
):
    if not destination.strip():
        raise HTTPException(
            status_code=422,
            detail="destination cannot be empty or whitespace"
        )

    has_category = bool(category and category.strip())
    has_query = bool(query and query.strip())
    if not has_category and not has_query:
        raise HTTPException(
            status_code=422,
            detail="At least one search intent ('category' or 'query') must be provided"
        )

    if not SERPAPI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="SERPAPI_API_KEY is not configured in .env"
        )

    params = build_restaurant_search_params(
        destination=destination,
        category=category,
        query=query,
        api_key=SERPAPI_API_KEY,
    )

    try:
        raw_data = fetch_restaurants_from_serpapi(params)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to communicate with SerpApi: {str(exc)}"
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )

    restaurants = normalize_restaurants_response(raw_data, limit=limit)
    return RestaurantResponse(restaurants=restaurants)


# --- DIRECTIONS ---


@app.get("/directions", response_model=DirectionResponse)
def get_directions(
    origin: str = Query(..., min_length=1, description="Starting location as coordinates (lat,lng) or address"),
    destination: str = Query(..., min_length=1, description="Ending location as coordinates (lat,lng) or address"),
    mode: str = Query("driving", description="Travel mode: driving, walking, bicycling, transit, two_wheeler"),
):
    clean_origin = origin.strip()
    clean_destination = destination.strip()

    if not clean_origin:
        raise HTTPException(
            status_code=422,
            detail="origin cannot be empty or whitespace"
        )

    if not clean_destination:
        raise HTTPException(
            status_code=422,
            detail="destination cannot be empty or whitespace"
        )

    if clean_origin.lower() == clean_destination.lower():
        raise HTTPException(
            status_code=422,
            detail="origin and destination cannot be identical"
        )

    clean_mode = mode.strip().lower()
    if clean_mode not in SUPPORTED_TRAVEL_MODES:
        valid_modes = ", ".join(SUPPORTED_TRAVEL_MODES.keys())
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported travel mode '{mode}'. Supported modes are: {valid_modes}"
        )

    if not SERPAPI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="SERPAPI_API_KEY is not configured in .env"
        )

    params = build_direction_params(
        origin=clean_origin,
        destination=clean_destination,
        mode=clean_mode,
        api_key=SERPAPI_API_KEY,
    )

    try:
        raw_data = fetch_directions_from_serpapi(params)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to communicate with SerpApi: {str(exc)}"
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )

    direction = normalize_direction_response(
        raw_data,
        origin=clean_origin,
        destination=clean_destination,
        mode=clean_mode
    )

    if not direction:
        raise HTTPException(
            status_code=404,
            detail="No route found between the specified origin and destination"
        )

    return direction


# --- ROUTE OPTIMIZATION ---


@app.post("/route/optimize", response_model=OptimizedRoute)
def optimize_travel_route(request: RouteRequest):
    clean_mode = request.mode.strip().lower()
    if clean_mode not in SUPPORTED_TRAVEL_MODES:
        valid_modes = ", ".join(SUPPORTED_TRAVEL_MODES.keys())
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported travel mode '{request.mode}'. Supported modes are: {valid_modes}"
        )

    # Validate unique stop IDs
    stop_ids = [s.id.strip() for s in request.stops]
    if len(stop_ids) != len(set(stop_ids)):
        raise HTTPException(
            status_code=422,
            detail="Duplicate stop IDs found. Each stop must have a unique ID."
        )

    if not SERPAPI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="SERPAPI_API_KEY is not configured in .env"
        )

    try:
        return optimize_route(request, api_key=SERPAPI_API_KEY)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail=f"Failed to communicate with SerpApi: {str(exc)}")


# --- ITINERARY GENERATION ---


@app.post("/itinerary/generate", response_model=ItineraryResponse)
def generate_travel_itinerary(request: ItineraryRequest):
    try:
        return generate_itinerary(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# --- LLM TEST ENDPOINT (Step 10.3) ---


class LLMTestRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Test message to send to OpenRouter")


class LLMTestResponse(BaseModel):
    content: str
    model: str
    tokens: Optional[dict] = None


@app.post("/llm/test", response_model=LLMTestResponse)
def test_llm_communication(request: LLMTestRequest):
    if not request.message.strip():
        raise HTTPException(status_code=422, detail="message cannot be empty or whitespace")

    try:
        client = OpenRouterClient()
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": request.message.strip()},
        ]
        res = client.send_message(messages)
        return LLMTestResponse(
            content=res.content,
            model=res.model,
            tokens={
                "input": res.input_tokens,
                "output": res.output_tokens,
                "total": res.total_tokens,
            },
        )
    except LLMConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except LLMAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# Include Agent API router (Step 10.5)
from app.api.agent import router as agent_router
app.include_router(agent_router)

