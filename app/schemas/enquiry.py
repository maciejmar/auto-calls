from pydantic import BaseModel, ConfigDict


class ExtractedEnquiry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    phone: str | None = None
    email: str | None = None
    topic: str | None = None
    notes: str | None = None
    appointment_requested: bool = False
