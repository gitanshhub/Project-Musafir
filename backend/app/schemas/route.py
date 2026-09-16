from typing import Optional, List
from pydantic import BaseModel, Field


class LocationPoint(BaseModel):
    name: Optional[str] = None
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Latitude between -90 and 90")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Longitude between -180 and 180")


class RouteStop(BaseModel):
    id: str = Field(..., min_length=1, description="Unique stop identifier")
    name: str = Field(..., min_length=1, description="Name of the stop/attraction/restaurant")
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Latitude between -90 and 90")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Longitude between -180 and 180")
    type: Optional[str] = Field(None, description="Type: attraction, restaurant, cafe, etc.")
    duration_minutes: Optional[int] = Field(None, ge=0, description="Estimated time spent at the stop in minutes")
    meal_type: Optional[str] = Field(None, description="Meal type: breakfast, lunch, dinner, cafe, etc.")
    earliest_visit: Optional[str] = Field(None, description="Earliest visit time (e.g., '10:00' or 'morning')")
    latest_visit: Optional[str] = Field(None, description="Latest visit time (e.g., '17:00' or 'night')")


class RouteRequest(BaseModel):
    start_location: LocationPoint
    end_location: Optional[LocationPoint] = None
    stops: List[RouteStop] = Field(..., min_length=1, max_length=6, description="1 to 6 stops to visit")
    mode: str = Field("driving", description="Travel mode: driving, walking, bicycling, transit, two_wheeler")
    respect_user_order: bool = Field(False, description="If True, preserve the user-provided order of stops without reordering")


class RouteSegment(BaseModel):
    from_stop: str
    to_stop: str
    distance_meters: Optional[float] = None
    duration_seconds: Optional[float] = None
    distance_text: Optional[str] = None
    duration_text: Optional[str] = None


class OptimizedRoute(BaseModel):
    ordered_stops: List[RouteStop]
    segments: List[RouteSegment]
    total_distance_meters: float
    total_duration_seconds: float
    score: float
    reason: Optional[str] = None
