import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enquiry import Enquiry


async def create(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    name: str | None,
    phone: str | None,
    email: str | None,
    topic: str | None,
    notes: str | None,
    appointment_requested: bool,
) -> Enquiry:
    enquiry = Enquiry(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        call_id=call_id,
        name=name,
        phone=phone,
        email=email,
        topic=topic,
        notes=notes,
        appointment_requested=appointment_requested,
    )
    db.add(enquiry)
    await db.commit()
    return enquiry
