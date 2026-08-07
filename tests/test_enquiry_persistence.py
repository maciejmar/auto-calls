import json
import uuid
from pathlib import Path

from sqlalchemy import select

from app.models.call import Call
from app.models.enquiry import Enquiry
from app.models.tenant import Tenant
from app.services import extraction_service

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "end_of_call_report.json"


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str):
        self._content = content

    async def create(self, **kwargs):
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content: str):
        self.completions = _FakeCompletions(content)


class FakeOpenAIClient:
    def __init__(self, *args, **kwargs):
        self.chat = _FakeChat(
            '{"name": "Jan Kowalski", "phone": "+48600100200", "email": null, '
            '"topic": "Akt notarialny", "notes": null, "appointment_requested": true}'
        )


async def test_end_to_end_webhook_persists_enquiry(client, db_session, monkeypatch):
    monkeypatch.setattr(extraction_service, "AsyncOpenAI", FakeOpenAIClient)

    tenant = Tenant(id=uuid.uuid4(), name="Kancelaria A", vapi_assistant_id="assistant_a")
    db_session.add(tenant)
    await db_session.commit()

    payload = json.loads(FIXTURE_PATH.read_text())
    payload["message"]["call"]["assistantId"] = "assistant_a"
    payload["message"]["call"]["id"] = "call_e2e_1"

    response = await client.post(
        "/webhooks/vapi",
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "x-vapi-secret": "test-secret"},
    )
    assert response.status_code == 200

    call_result = await db_session.execute(select(Call).where(Call.vapi_call_id == "call_e2e_1"))
    call = call_result.scalar_one()
    assert call.processing_status == "extracted"

    enquiry_result = await db_session.execute(select(Enquiry).where(Enquiry.call_id == call.id))
    enquiry = enquiry_result.scalar_one()
    assert enquiry.name == "Jan Kowalski"
    assert enquiry.appointment_requested is True
    assert enquiry.tenant_id == tenant.id
