import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.repositories import call_repository
from app.schemas.vapi_events import VapiMessage
from app.services import tenant_service

logger = get_logger(__name__)

WebhookOutcome = Literal["processed", "duplicate", "unknown_tenant"]


@dataclass
class WebhookResult:
    outcome: WebhookOutcome
    call_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def handle_end_of_call_report(db: AsyncSession, message: VapiMessage) -> WebhookResult:
    call = message.call
    tenant = await tenant_service.resolve_tenant(db, call.assistantId, call.phoneNumberId)

    if tenant is None:
        logger.warning(
            "vapi_webhook.unknown_tenant",
            assistant_id=call.assistantId,
            phone_number_id=call.phoneNumberId,
        )
        return WebhookResult(outcome="unknown_tenant")

    # Captured up front: insert_if_new may roll back the session on a unique
    # violation (sqlite fallback path), which expires already-loaded ORM
    # attributes and would force a lazy reload outside of an async context.
    tenant_id = tenant.id

    artifact = message.artifact
    analysis = message.analysis

    inserted_call_id = await call_repository.insert_if_new(
        db,
        tenant_id=tenant_id,
        vapi_call_id=call.id,
        caller_phone=call.customer.number if call.customer else None,
        started_at=_parse_dt(call.createdAt),
        ended_at=_parse_dt(call.updatedAt),
        ended_reason=message.endedReason,
        transcript=artifact.transcript if artifact else None,
        summary=analysis.summary if analysis else None,
        recording_url=artifact.recording.stereoUrl if artifact and artifact.recording else None,
        raw_payload=message.model_dump(mode="json"),
    )

    if inserted_call_id is None:
        logger.info("vapi_webhook.duplicate", vapi_call_id=call.id, tenant_id=str(tenant_id))
        return WebhookResult(outcome="duplicate", tenant_id=tenant_id)

    logger.info(
        "vapi_webhook.processed",
        vapi_call_id=call.id,
        tenant_id=str(tenant_id),
        call_id=str(inserted_call_id),
    )
    return WebhookResult(outcome="processed", call_id=inserted_call_id, tenant_id=tenant_id)
