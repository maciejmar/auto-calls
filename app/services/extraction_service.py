import uuid

import httpx
from openai import APIError, APITimeoutError, AsyncOpenAI
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.db import session as db_session_module
from app.logging import get_logger
from app.models.call import Call
from app.repositories import call_repository, enquiry_repository
from app.schemas.enquiry import ExtractedEnquiry

logger = get_logger(__name__)

SYSTEM_PROMPT = (
    "Jestes asystentem ekstrahujacym dane kontaktowe i temat zgloszenia z transkryptu "
    "rozmowy telefonicznej z kancelaria notarialna. Zwracaj wylacznie dane faktycznie "
    "obecne w transkrypcie, w formacie JSON zgodnym z podanym schematem. Jesli danej "
    "informacji brakuje, zwroc null. Nie zmyslaj danych."
)


class ExtractionError(Exception):
    pass


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((APITimeoutError, httpx.TimeoutException)),
)
async def _request_completion(client: AsyncOpenAI, model: str, transcript: str) -> str:
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "ExtractedEnquiry",
                "schema": ExtractedEnquiry.model_json_schema(),
                "strict": True,
            },
        },
    )
    return response.choices[0].message.content


async def extract_enquiry(client: AsyncOpenAI, model: str, transcript: str) -> ExtractedEnquiry:
    try:
        content = await _request_completion(client, model, transcript)
    except (APIError, httpx.TimeoutException) as exc:
        raise ExtractionError(f"OpenAI request failed: {exc}") from exc

    try:
        return ExtractedEnquiry.model_validate_json(content)
    except ValidationError as exc:
        raise ExtractionError(f"LLM returned an invalid enquiry payload: {exc}") from exc


async def process_call(call_id: uuid.UUID) -> None:
    settings = get_settings()

    async with db_session_module.async_session_factory() as db:
        call = await db.get(Call, call_id)
        if call is None:
            logger.warning("extraction.call_not_found", call_id=str(call_id))
            return

        if not call.transcript:
            logger.warning("extraction.empty_transcript", call_id=str(call_id))
            await call_repository.update_processing_status(db, call.id, "failed")
            return

        try:
            client = AsyncOpenAI(
                api_key=settings.openai_api_key, timeout=httpx.Timeout(10.0, connect=10.0)
            )
            extracted = await extract_enquiry(client, settings.openai_model, call.transcript)
        except Exception:
            # process_call is the top-level entry point for a BackgroundTask:
            # nothing above it will catch an escaped exception, so any
            # failure (bad credentials, network error, invalid LLM output)
            # must resolve to a "failed" status rather than propagate.
            logger.exception("extraction.failed", call_id=str(call_id))
            await call_repository.update_processing_status(db, call.id, "failed")
            return

        await enquiry_repository.create(
            db,
            tenant_id=call.tenant_id,
            call_id=call.id,
            name=extracted.name,
            phone=extracted.phone,
            email=extracted.email,
            topic=extracted.topic,
            notes=extracted.notes,
            appointment_requested=extracted.appointment_requested,
        )
        await call_repository.update_processing_status(db, call.id, "extracted")
        logger.info("extraction.processed", call_id=str(call_id))
