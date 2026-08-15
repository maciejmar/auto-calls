from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.logging import get_logger
from app.models.tenant import Tenant
from app.repositories import appointment_repository
from app.schemas.calendar import CheckAvailabilityArgs, CreateAppointmentArgs
from app.schemas.vapi_events import VapiMessage
from app.services import tenant_service
from app.services.calendar_service import CalendarService, CalendarServiceError, get_calendar_service

logger = get_logger(__name__)

GENERIC_ERROR_MESSAGE = "Przepraszam, nie udało się teraz sprawdzić kalendarza. Proszę spróbować za chwilę."
INVALID_ARGS_MESSAGE = "Nie udało się odczytać podanej daty lub godziny. Proszę podać termin ponownie."
NO_CALENDAR_MESSAGE = (
    "Kancelaria nie ma jeszcze skonfigurowanego kalendarza online — zapiszę zgłoszenie, "
    "a pracownik skontaktuje się w sprawie terminu."
)
NO_SLOTS_MESSAGE = (
    "Nie mam w tej chwili żadnych wolnych terminów w najbliższych dwóch tygodniach. "
    "Zapiszę zgłoszenie, a pracownik skontaktuje się w sprawie terminu."
)
UNKNOWN_TOOL_MESSAGE = "To narzędzie nie jest jeszcze obsługiwane."
UNKNOWN_TENANT_MESSAGE = "Przepraszam, wystąpił problem techniczny. Proszę spróbować później."

MAX_SUGGESTED_SLOTS = 5
MIN_LEAD_TIME = timedelta(minutes=60)
SLOT_SEARCH_HORIZONS_DAYS = (7, 14)
_WEEKDAY_PL = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]


def _parse_slot(tenant: Tenant, date: str, time: str) -> tuple[datetime, datetime] | None:
    try:
        tz = ZoneInfo(tenant.timezone)
        start = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    except ValueError:
        return None
    end = start + timedelta(minutes=tenant.appointment_duration_minutes)
    return start, end


def _within_business_hours(tenant: Tenant, start: datetime) -> bool:
    if start.weekday() >= 5:  # sobota/niedziela
        return False
    slot_time = start.strftime("%H:%M")
    return tenant.business_hours_start <= slot_time < tenant.business_hours_end


def _business_hours_message(tenant: Tenant) -> str:
    return (
        f"Kancelaria pracuje od poniedziałku do piątku w godzinach "
        f"{tenant.business_hours_start}–{tenant.business_hours_end}. Proszę zaproponować inny termin."
    )


def _format_pl(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y o %H:%M")


def _format_pl_with_weekday(dt: datetime) -> str:
    return f"{_WEEKDAY_PL[dt.weekday()]} {dt.strftime('%d.%m.%Y')} o {dt.strftime('%H:%M')}"


def _free_slots_for_range(
    tenant: Tenant,
    busy_intervals: list[tuple[str, str]],
    range_start: datetime,
    range_end: datetime,
) -> list[datetime]:
    """Business-hours slots of tenant.appointment_duration_minutes, grid-aligned
    to business_hours_start each day, that don't overlap any busy interval and
    fall within [range_start, range_end). Weekends are skipped."""
    tz = ZoneInfo(tenant.timezone)
    busy = [
        (datetime.fromisoformat(start).astimezone(tz), datetime.fromisoformat(end).astimezone(tz))
        for start, end in busy_intervals
    ]
    duration = timedelta(minutes=tenant.appointment_duration_minutes)
    business_start = datetime.strptime(tenant.business_hours_start, "%H:%M").time()
    business_end = datetime.strptime(tenant.business_hours_end, "%H:%M").time()

    slots: list[datetime] = []
    day = range_start.date()
    while day <= range_end.date():
        if day.weekday() < 5:  # pon-pt
            day_start = datetime.combine(day, business_start, tzinfo=tz)
            day_end = datetime.combine(day, business_end, tzinfo=tz)

            cursor = max(day_start, range_start)
            grid_offset = (cursor - day_start) % duration
            if grid_offset:
                cursor += duration - grid_offset

            while cursor + duration <= day_end and cursor <= range_end:
                slot_end = cursor + duration
                if not any(cursor < b_end and slot_end > b_start for b_start, b_end in busy):
                    slots.append(cursor)
                cursor += duration
        day += timedelta(days=1)

    return slots


def _resolve_slot_or_message(tenant: Tenant, date: str, time: str) -> tuple[datetime, datetime] | str:
    slot = _parse_slot(tenant, date, time)
    if slot is None:
        return INVALID_ARGS_MESSAGE
    start, _ = slot
    if not _within_business_hours(tenant, start):
        return _business_hours_message(tenant)
    return slot


async def _check_availability(
    service: CalendarService | None, tenant: Tenant, raw_args: dict
) -> str:
    try:
        args = CheckAvailabilityArgs.model_validate(raw_args)
    except ValidationError:
        return INVALID_ARGS_MESSAGE

    slot = _resolve_slot_or_message(tenant, args.date, args.time)
    if isinstance(slot, str):
        return slot
    start, end = slot

    if service is None:
        return NO_CALENDAR_MESSAGE

    try:
        available = await service.check_availability(tenant.calendar_id, start.isoformat(), end.isoformat())
    except CalendarServiceError:
        logger.exception("calendar_tool.check_availability_failed", tenant_id=str(tenant.id))
        return GENERIC_ERROR_MESSAGE

    if available:
        return f"Termin {_format_pl(start)} jest wolny."
    return f"Termin {_format_pl(start)} jest niestety zajęty. Proszę zaproponować inny termin."


async def _create_appointment(
    db: AsyncSession,
    service: CalendarService | None,
    tenant: Tenant,
    vapi_call_id: str,
    raw_args: dict,
) -> str:
    try:
        args = CreateAppointmentArgs.model_validate(raw_args)
    except ValidationError:
        return INVALID_ARGS_MESSAGE

    slot = _resolve_slot_or_message(tenant, args.date, args.time)
    if isinstance(slot, str):
        return slot
    start, end = slot

    if service is None:
        return NO_CALENDAR_MESSAGE

    # Captured up front: insert_if_available may roll back the session on a
    # unique violation (sqlite fallback path, mirroring
    # call_repository.insert_if_new), which expires already-loaded ORM
    # attributes on `tenant` and would force a lazy reload outside of an
    # async context.
    tenant_id = tenant.id
    calendar_id = tenant.calendar_id

    try:
        still_available = await service.check_availability(calendar_id, start.isoformat(), end.isoformat())
    except CalendarServiceError:
        logger.exception("calendar_tool.recheck_failed", tenant_id=str(tenant_id))
        return GENERIC_ERROR_MESSAGE

    if not still_available:
        return f"Termin {_format_pl(start)} jest niestety zajęty. Proszę zaproponować inny termin."

    summary = f"Spotkanie: {args.client_name or 'klient kancelarii'}"
    description = "\n".join(
        part
        for part in (
            f"Temat: {args.topic}" if args.topic else None,
            f"Telefon: {args.client_phone}" if args.client_phone else None,
        )
        if part
    )

    try:
        google_event_id = await service.create_appointment(
            calendar_id, start.isoformat(), end.isoformat(), summary, description
        )
    except CalendarServiceError:
        logger.exception("calendar_tool.create_failed", tenant_id=str(tenant_id))
        return GENERIC_ERROR_MESSAGE

    inserted_id = await appointment_repository.insert_if_available(
        db,
        tenant_id=tenant_id,
        call_id=None,
        vapi_call_id=vapi_call_id,
        google_event_id=google_event_id,
        starts_at=start,
        ends_at=end,
        client_name=args.client_name,
        client_phone=args.client_phone,
        topic=args.topic,
    )

    if inserted_id is None:
        # Lost a race against another booking for the same slot between the
        # freeBusy re-check and our DB write — undo the Google event we just
        # created so we don't leave an orphaned booking behind.
        try:
            await service.cancel_appointment(calendar_id, google_event_id)
        except CalendarServiceError:
            logger.exception("calendar_tool.rollback_failed", tenant_id=str(tenant_id))
        return f"Termin {_format_pl(start)} jest niestety zajęty. Proszę zaproponować inny termin."

    logger.info(
        "calendar_tool.appointment_created", tenant_id=str(tenant_id), appointment_id=str(inserted_id)
    )
    return f"Termin został zarezerwowany na {_format_pl(start)}."


async def _list_available_slots(service: CalendarService | None, tenant: Tenant) -> str:
    if service is None:
        return NO_CALENDAR_MESSAGE

    tz = ZoneInfo(tenant.timezone)
    now = datetime.now(tz)
    slots: list[datetime] = []

    for horizon_days in SLOT_SEARCH_HORIZONS_DAYS:
        range_start = now + MIN_LEAD_TIME
        range_end = now + timedelta(days=horizon_days)
        try:
            busy = await service.list_busy_intervals(
                tenant.calendar_id, range_start.isoformat(), range_end.isoformat()
            )
        except CalendarServiceError:
            logger.exception("calendar_tool.list_slots_failed", tenant_id=str(tenant.id))
            return GENERIC_ERROR_MESSAGE

        slots = _free_slots_for_range(tenant, busy, range_start, range_end)
        if slots:
            break

    if not slots:
        return NO_SLOTS_MESSAGE

    proposals = ", ".join(_format_pl_with_weekday(slot) for slot in slots[:MAX_SUGGESTED_SLOTS])
    return f"Mam wolne następujące terminy: {proposals}."


async def handle_tool_calls(db: AsyncSession, settings: Settings, message: VapiMessage) -> dict:
    tenant = await tenant_service.resolve_tenant(db, message.call.assistantId, message.call.phoneNumberId)
    service = (
        get_calendar_service(tenant, settings.google_service_account_file)
        if tenant is not None and tenant.calendar_provider == "google" and tenant.calendar_id
        else None
    )

    results = []
    for tool_call in message.toolCallList or []:
        if tenant is None:
            result_text = UNKNOWN_TENANT_MESSAGE
        elif tool_call.tool_name == "check_availability":
            result_text = await _check_availability(service, tenant, tool_call.tool_arguments)
        elif tool_call.tool_name == "create_appointment":
            result_text = await _create_appointment(
                db, service, tenant, message.call.id, tool_call.tool_arguments
            )
        elif tool_call.tool_name == "list_available_slots":
            result_text = await _list_available_slots(service, tenant)
        else:
            result_text = UNKNOWN_TOOL_MESSAGE

        results.append({"toolCallId": tool_call.id, "result": result_text})

    return {"results": results}
