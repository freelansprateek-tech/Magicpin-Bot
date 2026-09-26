"""Phase 5: reply intents + conversation policy, incl. the judge's replay scenarios."""

import pytest

from app.reply.intent import classify, detect_language
from tests.conftest import push_all

MID = "m_001_drmeera_dentist_delhi"


def reply(client, conv, msg, turn=2, mid=MID, role="merchant", cid=None):
    r = client.post("/v1/reply", json={"conversation_id": conv, "merchant_id": mid, "customer_id": cid,
                                       "from_role": role, "message": msg,
                                       "received_at": "2026-04-26T10:42:00Z", "turn_number": turn})
    assert r.status_code == 200
    return r.json()


# ------------------------------------------------------------ classifier
@pytest.mark.parametrize("msg,intent", [
    ("Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly.", "auto_reply"),
    ("Aapki jaankari ke liye bahut-bahut shukriya. Main aapki yeh sabhi baatein team tak pahuncha deti hoon.", "auto_reply"),
    ("Why are you bothering me. This is useless. Stop sending these.", "hostile"),
    ("Not interested. Stop messaging me.", "opt_out"),
    ("Btw can you also help me with my GST filing this month?", "off_topic"),
    ("Ok lets do it. Whats next?", "yes"),
    ("Yes please send the abstract. Also draft the patient WhatsApp.", "yes"),
    ("Mujhe magicpin judrna hai.", "yes"),
    ("Busy right now, call me tomorrow", "later"),
    ("Too expensive for me", "objection"),
    ("How much does it cost?", "question"),
    ("hmm", "unclear"),
])
def test_classifier(msg, intent):
    assert classify(msg) == intent


def test_language_detection():
    assert detect_language("haan theek hai, kar do") == "hi-en"
    assert detect_language("Yes please go ahead") == "en"


# ------------------------------------------------------------ replay tests
def test_auto_reply_hell_across_conversation_ids(client):
    """judge_simulator.py uses a NEW conversation_id on every auto-reply turn."""
    auto = "Thank you for contacting us! Our team will respond shortly."
    actions = [reply(client, f"conv_auto_{i}", auto, turn=i + 1)["action"] for i in range(1, 5)]
    assert actions[0] == "send"
    assert actions[1] == "wait"
    assert actions[2] == "end"


def test_auto_reply_first_response_is_a_short_owner_nudge(client):
    out = reply(client, "conv_a", "Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly.")
    assert out["action"] == "send" and "auto-reply" in out["body"].lower()


def test_intent_transition_switches_to_action(client):
    out = reply(client, "conv_intent_1", "Ok lets do it. Whats next?")
    body = out["body"].lower()
    assert out["action"] == "send"
    assert not any(q in body for q in ["would you", "do you", "can you tell", "what if", "how about"])
    assert any(a in body for a in ["done", "sending", "draft", "here", "confirm", "proceed", "next"])


def test_confirm_after_action_then_close(client):
    assert reply(client, "c1", "yes go ahead")["cta"] == "binary_confirm_cancel"
    second = reply(client, "c1", "CONFIRM", turn=3)
    assert second["action"] == "send" and "confirmed" in second["body"].lower()
    assert reply(client, "c1", "ok", turn=4)["action"] == "end"


def test_hostile_ends_and_silences_merchant(client, seeds):
    push_all(client, seeds)
    out = reply(client, "conv_hostile", "Stop messaging me. This is useless spam.")
    assert out["action"] == "end"
    tick = client.post("/v1/tick", json={"now": "2026-04-26T11:00:00Z",
                                         "available_triggers": ["trg_001_research_digest_dentists"]}).json()
    assert tick["actions"] == []                     # merchant is suppressed


def test_hostile_then_off_topic_stays_closed(client):
    reply(client, "conv_h2", "This is useless, stop bothering me")
    assert reply(client, "conv_h2", "can you also help me file my GST?", turn=3)["action"] == "end"


def test_off_topic_declined_politely_once(client):
    first = reply(client, "conv_gst", "Btw can you also help me with my GST filing this month?")
    assert first["action"] == "send" and "ca" in first["body"].lower()
    second = reply(client, "conv_gst", "What about my income tax return?", turn=3)
    assert second["action"] == "wait"


def test_opt_out_ends(client):
    assert reply(client, "conv_no", "Not interested. Stop messaging me.")["action"] == "end"


def test_later_waits(client):
    out = reply(client, "conv_later", "Busy right now, later")
    assert out["action"] == "wait" and out["wait_seconds"] > 0


def test_never_repeats_a_body_in_one_conversation(client):
    bodies = []
    for turn, msg in enumerate(["hmm", "hmm", "hmm"], start=2):
        out = reply(client, "conv_rep", msg, turn=turn)
        if out["action"] == "send":
            bodies.append(out["body"])
    assert len(bodies) == len(set(bodies))


def test_hinglish_reply_gets_hinglish_answer(client):
    out = reply(client, "conv_hi", "haan theek hai, kar do")
    assert out["action"] == "send" and ("kaam" in out["body"] or "bhejti" in out["body"])


def test_customer_slot_choice_confirms_booking(client, seeds):
    push_all(client, seeds)
    tick = client.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z",
                                         "available_triggers": ["trg_003_recall_due_priya"]}).json()
    action = tick["actions"][0]
    assert action["send_as"] == "merchant_on_behalf"
    out = reply(client, action["conversation_id"], "1", role="customer", cid="c_001_priya_for_m001")
    assert out["action"] == "send" and "Wed 5 Nov, 6pm" in out["body"]


def test_end_to_end_tick_then_yes(client, seeds):
    push_all(client, seeds)
    tick = client.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z",
                                         "available_triggers": ["trg_001_research_digest_dentists"]}).json()
    conv = tick["actions"][0]["conversation_id"]
    out = reply(client, conv, "Yes please send the abstract. Also draft the patient WhatsApp.")
    assert out["action"] == "send" and "summary" in out["body"]
