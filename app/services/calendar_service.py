from abc import ABC, abstractmethod


class CalendarService(ABC):
    @abstractmethod
    async def check_availability(self, tenant_calendar_id: str, start: str, end: str) -> bool: ...

    @abstractmethod
    async def create_appointment(self, tenant_calendar_id: str, start: str, end: str, summary: str) -> str: ...

    @abstractmethod
    async def cancel_appointment(self, tenant_calendar_id: str, appointment_id: str) -> None: ...


class NullCalendarService(CalendarService):
    """No-op stub used until a real calendar provider is wired up post-ETAP 8."""

    async def check_availability(self, tenant_calendar_id: str, start: str, end: str) -> bool:
        return False

    async def create_appointment(self, tenant_calendar_id: str, start: str, end: str, summary: str) -> str:
        raise NotImplementedError("Calendar integration not yet configured for this tenant")

    async def cancel_appointment(self, tenant_calendar_id: str, appointment_id: str) -> None:
        raise NotImplementedError("Calendar integration not yet configured for this tenant")


def get_calendar_service(calendar_provider: str) -> CalendarService:
    return NullCalendarService()
