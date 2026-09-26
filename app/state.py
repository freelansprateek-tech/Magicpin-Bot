"""Process-wide singletons shared by all endpoints.

One uvicorn worker == one copy of this state. See app/core/store.py for why.
"""

from __future__ import annotations

from app.compose.composer import Composer
from app.core.conversations import ConversationStore
from app.core.store import ContextStore
from app.decision.suppression import SuppressionLedger
from app.reply.handler import ReplyHandler

store = ContextStore()
ledger = SuppressionLedger()
conversations = ConversationStore()
composer = Composer()                     # LLM provider chosen from .env (or none)
reply_handler = ReplyHandler(store, conversations, ledger)


def reset_all() -> None:
    """Used by POST /v1/teardown and by tests."""
    store.clear()
    ledger.clear()
    conversations.clear()
