
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PaymentIntentCreate(BaseModel):
    ride_id: int = Field(gt=0)


class PaymentResponse(BaseModel):
    id: int
    ride_id: int
    rider_id: int
    amount: int
    currency: str
    status: str
    stripe_payment_intent_id: str | None = None
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentIntentResponse(BaseModel):
    payment: PaymentResponse
    client_secret: str
