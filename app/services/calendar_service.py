import asyncio
from abc import ABC, abstractmethod

import httpx

from app.models.tenant import Tenant

GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
GOOGLE_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
REQUEST_TIMEOUT = httpx.Timeout(4.0, connect=4.0)


class CalendarServiceError(Exception):
    """Raised when the calendar provider can't be reached or returns an error."""


class CalendarService(ABC):
    @abstractmethod
    async def check_availability(self, calendar_id: str, start: str, end: str) -> bool: ...

    @abstractmethod
    async def create_appointment(
        self, calendar_id: str, start: str, end: str, summary: str, description: str = ""
    ) -> str: ...

    @abstractmethod
    async def cancel_appointment(self, calendar_id: str, appointment_id: str) -> None: ...


class NullCalendarService(CalendarService):
    """No-op stub for tenants without a configured calendar provider."""

    async def check_availability(self, calendar_id: str, start: str, end: str) -> bool:
        return False

    async def create_appointment(
        self, calendar_id: str, start: str, end: str, summary: str, description: str = ""
    ) -> str:
        raise CalendarServiceError("Calendar integration not configured for this tenant")

    async def cancel_appointment(self, calendar_id: str, appointment_id: str) -> None:
        raise CalendarServiceError("Calendar integration not configured for this tenant")


class GoogleCalendarService(CalendarService):
    """Talks to the Google Calendar REST API directly over httpx, using a
    single service-account key shared across tenants. Each tenant shares
    their own calendar with the service account's email; `calendar_id`
    (per tenant) is the only thing that differs between them."""

    def __init__(self, service_account_file: str):
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from google.oauth2 import service_account

        self._google_auth_request = GoogleAuthRequest
        self._credentials = service_account.Credentials.from_service_account_file(
            service_account_file, scopes=GOOGLE_CALENDAR_SCOPES
        )

    async def _access_token(self) -> str:
        if not self._credentials.valid:
            await asyncio.to_thread(self._credentials.refresh, self._google_auth_request())
        return self._credentials.token

    async def _client(self) -> httpx.AsyncClient:
        token = await self._access_token()
        return httpx.AsyncClient(
            base_url=GOOGLE_CALENDAR_API_BASE,
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )

    async def check_availability(self, calendar_id: str, start: str, end: str) -> bool:
        try:
            async with await self._client() as client:
                response = await client.post(
                    "/freeBusy",
                    json={"timeMin": start, "timeMax": end, "items": [{"id": calendar_id}]},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CalendarServiceError(f"Google freeBusy request failed: {exc}") from exc

        busy = response.json().get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return len(busy) == 0

    async def create_appointment(
        self, calendar_id: str, start: str, end: str, summary: str, description: str = ""
    ) -> str:
        try:
            async with await self._client() as client:
                response = await client.post(
                    f"/calendars/{calendar_id}/events",
                    json={
                        "summary": summary,
                        "description": description,
                        "start": {"dateTime": start},
                        "end": {"dateTime": end},
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CalendarServiceError(f"Google events.insert request failed: {exc}") from exc

        return response.json()["id"]

    async def cancel_appointment(self, calendar_id: str, appointment_id: str) -> None:
        try:
            async with await self._client() as client:
                response = await client.delete(f"/calendars/{calendar_id}/events/{appointment_id}")
                if response.status_code not in (200, 204, 410):
                    response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CalendarServiceError(f"Google events.delete request failed: {exc}") from exc


def get_calendar_service(tenant: Tenant, service_account_file: str) -> CalendarService:
    if tenant.calendar_provider == "google" and service_account_file:
        return GoogleCalendarService(service_account_file)
    return NullCalendarService()
