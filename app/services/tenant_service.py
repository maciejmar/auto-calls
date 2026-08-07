from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.repositories import tenant_repository


async def resolve_tenant(
    db: AsyncSession, assistant_id: str | None, phone_number_id: str | None
) -> Tenant | None:
    if assistant_id:
        tenant = await tenant_repository.get_by_assistant_id(db, assistant_id)
        if tenant:
            return tenant

    if phone_number_id:
        tenant = await tenant_repository.get_by_phone_number_id(db, phone_number_id)
        if tenant:
            return tenant

    return None
