
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.promo_code import PromoCode
from app.models.user import User, UserRole
from app.schemas.promo_code import (
    PromoCodeApply,
    PromoCodeApplyResponse,
    PromoCodeCreate,
    PromoCodeResponse,
)
from app.utils.auth import get_current_user
from app.utils.permissions import get_current_admin


router = APIRouter(
    prefix="/api/promo-codes",
    tags=["Promo Codes"],
)


def normalize_code(code: str) -> str:
    return code.strip().upper()


def validate_promo_code(db: Session, code: str) -> PromoCode:
    normalized_code = normalize_code(code)

    promo = (
        db.query(PromoCode)
        .filter(PromoCode.code == normalized_code)
        .first()
    )

    if promo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promo code not found",
        )

    if not promo.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Promo code is inactive",
        )

    now = datetime.now(timezone.utc)

    if promo.expires_at is not None:
        expires_at = promo.expires_at

        # PostgreSQL may return a naive datetime for a timezone-naive column.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at <= now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Promo code has expired",
            )

    if promo.max_uses is not None and promo.used_count >= promo.max_uses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Promo code usage limit reached",
        )

    return promo


@router.post(
    "",
    response_model=PromoCodeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_promo_code(
    payload: PromoCodeCreate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    code = normalize_code(payload.code)

    existing = (
        db.query(PromoCode)
        .filter(PromoCode.code == code)
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Promo code already exists",
        )

    if payload.expires_at is not None:
        expires_at = payload.expires_at

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at <= datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Expiration must be in the future",
            )

    promo = PromoCode(
        code=code,
        discount_percent=payload.discount_percent,
        max_uses=payload.max_uses,
        expires_at=payload.expires_at,
        is_active=True,
        used_count=0,
    )

    db.add(promo)

    try:
        db.commit()
        db.refresh(promo)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Promo code already exists",
        )

    return promo


@router.get(
    "",
    response_model=list[PromoCodeResponse],
)
def list_promo_codes(
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    return (
        db.query(PromoCode)
        .order_by(PromoCode.id.desc())
        .all()
    )


@router.patch(
    "/{promo_id}/status",
    response_model=PromoCodeResponse,
)
def update_promo_status(
    promo_id: int,
    is_active: bool,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    promo = (
        db.query(PromoCode)
        .filter(PromoCode.id == promo_id)
        .first()
    )

    if promo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promo code not found",
        )

    promo.is_active = is_active
    db.commit()
    db.refresh(promo)

    return promo


@router.post(
    "/apply",
    response_model=PromoCodeApplyResponse,
)
def apply_promo_code(
    payload: PromoCodeApply,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != UserRole.RIDER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only riders can apply promo codes",
        )

    promo = validate_promo_code(db, payload.code)

    original_fare = round(payload.fare, 2)
    discount_amount = round(
        original_fare * promo.discount_percent / 100,
        2,
    )
    final_fare = round(
        max(0, original_fare - discount_amount),
        2,
    )

    # This endpoint previews the discount only.
    # Usage is not incremented until redemption is connected
    # to the ride/payment workflow.

    return PromoCodeApplyResponse(
        code=promo.code,
        original_fare=original_fare,
        discount_percent=promo.discount_percent,
        discount_amount=discount_amount,
        final_fare=final_fare,
    )
