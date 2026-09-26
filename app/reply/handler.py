"""Reply policy for POST /v1/reply.

intent        -> action
-----------------------------------------------------------------------------
auto_reply    1st in a row (per merchant): one short nudge for the owner
              2nd: wait 24h           3rd+: end
hostile       end + silence the merchant for 30 days
opt_out       end + silence the merchant for 30 days (explicit stop)
later         wait (30 min / 1 day / 1 week depending on what they said)
off_topic     1st: polite decline + redirect to the original topic; 2nd: wait
yes           pitch stage  -> switch to ACTION immediately (no qualifying)
              action stage -> confirm it's done
              confirmed    -> end (task complete)
objection     1st: reframe once with a real fact; 2nd: graceful close
question      answer from the fact sheet if possible, else say so honestly
unclear       1st: one binary clarifier; 2nd: wait

Guards on every turn: conversation already ended -> end; 5 bot turns -> end;
never send a body already sent in this conversation (-> wait instead).
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from app.core.conversations import Conversation, ConversationStore
from app.core.store import ContextStore
from app.decision.kinds import rule_for
from app.decision.suppression import SuppressionLedger
from app.reply.intent import classify, detect_language, slot_choice

MAX_BOT_TURNS = 5
SILENCE_DAYS = 30


@dataclass
class ReplyDecision:
    action: str                         # send | wait | end
    rationale: str
    body: Optional[str] = None
    cta: Optional[str] = None
    wait_seconds: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        if self.action == "send":
            return {"action": "send", "body": self.body, "cta": self.cta or "open_ended",
                    "rationale": self.rationale}
        if self.action == "wait":
            return {"action": "wait", "wait_seconds": int(self.wait_seconds or 1800),
                    "rationale": self.rationale}
        return {"action": "end", "rationale": self.rationale}


# What "yes" unlocks, per trigger family. Phrased as work Vera does now.
DELIVERABLES = {
    "knowledge": "the 2-min summary and a patient-friendly WhatsApp draft",
    "compliance": "the compliance checklist",
    "event": "the post and offer banner",
    "performance": "your refreshed Google post",
    "account": "the renewal details",
    "relationship": "the snapshot of what to fix first",
    "planning": "the outline, the Google post and the WhatsApp message",
    "customer": "the booking",
    "other": "the first draft",
}
KIND_DELIVERABLES = {
    "supply_alert": "the filtered customer list and the replacement WhatsApp",
    "review_theme_emerged": "a polite public reply you can reuse",
    "gbp_unverified": "the verification request",
    "curious_ask_due": "the Google post and the pricing reply",
    "milestone_reached": "the review-request message",
    "cde_opportunity": "the event reminder",
}


def _deliverable(conv: Conversation) -> str:
    if conv.kind in KIND_DELIVERABLES:
        return KIND_DELIVERABLES[conv.kind]
    family = rule_for(conv.kind or "", "customer" if conv.customer_id else "merchant").family
    return DELIVERABLES.get(family, DELIVERABLES["other"])


def _topic(conv: Conversation) -> str:
    return (conv.kind or "our last message").replace("_", " ")


class ReplyHandler:
    def __init__(self, store: ContextStore, conversations: ConversationStore, ledger: SuppressionLedger) -> None:
        self.store = store
        self.conversations = conversations
        self.ledger = ledger

    def handle(self, *, conversation_id: str, merchant_id: Optional[str], customer_id: Optional[str],
               from_role: str, message: str, now: datetime) -> ReplyDecision:
        conv = self.conversations.get_or_create(conversation_id, merchant_id, customer_id)
        merchant_id = merchant_id or conv.merchant_id
        mstate = self.conversations.merchant_state(merchant_id)
        intent = classify(message, mstate.last_inbound)
        lang = detect_language(message)
        self.conversations.record_inbound(conv, from_role, message)

        # Per-merchant auto-reply streak (judge may use a new conversation_id each time).
        if intent == "auto_reply":
            mstate.consecutive_auto_replies += 1
        else:
            mstate.consecutive_auto_replies = 0
        mstate.last_inbound = message

        if conv.status == "ended":
            return ReplyDecision("end", "Conversation was already closed; not re-opening it.")
        if conv.bot_turns >= MAX_BOT_TURNS and intent not in {"hostile", "opt_out"}:
            self.conversations.end(conv)
            return ReplyDecision("end", f"Reached {MAX_BOT_TURNS} bot turns; closing to avoid fatigue.")

        if from_role == "customer":
            decision = self._customer_turn(conv, intent, message, lang, now)
        else:
            decision = self._merchant_turn(conv, intent, message, lang, now, merchant_id, mstate)

        if decision.action == "send" and decision.body:
            if conv.already_sent(decision.body):
                return ReplyDecision("wait", f"Would have repeated an earlier message ({intent}); "
                                             "backing off instead.", wait_seconds=3600)
            self.conversations.record_outbound(conv, decision.body)
        if decision.action == "end":
            self.conversations.end(conv)
        return decision

    # ------------------------------------------------------------ merchant
    def _merchant_turn(self, conv: Conversation, intent: str, message: str, lang: str,
                       now: datetime, merchant_id: Optional[str], mstate) -> ReplyDecision:
        hi = lang == "hi-en"
        deliverable = _deliverable(conv)

        if intent == "auto_reply":
            n = mstate.consecutive_auto_replies
            if n == 1 and not mstate.auto_reply_prompt_sent:
                mstate.auto_reply_prompt_sent = True
                body = ("Lagta hai yeh auto-reply hai 🙂 Owner dekhein toh bas YES reply kar dein — main aage badha dungi."
                        if hi else
                        "Looks like an auto-reply 🙂 When the owner sees this, just reply YES and I'll take it from there.")
                return ReplyDecision("send", "Detected a canned WhatsApp Business auto-reply; one short nudge for the owner, no pitch.",
                                     body=body, cta="binary_yes_no")
            if n <= 2:
                return ReplyDecision("wait", f"Auto-reply again ({n} in a row); owner not at the phone. Waiting 24h.",
                                     wait_seconds=86400)
            return ReplyDecision("end", f"Auto-reply {n} times in a row with no human reply; closing gracefully.")

        if intent == "hostile":
            self._silence(merchant_id, now, "hostile")
            return ReplyDecision("end", "Merchant is frustrated; exiting without further messages and suppressing "
                                        f"all outreach to this merchant for {SILENCE_DAYS} days.")

        if intent == "opt_out":
            self._silence(merchant_id, now, "opt_out")
            return ReplyDecision("end", "Merchant explicitly declined / asked to stop; closing and suppressing "
                                        f"outreach for {SILENCE_DAYS} days.")

        if intent == "later":
            text = message.lower()
            seconds = 7 * 86400 if "next week" in text else 86400 if ("tomorrow" in text or "kal" in text) else 1800
            return ReplyDecision("wait", f"Merchant asked for time; backing off {seconds // 60} minutes.",
                                 wait_seconds=seconds)

        if intent == "off_topic":
            conv.off_topic_count += 1
            if conv.off_topic_count > 1:
                return ReplyDecision("wait", "Second off-topic request; not engaging further on it, backing off 1h.",
                                     wait_seconds=3600)
            body = (f"Yeh mere scope se bahar hai — iske liye aapke CA/advisor best rahenge. Wapas {_topic(conv)} par: "
                    f"{deliverable} ready kar doon? Reply YES."
                    if hi else
                    f"That's outside what I can help with — your CA or advisor is the right person for it. "
                    f"Coming back to the {_topic(conv)}: shall I prepare {deliverable}? Reply YES.")
            return ReplyDecision("send", "Out-of-scope ask politely declined; redirected to the original trigger once.",
                                 body=body, cta="binary_yes_no")

        if intent == "yes":
            return self._advance(conv, hi, deliverable)

        if intent == "objection":
            conv.objection_count += 1
            if conv.objection_count > 1:
                return ReplyDecision("end", "Second objection; respecting it and closing without pushing further.")
            fact = self._reframe_fact(conv.merchant_id)
            body = (f"Samajh sakti hoon. {fact} Main saara kaam khud karungi, aap sirf approve karein — "
                    f"ek hafte ke liye try karein? Reply YES."
                    if hi else
                    f"Fair point. {fact} I'll do all the work — you only approve. Try it for one week? Reply YES.")
            return ReplyDecision("send", "Objection handled with one factual reframe and an effort-free trial; will not push twice.",
                                 body=body.replace("  ", " ").strip(), cta="binary_yes_no")

        if intent == "question":
            conv.question_count += 1
            if conv.question_count > 3:
                return ReplyDecision("wait", "Many questions in a row; pausing so the merchant can decide.",
                                     wait_seconds=3600)
            answer = self._answer(conv, message)
            body = (f"{answer} {deliverable.capitalize()} bana doon? Reply YES." if hi
                    else f"{answer} Shall I prepare {deliverable}? Reply YES.")
            return ReplyDecision("send", "Answered from known facts only (no guessing), then restated the single next step.",
                                 body=body, cta="binary_yes_no")

        # unclear
        conv.unclear_count += 1
        if conv.unclear_count > 1:
            return ReplyDecision("wait", "Still unclear after one clarifier; backing off.", wait_seconds=3600)
        body = (f"Bas confirm kar dijiye — {deliverable} ke saath aage badhun? Reply YES ya STOP."
                if hi else f"Just to confirm — shall I go ahead with {deliverable}? Reply YES or STOP.")
        return ReplyDecision("send", "Ambiguous reply; one binary clarifier instead of guessing.", body=body,
                             cta="binary_yes_no")

    def _advance(self, conv: Conversation, hi: bool, deliverable: str) -> ReplyDecision:
        if conv.stage == "pitch":
            conv.stage = "action"
            body = (f"Done — {re.sub(r'^the ', '', deliverable)} par kaam shuru kar diya hai; draft yahin bhejti hoon. "
                    f"Aap dekh lein, phir CONFIRM reply karein aur main live kar dungi."
                    if hi else
                    f"Great — starting on {deliverable} now; I'll send the draft here for your review. "
                    f"Once you've seen it, reply CONFIRM and I'll put it live.")
            return ReplyDecision("send", "Merchant committed: switched from pitch to action immediately, "
                                         "concrete deliverable, one confirm step.", body=body,
                                 cta="binary_confirm_cancel")
        if conv.stage == "action":
            conv.stage = "confirmed"
            body = ("Confirmed — live kar rahi hoon. Ho jaane par yahin update dungi. 🙏" if hi
                    else "Confirmed — putting it live now. I'll update you here once it's done.")
            return ReplyDecision("send", "Merchant confirmed; executing and closing the loop.", body=body, cta="none")
        return ReplyDecision("end", "Task already confirmed and completed; nothing further to ask.")

    # ------------------------------------------------------------ customer
    def _customer_turn(self, conv: Conversation, intent: str, message: str, lang: str,
                       now: datetime) -> ReplyDecision:
        choice = slot_choice(message)
        if intent in {"hostile", "opt_out"}:
            return ReplyDecision("end", "Customer opted out; stopping all messages to them.")
        if choice is not None or intent == "yes":
            slots = self._slots(conv)
            picked = slots[choice - 1] if choice and 0 < choice <= len(slots) else None
            body = (f"Booked for {picked}. See you then! Reply CHANGE if you need a different time."
                    if picked else "Thank you! We'll confirm your slot shortly. Reply CHANGE if you need a different time.")
            conv.stage = "confirmed"
            return ReplyDecision("send", "Customer accepted; confirmed the booking in one message.", body=body,
                                 cta="none")
        if intent == "later":
            return ReplyDecision("wait", "Customer asked for time.", wait_seconds=86400)
        if intent == "auto_reply":
            return ReplyDecision("end", "Automated response from the customer's number; not continuing.")
        if intent == "question":
            return ReplyDecision("send", "Customer question; routed to the merchant's team rather than guessing.",
                                 body="Good question — someone from the team will reply to you here shortly.",
                                 cta="none")
        return ReplyDecision("wait", "Unclear customer reply; giving them time.", wait_seconds=3600)

    # ------------------------------------------------------------- helpers
    def _silence(self, merchant_id: Optional[str], now: datetime, reason: str) -> None:
        if merchant_id:
            self.ledger.block_merchant(merchant_id, now + timedelta(days=SILENCE_DAYS), reason)

    def _slots(self, conv: Conversation) -> list[str]:
        trigger = self.store.get("trigger", conv.trigger_id) if conv.trigger_id else None
        payload = (trigger or {}).get("payload") or {}
        raw = payload.get("available_slots") or payload.get("next_session_options") or []
        return [s.get("label") for s in raw if isinstance(s, dict) and s.get("label")]

    def _reframe_fact(self, merchant_id: Optional[str]) -> str:
        merchant = self.store.get("merchant", merchant_id) if merchant_id else None
        if not merchant:
            return "It takes only a couple of minutes of your time."
        category = self.store.category_for(merchant) or {}
        perf = merchant.get("performance") or {}
        peer = category.get("peer_stats") or {}
        ctr, peer_ctr = perf.get("ctr"), peer.get("avg_ctr")
        if isinstance(ctr, (int, float)) and isinstance(peer_ctr, (int, float)) and ctr < peer_ctr:
            return (f"Right now your profile converts {ctr * 100:.1f}% of views into actions vs "
                    f"{peer_ctr * 100:.1f}% for similar businesses — that gap is what this closes.")
        if isinstance(perf.get("views"), int):
            return f"Your profile already gets {perf['views']:,} views a month — this makes more of them count."
        return "It takes only a couple of minutes of your time."

    def _answer(self, conv: Conversation, message: str) -> str:
        text = message.lower()
        merchant = self.store.get("merchant", conv.merchant_id) if conv.merchant_id else None
        trigger = self.store.get("trigger", conv.trigger_id) if conv.trigger_id else None
        payload = (trigger or {}).get("payload") or {}
        if any(w in text for w in ("price", "cost", "kitna", "how much", "charge", "fee")):
            if payload.get("renewal_amount"):
                return f"The renewal is ₹{int(payload['renewal_amount']):,}."
            offers = [o.get("title") for o in (merchant or {}).get("offers") or [] if o.get("status") == "active"]
            if offers:
                return f"Your live offer is {offers[0]}; I'd build on that."
            return "There's no extra charge from my side for drafting this."
        if any(w in text for w in ("when", "date", "deadline", "kab")):
            for key in ("deadline_iso", "date", "due_date", "match_time_iso", "stock_runs_out_iso"):
                if payload.get(key):
                    return f"The date on record is {str(payload[key])[:10]}."
        if any(w in text for w in ("what", "details", "more", "explain", "kya")):
            category = self.store.category_for(merchant) if merchant else None
            item_id = payload.get("top_item_id") or payload.get("digest_item_id") or payload.get("alert_id")
            for item in (category or {}).get("digest") or []:
                if item.get("id") == item_id and item.get("summary"):
                    return item["summary"].split(". ")[0].rstrip(".") + "."
        return "I don't have that detail with me right now, so I won't guess — I'll check and get back to you."
