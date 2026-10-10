
from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


class ReviewCreate(BaseModel):
    ride_id: int = Field(gt=0)
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(
        default=None,
        max_length=1000,
    )


class ReviewResponse(BaseModel):
    id: int
    ride_id: int
    rider_id: int
    driver_id: int
    rating: int
    comment: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DriverRatingResponse(BaseModel):
    driver_id: int
    driver_name: str
    rating_avg: float
    total_reviews: int
