"""Phase 2: versioned context store — unit level and through HTTP."""

import threading

import pytest

from app.core.store import ContextStore
from tests.conftest import push


def test_first_push_is_accepted():
    s = ContextStore()
    r = s.put("merchant", "m1", 1, {"views": 10})
    assert r.accepted and r.current_version == 1
    assert s.get("merchant", "m1") == {"views": 10}


def test_same_version_is_a_noop():
    s = ContextStore()
    s.put("merchant", "m1", 1, {"views": 10})
    r = s.put("merchant", "m1", 1, {"views": 999})
    assert not r.accepted and r.current_version == 1
    assert s.get("merchant", "m1") == {"views": 10}      # unchanged


def test_higher_version_replaces():
    s = ContextStore()
    s.put("merchant", "m1", 1, {"views": 10, "old_field": True})
    r = s.put("merchant", "m1", 2, {"views": 2580})
    assert r.accepted and r.current_version == 2
    assert s.get("merchant", "m1") == {"views": 2580}    # replaced, not merged


def test_lower_version_is_ignored():
    s = ContextStore()
    s.put("merchant", "m1", 5, {"views": 50})
    r = s.put("merchant", "m1", 3, {"views": 30})
    assert not r.accepted and r.current_version == 5
    assert s.get("merchant", "m1") == {"views": 50}


def test_scope_is_part_of_the_key():
    s = ContextStore()
    s.put("merchant", "x", 1, {"a": 1})
    assert s.put("customer", "x", 1, {"b": 2}).accepted
    assert s.counts() == {"category": 0, "merchant": 1, "customer": 1, "trigger": 0}


def test_stored_payload_cannot_be_mutated_by_caller():
    s = ContextStore()
    payload = {"offers": [{"title": "A"}]}
    s.put("merchant", "m1", 1, payload)
    payload["offers"].append({"title": "B"})
    assert s.get("merchant", "m1") == {"offers": [{"title": "A"}]}


def test_invalid_scope_raises():
    with pytest.raises(ValueError):
        ContextStore().put("planet", "x", 1, {})


def test_concurrent_pushes_keep_highest_version():
    s = ContextStore()
    threads = [threading.Thread(target=s.put, args=("merchant", "m1", v, {"v": v})) for v in range(1, 51)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert s.version("merchant", "m1") == 50
    assert s.get("merchant", "m1") == {"v": 50}


# ------------------------------------------------------------------ via HTTP
def test_http_same_version_returns_409(client):
    assert push(client, "merchant", "m1", {"views": 1}).status_code == 200
    r = push(client, "merchant", "m1", {"views": 2})
    assert r.status_code == 409
    assert r.json() == {"accepted": False, "reason": "stale_version", "current_version": 1}


def test_http_version_bump_and_healthz_counts(client, seeds):
    for slug, cat in seeds["categories"].items():
        assert push(client, "category", slug, cat).status_code == 200
    for mid, m in seeds["merchants"].items():
        assert push(client, "merchant", mid, m).status_code == 200
    for cid, c in seeds["customers"].items():
        assert push(client, "customer", cid, c).status_code == 200
    counts = client.get("/v1/healthz").json()["contexts_loaded"]
    assert counts == {"category": 5, "merchant": 10, "customer": 15, "trigger": 0}

    meera = dict(seeds["merchants"]["m_001_drmeera_dentist_delhi"])
    meera["performance"] = {**meera["performance"], "views": 2580}
    r = push(client, "merchant", "m_001_drmeera_dentist_delhi", meera, version=2)
    assert r.status_code == 200 and r.json()["ack_id"].endswith("_v2")
    from app import state
    assert state.store.get("merchant", "m_001_drmeera_dentist_delhi")["performance"]["views"] == 2580
