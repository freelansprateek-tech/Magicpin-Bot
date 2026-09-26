"""Phase 4: composer — templates, LLM path with a fake provider, fallback, cache, bot.py."""

import tempfile

import pytest

import bot
from app.compose.composer import Composer
from app.core.timeutil import now_from
from app.decision.consent import check_customer_consent
from app.llm.base import LLMError, LLMProvider
from app.llm.cache import DiskCache

NOW = now_from("2026-04-26T10:00:00Z")


class FakeProvider(LLMProvider):
    """Returns scripted responses so tests never touch the network."""
    name = "fake"

    def __init__(self, responses):
        super().__init__("fake-model", 1.0)
        self.responses = list(responses)
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _meera_args(seeds):
    return dict(category=seeds["categories"]["dentists"],
                merchant=seeds["merchants"]["m_001_drmeera_dentist_delhi"],
                trigger=seeds["triggers"]["trg_001_research_digest_dentists"],
                customer=None, customer_facing=False, consent_reason=None, now=NOW)


def _composer(provider=None):
    return Composer(provider=provider, cache=DiskCache(tempfile.mkdtemp()), use_default_provider=False)


def test_every_expanded_trigger_template_passes_validator(expanded):
    comp = _composer()
    for tid, trg in expanded["triggers"].items():
        merchant = expanded["merchants"][trg["merchant_id"]]
        customer = expanded["customers"].get(trg.get("customer_id") or "")
        facing, reason = False, None
        if customer is not None or trg.get("scope") == "customer":
            d = check_customer_consent(trg, customer)
            facing, reason = d.allowed, d.reason
        msg = comp.compose(category=expanded["categories"][merchant["category_slug"]], merchant=merchant,
                           trigger=trg, customer=customer, customer_facing=facing, consent_reason=reason, now=NOW)
        assert msg.meta["validator_errors"] == [], (tid, msg.body, msg.meta["validator_errors"])
        assert msg.send_as == ("merchant_on_behalf" if facing else "vera")


def test_template_output_is_deterministic(seeds):
    a = _composer().compose(**_meera_args(seeds)).to_dict()
    b = _composer().compose(**_meera_args(seeds)).to_dict()
    assert a == b


def test_valid_llm_draft_is_used(seeds):
    good = ('{"body": "Dr. Meera, JIDA Oct 2026 p.14: 38% lower caries recurrence with 3-month recall '
            '(2,100 patients). Worth it for your 124 high-risk adults. Want the 2-min summary? Reply YES.", '
            '"cta": "binary_yes_no", "rationale": "digest + cohort"}')
    provider = FakeProvider([good])
    msg = _composer(provider).compose(**_meera_args(seeds))
    assert msg.meta["path"] == "llm" and "124 high-risk" in msg.body


def test_hallucinating_llm_is_retried_then_falls_back_to_template(seeds):
    bad = '{"body": "Dr. Meera, 73% of clinics already switched. Reply YES.", "cta": "binary_yes_no", "rationale": "x"}'
    provider = FakeProvider([bad, bad])
    msg = _composer(provider).compose(**_meera_args(seeds))
    assert provider.calls == 2
    assert msg.meta["path"] == "template"
    assert "73" not in msg.body
    assert msg.meta["llm_errors"]


def test_llm_error_falls_back_to_template(seeds):
    provider = FakeProvider([LLMError("timeout")])
    msg = _composer(provider).compose(**_meera_args(seeds))
    assert msg.meta["path"] == "template" and msg.body


def test_cache_makes_second_call_free(seeds):
    good = ('{"body": "Dr. Meera, JIDA Oct 2026 p.14 shows 38% lower caries recurrence with 3-month recall. '
            'Want the summary? Reply YES.", "cta": "binary_yes_no", "rationale": "r"}')
    cache = DiskCache(tempfile.mkdtemp())
    provider = FakeProvider([good])
    first = Composer(provider=provider, cache=cache).compose(**_meera_args(seeds))
    second = Composer(provider=provider, cache=cache).compose(**_meera_args(seeds))
    assert provider.calls == 1
    assert second.meta["path"] == "cache" and first.body == second.body


def test_customer_facing_recall_uses_real_slots_and_language(seeds):
    trg = seeds["triggers"]["trg_003_recall_due_priya"]
    msg = _composer().compose(category=seeds["categories"]["dentists"],
                              merchant=seeds["merchants"]["m_001_drmeera_dentist_delhi"], trigger=trg,
                              customer=seeds["customers"]["c_001_priya_for_m001"], customer_facing=True,
                              consent_reason="consent_ok", now=NOW)
    assert msg.send_as == "merchant_on_behalf"
    assert msg.cta == "multi_choice_slot"
    assert "Wed 5 Nov, 6pm" in msg.body and "Priya" in msg.body
    assert "slots ready hain" in msg.body          # hi-en mix honoured


def test_generated_dentist_is_not_double_titled(expanded):
    m = expanded["merchants"]["m_011_dr_sameer_dentist_bangalore"]
    trg = {"id": "t", "kind": "dormant_with_vera", "scope": "merchant", "payload": {}, "urgency": 2,
           "merchant_id": m["merchant_id"], "suppression_key": "k"}
    msg = _composer().compose(category=expanded["categories"]["dentists"], merchant=m, trigger=trg, customer=None,
                              customer_facing=False, consent_reason=None, now=NOW)
    assert "Dr. Dr." not in msg.body and msg.body.startswith("Dr. Sameer")


def test_bot_compose_contract(seeds):
    out = bot.compose(seeds["categories"]["dentists"], seeds["merchants"]["m_001_drmeera_dentist_delhi"],
                      seeds["triggers"]["trg_001_research_digest_dentists"], None)
    for key in ("body", "cta", "send_as", "suppression_key", "rationale"):
        assert out[key]
    assert out["suppression_key"] == "research:dentists:2026-W17"
