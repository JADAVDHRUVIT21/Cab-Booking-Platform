
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PromoCodeCreate(BaseModel):
    code: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
    discount_percent: float = Field(gt=0, le=100)
    max_uses: int | None = Field(default=None, gt=0)
    expires_at: datetime | None = None


class PromoCodeResponse(BaseModel):
    id: int
    code: str
    discount_percent: float
    max_uses: int | None
    used_count: int
    expires_at: datetime | None
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PromoCodeApply(BaseModel):
    code: str = Field(min_length=3, max_length=50)
    fare: float = Field(gt=0)


class PromoCodeApplyResponse(BaseModel):
    code: str
    original_fare: float
    discount_percent: float
    discount_amount: float
    final_fare: float
