from fastapi.testclient import TestClient

from app.core.database import Base, get_db
from app.main import app
from app.models.user import User
from app.utils.security import hash_password
from tests.test_auth_api import TestingSessionLocal, test_engine


RIDE_PAYLOAD = {
    "pickup_address": "Rajkot Railway Station",
    "dropoff_address": "Rajkot Airport",
    "pickup_lat": 22.3039,
    "pickup_lng": 70.8022,
    "dropoff_lat": 22.3094,
    "dropoff_lng": 70.7797,
}


def test_driver_rejection_reassigns_ride_and_publishes_events(
    monkeypatch,
):
    from app.routers import rides as rides_router

    Base.metadata.create_all(bind=test_engine)

    published_events = []

    def fake_publish_ride_event(**kwargs):
        published_events.append(kwargs)

    monkeypatch.setattr(
        rides_router,
        "publish_ride_event",
        fake_publish_ride_event,
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    db = TestingSessionLocal()

    try:
        rider = User(
            name="Rejection Test Rider",
            email="reject-rider@example.com",
            password_hash=hash_password("RiderTest@2026"),
            role="rider",
            rating_avg=0.0,
        )

        first_driver = User(
            name="First Rejection Driver",
            email="reject-driver1@example.com",
            password_hash=hash_password("DriverTest@2026"),
            role="driver",
            rating_avg=0.0,
            is_available=True,
            current_lat=22.3039,
            current_lng=70.8022,
        )

        # Keep the second driver unavailable until the first
        # driver has been assigned the ride.
        next_driver = User(
            name="Next Rejection Driver",
            email="reject-driver2@example.com",
            password_hash=hash_password("DriverTest@2026"),
            role="driver",
            rating_avg=0.0,
            is_available=False,
            current_lat=22.3040,
            current_lng=70.8023,
        )

        db.add_all([rider, first_driver, next_driver])
        db.commit()

        # Save IDs before closing the session.
        rider_id = rider.id
        first_driver_id = first_driver.id
        next_driver_id = next_driver.id

    finally:
        db.close()

    try:
        with TestClient(app) as client:
            # Log in the rider.
            rider_login = client.post(
                "/api/auth/login",
                json={
                    "email": "reject-rider@example.com",
                    "password": "RiderTest@2026",
                },
            )
            assert rider_login.status_code == 200, (
                f"Rider login failed: {rider_login.text}"
            )

            # Log in the first driver.
            first_driver_login = client.post(
                "/api/auth/login",
                json={
                    "email": "reject-driver1@example.com",
                    "password": "DriverTest@2026",
                },
            )
            assert first_driver_login.status_code == 200, (
                f"Driver login failed: {first_driver_login.text}"
            )

            rider_headers = {
                "Authorization": (
                    f"Bearer {rider_login.json()['access_token']}"
                )
            }

            first_driver_headers = {
                "Authorization": (
                    f"Bearer "
                    f"{first_driver_login.json()['access_token']}"
                )
            }

            # Request a ride. Only the first driver is available.
            request_response = client.post(
                "/api/rides/request",
                json=RIDE_PAYLOAD,
                headers=rider_headers,
            )

            assert request_response.status_code == 201, (
                f"Ride request failed: {request_response.text}"
            )

            ride = request_response.json()
            ride_id = ride["id"]
            previous_driver_id = ride["driver_id"]

            assert ride["status"].lower() == "matched"
            assert previous_driver_id == first_driver_id, (
                "The ride should initially be assigned "
                "to the first driver."
            )

            # Make the second driver available for reassignment.
            db = TestingSessionLocal()
            try:
                next_driver_record = (
                    db.query(User)
                    .filter(User.id == next_driver_id)
                    .first()
                )

                assert next_driver_record is not None

                next_driver_record.is_available = True
                db.commit()
            finally:
                db.close()

            # The first driver rejects the ride.
            reject_response = client.patch(
                f"/api/rides/{ride_id}/reject",
                headers=first_driver_headers,
            )

            assert reject_response.status_code == 200, (
                f"Ride rejection failed: {reject_response.text}"
            )

            reassigned_ride = reject_response.json()

            assert reassigned_ride["status"].lower() == "matched"
            assert reassigned_ride["driver_id"] == next_driver_id, (
                "The ride should be reassigned to the second driver, "
                "not returned to the rejecting driver."
            )

            # Verify both drivers' availability in the database.
            db = TestingSessionLocal()
            try:
                previous_driver = (
                    db.query(User)
                    .filter(User.id == previous_driver_id)
                    .first()
                )

                reassigned_driver = (
                    db.query(User)
                    .filter(
                        User.id == reassigned_ride["driver_id"]
                    )
                    .first()
                )

                assert previous_driver is not None
                assert previous_driver.is_available is True

                assert reassigned_driver is not None
                assert reassigned_driver.is_available is False

            finally:
                db.close()

            # Verify the reassignment event was published.
            assert any(
                event["event_type"] == "ride.reassigned"
                and event["ride_id"] == ride_id
                and event["driver_id"] == next_driver_id
                for event in published_events
            ), "The ride.reassigned event was not published."

            # Verify the rejection event targets the previous driver.
            assert any(
                event["event_type"] == "ride.rejected"
                and event["ride_id"] == ride_id
                and event["driver_id"] == previous_driver_id
                for event in published_events
            ), "The ride.rejected event was not published."

    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)