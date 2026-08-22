from __future__ import annotations

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request

from commit_pulse.config import get_settings
from commit_pulse.kafka_io import make_producer, publish_push_payload

logger = logging.getLogger(__name__)
settings = get_settings(require_webhook_secret=True)


def verify_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        producer = make_producer(settings)
        app.state.producer = producer
        yield
        producer.close()

    app = FastAPI(title="commit-pulse webhook receiver", lifespan=lifespan)

    @app.post("/webhook")
    async def webhook(
        request: Request,
        x_hub_signature_256: str | None = Header(None),
        x_github_event: str | None = Header(None),
    ):
        body = await request.body()
        if not verify_signature(body, x_hub_signature_256, settings.github_webhook_secret):
            raise HTTPException(status_code=401, detail="invalid signature")
        if x_github_event != "push":
            return {"status": "ignored", "event": x_github_event}
        payload = await request.json()
        publish_push_payload(request.app.state.producer, settings, payload)
        return {"status": "accepted", "commits": len(payload.get("commits", []))}

    return app
