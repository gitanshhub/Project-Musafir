from typing import Any, Dict, List, Optional
import requests
from app.schemas.restaurant import Restaurant


def build_restaurant_search_params(
    destination: str,
    category: Optional[str] = None,
    query: Optional[str] = None,
    api_key: str = "",
    location_anchor: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    meal_type: Optional[str] = None,
    dietary: Optional[str] = None,
    cuisine: Optional[str] = None,
    price_level: Optional[str] = None,
) -> Dict[str, Any]:
    """Translate destination, dining intent, qualifiers, and location anchor into SerpApi Google Maps parameters."""
    qualifiers: List[str] = []
    if dietary and dietary.strip():
        d_clean = dietary.strip()
        if not (query and d_clean.lower() in query.lower()):
            qualifiers.append(d_clean)
    if cuisine and cuisine.strip():
        c_clean = cuisine.strip()
        if not (query and c_clean.lower() in query.lower()):
            qualifiers.append(c_clean)
    if price_level and price_level.strip():
        p_clean = price_level.strip()
        if p_clean.lower() in {"cheap", "budget", "inexpensive"} and not (
            query and any(w in query.lower() for w in ("cheap", "budget", "inexpensive"))
        ):
            qualifiers.append("cheap")
    if meal_type and meal_type.strip():
        m_clean = meal_type.strip()
        if not (query and m_clean.lower() in query.lower()):
            qualifiers.append(m_clean)

    qualifier_str = " ".join(qualifiers).strip()

    if location_anchor and location_anchor.strip():
        anchor_clean = location_anchor.strip()
        dest_clean = destination.strip()
        
        # Determine base dining term
        if query and query.strip():
            base_term = query.strip()
        elif category and "cafe" in category.lower():
            base_term = "cafes"
        elif meal_type and meal_type.lower() in {"breakfast", "brunch", "coffee"}:
            base_term = "cafes" if meal_type.lower() == "coffee" else "restaurants"
        else:
            base_term = "restaurants"

        if qualifier_str:
            # Prepend qualifiers if not already in base_term
            missing_quals = [q for q in qualifiers if q.lower() not in base_term.lower()]
            if missing_quals:
                base_term = f"{' '.join(missing_quals)} {base_term}"

        if anchor_clean.lower() in base_term.lower():
            search_query = base_term if dest_clean.lower() in base_term.lower() else f"{base_term}, {dest_clean}"
        else:
            search_query = f"{base_term} near {anchor_clean}, {dest_clean}"
    else:
        parts = [destination.strip()]
        if qualifier_str:
            parts.append(qualifier_str)
        if query and query.strip():
            parts.append(query.strip())
        if category and category.strip():
            cat_clean = category.strip()
            # Avoid repeating the category if already present in query or qualifiers
            if not (query and cat_clean.lower() in query.lower()) and not (
                qualifier_str and cat_clean.lower() in qualifier_str.lower()
            ):
                parts.append(cat_clean)

        search_query = " ".join(parts)

    params: Dict[str, Any] = {
        "engine": "google_maps",
        "type": "search",
        "q": search_query,
        "api_key": api_key,
    }

    if latitude is not None and longitude is not None:
        params["ll"] = f"@{latitude},{longitude},15z"

    return params


def fetch_restaurants_from_serpapi(params: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    """Execute HTTP request against SerpApi Google Maps for restaurants."""
    response = requests.get(
        "https://serpapi.com/search",
        params=params,
        timeout=timeout,
    )
    if response.status_code != 200:
        error_detail = "Failed to fetch restaurants from SerpApi Google Maps"
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


def extract_cuisine(raw: Dict[str, Any]) -> List[str]:
    """Safely extract cuisine types from Google Maps local result."""
    cuisine_list: List[str] = []
    
    # Check types array
    types = raw.get("types")
    if isinstance(types, list):
        for t in types:
            if isinstance(t, str) and t.strip():
                cuisine_list.append(t.strip())

    # If types was not a list, check type string
    raw_type = raw.get("type")
    if isinstance(raw_type, str) and raw_type.strip():
        if raw_type.strip() not in cuisine_list:
            cuisine_list.append(raw_type.strip())
    elif isinstance(raw_type, list):
        for t in raw_type:
            if isinstance(t, str) and t.strip() and t.strip() not in cuisine_list:
                cuisine_list.append(t.strip())

    return cuisine_list


def normalize_restaurant(raw: Dict[str, Any]) -> Optional[Restaurant]:
    """Convert a single Google Maps restaurant result into a Project Musafir Restaurant model."""
    if not isinstance(raw, dict):
        return None

    name = raw.get("title") or raw.get("name")
    if not name or not isinstance(name, str):
        return None

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

    # Category
    category_val = raw.get("type") or raw.get("category")
    category: Optional[str] = None
    if category_val:
        if isinstance(category_val, list) and len(category_val) > 0:
            category = str(category_val[0]).strip()
        else:
            category = str(category_val).strip()

    # Cuisine
    cuisine = extract_cuisine(raw)

    # Price level (symbolic string, e.g. "₹₹", "$$", "Inexpensive", without inventing fake numbers)
    price_level = raw.get("price") or raw.get("price_level")
    if price_level and not isinstance(price_level, str):
        price_level = str(price_level).strip()
    elif isinstance(price_level, str):
        price_level = price_level.strip()

    # Thumbnail
    thumbnail = raw.get("thumbnail")
    if thumbnail and not isinstance(thumbnail, str):
        thumbnail = None

    return Restaurant(
        name=name.strip(),
        rating=rating,
        review_count=review_count,
        address=address.strip() if address else None,
        latitude=latitude,
        longitude=longitude,
        category=category,
        cuisine=cuisine,
        price_level=price_level if price_level else None,
        thumbnail=thumbnail.strip() if thumbnail else None,
        data_id=data_id,
    )


def normalize_restaurants_response(
    raw_response: Dict[str, Any],
    limit: int = 10
) -> List[Restaurant]:
    """Extract, deduplicate by data_id, and normalize Google Maps restaurant results."""
    if not isinstance(raw_response, dict):
        return []

    local_results = raw_response.get("local_results", [])
    if not isinstance(local_results, list):
        return []

    restaurants: List[Restaurant] = []
    seen_ids = set()

    for item in local_results:
        try:
            restaurant = normalize_restaurant(item)
            if restaurant and restaurant.data_id not in seen_ids:
                seen_ids.add(restaurant.data_id)
                restaurants.append(restaurant)
                if len(restaurants) >= limit:
                    break
        except Exception:
            continue

    return restaurants
