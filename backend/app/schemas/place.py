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
    priority: Optional[str] = "PREFERRED"
    is_required: bool = False
    visit_duration_minutes: Optional[int] = 60


class PlaceResponse(BaseModel):
    places: List[Place]
