import json
import uuid
from pathlib import Path

from sqlalchemy import select

from app.models.call import Call
from app.models.tenant import Tenant

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "end_of_call_report.json"


def _load_fixture(assistant_id: str, vapi_call_id: str) -> bytes:
    payload = json.loads(FIXTURE_PATH.read_text())
    payload["message"]["call"]["assistantId"] = assistant_id
    payload["message"]["call"]["id"] = vapi_call_id
    return json.dumps(payload).encode()


async def _make_tenant(db_session, *, name: str, assistant_id: str) -> Tenant:
    tenant = Tenant(id=uuid.uuid4(), name=name, vapi_assistant_id=assistant_id)
    db_session.add(tenant)
    await db_session.commit()
    return tenant


async def test_call_is_attributed_to_correct_tenant(client, db_session):
    tenant_a = await _make_tenant(db_session, name="Kancelaria A", assistant_id="assistant_a")
    await _make_tenant(db_session, name="Kancelaria B", assistant_id="assistant_b")

    response = await client.post(
        "/webhooks/vapi",
        content=_load_fixture("assistant_a", "call_tenant_a_1"),
        headers={"content-type": "application/json", "x-vapi-secret": "test-secret"},
    )
    assert response.status_code == 200

    result = await db_session.execute(select(Call).where(Call.vapi_call_id == "call_tenant_a_1"))
    call = result.scalar_one()
    assert call.tenant_id == tenant_a.id


async def test_tenants_are_isolated(client, db_session):
    tenant_a = await _make_tenant(db_session, name="Kancelaria A", assistant_id="assistant_a")
    tenant_b = await _make_tenant(db_session, name="Kancelaria B", assistant_id="assistant_b")

    await client.post(
        "/webhooks/vapi",
        content=_load_fixture("assistant_a", "call_a"),
        headers={"content-type": "application/json", "x-vapi-secret": "test-secret"},
    )
    await client.post(
        "/webhooks/vapi",
        content=_load_fixture("assistant_b", "call_b"),
        headers={"content-type": "application/json", "x-vapi-secret": "test-secret"},
    )

    result_a = await db_session.execute(select(Call).where(Call.vapi_call_id == "call_a"))
    result_b = await db_session.execute(select(Call).where(Call.vapi_call_id == "call_b"))
    assert result_a.scalar_one().tenant_id == tenant_a.id
    assert result_b.scalar_one().tenant_id == tenant_b.id


async def test_unknown_assistant_id_returns_200_and_stores_nothing(client, db_session):
    response = await client.post(
        "/webhooks/vapi",
        content=_load_fixture("assistant_does_not_exist", "call_unknown"),
        headers={"content-type": "application/json", "x-vapi-secret": "test-secret"},
    )
    assert response.status_code == 200

    result = await db_session.execute(select(Call).where(Call.vapi_call_id == "call_unknown"))
    assert result.scalar_one_or_none() is None
