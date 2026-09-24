"""HttpOmnigentClient wire-format test — verifies the real Omnigent server calls
(v1 session API) without a live server (monkeypatch urlopen)."""

from __future__ import annotations

import json

import pytest

from holodeck.config import Config
from holodeck.console import omnigent as omni_mod


class _Resp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capturing(monkeypatch, payload: bytes = b"{}"):
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append({
            "url": req.full_url,
            "method": req.method,
            "headers": {k.lower(): v for k, v in req.header_items()},
            "data": json.loads(req.data) if req.data else None,
        })
        return _Resp(payload)

    monkeypatch.setattr(omni_mod.urllib.request, "urlopen", fake_urlopen)
    cfg = Config(omnigent_url="https://omni.example", omnigent_token="tok",
                 omnigent_agent="compliance-fixer")
    return omni_mod.HttpOmnigentClient(cfg), seen


def test_start_posts_managed_session_with_bearer_and_prompt(monkeypatch):
    client, seen = _capturing(monkeypatch, payload=b'{"session_id": "sess-1"}')
    sid = client.start("CPL-1", "compliance", "do the thing")
    assert sid == "sess-1"
    call = seen[-1]
    assert call["url"] == "https://omni.example/v1/sessions"
    assert call["method"] == "POST"
    assert call["headers"]["authorization"] == "Bearer tok"
    # host_type=managed is what makes the server provision via the holodeck provider
    assert call["data"]["host_type"] == "managed"
    assert "host_id" not in call["data"] and "workspace" not in call["data"]
    assert call["data"]["agent_id"] == "compliance-fixer"
    # prompt rides in as the first user message (SessionEventInput shape)
    item = call["data"]["initial_items"][0]
    assert item["type"] == "message"
    assert item["data"]["content"][0]["text"] == "do the thing"
    assert call["data"]["labels"]["holodeck_ticket"] == "CPL-1"


def test_status_surfaces_pending_elicitation(monkeypatch):
    # a pending elicitation == the agent is blocked on a human decision:
    # waiting True + the elicitation_id + question surfaced for the bridge.
    client, seen = _capturing(monkeypatch, payload=(
        b'{"status": "running", "pending_elicitations": '
        b'[{"elicitation_id": "elicit_1", "params": {"message": "Deploy to prod?"}}]}'))
    st = client.status("sess-1")
    assert st.state == "running" and st.waiting is True
    assert st.elicitation_id == "elicit_1"
    assert st.question == "Deploy to prod?"
    assert seen[-1]["url"].endswith("/v1/sessions/sess-1") and seen[-1]["method"] == "GET"


def test_status_pending_inputs_alone_is_not_waiting(monkeypatch):
    # queued messages are NOT a human-decision pause -> not "waiting".
    client, _ = _capturing(
        monkeypatch, payload=b'{"status": "running", "pending_inputs": [{"q": "?"}]}')
    st = client.status("sess-1")
    assert st.waiting is False and st.elicitation_id is None


def test_status_not_waiting_when_no_pending(monkeypatch):
    client, _ = _capturing(monkeypatch, payload=b'{"status": "running", "pending_inputs": []}')
    assert client.status("sess-1").waiting is False


def test_send_posts_message_event(monkeypatch):
    client, seen = _capturing(monkeypatch, payload=b'{}')
    client.send("sess-1", "handle the null case")
    assert seen[-1]["url"].endswith("/v1/sessions/sess-1/events")
    assert seen[-1]["method"] == "POST"
    assert seen[-1]["data"]["type"] == "message"
    assert seen[-1]["data"]["data"]["content"][0]["text"] == "handle the null case"


def test_approve_posts_approval_event(monkeypatch):
    client, seen = _capturing(monkeypatch, payload=b'{}')
    client.approve("sess-1", "elicit_9", accept=True)
    call = seen[-1]
    assert call["url"].endswith("/v1/sessions/sess-1/events") and call["method"] == "POST"
    assert call["data"]["type"] == "approval"
    assert call["data"]["data"] == {"elicitation_id": "elicit_9", "action": "accept"}


def test_approve_decline_posts_action_decline(monkeypatch):
    client, seen = _capturing(monkeypatch, payload=b'{}')
    client.approve("sess-1", "elicit_9", accept=False)
    assert seen[-1]["data"]["data"]["action"] == "decline"


def test_send_recovers_from_no_runner_bound(monkeypatch):
    # WORKAROUND path: first /events 503s "no runner bound" (the managed
    # runner-bind race); client looks up host_id/workspace, launches a runner,
    # and retries the event, which then succeeds.
    import io
    calls = []
    events = {"n": 0}

    def fake_urlopen(req, timeout=None):
        path = req.full_url.split("omni.example")[-1]
        calls.append((req.method, path))
        if req.method == "POST" and path.endswith("/events"):
            events["n"] += 1
            if events["n"] == 1:
                raise omni_mod.urllib.error.HTTPError(
                    req.full_url, 503, "Service Unavailable", {},
                    io.BytesIO(b'{"error":"No runner bound for session"}'))
            return _Resp(b"{}")                              # retry succeeds
        if req.method == "GET" and "/v1/sessions/" in path:
            return _Resp(b'{"host_id":"host_x","workspace":"/ws"}')
        return _Resp(b"{}")                                  # /hosts/.../runners etc.

    monkeypatch.setattr(omni_mod.urllib.request, "urlopen", fake_urlopen)
    cfg = Config(omnigent_url="https://omni.example", omnigent_token="tok")
    omni_mod.HttpOmnigentClient(cfg).send("conv_1", "please continue")

    assert events["n"] == 2                                  # failed once, retried once
    assert ("GET", "/v1/sessions/conv_1") in calls           # looked up host_id/workspace
    assert any(m == "POST" and p.endswith("/hosts/host_x/runners") for m, p in calls)


def test_missing_url_raises():
    cfg = Config(omnigent_url="", omnigent_token="t")
    with pytest.raises(omni_mod.OmnigentError):
        omni_mod.HttpOmnigentClient(cfg).status("x")
