
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.payment import Payment
from app.models.payment_webhook_event import PaymentWebhookEvent
from app.models.ride import Ride, RideStatus
from app.models.user import User, UserRole


TEST_DATABASE_URL = "sqlite://"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    bind=test_engine,
)


@pytest.fixture()
def payment_client():
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)


def seed_payment(status_value="processing"):
    db = TestingSessionLocal()
    try:
        rider = User(
            name="Payment Test Rider",
            email=f"payment-rider-{uuid.uuid4().hex}@example.com",
            password_hash="test-password-hash",
            role=UserRole.RIDER,
            rating_avg=0.0,
            is_available=False,
        )
        db.add(rider)
        db.flush()

        driver = User(
            name="Payment Test Driver",
            email=f"payment-driver-{uuid.uuid4().hex}@example.com",
            password_hash="test-password-hash",
            role=UserRole.DRIVER,
            rating_avg=0.0,
            is_available=True,
        )
        db.add(driver)
        db.flush()

        ride = Ride(
            rider_id=rider.id,
            driver_id=driver.id,
            pickup_address="Pickup location",
            dropoff_address="Dropoff location",
            pickup_lat=22.3039,
            pickup_lng=72.8022,
            dropoff_lat=22.2916,
            dropoff_lng=72.7932,
            estimated_distance_km=5.0,
            estimated_fare=100.0,
            final_fare=100.0,
            status=RideStatus.COMPLETED,
        )
        db.add(ride)
        db.flush()

        payment = Payment(
            ride_id=ride.id,
            rider_id=rider.id,
            amount=10000,
            currency="inr",
            status=status_value,
            stripe_payment_intent_id="pi_test_123",
        )
        db.add(payment)
        db.commit()

        return {
            "payment_id": payment.id,
            "ride_id": ride.id,
            "rider_id": rider.id,
        }
    finally:
        db.close()


def make_event(
    event_type,
    ids,
    *,
    event_id=None,
    amount=10000,
    currency="inr",
    metadata=None,
    latest_charge="ch_test_123",
    last_payment_error=None,
):
    if metadata is None:
        metadata = {
            "payment_id": str(ids["payment_id"]),
            "ride_id": str(ids["ride_id"]),
            "rider_id": str(ids["rider_id"]),
        }

    return {
        "id": event_id or f"evt_test_{uuid.uuid4().hex}",
        "type": event_type,
        "data": {
            "object": {
                "id": "pi_test_123",
                "status": {
                    "payment_intent.succeeded": "succeeded",
                    "payment_intent.payment_failed": "requires_payment_method",
                    "payment_intent.canceled": "canceled",
                }[event_type],
                "amount": amount,
                "currency": currency,
                "metadata": metadata,
                "latest_charge": latest_charge,
                "last_payment_error": last_payment_error,
            }
        },
    }


def dispatch_event(monkeypatch, event):
    import stripe

    monkeypatch.setattr(
        stripe.Webhook,
        "construct_event",
        lambda **kwargs: event,
    )


def read_payment(payment_id):
    db = TestingSessionLocal()
    try:
        return db.query(Payment).filter_by(id=payment_id).one()
    finally:
        db.close()


def read_webhook_event(event_id):
    db = TestingSessionLocal()
    try:
        return (
            db.query(PaymentWebhookEvent)
            .filter_by(stripe_event_id=event_id)
            .first()
        )
    finally:
        db.close()


def post_webhook(client):
    return client.post(
        "/api/webhooks/stripe",
        content=b'{"test": "payload"}',
        headers={"stripe-signature": "test-signature"},
    )


def test_successful_event_marks_payment_succeeded(
    payment_client,
    monkeypatch,
):
    ids = seed_payment()
    event = make_event("payment_intent.succeeded", ids)
    dispatch_event(monkeypatch, event)

    response = post_webhook(payment_client)

    assert response.status_code == 200
    assert response.json() == {"received": True, "processed": True}

    payment = read_payment(ids["payment_id"])
    assert payment.status == "succeeded"
    assert payment.failure_reason is None
    assert payment.stripe_charge_id == "ch_test_123"

    stored_event = read_webhook_event(event["id"])
    assert stored_event is not None
    assert stored_event.event_type == "payment_intent.succeeded"


@pytest.mark.parametrize(
    ("event_type", "expected_status"),
    [
        ("payment_intent.payment_failed", "failed"),
        ("payment_intent.canceled", "cancelled"),
    ],
)
def test_failed_and_cancelled_events_update_payment(
    payment_client,
    monkeypatch,
    event_type,
    expected_status,
):
    ids = seed_payment()
    event = make_event(
        event_type,
        ids,
        last_payment_error={"message": "Card was declined"},
    )
    dispatch_event(monkeypatch, event)

    response = post_webhook(payment_client)

    assert response.status_code == 200
    assert response.json()["processed"] is True

    payment = read_payment(ids["payment_id"])
    assert payment.status == expected_status

    if event_type == "payment_intent.payment_failed":
        assert payment.failure_reason == "Card was declined"

    assert read_webhook_event(event["id"]) is not None


def test_missing_signature_is_rejected(payment_client):
    response = payment_client.post(
        "/api/webhooks/stripe",
        content=b'{"test": "payload"}',
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Missing Stripe signature"


def test_invalid_signature_is_rejected(
    payment_client,
    monkeypatch,
):
    import stripe

    def reject_signature(**kwargs):
        raise ValueError("Invalid test signature")

    monkeypatch.setattr(
        stripe.Webhook,
        "construct_event",
        reject_signature,
    )

    response = payment_client.post(
        "/api/webhooks/stripe",
        content=b'{"test": "payload"}',
        headers={"stripe-signature": "invalid-signature"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Invalid Stripe webhook payload or signature"
    )


@pytest.mark.parametrize(
    ("overrides", "expected_detail"),
    [
        (
            {"amount": 9999},
            "Payment amount or currency mismatch",
        ),
        (
            {"currency": "usd"},
            "Payment amount or currency mismatch",
        ),
        (
            {
                "metadata": {
                    "payment_id": "999",
                    "ride_id": "1",
                    "rider_id": "1",
                }
            },
            "Payment metadata mismatch",
        ),
    ],
)
def test_mismatched_event_data_is_rejected(
    payment_client,
    monkeypatch,
    overrides,
    expected_detail,
):
    ids = seed_payment()
    event = make_event(
        "payment_intent.succeeded",
        ids,
        **overrides,
    )
    dispatch_event(monkeypatch, event)

    response = post_webhook(payment_client)

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail

    payment = read_payment(ids["payment_id"])
    assert payment.status == "processing"
    assert read_webhook_event(event["id"]) is None


def test_delayed_failure_cannot_overwrite_success(
    payment_client,
    monkeypatch,
):
    ids = seed_payment()

    success_event = make_event("payment_intent.succeeded", ids)
    dispatch_event(monkeypatch, success_event)

    success_response = post_webhook(payment_client)
    assert success_response.status_code == 200
    assert read_payment(ids["payment_id"]).status == "succeeded"

    # This is a distinct Stripe event for the same PaymentIntent.
    failure_event = make_event("payment_intent.payment_failed", ids)
    dispatch_event(monkeypatch, failure_event)

    failure_response = post_webhook(payment_client)

    assert failure_response.status_code == 200
    assert failure_response.json()["processed"] is True

    payment = read_payment(ids["payment_id"])
    assert payment.status == "succeeded"
    assert payment.failure_reason is None
    assert read_webhook_event(success_event["id"]) is not None
    assert read_webhook_event(failure_event["id"]) is not None


def test_duplicate_success_event_is_safe(
    payment_client,
    monkeypatch,
):
    ids = seed_payment()
    event = make_event("payment_intent.succeeded", ids)
    dispatch_event(monkeypatch, event)

    first_response = post_webhook(payment_client)
    assert first_response.status_code == 200
    assert first_response.json() == {
        "received": True,
        "processed": True,
    }

    second_response = post_webhook(payment_client)
    assert second_response.status_code == 200
    assert second_response.json() == {
        "received": True,
        "processed": True,
        "duplicate": True,
    }

    payment = read_payment(ids["payment_id"])
    assert payment.status == "succeeded"

    db = TestingSessionLocal()
    try:
        event_count = (
            db.query(PaymentWebhookEvent)
            .filter_by(stripe_event_id=event["id"])
            .count()
        )
        assert event_count == 1
    finally:
        db.close()


def test_unknown_payment_intent_is_acknowledged(
    payment_client,
    monkeypatch,
):
    ids = seed_payment()
    event = make_event("payment_intent.succeeded", ids)
    event["data"]["object"]["id"] = "pi_unknown"
    dispatch_event(monkeypatch, event)

    response = post_webhook(payment_client)

    assert response.status_code == 200
    assert response.json() == {"received": True, "processed": False}
    assert read_webhook_event(event["id"]) is None


def test_missing_event_id_is_rejected(
    payment_client,
    monkeypatch,
):
    ids = seed_payment()
    event = make_event("payment_intent.succeeded", ids)
    event.pop("id")
    dispatch_event(monkeypatch, event)

    response = post_webhook(payment_client)

    assert response.status_code == 400
    assert response.json()["detail"] == "Stripe event ID or type is missing"
    assert read_payment(ids["payment_id"]).status == "processing"


def test_unsupported_event_is_acknowledged(
    payment_client,
    monkeypatch,
):
    event = {
        "id": f"evt_unsupported_{uuid.uuid4().hex}",
        "type": "customer.updated",
        "data": {"object": {}},
    }
    dispatch_event(monkeypatch, event)

    response = post_webhook(payment_client)

    assert response.status_code == 200
    assert response.json() == {"received": True, "processed": False}
