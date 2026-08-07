from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant


async def get_by_assistant_id(db: AsyncSession, assistant_id: str) -> Tenant | None:
    result = await db.execute(select(Tenant).where(Tenant.vapi_assistant_id == assistant_id))
    return result.scalar_one_or_none()


async def get_by_phone_number_id(db: AsyncSession, phone_number_id: str) -> Tenant | None:
    result = await db.execute(select(Tenant).where(Tenant.vapi_phone_number_id == phone_number_id))
    return result.scalar_one_or_none()
