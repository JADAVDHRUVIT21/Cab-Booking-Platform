from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class DriverRegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)

    phone: str = Field(min_length=7, max_length=20)

    vehicle_type: str = Field(
        min_length=2,
        max_length=50,
    )

    vehicle_model: str = Field(
        min_length=2,
        max_length=100,
    )

    vehicle_number: str = Field(
        min_length=2,
        max_length=50,
    )


class UserResponse(BaseModel):
    id: int
    name: str
    email: EmailStr
    phone: str | None
    role: str
    profile_pic: str | None
    rating_avg: float

    model_config = {
        "from_attributes": True
    }


class DriverAvailabilityRequest(BaseModel):
    is_available: bool


class DriverAvailabilityResponse(BaseModel):
    is_available: bool


class DriverLocationUpdateRequest(BaseModel):
    latitude: float
    longitude: float