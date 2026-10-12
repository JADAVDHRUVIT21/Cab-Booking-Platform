
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.ride import Ride
from app.models.user import User
from app.schemas.payment import PaymentIntentCreate, PaymentIntentResponse
from app.services.payments import create_payment_intent_for_ride
from app.utils.auth import get_current_user


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/payments",
    tags=["Payments"],
)


@router.post(
    "/create-intent",
    response_model=PaymentIntentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_payment_intent(
    request: PaymentIntentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ride = db.query(Ride).filter(
        Ride.id == request.ride_id
    ).first()

    if ride is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ride not found",
        )

    payment, client_secret = create_payment_intent_for_ride(
        db=db,
        ride=ride,
        rider=current_user,
    )

    return {
        "payment": payment,
        "client_secret": client_secret,
    }
