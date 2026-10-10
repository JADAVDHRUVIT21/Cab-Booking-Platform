from enum import Enum

from sqlalchemy import Boolean, Float, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserRole(str, Enum):
    RIDER = "rider"
    DRIVER = "driver"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False
    )

    phone: Mapped[str | None] = mapped_column(
        String(20),
        unique=True,
        nullable=True
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    role: Mapped[UserRole] = mapped_column(
        SQLEnum(UserRole),
        nullable=False,
        default=UserRole.RIDER
    )

    profile_pic: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )

    rating_avg: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0
    )

    is_available: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False
    )

    current_lat: Mapped[float | None] = mapped_column(
        Float,
        nullable=True
    )

    current_lng: Mapped[float | None] = mapped_column(
        Float,
        nullable=True
    )

    vehicle_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    vehicle_model: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True
    )

    vehicle_number: Mapped[str | None] = mapped_column(
        String(50),
        unique=True,
        nullable=True
    )