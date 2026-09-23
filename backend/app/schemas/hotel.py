from typing import Optional, List
from pydantic import BaseModel, Field


class Hotel(BaseModel):
    name: str
    rating: Optional[float] = None
    review_count: Optional[int] = None
    price_per_night: Optional[float] = None
    currency: str = "INR"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    thumbnail: Optional[str] = None
    amenities: List[str] = Field(default_factory=list)
    property_token: Optional[str] = None


class HotelResponse(BaseModel):
    hotels: List[Hotel]


class HotelImage(BaseModel):
    thumbnail: Optional[str] = None
    original: Optional[str] = None


class HotelDetail(BaseModel):
    name: str
    rating: Optional[float] = None
    review_count: Optional[int] = None
    hotel_class: Optional[str] = None
    description: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    amenities: List[str] = Field(default_factory=list)
    images: List[HotelImage] = Field(default_factory=list)
    check_in_time: Optional[str] = None
    check_out_time: Optional[str] = None
    price_per_night: Optional[float] = None
    total_price: Optional[float] = None
    currency: str = "INR"
    property_token: str
