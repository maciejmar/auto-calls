import json
import uuid
from pathlib import Path

from sqlalchemy import func, select

from app.models.call import Call
from app.models.tenant import Tenant

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "end_of_call_report.json"


def _load_fixture(assistant_id: str, vapi_call_id: str) -> bytes:
    payload = json.loads(FIXTURE_PATH.read_text())
    payload["message"]["call"]["assistantId"] = assistant_id
    payload["message"]["call"]["id"] = vapi_call_id
    return json.dumps(payload).encode()


async def test_duplicate_webhook_delivery_is_not_reprocessed(client, db_session):
    tenant = Tenant(id=uuid.uuid4(), name="Kancelaria A", vapi_assistant_id="assistant_a")
    db_session.add(tenant)
    await db_session.commit()

    fixture = _load_fixture("assistant_a", "call_duplicate_test")

    first = await client.post(
        "/webhooks/vapi",
        content=fixture,
        headers={"content-type": "application/json", "x-vapi-secret": "test-secret"},
    )
    second = await client.post(
        "/webhooks/vapi",
        content=fixture,
        headers={"content-type": "application/json", "x-vapi-secret": "test-secret"},
    )

    assert first.status_code == 200
    assert second.status_code == 200

    result = await db_session.execute(
        select(func.count()).select_from(Call).where(Call.vapi_call_id == "call_duplicate_test")
    )
    assert result.scalar_one() == 1
