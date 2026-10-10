from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, ConfigDict


class RideStatus(str, Enum):
    REQUESTED = "requested"
    MATCHED = "matched"
    ACCEPTED = "accepted"
    DRIVER_ARRIVED = "driver_arrived"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RideRequest(BaseModel):
    pickup_address: str = Field(min_length=2, max_length=500)
    dropoff_address: str = Field(min_length=2, max_length=500)

    pickup_lat: float = Field(ge=-90, le=90)
    pickup_lng: float = Field(ge=-180, le=180)
    dropoff_lat: float = Field(ge=-90, le=90)
    dropoff_lng: float = Field(ge=-180, le=180)

    # Optional promo code. Existing clients can omit it.
    promo_code: str | None = Field(
        default=None,
        min_length=3,
        max_length=50,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class RideResponse(BaseModel):
    id: int
    rider_id: int
    driver_id: int | None

    pickup_address: str
    dropoff_address: str

    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float

    estimated_distance_km: float
    estimated_fare: float
    final_fare: float | None

    promo_code_id: int | None = None
    original_fare: float | None = None
    discount_amount: float = 0.0

    status: str
    cancellation_reason: str | None

    requested_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RideEstimateResponse(BaseModel):
    estimated_distance_km: float
    estimated_fare: float