from datetime import date as dt_date
from typing import Optional, List
from pydantic import BaseModel, Field

from app.schemas.route import OptimizedRoute


class ItineraryItem(BaseModel):
    stop_id: str = Field(..., description="Unique identifier for the stop")
    name: str = Field(..., description="Name of the stop/attraction/restaurant")
    type: Optional[str] = Field(None, description="Type: attraction, restaurant, cafe, etc.")
    meal_type: Optional[str] = Field(None, description="Meal type: breakfast, lunch, dinner, cafe, etc.")
    arrival_time: str = Field(..., description="Arrival time at the stop in HH:MM format")
    departure_time: str = Field(..., description="Departure time from the stop in HH:MM format")
    duration_minutes: int = Field(..., ge=0, description="Minutes spent visiting the stop")
    latitude: Optional[float] = Field(None, description="Latitude coordinate")
    longitude: Optional[float] = Field(None, description="Longitude coordinate")


class ItinerarySegment(BaseModel):
    from_stop: str = Field(..., description="Starting stop identifier or location name")
    to_stop: str = Field(..., description="Ending stop identifier or location name")
    departure_time: str = Field(..., description="Time departure begins in HH:MM format")
    arrival_time: str = Field(..., description="Time arrival occurs in HH:MM format")
    duration_seconds: Optional[float] = Field(None, description="Travel duration in seconds")
    distance_meters: Optional[float] = Field(None, description="Travel distance in meters")
    duration_text: Optional[str] = Field(None, description="Human-readable duration")
    distance_text: Optional[str] = Field(None, description="Human-readable distance")


class DailyItinerary(BaseModel):
    day_number: int = Field(..., ge=1, description="Day number of the trip (1, 2, ...)")
    date: dt_date = Field(..., description="Calendar date for this day of the trip")
    start_time: str = Field(..., description="Start of day time in HH:MM format")
    end_time: str = Field(..., description="End of day time in HH:MM format")
    items: List[ItineraryItem] = Field(default_factory=list, description="Scheduled activities for this day")
    segments: List[ItinerarySegment] = Field(default_factory=list, description="Travel segments connecting stops")
    total_travel_seconds: float = Field(0.0, description="Total travel time across segments in seconds")
    total_visit_minutes: int = Field(0, description="Total time spent visiting places in minutes")


class ItineraryRequest(BaseModel):
    route: OptimizedRoute = Field(..., description="Optimized route from Step 8 containing ordered stops and segments")
    trip_date: dt_date = Field(..., description="Starting date of the itinerary (YYYY-MM-DD)")
    start_time: str = Field("09:00", description="Departure time from starting location on Day 1 (HH:MM)")
    day_start_time: str = Field("09:00", description="Standard start time for each day (HH:MM)")
    day_end_time: str = Field("22:00", description="Hard end time for each day (HH:MM)")
    split_days: bool = Field(True, description="Whether to automatically rollover stops exceeding day_end_time to subsequent days")


class ItineraryResponse(BaseModel):
    days: List[DailyItinerary] = Field(..., description="List of day-by-day itineraries")
    total_days: int = Field(..., description="Total number of days scheduled")
    total_distance_meters: float = Field(..., description="Total travel distance across all days in meters")
    total_travel_seconds: float = Field(..., description="Total travel time across all days in seconds")
    total_visit_minutes: int = Field(..., description="Total time spent at visits across all days in minutes")
