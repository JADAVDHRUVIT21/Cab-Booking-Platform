
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.ride import Ride, RideStatus
from app.models.review import Review
from app.models.user import User, UserRole
from app.utils.auth import get_current_user


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
def review_client():
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)


def create_user(
    db,
    email: str,
    role: UserRole,
) -> User:
    user = User(
        name=email.split("@")[0],
        email=email,
        password_hash="test-password-hash",
        role=role,
        rating_avg=0.0,
        is_available=(role == UserRole.DRIVER),
    )
    db.add(user)
    db.flush()
    return user


def create_completed_ride(
    db,
    rider_id: int,
    driver_id: int | None,
    status: RideStatus = RideStatus.COMPLETED,
) -> Ride:
    ride = Ride(
        rider_id=rider_id,
        driver_id=driver_id,
        pickup_address="Pickup location",
        dropoff_address="Dropoff location",
        pickup_lat=22.3039,
        pickup_lng=70.8022,
        dropoff_lat=22.2916,
        dropoff_lng=70.7932,
        estimated_distance_km=5.0,
        estimated_fare=100.0,
        final_fare=(
            100.0 if status == RideStatus.COMPLETED else None
        ),
        status=status,
    )
    db.add(ride)
    db.flush()
    return ride


def set_current_user(user: User):
    # Preserve the get_db override configured by the fixture.
    app.dependency_overrides[get_current_user] = lambda: user


def seed_review_scenario(
    *,
    ride_status: RideStatus = RideStatus.COMPLETED,
    assigned_driver: bool = True,
):
    db = TestingSessionLocal()

    try:
        rider = create_user(
            db,
            "review-rider@example.com",
            UserRole.RIDER,
        )
        driver = create_user(
            db,
            "review-driver@example.com",
            UserRole.DRIVER,
        )
        other_rider = create_user(
            db,
            "other-rider@example.com",
            UserRole.RIDER,
        )

        ride = create_completed_ride(
            db,
            rider_id=rider.id,
            driver_id=driver.id if assigned_driver else None,
            status=ride_status,
        )

        db.commit()

        return {
            "rider": rider,
            "driver": driver,
            "other_rider": other_rider,
            "ride_id": ride.id,
            "driver_id": driver.id,
        }
    finally:
        db.close()


def test_review_requires_authentication(review_client):
    response = review_client.post(
        "/api/reviews",
        json={
            "ride_id": 1,
            "rating": 5,
            "comment": "Great ride",
        },
    )

    assert response.status_code == 401


def test_rider_can_review_own_completed_ride(review_client):
    data = seed_review_scenario()
    set_current_user(data["rider"])

    response = review_client.post(
        "/api/reviews",
        json={
            "ride_id": data["ride_id"],
            "rating": 5,
            "comment": "Great ride",
        },
    )

    assert response.status_code == 201

    body = response.json()
    assert body["ride_id"] == data["ride_id"]
    assert body["rider_id"] == data["rider"].id
    assert body["driver_id"] == data["driver_id"]
    assert body["rating"] == 5


def test_driver_cannot_submit_review(review_client):
    data = seed_review_scenario()
    set_current_user(data["driver"])

    response = review_client.post(
        "/api/reviews",
        json={
            "ride_id": data["ride_id"],
            "rating": 5,
            "comment": "Great ride",
        },
    )

    assert response.status_code == 403


def test_rider_cannot_review_another_riders_ride(review_client):
    data = seed_review_scenario()
    set_current_user(data["other_rider"])

    response = review_client.post(
        "/api/reviews",
        json={
            "ride_id": data["ride_id"],
            "rating": 5,
            "comment": "Great ride",
        },
    )

    assert response.status_code == 403


def test_non_completed_ride_cannot_be_reviewed(review_client):
    data = seed_review_scenario(
        ride_status=RideStatus.STARTED,
    )
    set_current_user(data["rider"])

    response = review_client.post(
        "/api/reviews",
        json={
            "ride_id": data["ride_id"],
            "rating": 5,
            "comment": "Great ride",
        },
    )

    assert response.status_code == 400


def test_ride_without_driver_cannot_be_reviewed(review_client):
    data = seed_review_scenario(assigned_driver=False)
    set_current_user(data["rider"])

    response = review_client.post(
        "/api/reviews",
        json={
            "ride_id": data["ride_id"],
            "rating": 5,
            "comment": "Great ride",
        },
    )

    assert response.status_code == 400


def test_duplicate_review_is_rejected(review_client):
    data = seed_review_scenario()
    set_current_user(data["rider"])

    payload = {
        "ride_id": data["ride_id"],
        "rating": 5,
        "comment": "Great ride",
    }

    first = review_client.post("/api/reviews", json=payload)
    second = review_client.post("/api/reviews", json=payload)

    assert first.status_code == 201
    assert second.status_code == 409


@pytest.mark.parametrize("rating", [0, 6, -1])
def test_rating_outside_allowed_range_is_rejected(
    review_client,
    rating,
):
    data = seed_review_scenario()
    set_current_user(data["rider"])

    response = review_client.post(
        "/api/reviews",
        json={
            "ride_id": data["ride_id"],
            "rating": rating,
            "comment": "Invalid rating",
        },
    )

    assert response.status_code == 422


def test_driver_rating_endpoint_returns_average_and_count(
    review_client,
):
    data = seed_review_scenario()
    set_current_user(data["rider"])

    create_response = review_client.post(
        "/api/reviews",
        json={
            "ride_id": data["ride_id"],
            "rating": 4,
            "comment": "Good ride",
        },
    )

    assert create_response.status_code == 201

    response = review_client.get(
        f"/api/reviews/drivers/{data['driver_id']}/rating"
    )

    assert response.status_code == 200

    body = response.json()
    assert body["driver_id"] == data["driver_id"]
    assert body["rating_avg"] == 4.0
    assert body["total_reviews"] == 1


def test_missing_driver_returns_404(review_client):
    response = review_client.get(
        "/api/reviews/drivers/99999"
    )

    assert response.status_code == 404
