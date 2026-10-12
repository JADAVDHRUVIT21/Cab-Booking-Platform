
from types import SimpleNamespace

import pytest
import stripe
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.models.payment import Payment
from app.models.ride import Ride, RideStatus
from app.models.user import User, UserRole
from app.utils.auth import get_current_user


test_engine = create_engine(
    "sqlite://",
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
def payment_intent_client():
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


def seed_ride(status_value=RideStatus.COMPLETED, fare=100.0):
    db = TestingSessionLocal()
    try:
        rider = User(
            name="Intent Test Rider",
            email="intent-rider@example.com",
            password_hash="test-password-hash",
            role=UserRole.RIDER,
            rating_avg=0.0,
            is_available=False,
        )
        db.add(rider)
        db.flush()

        other_rider = User(
            name="Other Test Rider",
            email="intent-other-rider@example.com",
            password_hash="test-password-hash",
            role=UserRole.RIDER,
            rating_avg=0.0,
            is_available=False,
        )
        db.add(other_rider)
        db.flush()

        driver = User(
            name="Intent Test Driver",
            email="intent-driver@example.com",
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
            pickup_address="Test pickup",
            dropoff_address="Test dropoff",
            pickup_lat=22.3039,
            pickup_lng=72.8022,
            dropoff_lat=22.2916,
            dropoff_lng=72.7932,
            estimated_distance_km=5.0,
            estimated_fare=fare,
            final_fare=fare if status_value == RideStatus.COMPLETED else None,
            status=status_value,
        )
        db.add(ride)
        db.commit()

        return {
            "ride_id": ride.id,
            "rider_id": rider.id,
            "other_rider_id": other_rider.id,
            "driver_id": driver.id,
        }
    finally:
        db.close()


def set_current_user(user_id, role=UserRole.RIDER):
    user = SimpleNamespace(
        id=user_id,
        role=role,
        email="test@example.com",
        name="Test User",
    )
    app.dependency_overrides[get_current_user] = lambda: user


def fake_intent(
    intent_id="pi_mock_123",
    client_secret="pi_mock_123_secret_mock",
    status_value="requires_payment_method",
):
    return SimpleNamespace(
        id=intent_id,
        client_secret=client_secret,
        status=status_value,
    )


def mock_stripe_create(monkeypatch, intent=None):
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return intent or fake_intent()

    monkeypatch.setattr(
        stripe.PaymentIntent,
        "create",
        fake_create,
    )
    return calls


def read_payment_for_ride(ride_id):
    db = TestingSessionLocal()
    try:
        return (
            db.query(Payment)
            .filter(Payment.ride_id == ride_id)
            .first()
        )
    finally:
        db.close()


def test_completed_ride_creates_payment_intent(
    payment_intent_client,
    monkeypatch,
):
    ids = seed_ride()
    set_current_user(ids["rider_id"])
    calls = mock_stripe_create(monkeypatch)

    response = payment_intent_client.post(
        "/api/payments/create-intent",
        json={"ride_id": ids["ride_id"]},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["client_secret"] == "pi_mock_123_secret_mock"
    assert data["payment"]["ride_id"] == ids["ride_id"]
    assert data["payment"]["rider_id"] == ids["rider_id"]
    assert data["payment"]["amount"] == 10000
    assert data["payment"]["currency"] == settings.STRIPE_CURRENCY.lower()
    assert data["payment"]["status"] == "processing"

    assert len(calls) == 1
    assert calls[0]["amount"] == 10000
    assert calls[0]["metadata"]["ride_id"] == str(ids["ride_id"])

    payment = read_payment_for_ride(ids["ride_id"])
    assert payment is not None
    assert payment.stripe_payment_intent_id == "pi_mock_123"


def test_missing_ride_returns_404(payment_intent_client):
    ids = seed_ride()
    set_current_user(ids["rider_id"])

    response = payment_intent_client.post(
        "/api/payments/create-intent",
        json={"ride_id": 99999},
    )

    assert response.status_code == 404


def test_another_rider_cannot_pay_for_ride(
    payment_intent_client,
    monkeypatch,
):
    ids = seed_ride()
    set_current_user(ids["other_rider_id"])
    calls = mock_stripe_create(monkeypatch)

    response = payment_intent_client.post(
        "/api/payments/create-intent",
        json={"ride_id": ids["ride_id"]},
    )

    assert response.status_code == 403
    assert calls == []
    assert read_payment_for_ride(ids["ride_id"]) is None


def test_driver_cannot_pay_for_ride(
    payment_intent_client,
    monkeypatch,
):
    ids = seed_ride()
    set_current_user(ids["driver_id"], UserRole.DRIVER)
    calls = mock_stripe_create(monkeypatch)

    response = payment_intent_client.post(
        "/api/payments/create-intent",
        json={"ride_id": ids["ride_id"]},
    )

    assert response.status_code == 403
    assert calls == []


def test_payment_requires_completed_ride(
    payment_intent_client,
    monkeypatch,
):
    ids = seed_ride(status_value=RideStatus.STARTED)
    set_current_user(ids["rider_id"])
    calls = mock_stripe_create(monkeypatch)

    response = payment_intent_client.post(
        "/api/payments/create-intent",
        json={"ride_id": ids["ride_id"]},
    )

    assert response.status_code == 409
    assert calls == []


def test_already_paid_ride_cannot_be_paid_again(
    payment_intent_client,
    monkeypatch,
):
    ids = seed_ride()
    db = TestingSessionLocal()
    try:
        db.add(
            Payment(
                ride_id=ids["ride_id"],
                rider_id=ids["rider_id"],
                amount=10000,
                currency="inr",
                status="succeeded",
                stripe_payment_intent_id="pi_already_paid",
            )
        )
        db.commit()
    finally:
        db.close()

    set_current_user(ids["rider_id"])
    calls = mock_stripe_create(monkeypatch)

    response = payment_intent_client.post(
        "/api/payments/create-intent",
        json={"ride_id": ids["ride_id"]},
    )

    assert response.status_code == 409
    assert calls == []


def test_existing_active_intent_is_reused(
    payment_intent_client,
    monkeypatch,
):
    ids = seed_ride()
    db = TestingSessionLocal()
    try:
        db.add(
            Payment(
                ride_id=ids["ride_id"],
                rider_id=ids["rider_id"],
                amount=10000,
                currency="inr",
                status="processing",
                stripe_payment_intent_id="pi_existing",
            )
        )
        db.commit()
    finally:
        db.close()

    set_current_user(ids["rider_id"])
    create_calls = mock_stripe_create(monkeypatch)
    retrieve_calls = []

    def fake_retrieve(intent_id):
        retrieve_calls.append(intent_id)
        return fake_intent(
            intent_id="pi_existing",
            client_secret="pi_existing_secret_mock",
        )

    monkeypatch.setattr(
        stripe.PaymentIntent,
        "retrieve",
        fake_retrieve,
    )

    response = payment_intent_client.post(
        "/api/payments/create-intent",
        json={"ride_id": ids["ride_id"]},
    )

    assert response.status_code == 201
    assert response.json()["client_secret"] == "pi_existing_secret_mock"
    assert retrieve_calls == ["pi_existing"]
    assert create_calls == []


def test_stripe_failure_returns_safe_error(
    payment_intent_client,
    monkeypatch,
):
    ids = seed_ride()
    set_current_user(ids["rider_id"])

    def fail_create(**kwargs):
        raise stripe.APIConnectionError("mock connection failure")

    monkeypatch.setattr(
        stripe.PaymentIntent,
        "create",
        fail_create,
    )

    response = payment_intent_client.post(
        "/api/payments/create-intent",
        json={"ride_id": ids["ride_id"]},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Could not initialize payment with Stripe"
    )
    assert "mock connection failure" not in response.text


def test_zero_fare_cannot_be_paid(
    payment_intent_client,
    monkeypatch,
):
    ids = seed_ride(fare=0.0)
    set_current_user(ids["rider_id"])
    calls = mock_stripe_create(monkeypatch)

    response = payment_intent_client.post(
        "/api/payments/create-intent",
        json={"ride_id": ids["ride_id"]},
    )

    assert response.status_code == 400
    assert calls == []
