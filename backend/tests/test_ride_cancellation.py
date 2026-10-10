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


def test_rider_can_cancel_matched_ride():
    Base.metadata.create_all(bind=test_engine)

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
            name="Cancellation Test Rider",
            email="cancel-rider@example.com",
            password_hash=hash_password("RiderTest@2026"),
            role="rider",
            rating_avg=0.0,
        )

        driver = User(
            name="Cancellation Test Driver",
            email="cancel-driver@example.com",
            password_hash=hash_password("DriverTest@2026"),
            role="driver",
            rating_avg=0.0,
            is_available=True,
            current_lat=22.3039,
            current_lng=70.8022,
        )

        db.add_all([rider, driver])
        db.commit()

    finally:
        db.close()

    try:
        with TestClient(app) as client:
            rider_login = client.post(
                "/api/auth/login",
                json={
                    "email": "cancel-rider@example.com",
                    "password": "RiderTest@2026",
                },
            )
            assert rider_login.status_code == 200

            rider_headers = {
                "Authorization": (
                    f"Bearer {rider_login.json()['access_token']}"
                )
            }

            # Request a ride.
            request_response = client.post(
                "/api/rides/request",
                json=RIDE_PAYLOAD,
                headers=rider_headers,
            )
            assert request_response.status_code == 201

            ride = request_response.json()
            ride_id = ride["id"]

            assert ride["status"].lower() == "matched"
            assert ride["driver_id"] is not None

            # Cancel the ride.
            cancel_response = client.patch(
                f"/api/rides/{ride_id}/cancel",
                params={"reason": "Test cancellation"},
                headers=rider_headers,
            )

            assert cancel_response.status_code == 200

            cancelled_ride = cancel_response.json()
            assert cancelled_ride["status"].lower() == "cancelled"
            assert cancelled_ride["cancellation_reason"] == "Test cancellation"

            # Verify the assigned driver is available again.
            db = TestingSessionLocal()
            try:
                updated_driver = (
                    db.query(User)
                    .filter(User.id == ride["driver_id"])
                    .first()
                )

                assert updated_driver is not None
                assert updated_driver.is_available is True

            finally:
                db.close()

    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)