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


def test_complete_ride_lifecycle():
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
            name="Lifecycle Test Rider",
            email="lifecycle-rider@example.com",
            password_hash=hash_password("RiderTest@2026"),
            role="rider",
            rating_avg=0.0,
        )

        driver = User(
            name="Lifecycle Test Driver",
            email="lifecycle-driver@example.com",
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
            # 1. Rider login
            rider_login = client.post(
                "/api/auth/login",
                json={
                    "email": "lifecycle-rider@example.com",
                    "password": "RiderTest@2026",
                },
            )

            assert rider_login.status_code == 200
            rider_token = rider_login.json()["access_token"]

            # 2. Driver login
            driver_login = client.post(
                "/api/auth/login",
                json={
                    "email": "lifecycle-driver@example.com",
                    "password": "DriverTest@2026",
                },
            )

            assert driver_login.status_code == 200
            driver_token = driver_login.json()["access_token"]

            rider_headers = {
                "Authorization": f"Bearer {rider_token}"
            }

            driver_headers = {
                "Authorization": f"Bearer {driver_token}"
            }

            # 3. Rider requests a ride
            request_response = client.post(
                "/api/rides/request",
                json=RIDE_PAYLOAD,
                headers=rider_headers,
            )

            assert request_response.status_code == 201

            ride = request_response.json()
            ride_id = ride["id"]

            assert ride["driver_id"] is not None
            assert ride["status"].lower() == "matched"
            assert ride["estimated_distance_km"] > 0
            assert ride["estimated_fare"] >= 50

            # 4. Driver accepts the ride
            accept_response = client.patch(
                f"/api/rides/{ride_id}/accept",
                headers=driver_headers,
            )

            assert accept_response.status_code == 200
            assert accept_response.json()["status"].lower() == "accepted"

            # 5. Driver marks arrival
            arrived_response = client.patch(
                f"/api/rides/{ride_id}/arrived",
                headers=driver_headers,
            )

            assert arrived_response.status_code == 200
            assert arrived_response.json()["status"].lower() == "driver_arrived"

            # 6. Driver starts the ride
            start_response = client.patch(
                f"/api/rides/{ride_id}/start",
                headers=driver_headers,
            )

            assert start_response.status_code == 200
            assert start_response.json()["status"].lower() == "started"

            # 7. Driver completes the ride
            complete_response = client.patch(
                f"/api/rides/{ride_id}/complete",
                headers=driver_headers,
            )

            assert complete_response.status_code == 200

            completed_ride = complete_response.json()

            assert completed_ride["status"].lower() == "completed"
            assert completed_ride["final_fare"] is not None
            assert completed_ride["final_fare"] == completed_ride["estimated_fare"]

            # 8. Rider can view the completed ride
            detail_response = client.get(
                f"/api/rides/{ride_id}",
                headers=rider_headers,
            )

            assert detail_response.status_code == 200
            assert detail_response.json()["status"].lower() == "completed"

            # 9. Completed ride appears in rider history
            history_response = client.get(
                "/api/rides/history",
                headers=rider_headers,
            )

            assert history_response.status_code == 200

            ride_ids = [
                item["id"] for item in history_response.json()
            ]

            assert ride_id in ride_ids

    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)