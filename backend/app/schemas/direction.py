from typing import Optional
from pydantic import BaseModel


class DirectionResponse(BaseModel):
    origin: str
    destination: str
    mode: str
    distance_text: Optional[str] = None
    distance_meters: Optional[float] = None
    duration_text: Optional[str] = None
    duration_seconds: Optional[float] = None
