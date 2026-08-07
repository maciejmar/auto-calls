import uuid
from datetime import datetime

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call


async def insert_if_new(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    vapi_call_id: str,
    caller_phone: str | None,
    started_at: datetime | None,
    ended_at: datetime | None,
    ended_reason: str | None,
    transcript: str | None,
    summary: str | None,
    recording_url: str | None,
    raw_payload: dict,
) -> uuid.UUID | None:
    new_id = uuid.uuid4()
    values = dict(
        id=new_id,
        tenant_id=tenant_id,
        vapi_call_id=vapi_call_id,
        caller_phone=caller_phone,
        started_at=started_at,
        ended_at=ended_at,
        ended_reason=ended_reason,
        transcript=transcript,
        summary=summary,
        recording_url=recording_url,
        raw_payload=raw_payload,
        processing_status="received",
    )

    # ON CONFLICT DO NOTHING is Postgres-specific (production target); other
    # dialects (e.g. sqlite in tests) fall back to catching the unique
    # violation, which gives the same idempotency guarantee.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        stmt = pg_insert(Call).values(**values).on_conflict_do_nothing(index_elements=["vapi_call_id"])
        await db.execute(stmt)
        await db.commit()
    else:
        try:
            await db.execute(insert(Call).values(**values))
            await db.commit()
        except IntegrityError:
            await db.rollback()

    result = await db.execute(select(Call.id).where(Call.vapi_call_id == vapi_call_id))
    existing_id = result.scalar_one()
    return new_id if existing_id == new_id else None


async def update_processing_status(db: AsyncSession, call_id: uuid.UUID, status: str) -> None:
    call = await db.get(Call, call_id)
    if call is not None:
        call.processing_status = status
        await db.commit()
