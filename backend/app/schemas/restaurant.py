from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class FoodAnchorType(str, Enum):
    HOTEL = "hotel"
    PLACE = "place"
    ROUTE = "route"
    DESTINATION = "destination"


class FoodCategory(str, Enum):
    RESTAURANT = "restaurant"
    CAFE = "cafe"


class FoodSearchRequest(BaseModel):
    """
    Conceptual request model separating where to search (anchor/coordinates)
    from what to search for (category, meal, cuisine, dietary, price).
    """
    destination: str = Field(..., description="Target destination city or region")
    category: str = Field("restaurant", description="'restaurant' or 'cafe'")
    query: Optional[str] = Field(None, description="Culinary search term, e.g. 'South Indian thali', 'espresso'")
    anchor_type: Optional[str] = Field(None, description="'hotel', 'place', 'route', 'destination'")
    anchor_name: Optional[str] = Field(None, description="Name of the anchor location")
    anchor_id: Optional[str] = Field(None, description="Identifier of the anchor location")
    latitude: Optional[float] = Field(None, description="Latitude for Google Maps center @lat,lng")
    longitude: Optional[float] = Field(None, description="Longitude for Google Maps center @lat,lng")
    meal_type: Optional[str] = Field(None, description="'breakfast', 'lunch', 'dinner', 'snack', 'coffee'")
    cuisine: List[str] = Field(default_factory=list, description="Requested cuisines")
    dietary_preferences: List[str] = Field(default_factory=list, description="E.g. ['vegetarian', 'vegan', 'jain']")
    price_level: Optional[str] = Field(None, description="Price tier, e.g. 'cheap', 'mid-range', '$', '$$'")
    exclude_names: List[str] = Field(default_factory=list, description="Names to exclude from results")
    limit: int = Field(5, ge=1, le=20, description="Max results to return")


class Restaurant(BaseModel):
    name: str
    rating: Optional[float] = None
    review_count: Optional[int] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    category: Optional[str] = None
    cuisine: List[str] = Field(default_factory=list)
    price_level: Optional[str] = None
    thumbnail: Optional[str] = None
    data_id: str


class RestaurantResponse(BaseModel):
    restaurants: List[Restaurant]
