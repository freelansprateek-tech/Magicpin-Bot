"""Every endpoint answers with the shape the judge expects."""

from tests.conftest import push


def test_healthz_shape(client):
    r = client.get("/v1/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert isinstance(data["uptime_seconds"], int)
    assert data["contexts_loaded"] == {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}


def test_metadata_shape(client):
    r = client.get("/v1/metadata")
    assert r.status_code == 200
    data = r.json()
    for key in ["team_name", "team_members", "model", "approach",
                "contact_email", "version", "submitted_at"]:
        assert key in data
    assert isinstance(data["team_members"], list)


def test_context_accepts_valid_push(client):
    r = push(client, "category", "dentists", {"slug": "dentists"})
    assert r.status_code == 200
    data = r.json()
    assert data["accepted"] is True
    assert data["ack_id"] == "ack_dentists_v1"
    assert data["stored_at"].endswith("Z")


def test_context_rejects_bad_scope_with_400(client):
    r = client.post("/v1/context", json={"scope": "planet", "context_id": "x", "version": 1, "payload": {}})
    assert r.status_code == 400
    assert r.json()["accepted"] is False
    assert r.json()["reason"] == "invalid_scope"


def test_context_rejects_missing_fields_with_400(client):
    r = client.post("/v1/context", json={"scope": "merchant"})
    assert r.status_code == 400
    assert r.json()["reason"] == "invalid_payload"


def test_tick_with_nothing_loaded_returns_empty(client):
    r = client.post("/v1/tick", json={"now": "2026-04-26T10:35:00Z", "available_triggers": ["trg_001"]})
    assert r.status_code == 200
    assert r.json() == {"actions": []}


def test_reply_returns_valid_action_without_merchant_id(client):
    # merchant_id deliberately omitted, as in api-call-examples 2.5-2.7
    r = client.post("/v1/reply", json={
        "conversation_id": "conv_001", "from_role": "merchant",
        "message": "Yes please send the abstract",
        "received_at": "2026-04-26T10:42:00Z", "turn_number": 2,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["action"] in {"send", "wait", "end"}
    assert data["rationale"]
    if data["action"] == "send":
        assert data["body"].strip()


def test_teardown_wipes_state(client):
    push(client, "category", "dentists", {"slug": "dentists"})
    assert client.get("/v1/healthz").json()["contexts_loaded"]["category"] == 1
    r = client.post("/v1/teardown")
    assert r.json() == {"wiped": True}
    assert client.get("/v1/healthz").json()["contexts_loaded"]["category"] == 0
