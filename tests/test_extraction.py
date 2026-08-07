import httpx
import pytest
from openai import APIConnectionError

from app.services.extraction_service import ExtractionError, extract_enquiry


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
    def __init__(self, content: str | None = None, exception: Exception | None = None):
        self._content = content
        self._exception = exception

    async def create(self, **kwargs):
        if self._exception is not None:
            raise self._exception
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, completions: _FakeCompletions):
        self.completions = completions


class FakeOpenAIClient:
    def __init__(self, content: str | None = None, exception: Exception | None = None):
        self.chat = _FakeChat(_FakeCompletions(content, exception))


async def test_extract_enquiry_success():
    client = FakeOpenAIClient(
        content='{"name": "Jan Kowalski", "phone": "+48600100200", "email": "jan@example.com", '
        '"topic": "Akt notarialny", "notes": null, "appointment_requested": true}'
    )

    result = await extract_enquiry(client, "gpt-4o-mini", "transcript")

    assert result.name == "Jan Kowalski"
    assert result.email == "jan@example.com"
    assert result.appointment_requested is True


async def test_extract_enquiry_missing_email():
    client = FakeOpenAIClient(
        content='{"name": "Jan Kowalski", "phone": "+48600100200", "email": null, '
        '"topic": "Akt notarialny", "notes": null, "appointment_requested": false}'
    )

    result = await extract_enquiry(client, "gpt-4o-mini", "transcript")

    assert result.email is None
    assert result.name == "Jan Kowalski"


async def test_extract_enquiry_invalid_llm_response_raises():
    client = FakeOpenAIClient(content='{"name": "Jan", "unexpected_field": "boom"}')

    with pytest.raises(ExtractionError):
        await extract_enquiry(client, "gpt-4o-mini", "transcript")


async def test_extract_enquiry_api_error_raises():
    client = FakeOpenAIClient(
        exception=APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))
    )

    with pytest.raises(ExtractionError):
        await extract_enquiry(client, "gpt-4o-mini", "transcript")
