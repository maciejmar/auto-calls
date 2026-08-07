import hashlib
import hmac

from app.config import Settings

# Header name as configured on the Vapi HMAC credential; confirm against the
# dashboard on first real integration (see ETAP 8 open items).
HMAC_SIGNATURE_HEADER = "x-vapi-signature"
LEGACY_SECRET_HEADER = "x-vapi-secret"


def verify_vapi_webhook(raw_body: bytes, headers: dict[str, str], settings: Settings) -> bool:
    headers = {k.lower(): v for k, v in headers.items()}

    if settings.vapi_webhook_hmac_secret:
        signature = headers.get(HMAC_SIGNATURE_HEADER)
        if not signature:
            return False
        expected = hmac.new(
            settings.vapi_webhook_hmac_secret.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    if settings.vapi_webhook_secret:
        provided = headers.get(LEGACY_SECRET_HEADER)
        if not provided:
            return False
        return hmac.compare_digest(settings.vapi_webhook_secret, provided)

    return False
