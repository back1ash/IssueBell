"""GitHub webhook signature verification."""

import hashlib
import hmac

from app.config import settings


def verify_signature(body: bytes, signature_header: str | None) -> bool:
    """Return True if the X-Hub-Signature-256 header is valid."""
    if not settings.github_webhook_secret:
        # If no secret is configured, skip verification (dev only).
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        settings.github_webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)
