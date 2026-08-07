from pydantic import BaseModel, ConfigDict


class VapiCustomer(BaseModel):
    model_config = ConfigDict(extra="allow")

    number: str | None = None


class VapiCall(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    assistantId: str | None = None
    phoneNumberId: str | None = None
    customer: VapiCustomer | None = None
    status: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None


class VapiRecording(BaseModel):
    model_config = ConfigDict(extra="allow")

    stereoUrl: str | None = None


class VapiArtifact(BaseModel):
    model_config = ConfigDict(extra="allow")

    transcript: str | None = None
    messages: list[dict] | None = None
    recording: VapiRecording | None = None


class VapiAnalysis(BaseModel):
    model_config = ConfigDict(extra="allow")

    summary: str | None = None
    structuredData: dict | None = None


class VapiMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    endedReason: str | None = None
    call: VapiCall
    artifact: VapiArtifact | None = None
    analysis: VapiAnalysis | None = None


class VapiWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    message: VapiMessage
