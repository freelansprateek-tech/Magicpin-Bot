"""FastAPI app exposing the judge-facing HTTP contract.

  POST /v1/context   versioned context store            (app/core/store.py)
  POST /v1/tick      decision layer -> composer          (app/decision, app/compose)
  POST /v1/reply     intent classifier -> reply policy   (app/reply)
  GET  /v1/healthz   liveness + context counts
  GET  /v1/metadata  bot identity
  POST /v1/teardown  wipe all state (optional in the brief)

Run with ONE worker (state is in memory): uvicorn app.main:app --port 8080
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import state
from app.compose.composer import ComposedMessage
from app.config import settings
from app.core.conversations import Conversation
from app.core.timeutil import now_from
from app.decision.planner import Candidate, plan_tick
from app.schemas import (
    ContextAccepted,
    ContextCounts,
    ContextPush,
    ContextRejected,
    HealthResponse,
    MetadataResponse,
    ReplyRequest,
    TeardownResponse,
    TickAction,
    TickRequest,
    TickResponse,
)

logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)   # don't log every LLM HTTP call
log = logging.getLogger("vera.api")

START_TIME = time.time()
TICK_BUDGET_SECONDS = 9.0   # api-call-examples summary table: /v1/tick budget is 10 s

app = FastAPI(title="Vera bot — magicpin AI Challenge", version=settings.version)


def utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with milliseconds, e.g. 2026-04-26T10:00:00.123Z"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------
# Malformed requests -> 400 in the documented shape (FastAPI's default is 422)
# --------------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    bad_scope = any(err.get("loc", [None])[-1] == "scope" for err in errors)
    reason = "invalid_scope" if bad_scope else "invalid_payload"
    details = "; ".join(
        f"{'.'.join(str(p) for p in err.get('loc', []))}: {err.get('msg')}" for err in errors
    )
    return JSONResponse(
        status_code=400,
        content={"accepted": False, "reason": reason, "details": details[:500]},
    )


# --------------------------------------------------------------------------
# GET /v1/healthz, GET /v1/metadata
# --------------------------------------------------------------------------
@app.get("/v1/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(
        uptime_seconds=int(time.time() - START_TIME),
        contexts_loaded=ContextCounts(**state.store.counts()),
    )


@app.get("/v1/metadata", response_model=MetadataResponse)
async def metadata() -> MetadataResponse:
    provider = state.composer.provider
    return MetadataResponse(
        team_name=settings.team_name,
        team_members=settings.team_members,
        model=f"{provider.name}:{provider.model}" if provider else "template-only",
        approach=(
            "Rule-based decision layer picks one grounded signal per merchant; "
            "LLM writes from a validated fact sheet; deterministic template fallback; "
            "rule-based reply intents (auto-reply, intent transition, hostile, off-topic)"
        ),
        contact_email=settings.contact_email,
        version=settings.version,
        submitted_at=settings.submitted_at,
    )


# --------------------------------------------------------------------------
# POST /v1/context
# --------------------------------------------------------------------------
@app.post("/v1/context", response_model=None)
async def push_context(body: ContextPush):
    result = state.store.put(body.scope, body.context_id, body.version, body.payload)
    if not result.accepted:
        rejected = ContextRejected(reason="stale_version", current_version=result.current_version)
        return JSONResponse(status_code=409, content=rejected.model_dump(exclude_none=True))
    return ContextAccepted(ack_id=f"ack_{body.context_id}_v{body.version}", stored_at=result.stored_at or utc_now_iso())


# --------------------------------------------------------------------------
# POST /v1/tick
# --------------------------------------------------------------------------
@app.post("/v1/tick", response_model=TickResponse)
async def tick(body: TickRequest) -> TickResponse:
    now = now_from(body.now)
    candidates, skips = plan_tick(state.store, state.ledger, now, body.available_triggers)
    for skip in skips:
        log.info("tick skip %s: %s", skip.trigger_id, skip.reason)

    messages = await _compose_all(candidates, now)
    actions: list[TickAction] = []
    for cand, msg in zip(candidates, messages):
        conv_id = _conversation_id(cand)
        state.conversations.start(
            Conversation(conv_id, cand.merchant_id, cand.customer_id,
                         trigger_id=cand.trigger_id, kind=cand.trigger.get("kind"), send_as=msg.send_as),
            msg.body,
        )
        state.ledger.mark_sent(msg.suppression_key, cand.recipient, now)
        actions.append(TickAction(
            conversation_id=conv_id,
            merchant_id=cand.merchant_id,
            customer_id=cand.customer_id,
            send_as=msg.send_as,
            trigger_id=cand.trigger_id,
            template_name=msg.template_name,
            template_params=msg.template_params,
            body=msg.body,
            cta=msg.cta,
            suppression_key=msg.suppression_key,
            rationale=msg.rationale,
        ))
        log.info("tick send %s -> %s via %s", cand.trigger_id, cand.merchant_id, msg.meta.get("path"))
    return TickResponse(actions=actions)


def _compose_kwargs(cand: Candidate, now: datetime) -> dict:
    return dict(category=cand.category, merchant=cand.merchant, trigger=cand.trigger,
                customer=cand.customer, customer_facing=cand.customer_facing,
                consent_reason=cand.consent_reason, now=now, decision_rationale=cand.rationale())


async def _compose_all(candidates: list[Candidate], now: datetime) -> list[ComposedMessage]:
    """Compose in parallel within the tick budget; anything still waiting on the
    LLM at the deadline gets its deterministic template instead."""
    if not candidates:
        return []
    if state.composer.provider is None:
        return [state.composer.compose_template_only(**_compose_kwargs(c, now)) for c in candidates]

    semaphore = asyncio.Semaphore(max(1, settings.llm_max_parallel))

    async def one(cand: Candidate) -> ComposedMessage:
        async with semaphore:
            return await asyncio.to_thread(state.composer.compose, **_compose_kwargs(cand, now))

    tasks = [asyncio.create_task(one(c)) for c in candidates]
    done, _pending = await asyncio.wait(tasks, timeout=TICK_BUDGET_SECONDS)
    results: list[ComposedMessage] = []
    for cand, task in zip(candidates, tasks):
        if task in done and task.exception() is None:
            results.append(task.result())
        else:
            # Late or failed: fall back now. The background LLM call keeps
            # running and fills the cache, so the next identical request is fast.
            results.append(state.composer.compose_template_only(**_compose_kwargs(cand, now)))
    return results


def _conversation_id(cand: Candidate) -> str:
    """Readable + unique: conv_<merchant>_<kind>_<hash of trigger id>."""
    digest = hashlib.sha1(cand.trigger_id.encode("utf-8")).hexdigest()[:6]
    base = f"conv_{cand.merchant_id[:28]}_{cand.trigger.get('kind', 'msg')}_{digest}"
    conv_id, n = base, 2
    while state.conversations.exists(conv_id):
        conv_id, n = f"{base}_{n}", n + 1
    return conv_id


# --------------------------------------------------------------------------
# POST /v1/reply
# --------------------------------------------------------------------------
@app.post("/v1/reply", response_model=None)
async def reply(body: ReplyRequest):
    decision = state.reply_handler.handle(
        conversation_id=body.conversation_id,
        merchant_id=body.merchant_id,
        customer_id=body.customer_id,
        from_role=body.from_role,
        message=body.message,
        now=now_from(body.received_at),
    )
    log.info("reply %s -> %s", body.conversation_id, decision.action)
    return decision.to_dict()


# --------------------------------------------------------------------------
# POST /v1/teardown  (optional, challenge-testing-brief.md §11)
# --------------------------------------------------------------------------
@app.post("/v1/teardown", response_model=TeardownResponse)
async def teardown() -> TeardownResponse:
    state.reset_all()
    return TeardownResponse(wiped=True)
