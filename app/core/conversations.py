"""Conversation state for /v1/tick (bot-initiated) and /v1/reply (judge replies).

Two levels of memory:
  * per conversation: turns, bodies we already sent (anti-repetition),
    status, how far the merchant has moved (pitch -> action -> confirmed)
  * per merchant: consecutive auto-replies, last inbound text.
    Auto-reply detection MUST be per merchant: judge_simulator.py sends each
    auto-reply turn under a different conversation_id.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Conversation:
    conversation_id: str
    merchant_id: Optional[str]
    customer_id: Optional[str] = None
    trigger_id: Optional[str] = None
    kind: Optional[str] = None
    send_as: str = "vera"
    turns: list[dict[str, Any]] = field(default_factory=list)
    bot_bodies: list[str] = field(default_factory=list)
    status: str = "open"                 # open | ended
    stage: str = "pitch"                 # pitch | action | confirmed
    off_topic_count: int = 0
    objection_count: int = 0
    question_count: int = 0
    unclear_count: int = 0
    facts: dict[str, Any] = field(default_factory=dict)  # fact sheet used for the opener

    @property
    def bot_turns(self) -> int:
        return len(self.bot_bodies)

    def already_sent(self, body: str) -> bool:
        norm = " ".join(body.lower().split())
        return any(" ".join(b.lower().split()) == norm for b in self.bot_bodies)


@dataclass
class MerchantReplyState:
    consecutive_auto_replies: int = 0
    last_inbound: Optional[str] = None
    auto_reply_prompt_sent: bool = False


class ConversationStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._convs: dict[str, Conversation] = {}
        self._merchants: dict[str, MerchantReplyState] = {}

    def clear(self) -> None:
        with self._lock:
            self._convs.clear()
            self._merchants.clear()

    def exists(self, conversation_id: str) -> bool:
        with self._lock:
            return conversation_id in self._convs

    def start(self, conv: Conversation, first_body: str) -> None:
        with self._lock:
            conv.bot_bodies.append(first_body)
            conv.turns.append({"role": "bot", "text": first_body})
            self._convs[conv.conversation_id] = conv

    def get_or_create(self, conversation_id: str, merchant_id: Optional[str],
                      customer_id: Optional[str]) -> Conversation:
        with self._lock:
            conv = self._convs.get(conversation_id)
            if conv is None:
                conv = Conversation(conversation_id, merchant_id, customer_id,
                                    send_as="merchant_on_behalf" if customer_id else "vera")
                self._convs[conversation_id] = conv
            elif merchant_id and not conv.merchant_id:
                conv.merchant_id = merchant_id
            return conv

    def merchant_state(self, merchant_id: Optional[str]) -> MerchantReplyState:
        key = merchant_id or "_unknown"
        with self._lock:
            return self._merchants.setdefault(key, MerchantReplyState())

    def record_inbound(self, conv: Conversation, role: str, text: str) -> None:
        with self._lock:
            conv.turns.append({"role": role, "text": text})

    def record_outbound(self, conv: Conversation, body: str) -> None:
        with self._lock:
            conv.bot_bodies.append(body)
            conv.turns.append({"role": "bot", "text": body})

    def end(self, conv: Conversation) -> None:
        with self._lock:
            conv.status = "ended"
