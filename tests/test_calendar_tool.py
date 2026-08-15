import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models.appointment import Appointment
from app.models.tenant import Tenant
from app.schemas.vapi_events import VapiMessage
from app.services import calendar_tool_service
from app.services.calendar_service import CalendarServiceError


class FakeCalendarService:
    def __init__(
        self, *, busy: bool = False, check_error=None, create_error=None, busy_factory=None
    ):
        self.busy = busy
        self.check_error = check_error
        self.create_error = create_error
        self.created: list[tuple] = []
        self.cancelled: list[str] = []
        self.list_calls: list[tuple] = []
        # busy_factory(start, end, call_index) -> list[(start, end)]; call_index
        # starts at 1, lets a test give different answers per horizon (7d/14d).
        self.busy_factory = busy_factory or (lambda start, end, call_index: [])

    async def check_availability(self, calendar_id, start, end):
        if self.check_error:
            raise self.check_error
        return not self.busy

    async def list_busy_intervals(self, calendar_id, start, end):
        if self.check_error:
            raise self.check_error
        self.list_calls.append((start, end))
        return self.busy_factory(start, end, len(self.list_calls))

    async def create_appointment(self, calendar_id, start, end, summary, description=""):
        if self.create_error:
            raise self.create_error
        event_id = f"evt_{len(self.created) + 1}"
        self.created.append((calendar_id, start, end, summary, description))
        return event_id

    async def cancel_appointment(self, calendar_id, appointment_id):
        self.cancelled.append(appointment_id)


async def _make_tenant(db_session, *, name="Kancelaria A", assistant_id="assistant_a") -> Tenant:
    tenant = Tenant(
        id=uuid.uuid4(),
        name=name,
        vapi_assistant_id=assistant_id,
        calendar_provider="google",
        calendar_id="kancelaria-a@group.calendar.google.com",
    )
    db_session.add(tenant)
    await db_session.commit()
    return tenant


def _tool_call_message(tool_name: str, arguments: dict, *, assistant_id="assistant_a", call_id="call_live_1"):
    return VapiMessage.model_validate(
        {
            "type": "tool-calls",
            "call": {"id": call_id, "assistantId": assistant_id, "customer": {"number": "+48600100200"}},
            "toolCallList": [{"id": "toolu_1", "name": tool_name, "arguments": arguments}],
        }
    )


def _install_fake_service(monkeypatch, fake_service):
    monkeypatch.setattr(calendar_tool_service, "get_calendar_service", lambda tenant, path: fake_service)


async def test_check_availability_free_slot_reports_free(db_session, monkeypatch):
    tenant = await _make_tenant(db_session)
    fake = FakeCalendarService(busy=False)
    _install_fake_service(monkeypatch, fake)

    message = _tool_call_message("check_availability", {"date": "2026-08-14", "time": "11:00"})
    result = await calendar_tool_service.handle_tool_calls(db_session, get_settings(), message)

    assert result["results"][0]["toolCallId"] == "toolu_1"
    assert "wolny" in result["results"][0]["result"]


async def test_check_availability_busy_slot_reports_busy(db_session, monkeypatch):
    await _make_tenant(db_session)
    fake = FakeCalendarService(busy=True)
    _install_fake_service(monkeypatch, fake)

    message = _tool_call_message("check_availability", {"date": "2026-08-14", "time": "11:00"})
    result = await calendar_tool_service.handle_tool_calls(db_session, get_settings(), message)

    assert "zajęty" in result["results"][0]["result"]


async def test_check_availability_outside_business_hours(db_session, monkeypatch):
    await _make_tenant(db_session)
    _install_fake_service(monkeypatch, FakeCalendarService())

    # 2026-08-15 is a Saturday.
    message = _tool_call_message("check_availability", {"date": "2026-08-15", "time": "11:00"})
    result = await calendar_tool_service.handle_tool_calls(db_session, get_settings(), message)

    assert "godzinach" in result["results"][0]["result"]


async def test_check_availability_invalid_date_format(db_session, monkeypatch):
    await _make_tenant(db_session)
    _install_fake_service(monkeypatch, FakeCalendarService())

    message = _tool_call_message("check_availability", {"date": "14 sierpnia", "time": "11:00"})
    result = await calendar_tool_service.handle_tool_calls(db_session, get_settings(), message)

    assert "Nie udało się odczytać" in result["results"][0]["result"]


async def test_create_appointment_success_persists_row(db_session, monkeypatch):
    tenant = await _make_tenant(db_session)
    fake = FakeCalendarService(busy=False)
    _install_fake_service(monkeypatch, fake)

    message = _tool_call_message(
        "create_appointment",
        {"date": "2026-08-14", "time": "11:00", "client_name": "Jan Kowalski", "client_phone": "+48600100200"},
    )
    result = await calendar_tool_service.handle_tool_calls(db_session, get_settings(), message)

    assert "zarezerwowany" in result["results"][0]["result"]
    assert len(fake.created) == 1

    row = (
        await db_session.execute(select(Appointment).where(Appointment.tenant_id == tenant.id))
    ).scalar_one()
    assert row.client_name == "Jan Kowalski"
    assert row.vapi_call_id == "call_live_1"
    assert row.google_event_id == "evt_1"


async def test_create_appointment_conflict_is_rejected_without_booking(db_session, monkeypatch):
    await _make_tenant(db_session)
    fake = FakeCalendarService(busy=True)
    _install_fake_service(monkeypatch, fake)

    message = _tool_call_message("create_appointment", {"date": "2026-08-14", "time": "11:00"})
    result = await calendar_tool_service.handle_tool_calls(db_session, get_settings(), message)

    assert "zajęty" in result["results"][0]["result"]
    assert len(fake.created) == 0


async def test_create_appointment_db_race_rolls_back_google_event(db_session, monkeypatch):
    tenant = await _make_tenant(db_session)
    # insert_if_available's sqlite fallback rolls back the session on the
    # second (conflicting) insert, which expires all ORM attributes on
    # `tenant` for the rest of this session — capture the id now.
    tenant_id = tenant.id
    # Google reports the slot as free both times (simulates two concurrent
    # calls racing on freeBusy), but our own idempotency guard must still
    # catch the second write and undo the Google event it just created.
    fake = FakeCalendarService(busy=False)
    _install_fake_service(monkeypatch, fake)

    message = _tool_call_message("create_appointment", {"date": "2026-08-14", "time": "11:00"}, call_id="call_1")
    first = await calendar_tool_service.handle_tool_calls(db_session, get_settings(), message)
    assert "zarezerwowany" in first["results"][0]["result"]

    message_2 = _tool_call_message("create_appointment", {"date": "2026-08-14", "time": "11:00"}, call_id="call_2")
    second = await calendar_tool_service.handle_tool_calls(db_session, get_settings(), message_2)

    assert "zajęty" in second["results"][0]["result"]
    assert fake.cancelled == ["evt_2"]

    count = (
        await db_session.execute(select(Appointment).where(Appointment.tenant_id == tenant_id))
    ).scalars()
    assert len(list(count)) == 1


async def test_create_appointment_google_api_error_returns_generic_message(db_session, monkeypatch):
    await _make_tenant(db_session)
    fake = FakeCalendarService(busy=False, create_error=CalendarServiceError("boom"))
    _install_fake_service(monkeypatch, fake)

    message = _tool_call_message("create_appointment", {"date": "2026-08-14", "time": "11:00"})
    result = await calendar_tool_service.handle_tool_calls(db_session, get_settings(), message)

    assert "nie udało się" in result["results"][0]["result"]


async def test_unknown_tenant_returns_generic_message_without_crashing(db_session, monkeypatch):
    _install_fake_service(monkeypatch, FakeCalendarService())

    message = _tool_call_message(
        "check_availability", {"date": "2026-08-14", "time": "11:00"}, assistant_id="does-not-exist"
    )
    result = await calendar_tool_service.handle_tool_calls(db_session, get_settings(), message)

    assert result["results"][0]["toolCallId"] == "toolu_1"
    assert "problem techniczny" in result["results"][0]["result"]


async def test_webhook_tool_calls_end_to_end(client, db_session, monkeypatch):
    await _make_tenant(db_session)
    _install_fake_service(monkeypatch, FakeCalendarService(busy=False))

    payload = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "call_live_2", "assistantId": "assistant_a"},
            "toolCallList": [
                {"id": "toolu_9", "name": "check_availability", "arguments": {"date": "2026-08-14", "time": "11:00"}}
            ],
        }
    }
    response = await client.post(
        "/webhooks/vapi",
        json=payload,
        headers={"x-vapi-secret": "test-secret"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["toolCallId"] == "toolu_9"
    assert "wolny" in body["results"][0]["result"]


def test_tool_call_supports_nested_function_shape():
    from app.schemas.vapi_events import VapiToolCall

    nested = VapiToolCall.model_validate(
        {"id": "t1", "function": {"name": "check_availability", "arguments": {"date": "2026-08-14"}}}
    )
    assert nested.tool_name == "check_availability"
    assert nested.tool_arguments == {"date": "2026-08-14"}

    flat = VapiToolCall.model_validate(
        {"id": "t2", "name": "create_appointment", "arguments": {"date": "2026-08-14"}}
    )
    assert flat.tool_name == "create_appointment"


def _tenant_obj(**overrides) -> Tenant:
    defaults = dict(
        id=uuid.uuid4(),
        name="Kancelaria A",
        vapi_assistant_id="assistant_a",
        calendar_provider="google",
        calendar_id="kancelaria-a@group.calendar.google.com",
        timezone="Europe/Warsaw",
        business_hours_start="09:00",
        business_hours_end="17:00",
        appointment_duration_minutes=30,
    )
    defaults.update(overrides)
    return Tenant(**defaults)


# 2026-08-14 is a Friday, 2026-08-15/16 a weekend, 2026-08-17 a Monday.
_TZ = ZoneInfo("Europe/Warsaw")
_RANGE_START = datetime(2026, 8, 14, 16, 10, tzinfo=_TZ)  # Friday, not grid-aligned
_RANGE_END = datetime(2026, 8, 18, 11, 45, tzinfo=_TZ)  # Tuesday


def test_free_slots_for_range_skips_weekend_and_snaps_to_grid():
    tenant = _tenant_obj()

    slots = calendar_tool_service._free_slots_for_range(tenant, [], _RANGE_START, _RANGE_END)

    # Friday: only 16:10 onward is in range, but slots are grid-aligned to
    # 09:00 in 30-minute steps, so the first candidate is 16:30, not 16:10.
    assert datetime(2026, 8, 14, 16, 30, tzinfo=_TZ) in slots
    assert datetime(2026, 8, 14, 16, 10, tzinfo=_TZ) not in slots
    # No weekend slots.
    assert all(slot.date() not in (date(2026, 8, 15), date(2026, 8, 16)) for slot in slots)
    # Friday (1 slot) + Monday (16 slots, full 09:00-17:00) + Tuesday up to
    # 11:45 (6 slots: 09:00..11:30) = 23.
    assert len(slots) == 23


def test_free_slots_for_range_excludes_busy_interval():
    tenant = _tenant_obj()
    monday_fully_busy = [("2026-08-17T09:00:00+02:00", "2026-08-17T17:00:00+02:00")]

    slots = calendar_tool_service._free_slots_for_range(
        tenant, monday_fully_busy, _RANGE_START, _RANGE_END
    )

    assert all(slot.date() != date(2026, 8, 17) for slot in slots)
    assert datetime(2026, 8, 14, 16, 30, tzinfo=_TZ) in slots  # Friday untouched
    assert len(slots) == 23 - 16  # the 16 Monday slots are gone


async def test_list_available_slots_no_calendar_configured(db_session):
    tenant = await _make_tenant(db_session)
    tenant.calendar_provider = "none"

    result = await calendar_tool_service._list_available_slots(None, tenant)

    assert result == calendar_tool_service.NO_CALENDAR_MESSAGE


async def test_list_available_slots_google_error_returns_generic_message(db_session):
    tenant = await _make_tenant(db_session)
    fake = FakeCalendarService(check_error=CalendarServiceError("boom"))

    result = await calendar_tool_service._list_available_slots(fake, tenant)

    assert result == calendar_tool_service.GENERIC_ERROR_MESSAGE


async def test_list_available_slots_returns_proposals(db_session):
    tenant = await _make_tenant(db_session)
    fake = FakeCalendarService()  # busy_factory defaults to "nothing busy"

    result = await calendar_tool_service._list_available_slots(fake, tenant)

    assert result.startswith("Mam wolne następujące terminy:")
    assert any(day in result for day in calendar_tool_service._WEEKDAY_PL)
    assert len(fake.list_calls) == 1  # first (7-day) horizon already had slots


async def test_list_available_slots_widens_to_two_weeks_when_first_week_full(db_session):
    tenant = await _make_tenant(db_session)

    def busy_factory(start, end, call_index):
        # First call (7-day horizon): mark the whole queried range busy.
        # Second call (14-day horizon): leave it free.
        return [(start, end)] if call_index == 1 else []

    fake = FakeCalendarService(busy_factory=busy_factory)

    result = await calendar_tool_service._list_available_slots(fake, tenant)

    assert result.startswith("Mam wolne następujące terminy:")
    assert len(fake.list_calls) == 2


async def test_list_available_slots_no_availability_even_after_widening(db_session):
    tenant = await _make_tenant(db_session)
    fake = FakeCalendarService(busy_factory=lambda start, end, call_index: [(start, end)])

    result = await calendar_tool_service._list_available_slots(fake, tenant)

    assert result == calendar_tool_service.NO_SLOTS_MESSAGE
    assert len(fake.list_calls) == 2


async def test_webhook_list_available_slots_end_to_end(db_session, monkeypatch):
    await _make_tenant(db_session)
    _install_fake_service(monkeypatch, FakeCalendarService())

    message = _tool_call_message("list_available_slots", {})
    result = await calendar_tool_service.handle_tool_calls(db_session, get_settings(), message)

    assert result["results"][0]["toolCallId"] == "toolu_1"
    assert "Mam wolne następujące terminy" in result["results"][0]["result"]
