"""Pydantic request/response models for the HTTP API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="The user's message.")
    conversation_id: str | None = Field(
        default=None,
        description="Existing conversation to continue; a new one is created when omitted.",
    )
    owner_id: str | None = Field(
        default=None,
        description="Memory/persistence scope (e.g. 'shopper:42'). Reserved for later phases.",
    )


class ChatResponse(BaseModel):
    conversation_id: str
    agent: str | None
    response: str | None
    eval: dict | None = None


class HealthResponse(BaseModel):
    status: str
    components: dict[str, str]
