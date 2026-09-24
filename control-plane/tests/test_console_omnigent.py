"""Console Omnigent-driver path — trigger via Omnigent, correlate session↔lease,
waiting-for-input, reiterate, and the author tunnel command. All against the
FakeOmnigentClient + FakeLeaseClient (no server, no golden)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from holodeck.config import Config
from holodeck.console.driver import OmnigentDriver
from holodeck.console.leaseclient import FakeLeaseClient
from holodeck.console.manager import ConsoleError, ConsoleManager
from holodeck.console.omnigent import FakeOmnigentClient
from holodeck.console.routes import mount_console
from holodeck.console.store import ConsoleStore


def _omnigent_manager():
    cfg = Config(provider="fake", console_driver="omnigent", console_ssh_host="ec2-box")
    lease = FakeLeaseClient()
    omni = FakeOmnigentClient(lease)          # shares the lease client (simulates the provider strike)
    mgr = ConsoleManager(cfg, ConsoleStore(), lease, OmnigentDriver(lease, omni, cfg))
    return mgr, omni


def test_trigger_via_omnigent_captures_session_and_lease():
    mgr, _ = _omnigent_manager()
    rec = mgr.trigger("CPL-1", "compliance")
    assert rec.lease_id == "cpl-1"            # lease struck by the (fake) provider
    assert rec.session_id == "s-0001"         # Omnigent session captured
    assert rec.status == "ready"
    assert rec.preview_port and rec.preview_url


def test_waiting_for_input_then_reiterate():
    mgr, omni = _omnigent_manager()
    rec = mgr.trigger("CPL-1", "compliance")
    omni.force_waiting.add(rec.session_id)    # agent pauses for input
    mgr.refresh()
    assert mgr.store.get("CPL-1").status == "waiting-input"
    assert mgr.store.get("CPL-1").waiting is True

    mgr.reiterate("CPL-1", "also handle the empty-list case")
    assert mgr.store.get("CPL-1").waiting is False
    assert omni._sessions["s-0001"]["messages"] == ["also handle the empty-list case"]


def test_board_has_author_tunnel_command():
    mgr, _ = _omnigent_manager()
    mgr.trigger("CPL-1", "compliance")
    row = mgr.board()[0]
    assert row["tunnel_cmd"].startswith("ssh -L ")
    assert "ec2-box" in row["tunnel_cmd"]
    assert row["local_url"].startswith("http://localhost:")


def test_direct_driver_reiterate_is_rejected():
    # the direct driver has no session -> reiterate is a clean 400, not a crash
    from holodeck.console.driver import DirectDriver
    cfg = Config(provider="fake", console_driver="direct")
    lease = FakeLeaseClient()
    mgr = ConsoleManager(cfg, ConsoleStore(), lease, DirectDriver(lease))
    mgr.trigger("CPL-1", "compliance")
    with pytest.raises(ConsoleError):
        mgr.reiterate("CPL-1", "try again")


def test_omnigent_routes_via_app():
    cfg = Config(provider="fake", console_driver="omnigent", console_ssh_host="ec2-box")
    lease = FakeLeaseClient()
    omni = FakeOmnigentClient(lease)
    app = FastAPI()
    mount_console(app, cfg, lease_client=lease, omni_client=omni, start_poller=False)
    c = TestClient(app)

    r = c.post("/console/trigger", json={"ticket": "CPL-9", "app": "compliance"})
    assert r.status_code == 200, r.text
    assert r.json()["session_id"] == "s-0001"

    rr = c.post("/console/tasks/CPL-9/reiterate", json={"feedback": "fix the edge case"})
    assert rr.status_code == 200

    rc = c.get("/console", follow_redirects=False)   # runs board folded into /ops
    assert rc.status_code in (307, 308) and rc.headers["location"] == "/ops"


def test_board_surfaces_agent_session_url_and_answer_verdict():
    # the combined console needs: agent name + session URL on each run, and a
    # yes/no answer that resolves a pending elicitation as an approval.
    from holodeck.config import Config
    from holodeck.console.driver import OmnigentDriver
    from holodeck.console.leaseclient import FakeLeaseClient
    from holodeck.console.manager import ConsoleManager
    from holodeck.console.omnigent import FakeOmnigentClient
    from holodeck.console.store import ConsoleStore

    cfg = Config(provider="fake", console_driver="omnigent")
    lease = FakeLeaseClient()
    omni = FakeOmnigentClient(lease)
    mgr = ConsoleManager(cfg, ConsoleStore(), lease, OmnigentDriver(lease, omni, cfg))

    rec = mgr.trigger("CPL-1", "compliance")
    sid = rec.session_id
    omni.force_elicitation[sid] = ("elicit_1", "Deploy to prod?")
    mgr.refresh()

    row = mgr.board()[0]
    assert row["agent_name"] == "fake-agent"
    assert row["session_url"].endswith(sid)
    assert row["question"] == "Deploy to prod?" and row["waiting"] is True
    # the run carries the bound sandbox id so a managed lease row can map back to
    # its ticket (the fake strikes the lease under holo_id(ticket) = "cpl-1")
    assert row["workspace"] == "cpl-1"

    _, verdict = mgr.answer("CPL-1", "yes")
    assert verdict is True and omni.approvals == [(sid, "elicit_1", True)]


def test_extract_token_across_response_shapes():
    from holodeck.console.omnigent import _extract_token
    assert _extract_token({"token": "T"}) == "T"          # Omnigent's shape
    assert _extract_token({"access_token": "A"}) == "A"
    assert _extract_token({"data": {"token": "N"}}) == "N"  # nested
    assert _extract_token({"nope": 1}) == ""
    assert _extract_token("not-a-dict") == ""


def test_http_client_account_login_is_lazy_and_cached(monkeypatch):
    # no static token -> the client logs in with username/password once and caches it.
    from holodeck.config import Config
    from holodeck.console.omnigent import HttpOmnigentClient

    cfg = Config(provider="fake")
    cfg.omnigent_url = "http://omni.local"
    cfg.omnigent_token = ""                 # force the account-login path
    cfg.omnigent_user, cfg.omnigent_password = "pgupta", "secret"
    c = HttpOmnigentClient(cfg)

    calls = {"n": 0}
    monkeypatch.setattr(c, "_login", lambda: (calls.__setitem__("n", calls["n"] + 1) or "TESTTOK"))
    assert c._auth_token() == "TESTTOK"     # logs in
    assert c._auth_token() == "TESTTOK"     # cached — no second login
    assert calls["n"] == 1


def test_http_client_static_token_beats_login(monkeypatch):
    from holodeck.config import Config
    from holodeck.console.omnigent import HttpOmnigentClient

    cfg = Config(provider="fake")
    cfg.omnigent_url = "http://omni.local"
    cfg.omnigent_token = "STATIC"
    cfg.omnigent_user, cfg.omnigent_password = "pgupta", "secret"
    c = HttpOmnigentClient(cfg)

    def _boom():
        raise AssertionError("must not log in when a static token is set")
    monkeypatch.setattr(c, "_login", _boom)
    assert c._auth_token() == "STATIC"


def test_extract_pr_url_from_transcript():
    # Shape verified against a live session (agent "polly") on 2026-08-20:
    # role/content are nested under `data`. server/API.md's generic examples
    # show a flat shape instead — that does NOT match this deployment; see
    # HttpOmnigentClient._item_text's docstring before "fixing" this again.
    from holodeck.console.omnigent import HttpOmnigentClient as H
    items = [
        {"id": "msg_a", "type": "message", "status": "completed",
         "data": {"role": "user", "content": [{"type": "input_text", "text": "implement it"}]}},
        {"id": "msg_b", "type": "message", "status": "completed",
         "data": {"role": "assistant", "content": [{"type": "output_text",
         "text": "Opened https://github.com/junipersquare/compliance-backend/pull/4521 — done."}]}},
    ]
    assert H._extract_pr_url(items) == "https://github.com/junipersquare/compliance-backend/pull/4521"
    assert H._extract_pr_url([]) is None
    assert H._extract_pr_url([{"id": "msg_c", "type": "message", "status": "completed",
        "data": {"role": "assistant", "content": [{"type": "output_text", "text": "no link here"}]}}]) is None


def test_latest_assistant_message_reads_nested_data_item_shape():
    from holodeck.console.omnigent import HttpOmnigentClient as H
    items = [
        {"id": "msg_a", "type": "message", "status": "completed",
         "data": {"role": "user", "content": [{"type": "input_text", "text": "implement it"}]}},
        {"id": "msg_b", "type": "message", "status": "completed",
         "data": {"role": "assistant", "content": [{"type": "output_text", "text": "First reply."}]}},
        {"id": "msg_c", "type": "function_call", "status": "completed",
         "data": {"name": "run_tests", "arguments": "{}", "call_id": "call_1"}},
        {"id": "msg_d", "type": "message", "status": "completed",
         "data": {"role": "assistant", "content": [{"type": "output_text", "text": "Final reply."}]}},
    ]
    assert H._latest_assistant_message(items) == "Final reply."
    assert H._latest_assistant_message([]) is None


def test_pr_url_falls_back_to_message_text():
    # Even when the structured item scrape can't see it (per-agent item shape),
    # the PR link is recovered from the agent's narration — the same text we relay
    # to Jira ("Done. PR is up: [..](url)"). Last match wins.
    from holodeck.console.omnigent import HttpOmnigentClient as H
    msg = ("Done. PR is up: **[COMP-4945: Remove section]"
           "(https://github.com/junipersquare/compliance-backend/pull/2099)**")
    assert H._pr_from_text(msg) == "https://github.com/junipersquare/compliance-backend/pull/2099"
    assert H._pr_from_text(None) is None
    assert H._pr_from_text("no link here") is None


def test_agent_pr_url_surfaces_on_the_run():
    # the agent opens its own PR; the bridge scrapes it from the session and the
    # console shows it (mapped to the workspace row).
    from holodeck.config import Config
    from holodeck.console.driver import OmnigentDriver
    from holodeck.console.leaseclient import FakeLeaseClient
    from holodeck.console.manager import ConsoleManager
    from holodeck.console.omnigent import FakeOmnigentClient
    from holodeck.console.store import ConsoleStore

    cfg = Config(provider="fake", console_driver="omnigent")
    lease = FakeLeaseClient()
    omni = FakeOmnigentClient(lease)
    mgr = ConsoleManager(cfg, ConsoleStore(), lease, OmnigentDriver(lease, omni, cfg))

    rec = mgr.trigger("CPL-1", "compliance")
    omni.force_pr_url[rec.session_id] = "https://github.com/junipersquare/compliance-backend/pull/9"
    mgr.refresh()

    row = mgr.board()[0]
    assert row["pr_url"] == "https://github.com/junipersquare/compliance-backend/pull/9"
    assert row["workspace"] == "cpl-1"   # the row also carries the workspace for the ws->pr join


def test_pr_url_recovered_from_message_when_not_scraped():
    # No structured pr_url from the driver, but the agent narrated the PR (the same
    # text we relay to Jira). refresh() must recover + stick it so the console lights up.
    from holodeck.config import Config
    from holodeck.console.driver import OmnigentDriver
    from holodeck.console.leaseclient import FakeLeaseClient
    from holodeck.console.manager import ConsoleManager
    from holodeck.console.omnigent import FakeOmnigentClient
    from holodeck.console.store import ConsoleStore

    cfg = Config(provider="fake", console_driver="omnigent")
    lease = FakeLeaseClient()
    omni = FakeOmnigentClient(lease)
    mgr = ConsoleManager(cfg, ConsoleStore(), lease, OmnigentDriver(lease, omni, cfg))

    rec = mgr.trigger("CPL-2", "compliance")
    # note: NO force_pr_url — only the narration carries the link
    omni.force_latest_message[rec.session_id] = (
        "Done. PR is up: **[CPL-2: fix](https://github.com/junipersquare/compliance-backend/pull/77)**")
    mgr.refresh()

    assert mgr.board()[0]["pr_url"] == "https://github.com/junipersquare/compliance-backend/pull/77"


def test_answer_records_persisted_human_verdict_trace():
    # each human verdict (approve/decline/guidance) is stamped on the record so the
    # console can show "declined · 3m ago" and it survives a restart (it's persisted).
    from holodeck.config import Config
    from holodeck.console.driver import OmnigentDriver
    from holodeck.console.leaseclient import FakeLeaseClient
    from holodeck.console.manager import ConsoleManager
    from holodeck.console.omnigent import FakeOmnigentClient
    from holodeck.console.store import ConsoleStore

    cfg = Config(provider="fake", console_driver="omnigent")
    lease = FakeLeaseClient()
    omni = FakeOmnigentClient(lease)
    store = ConsoleStore()
    mgr = ConsoleManager(cfg, store, lease, OmnigentDriver(lease, omni, cfg))

    rec = mgr.trigger("CPL-1", "compliance")
    omni.force_elicitation[rec.session_id] = ("e1", "Deploy?")
    mgr.refresh()

    mgr.answer("CPL-1", "no")
    got = store.get("CPL-1")                     # re-read from the store (round-trips the DB)
    assert got.last_action == "declined" and got.last_action_at
    assert got.as_dict()["last_action"] == "declined"

    mgr.reiterate("CPL-1", "also handle the empty-list case")
    assert store.get("CPL-1").last_action == "guided"
