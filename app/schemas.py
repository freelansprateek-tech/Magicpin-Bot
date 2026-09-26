"""Request and response models for the 5 judge-facing endpoints (+ teardown).

Every shape here is taken from challenge-testing-brief.md §2-3 and
examples/api-call-examples.md. Request models use extra="allow" so the
judge can send fields we did not anticipate without getting a 400.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Scope = Literal["category", "merchant", "customer", "trigger"]
SendAs = Literal["vera", "merchant_on_behalf"]
Cta = Literal[
    "open_ended",
    "binary_yes_no",
    "binary_confirm_cancel",
    "multi_choice_slot",
    "none",
]


# ---------------------------------------------------------------- /v1/context
class ContextPush(BaseModel):
    model_config = ConfigDict(extra="allow")

    scope: Scope
    context_id: str = Field(min_length=1)
    version: int = Field(ge=0)
    payload: dict[str, Any]
    delivered_at: Optional[str] = None  # present in the docs; not needed for logic


class ContextAccepted(BaseModel):
    accepted: Literal[True] = True
    ack_id: str
    stored_at: str


class ContextRejected(BaseModel):
    accepted: Literal[False] = False
    reason: str
    current_version: Optional[int] = None
    details: Optional[str] = None


# ------------------------------------------------------------------ /v1/tick
class TickRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    now: str
    available_triggers: list[str] = Field(default_factory=list)


class TickAction(BaseModel):
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str] = None
    send_as: SendAs
    trigger_id: str
    template_name: str
    template_params: list[str]
    body: str = Field(min_length=1)
    cta: Cta
    suppression_key: str
    rationale: str


class TickResponse(BaseModel):
    actions: list[TickAction] = Field(default_factory=list)


# ----------------------------------------------------------------- /v1/reply
class ReplyRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    conversation_id: str
    merchant_id: Optional[str] = None  # omitted in api-call-examples 2.5-2.7
    customer_id: Optional[str] = None
    from_role: Literal["merchant", "customer"] = "merchant"
    message: str
    received_at: Optional[str] = None
    turn_number: Optional[int] = None


class ReplySend(BaseModel):
    action: Literal["send"] = "send"
    body: str = Field(min_length=1)  # empty body = malformed (-2)
    cta: Cta
    rationale: str


class ReplyWait(BaseModel):
    action: Literal["wait"] = "wait"
    wait_seconds: int = Field(ge=0)
    rationale: str


class ReplyEnd(BaseModel):
    action: Literal["end"] = "end"
    rationale: str


# ----------------------------------------------------- /v1/healthz, metadata
class ContextCounts(BaseModel):
    category: int = 0
    merchant: int = 0
    customer: int = 0
    trigger: int = 0


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    uptime_seconds: int
    contexts_loaded: ContextCounts


class MetadataResponse(BaseModel):
    team_name: str
    team_members: list[str]
    model: str
    approach: str
    contact_email: str
    version: str
    submitted_at: str


class TeardownResponse(BaseModel):
    wiped: bool
