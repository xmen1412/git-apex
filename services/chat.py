"""AI chat backend: question -> LLM router -> safe per-route query -> LLM summary."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from commit_pulse.config import get_settings
from commit_pulse.llm_router import route_question, summarize
from commit_pulse.query_executors import execute

logger = logging.getLogger(__name__)
settings = get_settings()


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    answer: str
    route: str
    reasoning: str
    params: dict[str, Any]
    data: Any


def create_app() -> FastAPI:
    app = FastAPI(title="commit-pulse AI chat")

    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest):
        if not settings.llm_api_key:
            raise HTTPException(status_code=503, detail="LLM_API_KEY not configured")
        decision = route_question(req.question, settings)
        logger.info("route=%s intent=%s reasoning=%s", decision.route, decision.params.get("intent"), decision.reasoning)
        try:
            data = execute(decision, settings)
        except Exception as exc:
            logger.exception("query execution failed")
            raise HTTPException(status_code=502, detail=f"query execution failed: {exc}") from exc
        answer = summarize(req.question, decision.route, data, settings)
        return ChatResponse(
            answer=answer,
            route=decision.route,
            reasoning=decision.reasoning,
            params=decision.params,
            data=data,
        )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app
