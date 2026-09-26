"""Phase 3: consent gates, scoring and the tick planner (incl. the expanded dataset)."""

from app import state
from app.core.timeutil import now_from
from app.decision.consent import check_customer_consent
from app.decision.planner import plan_tick
from tests.conftest import push, push_all

NOW = "2026-04-26T10:30:00Z"


# ------------------------------------------------------------------ consent
def test_consent_allows_matching_scope(seeds):
    trg = seeds["triggers"]["trg_003_recall_due_priya"]
    assert check_customer_consent(trg, seeds["customers"]["c_001_priya_for_m001"]).allowed


def test_consent_blocks_missing_customer(seeds):
    trg = seeds["triggers"]["trg_003_recall_due_priya"]
    d = check_customer_consent(trg, None)
    assert not d.allowed and d.reason == "customer_context_missing"


def test_consent_blocks_opted_out_walk_in(seeds):
    trg = {"kind": "customer_lapsed_soft", "scope": "customer"}
    d = check_customer_consent(trg, seeds["customers"]["c_015_anonymous_for_m010"])
    assert not d.allowed


def test_consent_blocks_scope_mismatch():
    customer = {"preferences": {"reminder_opt_in": True},
                "consent": {"opted_in_at": "2025-09-01", "scope": ["promotional_offers"]}, "state": "active"}
    assert not check_customer_consent({"kind": "recall_due"}, customer).allowed
    assert check_customer_consent({"kind": "customer_lapsed_soft"}, customer).allowed


def test_consent_blocks_churned_without_winback_scope():
    customer = {"preferences": {"reminder_opt_in": True}, "state": "churned",
                "consent": {"opted_in_at": "2025-09-01", "scope": ["promotional_offers"]}}
    assert not check_customer_consent({"kind": "customer_lapsed_soft"}, customer).allowed


# ------------------------------------------------------------------ planner
def _load_seeds(client, seeds):
    push_all(client, seeds)


def test_planner_one_action_per_recipient_and_cap(client, expanded):
    push_all(client, expanded)
    chosen, _ = plan_tick(state.store, state.ledger, now_from(NOW), list(expanded["triggers"]))
    assert 0 < len(chosen) <= 20
    recipients = [c.recipient for c in chosen]
    assert len(recipients) == len(set(recipients))          # one action per recipient per tick
    scores = [c.score.total for c in chosen]
    assert scores == sorted(scores, reverse=True)


def test_planner_is_deterministic(client, expanded):
    push_all(client, expanded)
    ids = list(expanded["triggers"])
    a, _ = plan_tick(state.store, state.ledger, now_from(NOW), ids)
    b, _ = plan_tick(state.store, state.ledger, now_from(NOW), list(reversed(ids)))
    assert [c.trigger_id for c in a] == [c.trigger_id for c in b]


def test_planner_skips_expired_trigger_it_picked_itself(client, seeds):
    _load_seeds(client, seeds)
    # No hint from the judge -> the bot checks expiry itself. trg_010 (IPL) expired on 26 Apr.
    chosen, skips = plan_tick(state.store, state.ledger, now_from("2026-04-28T10:00:00Z"), [])
    assert "trg_010_ipl_match_delhi" not in [c.trigger_id for c in chosen]
    assert any(s.trigger_id == "trg_010_ipl_match_delhi" and s.reason == "trigger_expired" for s in skips)


def test_judge_listed_trigger_is_treated_as_active(client, seeds):
    """judge_simulator.py sends real wall-clock time, after every seed trigger's expiry."""
    _load_seeds(client, seeds)
    chosen, _ = plan_tick(state.store, state.ledger, now_from("2026-09-26T10:00:00Z"),
                          ["trg_001_research_digest_dentists"])
    assert [c.trigger_id for c in chosen] == ["trg_001_research_digest_dentists"]


def test_planner_skips_customer_trigger_without_customer_context(client, seeds):
    for slug, cat in seeds["categories"].items():
        push(client, "category", slug, cat)
    push(client, "merchant", "m_001_drmeera_dentist_delhi", seeds["merchants"]["m_001_drmeera_dentist_delhi"])
    push(client, "trigger", "trg_003_recall_due_priya", seeds["triggers"]["trg_003_recall_due_priya"])
    chosen, skips = plan_tick(state.store, state.ledger, now_from(NOW), ["trg_003_recall_due_priya"])
    assert chosen == [] and skips[0].reason == "customer_not_loaded"


def test_consent_blocked_customer_trigger_becomes_merchant_note(client, seeds):
    push_all(client, seeds)
    priya = dict(seeds["customers"]["c_001_priya_for_m001"])
    priya["consent"] = {"opted_in_at": "2025-11-04", "scope": ["promotional_offers"]}  # no recall consent
    push(client, "customer", priya["customer_id"], priya, version=2)
    chosen, _ = plan_tick(state.store, state.ledger, now_from(NOW), ["trg_003_recall_due_priya"])
    assert len(chosen) == 1
    assert chosen[0].customer_facing is False
    assert "consent" in chosen[0].rationale().lower()


def test_weak_mismatched_trigger_is_held_back(client, expanded):
    """Placeholder recall for a gym + consent mismatch scores below the bar: restraint."""
    push_all(client, expanded)
    chosen, skips = plan_tick(state.store, state.ledger, now_from(NOW), ["trg_066_recall_due_m_008_zenyoga_gym_ch"])
    assert chosen == [] and skips[0].reason.startswith("below_threshold")


def test_tick_marks_suppression_key_so_nudge_is_never_repeated(client, seeds):
    _load_seeds(client, seeds)
    body = {"now": NOW, "available_triggers": ["trg_001_research_digest_dentists"]}
    first = client.post("/v1/tick", json=body).json()["actions"]
    assert len(first) == 1
    second = client.post("/v1/tick", json={**body, "now": "2026-04-26T12:30:00Z"}).json()["actions"]
    assert second == []


def test_tick_actions_have_every_required_field(client, expanded):
    push_all(client, expanded)
    actions = client.post("/v1/tick", json={"now": NOW, "available_triggers": list(expanded["triggers"])}).json()["actions"]
    required = {"conversation_id", "merchant_id", "customer_id", "send_as", "trigger_id", "template_name",
                "template_params", "body", "cta", "suppression_key", "rationale"}
    assert actions
    for a in actions:
        assert required <= set(a)
        assert a["body"].strip()
        assert a["send_as"] in {"vera", "merchant_on_behalf"}
        if a["send_as"] == "merchant_on_behalf":
            assert a["customer_id"]
    assert len({a["conversation_id"] for a in actions}) == len(actions)


def test_higher_urgency_wins_for_same_merchant(client, seeds):
    _load_seeds(client, seeds)
    # Dr. Meera: research (urgency 2) vs DCI compliance (urgency 4)
    chosen, _ = plan_tick(state.store, state.ledger, now_from(NOW),
                          ["trg_001_research_digest_dentists", "trg_002_compliance_dci_radiograph"])
    assert [c.trigger_id for c in chosen] == ["trg_002_compliance_dci_radiograph"]


def test_customer_reminder_not_blocked_by_note_to_the_merchant(client, seeds):
    """Phase 3 of the judge pushes customer recalls right after the merchant may have
    been messaged: the cooldown must be per recipient, not per merchant."""
    from app.decision.suppression import recipient_key
    push_all(client, seeds)
    state.ledger.mark_sent("some_other_key", recipient_key("m_001_drmeera_dentist_delhi"), now_from(NOW))
    chosen, skips = plan_tick(state.store, state.ledger, now_from(NOW), ["trg_003_recall_due_priya"])
    assert [c.trigger_id for c in chosen] == ["trg_003_recall_due_priya"], skips
    assert chosen[0].customer_id == "c_001_priya_for_m001"


def test_same_recipient_cooldown_then_released(client, seeds):
    from app.decision.suppression import recipient_key
    push_all(client, seeds)
    mid = "m_003_studio11_salon_hyderabad"
    state.ledger.mark_sent("x", recipient_key(mid), now_from(NOW))
    chosen, skips = plan_tick(state.store, state.ledger, now_from("2026-04-26T10:40:00Z"), ["trg_008_curious_ask_studio11"])
    assert not chosen and skips[0].reason == "recipient_cooldown"
    chosen, _ = plan_tick(state.store, state.ledger, now_from("2026-04-26T11:05:00Z"), ["trg_008_curious_ask_studio11"])
    assert [c.trigger_id for c in chosen] == ["trg_008_curious_ask_studio11"]
