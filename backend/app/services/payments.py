import logging

import stripe
from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.payment import Payment
from app.models.ride import Ride, RideStatus
from app.models.user import User


logger = logging.getLogger(__name__)

# Dedicated namespace for cab-booking payment locks.
PAYMENT_LOCK_NAMESPACE = 72641


def create_payment_intent_for_ride(
    db: Session,
    ride: Ride,
    rider: User,
) -> tuple[Payment, str]:
    """Create or reuse a Stripe PaymentIntent for the authenticated rider.

    PostgreSQL transaction-scoped advisory locks serialize concurrent
    payment initialization attempts for the same ride, even when the
    Payment row does not exist yet.
    """

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

    fare = (
        ride.final_fare
        if ride.final_fare is not None
        else ride.estimated_fare
    )

    amount = round(float(fare) * 100)
    currency = settings.STRIPE_CURRENCY.lower()

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
        # PostgreSQL-specific lock. SQLite does not support advisory locks,
        # so this is skipped for the SQLite-based automated test suite.
        if db.get_bind().dialect.name == "postgresql":
            db.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    f"{PAYMENT_LOCK_NAMESPACE}, :ride_id)"
                ),
                {"ride_id": ride.id},
            )

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

            # Reuse an existing PaymentIntent when a request is retried.
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
                        or payment.currency.lower() != currency
                    ):
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                "The existing payment amount differs "
                                "from the current ride fare"
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
                        detail=(
                            "The existing payment cannot be retried yet"
                        ),
                    )

            payment.amount = amount
            payment.currency = currency
            payment.status = "pending"
            payment.failure_reason = None

        else:
            payment = Payment(
                ride_id=ride.id,
                rider_id=rider.id,
                amount=amount,
                currency=currency,
                status="pending",
            )
            db.add(payment)
            db.flush()

        # Payment.ride_id is unique, providing an additional database
        # safeguard against duplicate payment records.
        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency=currency,
            metadata={
                "payment_id": str(payment.id),
                "ride_id": str(ride.id),
                "rider_id": str(rider.id),
            },
            idempotency_key=(
                f"ride-payment-{ride.id}-{payment.id}-"
                f"{amount}-{currency}"
            ),
        )

        if not intent.client_secret:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Stripe did not return a client secret",
            )

        payment.stripe_payment_intent_id = intent.id
        payment.status = "processing"

        db.commit()
        db.refresh(payment)

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