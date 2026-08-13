import uuid
from datetime import datetime

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment


async def insert_if_available(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID | None,
    vapi_call_id: str | None,
    google_event_id: str | None,
    starts_at: datetime,
    ends_at: datetime,
    client_name: str | None,
    client_phone: str | None,
    topic: str | None,
) -> uuid.UUID | None:
    new_id = uuid.uuid4()
    values = dict(
        id=new_id,
        tenant_id=tenant_id,
        call_id=call_id,
        vapi_call_id=vapi_call_id,
        google_event_id=google_event_id,
        starts_at=starts_at,
        ends_at=ends_at,
        client_name=client_name,
        client_phone=client_phone,
        topic=topic,
        status="confirmed",
    )

    # Same idempotency pattern as call_repository.insert_if_new: Postgres
    # uses ON CONFLICT DO NOTHING against the unique (tenant_id, starts_at)
    # index; other dialects (sqlite in tests) fall back to catching the
    # unique violation for the same guarantee.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        stmt = (
            pg_insert(Appointment)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["tenant_id", "starts_at"])
        )
        await db.execute(stmt)
        await db.commit()
    else:
        try:
            await db.execute(insert(Appointment).values(**values))
            await db.commit()
        except IntegrityError:
            await db.rollback()

    result = await db.execute(
        select(Appointment.id).where(
            Appointment.tenant_id == tenant_id, Appointment.starts_at == starts_at
        )
    )
    existing_id = result.scalar_one_or_none()
    return new_id if existing_id == new_id else None
