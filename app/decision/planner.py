"""Planner for POST /v1/tick: turn the current state into <= 20 prioritized actions.

Steps for each candidate trigger:
  1. resolve contexts (trigger -> merchant -> category, + customer if scoped)
  2. hard filters: missing context, expired*, suppression key already sent,
     merchant blocked (hostile/opt-out), merchant cooldown
     (*) expiry is only enforced for triggers the bot picks itself. When the
         judge lists a trigger in available_triggers it is, by definition,
         "active right now" — the judge is the source of truth for time.
  3. consent gate for customer triggers (fails -> merchant-facing note)
  4. score (scoring.py) and drop anything below MIN_SCORE (restraint)
  5. keep the single best candidate per merchant, sort, cap at 20

Deterministic: ties break on trigger id, never on dict/insertion order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from app.core.store import ContextStore
from app.core.timeutil import is_expired
from app.decision.consent import check_customer_consent
from app.decision.scoring import Score, score_candidate
from app.decision.suppression import SuppressionLedger, recipient_key

MIN_SCORE = 20
MAX_ACTIONS = 20


@dataclass
class Candidate:
    trigger_id: str
    trigger: dict[str, Any]
    merchant: dict[str, Any]
    category: dict[str, Any]
    customer: Optional[dict[str, Any]]
    customer_facing: bool          # False when a customer trigger was re-routed to the merchant
    consent_reason: Optional[str]
    score: Score

    @property
    def merchant_id(self) -> str:
        return self.merchant.get("merchant_id") or self.trigger.get("merchant_id", "")

    @property
    def customer_id(self) -> Optional[str]:
        """Set only when the message actually goes to the customer."""
        return self.customer.get("customer_id") if (self.customer and self.customer_facing) else None

    @property
    def recipient(self) -> str:
        return recipient_key(self.merchant_id, self.customer_id)

    def rationale(self) -> str:
        kind = self.trigger.get("kind", "unknown")
        head = f"Chose {kind} (score {self.score.total}): " + "; ".join(self.score.notes[:4])
        if self.consent_reason and not self.customer_facing:
            head += f". Customer outreach blocked ({self.consent_reason}), so the merchant is told instead"
        return head + "."


@dataclass
class Skip:
    trigger_id: str
    reason: str


def evaluate_trigger(
    trigger_id: str,
    store: ContextStore,
    ledger: SuppressionLedger,
    now: datetime,
    *,
    check_cooldown: bool = True,
    judge_says_active: bool = False,
) -> Candidate | Skip:
    trigger = store.get("trigger", trigger_id)
    if trigger is None:
        return Skip(trigger_id, "trigger_not_loaded")
    merchant = store.get("merchant", trigger.get("merchant_id"))
    if merchant is None:
        return Skip(trigger_id, "merchant_not_loaded")
    category = store.category_for(merchant)
    if category is None:
        return Skip(trigger_id, "category_not_loaded")
    if not judge_says_active and is_expired(trigger.get("expires_at"), now):
        return Skip(trigger_id, "trigger_expired")
    if ledger.was_sent(trigger.get("suppression_key", "")):
        return Skip(trigger_id, "suppression_key_already_sent")

    merchant_id = merchant.get("merchant_id", "")
    blocked = ledger.blocked_reason(merchant_id, now)
    if blocked:
        return Skip(trigger_id, f"merchant_blocked:{blocked}")
    try:
        urgency = int(trigger.get("urgency", 1))
    except (TypeError, ValueError):
        urgency = 1
    customer = None
    customer_facing = False
    consent_reason = None
    if trigger.get("scope") == "customer" or trigger.get("customer_id"):
        customer = store.get("customer", trigger.get("customer_id"))
        decision = check_customer_consent(trigger, customer)
        customer_facing = decision.allowed
        consent_reason = decision.reason
        if customer is None:
            # We know nothing about this person: saying anything about them to
            # the merchant would be invented. Wait until the context arrives.
            return Skip(trigger_id, "customer_not_loaded")

    recipient = recipient_key(merchant_id, customer.get("customer_id") if (customer and customer_facing) else None)
    if check_cooldown and ledger.in_cooldown(recipient, now, urgency):
        return Skip(trigger_id, "recipient_cooldown")

    score = score_candidate(category=category, merchant=merchant, trigger=trigger, now=now,
                            consent_blocked=customer is not None and not customer_facing)
    return Candidate(trigger_id, trigger, merchant, category, customer, customer_facing,
                     consent_reason, score)


def plan_tick(
    store: ContextStore,
    ledger: SuppressionLedger,
    now: datetime,
    available_triggers: list[str],
) -> tuple[list[Candidate], list[Skip]]:
    trigger_ids = list(dict.fromkeys(available_triggers))  # de-dup, keep order
    hinted = bool(trigger_ids)
    if not trigger_ids:
        trigger_ids = sorted(store.all("trigger"))

    # One action per RECIPIENT per tick (the brief allows one per merchant+conversation):
    # the merchant can get one note while one of their customers gets a reminder.
    best_per_recipient: dict[str, Candidate] = {}
    skips: list[Skip] = []
    for tid in trigger_ids:
        result = evaluate_trigger(tid, store, ledger, now, judge_says_active=hinted)
        if isinstance(result, Skip):
            skips.append(result)
            continue
        if result.score.total < MIN_SCORE:
            skips.append(Skip(tid, f"below_threshold({result.score.total})"))
            continue
        current = best_per_recipient.get(result.recipient)
        if current is None or _rank(result) < _rank(current):
            if current is not None:
                skips.append(Skip(current.trigger_id, "lost_to_better_trigger_same_recipient"))
            best_per_recipient[result.recipient] = result
        else:
            skips.append(Skip(tid, "lost_to_better_trigger_same_recipient"))

    chosen = sorted(best_per_recipient.values(), key=_rank)[:MAX_ACTIONS]
    return chosen, skips


def _rank(c: Candidate) -> tuple[int, str]:
    return (-c.score.total, c.trigger_id)
