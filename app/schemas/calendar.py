from pydantic import BaseModel, ConfigDict, Field

# LLM is instructed (via the Vapi tool's parameter description) to always
# emit these formats regardless of the spoken language of the call, so the
# backend never has to parse natural-language Polish dates/times.
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class CheckAvailabilityArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str = Field(pattern=DATE_PATTERN)
    time: str = Field(pattern=TIME_PATTERN)


class CreateAppointmentArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str = Field(pattern=DATE_PATTERN)
    time: str = Field(pattern=TIME_PATTERN)
    client_name: str | None = None
    client_phone: str | None = None
    topic: str | None = None
