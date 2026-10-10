from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, get_db
from app.main import app
from app.models.promo_code import PromoCode
from app.models.user import User, UserRole
from app.utils.auth import get_current_user
from tests.test_auth_api import TestingSessionLocal, test_engine


@pytest.fixture()
def promo_client():
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


def create_user(db, email, role):
    user = User(
        name=f"Test {role}",
        email=email,
        password_hash="unused-test-password-hash",
        role=role,
        rating_avg=0.0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_promo(
    db,
    code="TEST20",
    discount_percent=20,
    max_uses=5,
    used_count=0,
    is_active=True,
):
    promo = PromoCode(
        code=code,
        discount_percent=discount_percent,
        max_uses=max_uses,
        used_count=used_count,
        is_active=is_active,
    )
    db.add(promo)
    db.commit()
    db.refresh(promo)
    return promo


def make_user_for_request(user):
    """Copy the required user fields before the DB session closes."""
    return SimpleNamespace(
        id=user.id,
        role=user.role,
        email=user.email,
        name=user.name,
    )


def test_rider_can_preview_valid_promo(promo_client):
    db = TestingSessionLocal()
    try:
        rider = create_user(
            db,
            "promo-rider@example.com",
            UserRole.RIDER,
        )
        rider_for_request = make_user_for_request(rider)
        create_promo(db)
    finally:
        db.close()

    app.dependency_overrides[get_current_user] = (
        lambda: rider_for_request
    )

    try:
        response = promo_client.post(
            "/api/promo-codes/apply",
            json={"code": "test20", "fare": 500},
        )

        assert response.status_code == 200

        data = response.json()
        assert data["code"] == "TEST20"
        assert data["original_fare"] == 500
        assert data["discount_percent"] == 20
        assert data["discount_amount"] == 100
        assert data["final_fare"] == 400

        # Previewing a promo must not consume a use.
        db = TestingSessionLocal()
        try:
            promo = (
                db.query(PromoCode)
                .filter_by(code="TEST20")
                .one()
            )
            assert promo.used_count == 0
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    (
        "code",
        "active",
        "used_count",
        "max_uses",
        "expected_status",
    ),
    [
        ("MISSING", True, 0, 5, 404),
        ("INACTIVE", False, 0, 5, 400),
        ("EXHAUSTED", True, 5, 5, 400),
    ],
)
def test_invalid_promo_is_rejected(
    promo_client,
    code,
    active,
    used_count,
    max_uses,
    expected_status,
):
    db = TestingSessionLocal()
    try:
        rider = create_user(
            db,
            "promo-validation-rider@example.com",
            UserRole.RIDER,
        )
        rider_for_request = make_user_for_request(rider)

        if code != "MISSING":
            create_promo(
                db,
                code=code,
                is_active=active,
                used_count=used_count,
                max_uses=max_uses,
            )
    finally:
        db.close()

    app.dependency_overrides[get_current_user] = (
        lambda: rider_for_request
    )

    try:
        response = promo_client.post(
            "/api/promo-codes/apply",
            json={"code": code, "fare": 500},
        )

        assert response.status_code == expected_status
    finally:
        app.dependency_overrides.clear()