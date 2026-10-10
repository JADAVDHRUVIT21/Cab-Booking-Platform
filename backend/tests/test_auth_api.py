import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.user import User  # Ensure models are registered


TEST_DATABASE_URL = "sqlite://"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine,
)


@pytest.fixture()
def client():
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)


def test_register_rider(client):
    response = client.post(
        "/api/auth/register",
        json={
            "name": "API Test Rider",
            "email": "api-rider@example.com",
            "password": "TestPassword@2026",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "api-rider@example.com"
    assert data["role"].lower() == "rider"
    assert "password" not in data
    assert "password_hash" not in data


def test_duplicate_registration_returns_conflict(client):
    payload = {
        "name": "API Test Rider",
        "email": "duplicate@example.com",
        "password": "TestPassword@2026",
    }

    first = client.post("/api/auth/register", json=payload)
    second = client.post("/api/auth/register", json=payload)

    assert first.status_code == 201
    assert second.status_code == 409


def test_login_with_valid_credentials(client):
    client.post(
        "/api/auth/register",
        json={
            "name": "Login Test Rider",
            "email": "login-rider@example.com",
            "password": "TestPassword@2026",
        },
    )

    response = client.post(
        "/api/auth/login",
        json={
            "email": "login-rider@example.com",
            "password": "TestPassword@2026",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "bearer"
    assert isinstance(data["access_token"], str)
    assert len(data["access_token"]) > 0


def test_login_with_wrong_password_returns_401(client):
    client.post(
        "/api/auth/register",
        json={
            "name": "Login Test Rider",
            "email": "wrong-password@example.com",
            "password": "TestPassword@2026",
        },
    )

    response = client.post(
        "/api/auth/login",
        json={
            "email": "wrong-password@example.com",
            "password": "WrongPassword@2026",
        },
    )

    assert response.status_code == 401


def test_profile_requires_authentication(client):
    response = client.get("/api/auth/me")

    assert response.status_code == 401