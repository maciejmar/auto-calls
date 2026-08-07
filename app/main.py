from fastapi import FastAPI

from app.api import health, vapi_webhook
from app.config import get_settings
from app.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(title="Automatyczna Sekretarka AI", version="0.1.0")

app.include_router(health.router)
app.include_router(vapi_webhook.router)
