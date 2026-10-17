
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.auth import (
    DriverAvailabilityRequest,
    DriverAvailabilityResponse,
    DriverLocationUpdateRequest,
    DriverRegisterRequest,
    LoginRequest,
    RegisterRequest,
    UserResponse,
)
from app.utils.auth import get_current_user
from app.utils.security import hash_password, verify_password


router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)


def require_driver(current_user: User) -> None:
    """Reject requests from accounts that are not drivers."""
    if current_user.role.value != "driver":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only drivers can perform this action",
        )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    request: RegisterRequest,
    db: Session = Depends(get_db),
):
    existing_user = (
        db.query(User)
        .filter(User.email == request.email)
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        )

    user = User(
        name=request.name,
        email=request.email,
        password_hash=hash_password(request.password),
        role="rider",
        rating_avg=0.0,
    )

    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        )
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to register user. Please try again.",
        )

    return user


@router.post(
    "/register-driver",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_driver(
    request: DriverRegisterRequest,
    db: Session = Depends(get_db),
):
    existing_user = (
        db.query(User)
        .filter(User.email == request.email)
        .first()
    )
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        )

    existing_phone = (
        db.query(User)
        .filter(User.phone == request.phone)
        .first()
    )
    if existing_phone:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Phone number is already registered",
        )

    existing_vehicle = (
        db.query(User)
        .filter(User.vehicle_number == request.vehicle_number)
        .first()
    )
    if existing_vehicle:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Vehicle number is already registered",
        )

    user = User(
        name=request.name,
        email=request.email,
        phone=request.phone,
        password_hash=hash_password(request.password),
        role="driver",
        rating_avg=0.0,
        is_available=False,
        vehicle_type=request.vehicle_type,
        vehicle_model=request.vehicle_model,
        vehicle_number=request.vehicle_number,
    )

    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Email, phone number, or vehicle number "
                "is already registered"
            ),
        )
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to register driver. Please try again.",
        )

    return user


@router.post("/login")
def login(
    request: LoginRequest,
    db: Session = Depends(get_db),
):
    user = (
        db.query(User)
        .filter(User.email == request.email)
        .first()
    )

    if not user or not verify_password(
        request.password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )

    token_data = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.value,
        "exp": expire,
    }

    access_token = jwt.encode(
        token_data,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@router.get(
    "/me",
    response_model=UserResponse,
)
def get_me(
    current_user: User = Depends(get_current_user),
):
    return current_user


@router.patch(
    "/driver/availability",
    response_model=DriverAvailabilityResponse,
)
def update_driver_availability(
    request: DriverAvailabilityRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_driver(current_user)

    try:
        current_user.is_available = request.is_available
        db.commit()
        db.refresh(current_user)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update driver availability.",
        )

    return DriverAvailabilityResponse(
        is_available=bool(current_user.is_available),
    )


@router.patch("/driver/location")
def update_driver_location(
    request: DriverLocationUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_driver(current_user)

    # Reject invalid geographic coordinates.
    if not -90 <= request.latitude <= 90:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Latitude must be between -90 and 90.",
        )

    if not -180 <= request.longitude <= 180:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Longitude must be between -180 and 180.",
        )

    try:
        current_user.current_lat = request.latitude
        current_user.current_lng = request.longitude
        db.commit()
        db.refresh(current_user)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update driver location.",
        )

    return {
        "message": "Driver location updated successfully.",
        "latitude": current_user.current_lat,
        "longitude": current_user.current_lng,
    }
