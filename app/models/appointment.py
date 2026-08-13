import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_tenant_id", "tenant_id"),
        Index("ix_appointments_tenant_id_starts_at", "tenant_id", "starts_at", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    # Nullable: a tool-call arrives mid-conversation, before the `calls` row
    # exists (that's only inserted from end-of-call-report). vapi_call_id
    # (not a FK) is the identifier we actually have at booking time and is
    # what ties the appointment back to a call for audit purposes.
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("calls.id"), nullable=True
    )
    vapi_call_id: Mapped[str | None] = mapped_column(String, nullable=True)
    google_event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    client_name: Mapped[str | None] = mapped_column(String, nullable=True)
    client_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    topic: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
