from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db
from app.logging import get_logger
from app.schemas.vapi_events import VapiWebhookPayload
from app.security.webhook import verify_vapi_webhook
from app.services import calendar_tool_service, extraction_service, webhook_service

router = APIRouter()
logger = get_logger(__name__)


@router.post("/webhooks/vapi")
async def handle_vapi_webhook(
    request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
) -> Response:
    settings = get_settings()
    raw_body = await request.body()

    if not verify_vapi_webhook(raw_body, dict(request.headers), settings):
        logger.warning("vapi_webhook.invalid_signature")
        return Response(status_code=401)

    try:
        payload = VapiWebhookPayload.model_validate_json(raw_body)
    except ValidationError:
        logger.warning("vapi_webhook.malformed_payload")
        return Response(status_code=200)

    message = payload.message
    logger.info(
        "vapi_webhook.received",
        event_type=message.type,
        vapi_call_id=message.call.id,
        assistant_id=message.call.assistantId,
        phone_number_id=message.call.phoneNumberId,
    )

    if message.type == "end-of-call-report":
        result = await webhook_service.handle_end_of_call_report(db, message)
        if result.outcome == "processed" and result.call_id is not None:
            background_tasks.add_task(extraction_service.process_call, result.call_id)
        return Response(status_code=200)

    if message.type == "tool-calls":
        # Unlike end-of-call-report, the assistant is waiting on this
        # response to speak it back to the caller — must be handled inline,
        # never via BackgroundTasks.
        tool_response = await calendar_tool_service.handle_tool_calls(db, settings, message)
        return JSONResponse(content=tool_response, status_code=200)

    return Response(status_code=200)
