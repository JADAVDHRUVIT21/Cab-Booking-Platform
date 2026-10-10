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


def test_driver_cannot_start_ride_before_arrival():
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
            name="State Test Rider",
            email="state-rider@example.com",
            password_hash=hash_password("RiderTest@2026"),
            role="rider",
            rating_avg=0.0,
        )

        driver = User(
            name="State Test Driver",
            email="state-driver@example.com",
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
            # Log in as the rider.
            rider_login = client.post(
                "/api/auth/login",
                json={
                    "email": "state-rider@example.com",
                    "password": "RiderTest@2026",
                },
            )
            assert rider_login.status_code == 200

            rider_headers = {
                "Authorization": (
                    f"Bearer {rider_login.json()['access_token']}"
                )
            }

            # Log in as the driver.
            driver_login = client.post(
                "/api/auth/login",
                json={
                    "email": "state-driver@example.com",
                    "password": "DriverTest@2026",
                },
            )
            assert driver_login.status_code == 200

            driver_headers = {
                "Authorization": (
                    f"Bearer {driver_login.json()['access_token']}"
                )
            }

            # Request a ride; the driver should be assigned.
            request_response = client.post(
                "/api/rides/request",
                json=RIDE_PAYLOAD,
                headers=rider_headers,
            )

            assert request_response.status_code == 201

            ride = request_response.json()
            ride_id = ride["id"]

            assert ride["status"].lower() == "matched"

            # Attempt to start the ride without accepting it
            # or marking the driver as arrived.
            start_response = client.patch(
                f"/api/rides/{ride_id}/start",
                headers=driver_headers,
            )

            assert start_response.status_code == 409
            assert (
                start_response.json()["detail"]
                == "The driver must arrive before starting the ride"
            )

    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)