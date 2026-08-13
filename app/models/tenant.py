import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    vapi_assistant_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    vapi_phone_number_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    calendar_provider: Mapped[str] = mapped_column(String, nullable=False, default="none")
    calendar_id: Mapped[str | None] = mapped_column(String, nullable=True)
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="Europe/Warsaw")
    business_hours_start: Mapped[str] = mapped_column(String, nullable=False, default="09:00")
    business_hours_end: Mapped[str] = mapped_column(String, nullable=False, default="17:00")
    appointment_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
