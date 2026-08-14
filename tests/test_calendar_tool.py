import uuid

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models.appointment import Appointment
from app.models.tenant import Tenant
from app.schemas.vapi_events import VapiMessage
from app.services import calendar_tool_service
from app.services.calendar_service import CalendarServiceError

class FakeCalendarService:
    def __init__(self, *, busy: bool = False, check_error=None, create_error=None):
        self.busy = busy
        self.check_error = check_error
        self.create_error = create_error
        self.created: list[tuple] = []
        self.cancelled: list[str] = []

    async def check_availability(self, calendar_id, start, end):
        if self.check_error:
            raise self.check_error
        return not self.busy

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
