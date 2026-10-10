from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RideStatus(str, Enum):
    REQUESTED = "requested"
    MATCHED = "matched"
    ACCEPTED = "accepted"
    DRIVER_ARRIVED = "driver_arrived"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Ride(Base):
    __tablename__ = "rides"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
    )

    rider_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    driver_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    # Pickup and drop-off details
    pickup_address: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    dropoff_address: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    pickup_lat: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    pickup_lng: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    dropoff_lat: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    dropoff_lng: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    # Distance and fare details
    estimated_distance_km: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    estimated_fare: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    final_fare: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    # Promo code and discount details
    promo_code_id: Mapped[int | None] = mapped_column(
        ForeignKey("promo_codes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    original_fare: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    discount_amount: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
    )

    # Ride status
    status: Mapped[RideStatus] = mapped_column(
        SQLEnum(RideStatus),
        nullable=False,
        default=RideStatus.REQUESTED,
        index=True,
    )

    cancellation_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Ride timestamps
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )