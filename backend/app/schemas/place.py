from typing import Optional, List
from pydantic import BaseModel


class Place(BaseModel):
    name: str
    rating: Optional[float] = None
    review_count: Optional[int] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    category: Optional[str] = None
    thumbnail: Optional[str] = None
    data_id: str


class PlaceResponse(BaseModel):
    places: List[Place]
