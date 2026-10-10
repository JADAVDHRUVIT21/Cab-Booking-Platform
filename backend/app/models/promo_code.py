
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PromoCode(Base):
    __tablename__ = "promo_codes"

    __table_args__ = (
        CheckConstraint(
            "discount_percent >= 0 AND discount_percent <= 100",
            name="ck_promo_codes_discount_percent",
        ),
        CheckConstraint(
            "max_uses IS NULL OR max_uses > 0",
            name="ck_promo_codes_max_uses",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    code: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )

    discount_percent: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    max_uses: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    used_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
