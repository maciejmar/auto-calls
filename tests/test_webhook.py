from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "end_of_call_report.json"


@pytest.fixture
def raw_payload() -> bytes:
    return FIXTURE_PATH.read_bytes()


async def test_webhook_rejects_missing_signature(client, raw_payload):
    response = await client.post(
        "/webhooks/vapi", content=raw_payload, headers={"content-type": "application/json"}
    )
    assert response.status_code == 401


async def test_webhook_accepts_valid_end_of_call_report(client, raw_payload):
    response = await client.post(
        "/webhooks/vapi",
        content=raw_payload,
        headers={"content-type": "application/json", "x-vapi-secret": "test-secret"},
    )
    assert response.status_code == 200


async def test_webhook_returns_200_on_malformed_payload(client):
    response = await client.post(
        "/webhooks/vapi",
        content=b'{"nonsense": true}',
        headers={"content-type": "application/json", "x-vapi-secret": "test-secret"},
    )
    assert response.status_code == 200
