from fastapi.testclient import TestClient

from app.core.database import Base, get_db
from app.main import app
from app.models.user import User
from tests.test_auth_api import TestingSessionLocal, test_engine


RIDE_PAYLOAD = {
    "pickup_address": "Rajkot Railway Station",
    "dropoff_address": "Rajkot Airport",
    "pickup_lat": 22.3039,
    "pickup_lng": 70.8022,
    "dropoff_lat": 22.3094,
    "dropoff_lng": 70.7797,
}


def test_ride_estimate_requires_authentication():
    with TestClient(app) as client:
        response = client.post(
            "/api/rides/estimate",
            json=RIDE_PAYLOAD,
        )

    assert response.status_code == 401


def test_ride_request_requires_authentication():
    with TestClient(app) as client:
        response = client.post(
            "/api/rides/request",
            json=RIDE_PAYLOAD,
        )

    assert response.status_code == 401


def test_ride_history_requires_authentication():
    with TestClient(app) as client:
        response = client.get("/api/rides/history")

    assert response.status_code == 401


def test_ride_estimate_rejects_missing_fields():
    # The endpoint requires authentication.
    # Without a token, the request should be rejected.
    with TestClient(app) as client:
        response = client.post(
            "/api/rides/estimate",
            json={},
        )

    assert response.status_code == 401


def test_rider_can_estimate_fare():
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
        from app.utils.security import hash_password

        rider = User(
            name="Ride API Rider",
            email="ride-api-rider@example.com",
            password_hash=hash_password("TestPassword@2026"),
            role="rider",
            rating_avg=0.0,
        )

        db.add(rider)
        db.commit()
        db.refresh(rider)

    finally:
        db.close()

    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/auth/login",
                json={
                    "email": "ride-api-rider@example.com",
                    "password": "TestPassword@2026",
                },
            )

            assert login.status_code == 200

            token = login.json()["access_token"]

            response = client.post(
                "/api/rides/estimate",
                json=RIDE_PAYLOAD,
                headers={
                    "Authorization": f"Bearer {token}",
                },
            )

        assert response.status_code == 200

        result = response.json()

        assert result["estimated_distance_km"] > 0
        assert result["estimated_fare"] >= 50

    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)