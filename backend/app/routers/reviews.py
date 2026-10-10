
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.ride import Ride, RideStatus
from app.models.review import Review
from app.models.user import User
from app.schemas.review import (
    DriverRatingResponse,
    ReviewCreate,
    ReviewResponse,
)
from app.utils.auth import get_current_user


router = APIRouter(
    prefix="/api/reviews",
    tags=["Ratings & Reviews"],
)


@router.post(
    "",
    response_model=ReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_review(
    request: ReviewCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Only riders can submit reviews.
    if current_user.role.value != "rider":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only riders can submit reviews",
        )

    ride = db.query(Ride).filter(
        Ride.id == request.ride_id
    ).first()

    if not ride:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ride not found",
        )

    # A rider may review only their own completed ride.
    if ride.rider_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can review only your own rides",
        )

    if ride.status != RideStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only completed rides can be reviewed",
        )

    if ride.driver_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This ride has no assigned driver",
        )

    existing_review = db.query(Review).filter(
        Review.ride_id == ride.id
    ).first()

    if existing_review:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This ride has already been reviewed",
        )

    review = Review(
        ride_id=ride.id,
        rider_id=current_user.id,
        driver_id=ride.driver_id,
        rating=request.rating,
        comment=request.comment,
    )

    try:
        db.add(review)
        db.flush()

        # Recalculate the driver's average rating.
        average_rating = db.query(
            func.avg(Review.rating)
        ).filter(
            Review.driver_id == ride.driver_id
        ).scalar()

        driver = db.query(User).filter(
            User.id == ride.driver_id
        ).first()

        if driver is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assigned driver not found",
            )

        driver.rating_avg = round(
            float(average_rating or 0),
            2,
        )

        db.commit()
        db.refresh(review)

    except Exception:
        db.rollback()
        raise

    return review


@router.get(
    "/drivers/{driver_id}",
    response_model=list[ReviewResponse],
)
def get_driver_reviews(
    driver_id: int,
    db: Session = Depends(get_db),
):
    driver = db.query(User).filter(
        User.id == driver_id,
        User.role == "driver",
    ).first()

    if driver is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Driver not found",
        )

    return (
        db.query(Review)
        .filter(Review.driver_id == driver_id)
        .order_by(Review.created_at.desc())
        .all()
    )


@router.get(
    "/drivers/{driver_id}/rating",
    response_model=DriverRatingResponse,
)
def get_driver_rating(
    driver_id: int,
    db: Session = Depends(get_db),
):
    driver = db.query(User).filter(
        User.id == driver_id,
        User.role == "driver",
    ).first()

    if driver is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Driver not found",
        )

    total_reviews = db.query(Review).filter(
        Review.driver_id == driver_id
    ).count()

    return DriverRatingResponse(
        driver_id=driver.id,
        driver_name=driver.name,
        rating_avg=round(float(driver.rating_avg or 0), 2),
        total_reviews=total_reviews,
    )
