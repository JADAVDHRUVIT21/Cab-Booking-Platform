
import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.payment import Payment
from app.models.payment_webhook_event import PaymentWebhookEvent


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/webhooks",
    tags=["Payment Webhooks"],
)


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe webhook is not configured",
        )

    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe is not configured",
        )

    stripe.api_key = settings.STRIPE_SECRET_KEY
    payload = await request.body()
    signature = request.headers.get("stripe-signature")

    if not signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe signature",
        )

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=signature,
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe webhook payload or signature",
        ) from None

    event_id = event.get("id")
    event_type = event.get("type")

    if not event_id or not event_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stripe event ID or type is missing",
        )

    supported_events = {
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "payment_intent.canceled",
    }

    if event_type not in supported_events:
        return {"received": True, "processed": False}

    intent = event.get("data", {}).get("object", {})
    intent_id = intent.get("id")

    if not intent_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PaymentIntent ID is missing",
        )

    try:
        # A prior successful delivery of this exact event is a no-op.
        existing_event = (
            db.query(PaymentWebhookEvent)
            .filter(PaymentWebhookEvent.stripe_event_id == event_id)
            .first()
        )

        if existing_event is not None:
            return {
                "received": True,
                "processed": True,
                "duplicate": True,
            }

        payment = (
            db.query(Payment)
            .filter(Payment.stripe_payment_intent_id == intent_id)
            .with_for_update()
            .first()
        )

        if payment is None:
            logger.warning(
                "Stripe event %s references an unknown PaymentIntent",
                event_type,
            )
            return {"received": True, "processed": False}

        metadata = intent.get("metadata") or {}

        if (
            metadata.get("payment_id") != str(payment.id)
            or metadata.get("ride_id") != str(payment.ride_id)
            or metadata.get("rider_id") != str(payment.rider_id)
        ):
            logger.error(
                "Stripe metadata mismatch for payment %s",
                payment.id,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment metadata mismatch",
            )

        try:
            received_amount = int(intent.get("amount", -1))
        except (TypeError, ValueError):
            received_amount = -1

        if (
            received_amount != payment.amount
            or str(intent.get("currency", "")).lower()
            != payment.currency.lower()
        ):
            logger.error(
                "Stripe amount/currency mismatch for payment %s",
                payment.id,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment amount or currency mismatch",
            )

        # Never let delayed failure/cancel events undo a successful payment.
        if payment.status == "succeeded":
            db.add(
                PaymentWebhookEvent(
                    stripe_event_id=event_id,
                    event_type=event_type,
                )
            )
            db.commit()
            return {"received": True, "processed": True}

        if event_type == "payment_intent.succeeded":
            payment.status = "succeeded"
            payment.failure_reason = None

            latest_charge = intent.get("latest_charge")
            if isinstance(latest_charge, str):
                payment.stripe_charge_id = latest_charge
            elif isinstance(latest_charge, dict):
                payment.stripe_charge_id = latest_charge.get("id")

        elif event_type == "payment_intent.payment_failed":
            # Do not regress a cancelled payment due to a delayed failure.
            if payment.status != "cancelled":
                payment.status = "failed"

                last_error = intent.get("last_payment_error") or {}
                reason = last_error.get("message") or "Payment failed"
                payment.failure_reason = str(reason)[:500]

        elif event_type == "payment_intent.canceled":
            # A delayed cancellation must not overwrite a recorded failure.
            if payment.status not in {"failed", "cancelled"}:
                payment.status = "cancelled"

        # Save the payment transition and event ID in the same transaction.
        db.add(
            PaymentWebhookEvent(
                stripe_event_id=event_id,
                event_type=event_type,
            )
        )
        db.commit()

        return {"received": True, "processed": True}

    except HTTPException:
        db.rollback()
        raise

    except IntegrityError:
        # A concurrent delivery may have inserted this event first.
        db.rollback()

        duplicate = (
            db.query(PaymentWebhookEvent)
            .filter(PaymentWebhookEvent.stripe_event_id == event_id)
            .first()
        )

        if duplicate is not None:
            return {
                "received": True,
                "processed": True,
                "duplicate": True,
            }

        logger.exception(
            "Database integrity error processing Stripe event %s",
            event_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not process payment event",
        ) from None

    except Exception:
        db.rollback()
        logger.exception(
            "Could not process Stripe event %s",
            event_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not process payment event",
        ) from None
