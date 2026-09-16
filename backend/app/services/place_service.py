from typing import Any, Dict, List, Optional
import requests
from app.schemas.place import Place


def build_place_search_params(
    destination: str,
    category: Optional[str],
    query: Optional[str],
    api_key: str,
) -> Dict[str, Any]:
    """Translate destination and discovery intent into SerpApi Google Maps parameters."""
    parts = [destination.strip()]
    if query and query.strip():
        parts.append(query.strip())
    if category and category.strip():
        # Avoid duplicating if category is already in query
        cat_clean = category.strip()
        if not (query and cat_clean.lower() in query.lower()):
            parts.append(cat_clean)

    search_query = " ".join(parts)
    return {
        "engine": "google_maps",
        "type": "search",
        "q": search_query,
        "api_key": api_key,
    }


def fetch_places_from_serpapi(params: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    """Execute HTTP request against SerpApi Google Maps and return JSON response."""
    response = requests.get(
        "https://serpapi.com/search",
        params=params,
        timeout=timeout,
    )
    if response.status_code != 200:
        error_detail = "Failed to fetch places from SerpApi Google Maps"
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


def normalize_place(raw: Dict[str, Any]) -> Optional[Place]:
    """Convert a single Google Maps local result into a Project Musafir Place model."""
    if not isinstance(raw, dict):
        return None

    # Name is usually in 'title' or 'name'
    name = raw.get("title") or raw.get("name")
    if not name or not isinstance(name, str):
        return None

    # data_id is the primary unique identifier for Google Maps places
    data_id = raw.get("data_id") or raw.get("place_id") or raw.get("data_cid")
    if not data_id:
        return None
    data_id = str(data_id).strip()

    # Rating
    rating_val = raw.get("rating")
    rating: Optional[float] = None
    if rating_val is not None:
        try:
            rating = float(rating_val)
        except (ValueError, TypeError):
            rating = None

    # Reviews
    reviews_val = raw.get("reviews")
    review_count: Optional[int] = None
    if reviews_val is not None:
        try:
            review_count = int(reviews_val)
        except (ValueError, TypeError):
            review_count = None

    # Address
    address = raw.get("address")
    if address and not isinstance(address, str):
        address = str(address)

    # GPS coordinates
    gps = raw.get("gps_coordinates")
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    if isinstance(gps, dict):
        try:
            if gps.get("latitude") is not None:
                latitude = float(gps["latitude"])
            if gps.get("longitude") is not None:
                longitude = float(gps["longitude"])
        except (ValueError, TypeError):
            pass

    # Category / Type
    category_val = raw.get("type") or raw.get("category")
    category: Optional[str] = None
    if category_val:
        if isinstance(category_val, list) and len(category_val) > 0:
            category = str(category_val[0]).strip()
        else:
            category = str(category_val).strip()

    # Thumbnail
    thumbnail = raw.get("thumbnail")
    if thumbnail and not isinstance(thumbnail, str):
        thumbnail = None

    return Place(
        name=name.strip(),
        rating=rating,
        review_count=review_count,
        address=address.strip() if address else None,
        latitude=latitude,
        longitude=longitude,
        category=category,
        thumbnail=thumbnail.strip() if thumbnail else None,
        data_id=data_id,
    )


def normalize_places_response(
    raw_response: Dict[str, Any],
    limit: int = 10
) -> List[Place]:
    """Extract, deduplicate by data_id, and normalize Google Maps places."""
    if not isinstance(raw_response, dict):
        return []

    local_results = raw_response.get("local_results", [])
    if not isinstance(local_results, list):
        return []

    places: List[Place] = []
    seen_ids = set()

    for item in local_results:
        try:
            place = normalize_place(item)
            if place and place.data_id not in seen_ids:
                seen_ids.add(place.data_id)
                places.append(place)
                if len(places) >= limit:
                    break
        except Exception:
            continue

    return places
