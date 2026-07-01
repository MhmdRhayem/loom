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
        description="Cross-session memory scope (e.g. 'shopper:42'). Omit to run the turn without memory.",
    )


class ChatResponse(BaseModel):
    conversation_id: str
    agent: str | None
    response: str | None
    eval: dict | None = None


class HealthResponse(BaseModel):
    status: str
    components: dict[str, str]


class FeedbackRequest(BaseModel):
    conversation_id: str = Field(..., description="The conversation to rate.")
    rating: str = Field(..., description="Thumbs rating: 'up' or 'down'.")
    comment: str | None = Field(default=None, description="Optional free-text feedback.")


class FeedbackResponse(BaseModel):
    recorded: bool
    agent: str | None = None


class DreamResponse(BaseModel):
    ran: bool
    merged: int
    pruned: int
