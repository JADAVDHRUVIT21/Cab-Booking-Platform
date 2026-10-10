import logging
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.ride import Ride, RideStatus
from app.models.user import User
from app.models.promo_code import PromoCode
from app.schemas.ride import (
    RideEstimateResponse,
    RideRequest,
    RideResponse,
)
from app.services.fare import (
    calculate_distance_km,
    calculate_fare,
)
from app.services.matching import find_nearest_driver
from app.services.ride_events import publish_ride_event
from app.services.background_tasks import generate_ride_receipt
from app.utils.auth import get_current_user


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/rides",
    tags=["Rides"],
)


ACTIVE_RIDE_STATUSES = [
    RideStatus.REQUESTED,
    RideStatus.MATCHED,
    RideStatus.ACCEPTED,
    RideStatus.DRIVER_ARRIVED,
    RideStatus.STARTED,
]

DRIVER_ACTIVE_STATUSES = [
    RideStatus.MATCHED,
    RideStatus.ACCEPTED,
    RideStatus.DRIVER_ARRIVED,
    RideStatus.STARTED,
]

CANCELLABLE_STATUSES = {
    RideStatus.REQUESTED,
    RideStatus.MATCHED,
    RideStatus.ACCEPTED,
    RideStatus.DRIVER_ARRIVED,
}


def publish_ride_update(ride: Ride, event_type: str) -> None:
    """Publish an event without undoing a committed database operation."""
    try:
        publish_ride_event(
            event_type=event_type,
            ride_id=ride.id,
            rider_id=ride.rider_id,
            driver_id=ride.driver_id,
            status=ride.status.value,
        )
    except Exception:
        logger.exception(
            "Failed to publish event %s for ride %s",
            event_type,
            ride.id,
        )


def get_ride_or_404(
    db: Session,
    ride_id: int,
    lock: bool = False,
) -> Ride:
    query = db.query(Ride).filter(Ride.id == ride_id)

    if lock:
        query = query.with_for_update()

    ride = query.first()

    if ride is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ride not found",
        )

    return ride


def ensure_assigned_driver(
    ride: Ride,
    current_user: User,
) -> None:
    if current_user.role.value != "driver":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only drivers can perform this action",
        )

    if ride.driver_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This ride is not assigned to you",
        )


def validate_booking_promo(
    db: Session,
    code: str,
) -> PromoCode:
    """Validate and lock a promo code within the booking transaction."""
    normalized_code = code.strip().upper()

    promo = (
        db.query(PromoCode)
        .filter(PromoCode.code == normalized_code)
        .with_for_update()
        .first()
    )

    if promo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promo code not found",
        )

    if not promo.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Promo code is inactive",
        )

    now = datetime.now(timezone.utc)

    if promo.expires_at is not None:
        expiry = promo.expires_at

        # PostgreSQL may return a naive datetime for some configurations.
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        if expiry <= now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Promo code has expired",
            )

    if (
        promo.max_uses is not None
        and promo.used_count >= promo.max_uses
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Promo code usage limit reached",
        )

    return promo


@router.post(
    "/estimate",
    response_model=RideEstimateResponse,
)
def estimate_ride(
    request: RideRequest,
    current_user: User = Depends(get_current_user),
):
    if current_user.role.value != "rider":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only riders can estimate rides",
        )

    distance_km = calculate_distance_km(
        pickup_lat=request.pickup_lat,
        pickup_lng=request.pickup_lng,
        dropoff_lat=request.dropoff_lat,
        dropoff_lng=request.dropoff_lng,
    )

    return {
        "estimated_distance_km": distance_km,
        "estimated_fare": round(calculate_fare(distance_km), 2),
    }


@router.post(
    "/request",
    response_model=RideResponse,
    status_code=status.HTTP_201_CREATED,
)
def request_ride(
    request: RideRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logger.info(
        "Ride request received for rider_id=%s",
        current_user.id,
    )

    if current_user.role.value != "rider":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only riders can request rides",
        )

    distance_km = calculate_distance_km(
        pickup_lat=request.pickup_lat,
        pickup_lng=request.pickup_lng,
        dropoff_lat=request.dropoff_lat,
        dropoff_lng=request.dropoff_lng,
    )

    original_fare = round(calculate_fare(distance_km), 2)

    try:
        promo = None
        discount_amount = 0.0
        payable_fare = original_fare

        # Validate and calculate the discount on the server.
        if request.promo_code:
            promo = validate_booking_promo(
                db=db,
                code=request.promo_code,
            )

            discount_amount = round(
                original_fare * promo.discount_percent / 100,
                2,
            )

            payable_fare = round(
                max(0.0, original_fare - discount_amount),
                2,
            )

        logger.info(
            "Searching for driver at pickup coordinates (%s, %s)",
            request.pickup_lat,
            request.pickup_lng,
        )

        nearest_driver = find_nearest_driver(
            db=db,
            pickup_lat=request.pickup_lat,
            pickup_lng=request.pickup_lng,
        )

        if nearest_driver is not None:
            logger.info(
                "Driver matched: driver_id=%s",
                nearest_driver.id,
            )
        else:
            logger.warning(
                "No available driver found for rider_id=%s",
                current_user.id,
            )

        ride = Ride(
            rider_id=current_user.id,
            driver_id=(
                nearest_driver.id
                if nearest_driver is not None
                else None
            ),
            pickup_address=request.pickup_address,
            dropoff_address=request.dropoff_address,
            pickup_lat=request.pickup_lat,
            pickup_lng=request.pickup_lng,
            dropoff_lat=request.dropoff_lat,
            dropoff_lng=request.dropoff_lng,
            estimated_distance_km=distance_km,
            estimated_fare=payable_fare,
            original_fare=(
                original_fare if promo is not None else None
            ),
            discount_amount=discount_amount,
            promo_code_id=promo.id if promo is not None else None,
            status=(
                RideStatus.MATCHED
                if nearest_driver is not None
                else RideStatus.REQUESTED
            ),
        )

        db.add(ride)

        # Flush creates the ride within the transaction.
        db.flush()

        if nearest_driver is not None:
            nearest_driver.is_available = False

        # Record the promo use in the same transaction as the ride.
        if promo is not None:
            promo.used_count += 1

        db.commit()
        db.refresh(ride)

        logger.info(
            "Ride committed: ride_id=%s, status=%s, driver_id=%s, "
            "original_fare=%s, discount=%s, payable_fare=%s",
            ride.id,
            ride.status.value,
            ride.driver_id,
            original_fare,
            discount_amount,
            payable_fare,
        )

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Database error while creating a ride for rider_id=%s",
            current_user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save the ride. Check the server logs.",
        ) from None

    except Exception:
        db.rollback()
        logger.exception(
            "Unexpected error while creating a ride for rider_id=%s",
            current_user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create the ride. Check the server logs.",
        ) from None

    publish_ride_update(ride, "ride.requested")
    return ride


@router.patch(
    "/{ride_id}/accept",
    response_model=RideResponse,
)
def accept_ride(
    ride_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        ride = get_ride_or_404(db, ride_id, lock=True)
        ensure_assigned_driver(ride, current_user)

        if ride.status != RideStatus.MATCHED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only matched rides can be accepted",
            )

        ride.status = RideStatus.ACCEPTED
        ride.accepted_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(ride)

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        logger.exception("Failed to accept ride %s", ride_id)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not accept the ride",
        ) from None

    publish_ride_update(ride, "ride.accepted")
    return ride


@router.patch(
    "/{ride_id}/arrived",
    response_model=RideResponse,
)
def driver_arrived(
    ride_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        ride = get_ride_or_404(db, ride_id, lock=True)
        ensure_assigned_driver(ride, current_user)

        if ride.status != RideStatus.ACCEPTED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only accepted rides can be marked as arrived",
            )

        ride.status = RideStatus.DRIVER_ARRIVED

        db.commit()
        db.refresh(ride)

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        logger.exception("Failed to mark ride %s as arrived", ride_id)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not update the ride status",
        ) from None

    publish_ride_update(ride, "ride.driver_arrived")
    return ride


@router.patch(
    "/{ride_id}/start",
    response_model=RideResponse,
)
def start_ride(
    ride_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        ride = get_ride_or_404(db, ride_id, lock=True)
        ensure_assigned_driver(ride, current_user)

        if ride.status != RideStatus.DRIVER_ARRIVED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The driver must arrive before starting the ride",
            )

        ride.status = RideStatus.STARTED
        ride.started_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(ride)

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        logger.exception("Failed to start ride %s", ride_id)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not start the ride",
        ) from None

    publish_ride_update(ride, "ride.started")
    return ride


@router.patch(
    "/{ride_id}/complete",
    response_model=RideResponse,
)
def complete_ride(
    ride_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        ride = get_ride_or_404(db, ride_id, lock=True)
        ensure_assigned_driver(ride, current_user)

        if ride.status != RideStatus.STARTED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only started rides can be completed",
            )

        ride.status = RideStatus.COMPLETED
        ride.completed_at = datetime.now(timezone.utc)

        # estimated_fare already contains the discounted payable fare.
        ride.final_fare = ride.estimated_fare

        current_user.is_available = True

        db.commit()
        db.refresh(ride)

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        logger.exception("Failed to complete ride %s", ride_id)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not complete the ride",
        ) from None

    publish_ride_update(ride, "ride.completed")

    try:
        generate_ride_receipt.delay(ride.id)
    except Exception:
        logger.exception(
            "Ride %s was completed, but receipt generation could not be queued",
            ride.id,
        )

    return ride


@router.patch(
    "/{ride_id}/cancel",
    response_model=RideResponse,
)
def cancel_ride(
    ride_id: int,
    reason: str = Query(
        default="Cancelled by user",
        min_length=3,
        max_length=250,
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        ride = get_ride_or_404(db, ride_id, lock=True)

        if current_user.role.value == "rider":
            if ride.rider_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only cancel your own rides",
                )

        elif current_user.role.value == "driver":
            if ride.driver_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only cancel rides assigned to you",
                )

        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid user role",
            )

        if ride.status not in CANCELLABLE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This ride can no longer be cancelled",
            )

        ride.status = RideStatus.CANCELLED
        ride.cancellation_reason = reason

        if ride.driver_id is not None:
            assigned_driver = (
                db.query(User)
                .filter(User.id == ride.driver_id)
                .with_for_update()
                .first()
            )

            if assigned_driver is not None:
                assigned_driver.is_available = True

        db.commit()
        db.refresh(ride)

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        logger.exception("Failed to cancel ride %s", ride_id)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not cancel the ride",
        ) from None

    publish_ride_update(ride, "ride.cancelled")
    return ride


@router.patch(
    "/{ride_id}/reject",
    response_model=RideResponse,
)
def reject_ride(
    ride_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    previous_driver_id = None

    try:
        ride = get_ride_or_404(db, ride_id, lock=True)
        ensure_assigned_driver(ride, current_user)

        if ride.status != RideStatus.MATCHED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only matched rides can be rejected",
            )

        previous_driver_id = ride.driver_id
        current_user.is_available = True

        next_driver = find_nearest_driver(
            db=db,
            pickup_lat=ride.pickup_lat,
            pickup_lng=ride.pickup_lng,
        )

        if next_driver is not None:
            ride.driver_id = next_driver.id
            ride.status = RideStatus.MATCHED
            next_driver.is_available = False
        else:
            ride.driver_id = None
            ride.status = RideStatus.REQUESTED

        db.commit()
        db.refresh(ride)

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        logger.exception("Failed to reassign ride %s", ride_id)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not reassign the ride",
        ) from None

    publish_ride_update(ride, "ride.reassigned")

    try:
        publish_ride_event(
            event_type="ride.rejected",
            ride_id=ride.id,
            rider_id=ride.rider_id,
            driver_id=previous_driver_id,
            status=ride.status.value,
        )
    except Exception:
        logger.exception(
            "Failed to publish rejection event for ride %s",
            ride.id,
        )

    return ride


@router.get(
    "/history",
    response_model=list[RideResponse],
)
def get_ride_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role.value == "rider":
        query = db.query(Ride).filter(
            Ride.rider_id == current_user.id
        )
    elif current_user.role.value == "driver":
        query = db.query(Ride).filter(
            Ride.driver_id == current_user.id
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unsupported user role",
        )

    return (
        query
        .order_by(Ride.requested_at.desc(), Ride.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )


@router.get(
    "/driver/assigned",
    response_model=list[RideResponse],
)
def get_assigned_rides(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role.value != "driver":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only drivers can view assigned rides",
        )

    return (
        db.query(Ride)
        .filter(
            Ride.driver_id == current_user.id,
            Ride.status.in_(DRIVER_ACTIVE_STATUSES),
        )
        .order_by(Ride.requested_at.desc(), Ride.id.desc())
        .all()
    )


@router.get(
    "/driver/current",
    response_model=RideResponse | None,
)
def get_current_driver_ride(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role.value != "driver":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only drivers can view their current ride",
        )

    return (
        db.query(Ride)
        .filter(
            Ride.driver_id == current_user.id,
            Ride.status.in_(DRIVER_ACTIVE_STATUSES),
        )
        .order_by(Ride.requested_at.desc(), Ride.id.desc())
        .first()
    )


@router.get(
    "/rider/current",
    response_model=RideResponse | None,
)
def get_current_rider_ride(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role.value != "rider":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only riders can view their current ride",
        )

    return (
        db.query(Ride)
        .filter(
            Ride.rider_id == current_user.id,
            Ride.status.in_(ACTIVE_RIDE_STATUSES),
        )
        .order_by(Ride.requested_at.desc(), Ride.id.desc())
        .first()
    )


@router.get(
    "/{ride_id}",
    response_model=RideResponse,
)
def get_ride_details(
    ride_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ride = get_ride_or_404(db, ride_id)

    is_rider_owner = (
        current_user.role.value == "rider"
        and ride.rider_id == current_user.id
    )

    is_assigned_driver = (
        current_user.role.value == "driver"
        and ride.driver_id == current_user.id
    )

    if not (is_rider_owner or is_assigned_driver):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to view this ride",
        )

    return ride