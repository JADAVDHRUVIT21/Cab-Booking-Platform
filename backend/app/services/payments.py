
import logging

import stripe
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.payment import Payment
from app.models.ride import Ride, RideStatus
from app.models.user import User


logger = logging.getLogger(__name__)


def create_payment_intent_for_ride(
    db: Session,
    ride: Ride,
    rider: User,
) -> tuple[Payment, str]:
    """Create or reuse a Stripe PaymentIntent for the authenticated rider."""

    if rider.role.value != "rider" or ride.rider_id != rider.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only pay for your own rides",
        )

    if ride.status != RideStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payment can only be initiated for completed rides",
        )

    fare = ride.final_fare
    if fare is None:
        fare = ride.estimated_fare

    amount = round(float(fare) * 100)

    if amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The ride has no payable balance",
        )

    if not settings.STRIPE_SECRET_KEY:
        logger.error("Stripe secret key is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payment service is not configured",
        )

    stripe.api_key = settings.STRIPE_SECRET_KEY

    try:
        payment = (
            db.query(Payment)
            .filter(Payment.ride_id == ride.id)
            .with_for_update()
            .first()
        )

        if payment is not None:
            if payment.rider_id != rider.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not authorized to pay for this ride",
                )

            if payment.status == "succeeded":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This ride has already been paid",
                )

            # Reuse an existing active PaymentIntent on retries.
            if payment.stripe_payment_intent_id:
                existing_intent = stripe.PaymentIntent.retrieve(
                    payment.stripe_payment_intent_id
                )

                if existing_intent.status in {
                    "requires_payment_method",
                    "requires_confirmation",
                    "requires_action",
                    "processing",
                }:
                    if (
                        payment.amount != amount
                        or payment.currency != settings.STRIPE_CURRENCY.lower()
                    ):
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                "The existing payment amount differs from "
                                "the current ride fare"
                            ),
                        )

                    if not existing_intent.client_secret:
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Stripe did not return a client secret",
                        )

                    return payment, existing_intent.client_secret

                if existing_intent.status == "succeeded":
                    payment.status = "succeeded"
                    db.commit()
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="This ride has already been paid",
                    )

                # A canceled PaymentIntent can be replaced.
                if existing_intent.status != "canceled":
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="The existing payment cannot be retried yet",
                    )

            payment.amount = amount
            payment.currency = settings.STRIPE_CURRENCY.lower()
            payment.status = "pending"
            payment.failure_reason = None

        else:
            payment = Payment(
                ride_id=ride.id,
                rider_id=rider.id,
                amount=amount,
                currency=settings.STRIPE_CURRENCY.lower(),
                status="pending",
            )
            db.add(payment)
            db.flush()

        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency=settings.STRIPE_CURRENCY.lower(),
            metadata={
                "payment_id": str(payment.id),
                "ride_id": str(ride.id),
                "rider_id": str(rider.id),
            },
            idempotency_key=f"ride-payment-{ride.id}-{payment.id}-{amount}",
        )

        payment.stripe_payment_intent_id = intent.id
        payment.status = "processing"
        db.commit()
        db.refresh(payment)

        if not intent.client_secret:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Stripe did not return a client secret",
            )

        return payment, intent.client_secret

    except HTTPException:
        db.rollback()
        raise

    except stripe.StripeError as exc:
        db.rollback()
        logger.warning(
            "Stripe error while creating payment for ride %s: %s",
            ride.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not initialize payment with Stripe",
        ) from None

    except Exception:
        db.rollback()
        logger.exception(
            "Unexpected error creating payment for ride %s",
            ride.id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not initialize payment",
        ) from None
