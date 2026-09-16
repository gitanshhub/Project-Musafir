import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
import requests
from app.schemas.hotel import Hotel, HotelDetail, HotelImage


def build_hotel_search_params(
    destination: str,
    check_in: date,
    check_out: date,
    adults: int,
    children: int,
    api_key: str,
    currency: str = "INR",
) -> Dict[str, Any]:
    """Translate application search parameters to SerpApi Google Hotels format."""
    params: Dict[str, Any] = {
        "engine": "google_hotels",
        "q": destination.strip(),
        "check_in_date": check_in.strftime("%Y-%m-%d"),
        "check_out_date": check_out.strftime("%Y-%m-%d"),
        "adults": adults,
        "children": children,
        "currency": currency,
        "api_key": api_key,
    }
    return params


def build_hotel_detail_params(
    property_token: str,
    api_key: str,
    check_in: Optional[date] = None,
    check_out: Optional[date] = None,
    adults: Optional[int] = 2,
    children: Optional[int] = 0,
    currency: str = "INR",
) -> Dict[str, Any]:
    """Translate hotel detail request parameters to SerpApi Google Hotels format."""
    params: Dict[str, Any] = {
        "engine": "google_hotels",
        "property_token": property_token.strip(),
        "currency": currency,
        "api_key": api_key,
    }
    if check_in:
        params["check_in_date"] = check_in.strftime("%Y-%m-%d")
    if check_out:
        params["check_out_date"] = check_out.strftime("%Y-%m-%d")
    if adults is not None:
        params["adults"] = adults
    if children is not None:
        params["children"] = children
    return params


def fetch_hotels_from_serpapi(params: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    """Execute HTTP request against SerpApi and return the JSON response."""
    response = requests.get(
        "https://serpapi.com/search",
        params=params,
        timeout=timeout,
    )
    if response.status_code != 200:
        error_detail = "Failed to fetch data from SerpApi"
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


def extract_price_and_currency(
    raw: Dict[str, Any],
    default_currency: str = "INR"
) -> Tuple[Optional[float], str]:
    """Extract numeric price per night and currency from SerpApi property data."""
    # 1. Direct extracted_price
    if "extracted_price" in raw and raw["extracted_price"] is not None:
        try:
            return float(raw["extracted_price"]), default_currency
        except (ValueError, TypeError):
            pass

    # 2. Inside rate_per_night object
    rate_info = raw.get("rate_per_night")
    if isinstance(rate_info, dict):
        currency = rate_info.get("currency", default_currency)
        lowest = rate_info.get("extracted_lowest") or rate_info.get("extracted_before_taxes_fees")
        if lowest is not None:
            try:
                return float(lowest), currency
            except (ValueError, TypeError):
                pass

        # String extraction from lowest
        lowest_str = rate_info.get("lowest")
        if lowest_str and isinstance(lowest_str, str):
            nums = re.findall(r"\d+(?:[.,]\d+)?", lowest_str.replace(",", ""))
            if nums:
                try:
                    return float(nums[0]), currency
                except (ValueError, TypeError):
                    pass

    # 3. String price fallback at root level
    price_str = raw.get("price")
    if price_str and isinstance(price_str, str):
        currency = default_currency
        if "₹" in price_str or "INR" in price_str:
            currency = "INR"
        elif "$" in price_str or "USD" in price_str:
            currency = "USD"
        elif "€" in price_str:
            currency = "EUR"
        elif "£" in price_str:
            currency = "GBP"

        nums = re.findall(r"\d+(?:[.,]\d+)?", price_str.replace(",", ""))
        if nums:
            try:
                return float(nums[0]), currency
            except (ValueError, TypeError):
                pass

    return None, default_currency


def extract_total_price(raw: Dict[str, Any]) -> Optional[float]:
    """Extract numeric total stay price separately from nightly price."""
    if "extracted_total_price" in raw and raw["extracted_total_price"] is not None:
        try:
            return float(raw["extracted_total_price"])
        except (ValueError, TypeError):
            pass

    total_rate = raw.get("total_rate")
    if isinstance(total_rate, dict):
        lowest = total_rate.get("extracted_lowest") or total_rate.get("extracted_before_taxes_fees")
        if lowest is not None:
            try:
                return float(lowest)
            except (ValueError, TypeError):
                pass
        lowest_str = total_rate.get("lowest")
        if lowest_str and isinstance(lowest_str, str):
            nums = re.findall(r"\d+(?:[.,]\d+)?", lowest_str.replace(",", ""))
            if nums:
                try:
                    return float(nums[0])
                except (ValueError, TypeError):
                    pass

    return None


def extract_thumbnail(raw: Dict[str, Any]) -> Optional[str]:
    """Extract thumbnail url from property data or images array."""
    if raw.get("thumbnail") and isinstance(raw["thumbnail"], str):
        return raw["thumbnail"]

    images = raw.get("images")
    if isinstance(images, list) and len(images) > 0:
        first = images[0]
        if isinstance(first, dict):
            return first.get("thumbnail") or first.get("original_image")

    return None


def extract_hotel_images(raw: Dict[str, Any]) -> List[HotelImage]:
    """Normalize hotel-specific photos into clean thumbnail and original pairs."""
    images: List[HotelImage] = []
    raw_images = raw.get("images") or raw.get("photos") or []
    if isinstance(raw_images, list):
        for img in raw_images:
            if isinstance(img, dict):
                thumb = img.get("thumbnail")
                orig = img.get("original_image") or img.get("original") or img.get("image")
                if thumb or orig:
                    images.append(HotelImage(thumbnail=thumb, original=orig))
            elif isinstance(img, str) and img.strip():
                images.append(HotelImage(thumbnail=img.strip(), original=img.strip()))

    # Fallback to single thumbnail if images array is empty
    if not images and raw.get("thumbnail"):
        thumb = raw["thumbnail"]
        if isinstance(thumb, str) and thumb.strip():
            images.append(HotelImage(thumbnail=thumb.strip(), original=thumb.strip()))

    return images


def extract_amenities(raw: Dict[str, Any]) -> List[str]:
    """Safely extract amenities list from strings or nested objects."""
    raw_amenities = raw.get("amenities") or []
    amenities: List[str] = []
    if isinstance(raw_amenities, list):
        for item in raw_amenities:
            if isinstance(item, str) and item.strip():
                amenities.append(item.strip())
            elif isinstance(item, dict):
                title = item.get("title") or item.get("name")
                if title and isinstance(title, str) and title.strip():
                    amenities.append(title.strip())
    return amenities


def normalize_hotel(raw: Dict[str, Any], default_currency: str = "INR") -> Optional[Hotel]:
    """Convert a single SerpApi property result into a clean Project Musafir Hotel model."""
    if not isinstance(raw, dict):
        return None

    name = raw.get("name")
    if not name or not isinstance(name, str):
        return None

    # Rating
    rating_val = raw.get("overall_rating") or raw.get("rating")
    rating: Optional[float] = None
    if rating_val is not None:
        try:
            rating = float(rating_val)
        except (ValueError, TypeError):
            rating = None

    # Review count
    reviews_val = raw.get("reviews")
    review_count: Optional[int] = None
    if reviews_val is not None:
        try:
            review_count = int(reviews_val)
        except (ValueError, TypeError):
            review_count = None

    # Price and Currency
    price_per_night, currency = extract_price_and_currency(raw, default_currency=default_currency)

    # GPS Coordinates
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

    # Thumbnail
    thumbnail = extract_thumbnail(raw)

    # Amenities
    amenities = extract_amenities(raw)

    # Property token
    property_token = raw.get("property_token")
    if property_token is not None:
        property_token = str(property_token)

    return Hotel(
        name=name.strip(),
        rating=rating,
        review_count=review_count,
        price_per_night=price_per_night,
        currency=currency,
        latitude=latitude,
        longitude=longitude,
        thumbnail=thumbnail,
        amenities=amenities,
        property_token=property_token,
    )


def normalize_hotels_response(
    raw_response: Dict[str, Any],
    default_currency: str = "INR"
) -> List[Hotel]:
    """Extract and normalize all hotel properties from a raw SerpApi response."""
    if not isinstance(raw_response, dict):
        return []

    properties = raw_response.get("properties", [])
    if not isinstance(properties, list):
        return []

    hotels: List[Hotel] = []
    for item in properties:
        try:
            hotel = normalize_hotel(item, default_currency=default_currency)
            if hotel:
                hotels.append(hotel)
        except Exception:
            continue

    return hotels


def normalize_hotel_detail(
    raw_response: Dict[str, Any],
    property_token: str,
    default_currency: str = "INR"
) -> Optional[HotelDetail]:
    """Convert raw SerpApi property details response into a clean Project Musafir HotelDetail model."""
    if not isinstance(raw_response, dict):
        return None

    # Locate the target property dictionary
    prop = raw_response.get("property_details")
    if not prop and "properties" in raw_response and isinstance(raw_response["properties"], list):
        if len(raw_response["properties"]) > 0:
            prop = raw_response["properties"][0]
    if not prop and "name" in raw_response:
        prop = raw_response

    if not prop or not isinstance(prop, dict):
        return None

    name = prop.get("name")
    if not name or not isinstance(name, str):
        return None

    # Rating
    rating_val = prop.get("overall_rating") or prop.get("rating")
    rating: Optional[float] = None
    if rating_val is not None:
        try:
            rating = float(rating_val)
        except (ValueError, TypeError):
            rating = None

    # Review count
    reviews_val = prop.get("reviews")
    review_count: Optional[int] = None
    if reviews_val is not None:
        try:
            review_count = int(reviews_val)
        except (ValueError, TypeError):
            review_count = None

    # Hotel class
    hotel_class_val = prop.get("hotel_class") or prop.get("extracted_hotel_class")
    hotel_class = str(hotel_class_val).strip() if hotel_class_val is not None else None

    # Description
    description = prop.get("description") or prop.get("about") or prop.get("overview")
    if description and not isinstance(description, str):
        description = str(description)

    # Address
    address = prop.get("address") or prop.get("location")
    if address and not isinstance(address, str):
        address = str(address)

    # GPS Coordinates
    gps = prop.get("gps_coordinates")
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

    # Amenities & Images
    amenities = extract_amenities(prop)
    images = extract_hotel_images(prop)

    # Check-in and check-out times
    check_in_time = prop.get("check_in_time")
    check_out_time = prop.get("check_out_time")

    # Pricing: Nightly vs Total Stay
    price_per_night, currency = extract_price_and_currency(prop, default_currency=default_currency)
    total_price = extract_total_price(prop)

    return HotelDetail(
        name=name.strip(),
        rating=rating,
        review_count=review_count,
        hotel_class=hotel_class,
        description=description.strip() if description else None,
        address=address.strip() if address else None,
        latitude=latitude,
        longitude=longitude,
        amenities=amenities,
        images=images,
        check_in_time=str(check_in_time).strip() if check_in_time else None,
        check_out_time=str(check_out_time).strip() if check_out_time else None,
        price_per_night=price_per_night,
        total_price=total_price,
        currency=currency,
        property_token=property_token.strip(),
    )


def filter_hotels_by_budget(
    hotels: List[Hotel],
    max_price: Optional[float]
) -> List[Hotel]:
    """Filter hotels deterministically by nightly budget."""
    if max_price is None:
        return hotels

    return [
        h for h in hotels
        if h.price_per_night is not None and h.price_per_night <= max_price
    ]
