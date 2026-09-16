from typing import Optional, List
from pydantic import BaseModel, Field


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
