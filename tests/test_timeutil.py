"""Dates and times shown to merchants must be Indian time, not UTC."""

from app.core.timeutil import days_between, human_date, human_time, is_expired, now_from


def test_times_are_shown_in_ist():
    assert human_time("2026-04-26T19:30:00+05:30") == "7:30pm"      # IPL match, not "2:00pm"
    assert human_time("2026-04-26T10:30:00Z") == "4:00pm"


def test_dates_are_ist_calendar_dates():
    # Midnight IST on 28 Apr is still 27 Apr in UTC — the merchant must see 28 Apr.
    assert human_date("2026-04-28T00:00:00+05:30") == "28 Apr 2026"
    assert human_date("2026-12-15") == "15 Dec 2026"


def test_days_between_uses_ist_calendar():
    now = now_from("2026-04-26T10:30:00Z")          # 4pm IST, 26 Apr
    assert days_between(now, "2026-04-26T19:30:00+05:30") == 0      # "tonight"
    assert days_between(now, "2026-04-28T00:00:00+05:30") == 2


def test_now_from_never_uses_server_clock():
    assert now_from(None) == now_from("2026-04-26T10:30:00Z")
    assert now_from("not a date") == now_from("2026-04-26T10:30:00Z")


def test_is_expired():
    assert is_expired("2026-05-03T00:00:00Z", "2026-05-04T00:00:00Z")
    assert not is_expired("2026-05-03T00:00:00Z", "2026-04-26T10:30:00Z")
    assert not is_expired(None, "2026-04-26T10:30:00Z")
