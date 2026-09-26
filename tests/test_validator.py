"""Phase 4: grounding validator."""

import pytest

from app.compose.facts import build_fact_sheet
from app.compose.validator import normalize_number, numbers_in, validate
from app.core.timeutil import now_from


@pytest.fixture()
def meera_sheet(seeds):
    return build_fact_sheet(
        category=seeds["categories"]["dentists"],
        merchant=seeds["merchants"]["m_001_drmeera_dentist_delhi"],
        trigger=seeds["triggers"]["trg_001_research_digest_dentists"],
        customer=None, customer_facing=False, consent_reason=None,
        now=now_from("2026-04-26T10:00:00Z"),
    )


def test_number_normalisation():
    assert normalize_number("2,100") == "2100"
    assert normalize_number("2.10") == "2.1"
    assert numbers_in("₹1,499 and 38% of 2,100") == {"1499", "38", "2100"}


def test_grounded_message_passes(meera_sheet):
    body = ("Dr. Meera, JIDA Oct 2026 p.14: a 2,100-patient trial found 38% lower caries recurrence "
            "with 3-month recall. You have 124 high-risk adults. Want the summary? Reply YES.")
    assert validate(body, "binary_yes_no", meera_sheet).ok


def test_invented_number_is_rejected(meera_sheet):
    r = validate("Dr. Meera, 57% of your patients are overdue. Reply YES.", "binary_yes_no", meera_sheet)
    assert not r.ok and any("57" in e for e in r.errors)


def test_invented_price_is_rejected(meera_sheet):
    r = validate("Dr. Meera, run Teeth Whitening @ ₹999 this week? Reply YES.", "binary_yes_no", meera_sheet)
    assert not r.ok and any("999" in e for e in r.errors)


def test_url_is_rejected(meera_sheet):
    r = validate("Dr. Meera, read more at www.example.com. Reply YES.", "binary_yes_no", meera_sheet)
    assert any("URL" in e for e in r.errors)


def test_taboo_is_rejected(meera_sheet):
    r = validate("Dr. Meera, guaranteed results with 3-month recall. Reply YES.", "binary_yes_no", meera_sheet)
    assert any("taboo" in e for e in r.errors)


def test_multiple_ctas_rejected(meera_sheet):
    r = validate("Dr. Meera, reply YES for the summary or reply STOP to skip.", "binary_yes_no", meera_sheet)
    assert any("more than one" in e for e in r.errors)


def test_must_address_recipient(meera_sheet):
    r = validate("JIDA Oct 2026 p.14 is out. Reply YES.", "binary_yes_no", meera_sheet)
    assert any("address" in e for e in r.errors)


def test_repeat_is_rejected(meera_sheet):
    body = "Dr. Meera, want the JIDA summary? Reply YES."
    assert any("repeats" in e for e in validate(body, "binary_yes_no", meera_sheet, [body]).errors)


def test_preamble_is_rejected(meera_sheet):
    r = validate("Dr. Meera, I hope you're doing well. Want the summary? Reply YES.", "binary_yes_no", meera_sheet)
    assert any("preamble" in e for e in r.errors)


def test_small_effort_promise_is_allowed_but_big_ones_are_not(meera_sheet):
    ok = validate("Dr. Meera, JIDA's fluoride item is worth a look (2-min read). "
                  "I can have a patient note ready in 10 min. Want it? Reply YES.", "binary_yes_no", meera_sheet)
    assert ok.ok, ok.errors
    bad = validate("Dr. Meera, patients wait 55 minutes on Sundays. Want a fix? Reply YES.", "binary_yes_no", meera_sheet)
    assert not bad.ok and any("55" in e for e in bad.errors)
