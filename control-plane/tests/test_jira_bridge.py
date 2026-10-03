"""Jira bridge — webhook parsing, inbound start/feedback/release, spec→prompt,
outbound waiting-notify, and the webhook route. All against the fakes (no server,
no golden, no live Jira)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from holodeck.config import Config
from holodeck.console.bridge import JiraBridge
from holodeck.console.driver import OmnigentDriver
from holodeck.console.jira import FakeJiraClient, JiraEvent, parse_webhook
from holodeck.console.leaseclient import FakeLeaseClient
from holodeck.console.manager import ConsoleManager
from holodeck.console.omnigent import FakeOmnigentClient
from holodeck.console.routes import mount_console
from holodeck.console.store import ConsoleStore


def _bridge(jira_issues=None):
    cfg = Config(provider="fake", console_driver="omnigent", jira_enabled=True,
                 jira_fake=True, jira_bot_account="bot-1")
    lease = FakeLeaseClient()
    omni = FakeOmnigentClient(lease)
    mgr = ConsoleManager(cfg, ConsoleStore(), lease, OmnigentDriver(lease, omni, cfg))
    jira = FakeJiraClient(issues=jira_issues or {})
    return JiraBridge(mgr, jira, cfg), mgr, jira, omni


# ---- webhook parsing ----

def test_parse_comment_plain_and_adf_and_label():
    e = parse_webhook({"webhookEvent": "comment_created", "issue": {"key": "CPL-1"},
                       "comment": {"author": {"accountId": "u1"}, "body": "hello"}})
    assert (e.kind, e.issue_key, e.body, e.author) == ("comment", "CPL-1", "hello", "u1")

    adf = {"type": "doc", "content": [{"type": "paragraph",
           "content": [{"type": "text", "text": "adf text"}]}]}
    e2 = parse_webhook({"webhookEvent": "comment_created", "issue": {"key": "CPL-1"},
                        "comment": {"author": {"accountId": "u1"}, "body": adf}})
    assert e2.body == "adf text"

    e3 = parse_webhook({"webhookEvent": "jira:issue_updated", "issue": {"key": "CPL-1"},
                        "user": {"accountId": "u1"},
                        "changelog": {"items": [{"field": "labels", "fromString": "", "toString": "holodeck"}]}})
    assert (e3.kind, e3.label) == ("label", "holodeck")

    assert parse_webhook({"webhookEvent": "jira:issue_created", "issue": {"key": "CPL-1"}}) is None


# ---- inbound ----

def test_label_starts_task_and_comments_back():
    bridge, mgr, jira, _ = _bridge()
    ev = parse_webhook({"webhookEvent": "jira:issue_updated", "issue": {"key": "CPL-1"},
                        "user": {"accountId": "u1"},
                        "changelog": {"items": [{"field": "labels", "fromString": "", "toString": "holodeck"}]}})
    assert bridge.handle(ev) == "started"
    assert mgr.store.get("CPL-1").status == "ready"
    assert any("started" in body for _, body in jira.comments)


def test_ticket_spec_becomes_agent_prompt():
    # The raw description rides inside the wrapped seed prompt (behavioral
    # preamble + ticket body — see bridge._wrap_prompt), not verbatim.
    bridge, mgr, _, omni = _bridge(jira_issues={"CPL-1": {"summary": "x", "description": "SPEC-BODY"}})
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    sid = mgr.store.get("CPL-1").session_id
    prompt = omni._sessions[sid]["prompt"]
    assert "SPEC-BODY" in prompt
    # the seed prompt names the ticket key, tells the agent NOT to open its own
    # PR, and gives it the exact reply the ticket reporter should send.
    assert "CPL-1" in prompt and ("Meeseek, not you" in prompt or "Holodeck, not you" in prompt)
    assert "finalize" in prompt


def test_command_run_retry_stop():
    bridge, mgr, _, omni = _bridge()
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    assert mgr.store.get("CPL-1").status == "ready"
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck retry fix the edge case"))
    assert omni._sessions["s-0001"]["messages"] == ["fix the edge case"]
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck stop"))
    assert mgr.store.get("CPL-1").status == "released"


def test_plain_comment_is_feedback_when_active():
    bridge, mgr, _, omni = _bridge()
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    assert bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="also handle X")) == "reiterated"
    assert omni._sessions["s-0001"]["messages"][-1] == "also handle X"


def test_start_does_not_recreate_a_session_for_the_same_ticket():
    # one session per Jira id: after a ticket is released, a fresh /holodeck run
    # (or label re-add) must NOT spin up a second session for it.
    bridge, mgr, jira, _ = _bridge()
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    assert mgr.store.get("CPL-1").status == "ready"
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck stop"))
    assert mgr.store.get("CPL-1").status == "released"

    r = bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    assert r == "exists"
    assert mgr.store.get("CPL-1").status == "released"       # unchanged — no new session
    assert any("was not started" in b for _, b in jira.comments)


def test_plain_comment_ignored_when_no_active_task():
    bridge, mgr, _, _ = _bridge()
    assert bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="random chatter")) == "ignored (no active task)"
    assert mgr.store.get("CPL-1") is None


def test_bot_own_comment_ignored():
    bridge, mgr, _, _ = _bridge()
    assert bridge.handle(JiraEvent("comment", "CPL-1", "bot-1", body="/holodeck run")) == "ignored (self)"
    assert mgr.store.get("CPL-1") is None


# ---- outbound ----

def test_waiting_notifies_once():
    bridge, mgr, jira, omni = _bridge()
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    sid = mgr.store.get("CPL-1").session_id
    omni.force_waiting.add(sid)
    mgr.refresh()
    bridge.sync()
    bridge.sync()  # must not duplicate
    needs_input = [b for _, b in jira.comments if "needs your input" in b]
    assert len(needs_input) == 1


# ---- elicitation / approval round-trip (agent <-> human) ----

def _running_task_blocked_on(elicitation):
    """Start a task and put its agent into a pending elicitation."""
    bridge, mgr, jira, omni = _bridge()
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    sid = mgr.store.get("CPL-1").session_id
    omni.force_elicitation[sid] = elicitation  # (elicitation_id, question)
    mgr.refresh()
    return bridge, mgr, jira, omni, sid


def test_elicitation_question_posted_to_jira():
    _, _, jira, _, _ = _running_task_blocked_on(("elicit_9", "Deploy to prod?"))
    # (bridge.sync posts the agent's actual question)
    bridge, mgr, jira, omni, sid = _running_task_blocked_on(("elicit_9", "Deploy to prod?"))
    bridge.sync()
    assert any("Deploy to prod?" in b for _, b in jira.comments)


def test_yes_reply_resolves_as_approval():
    bridge, mgr, jira, omni, sid = _running_task_blocked_on(("elicit_9", "Deploy to prod?"))
    bridge.sync()
    r = bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="yes"))
    assert r == "approved"
    assert omni.approvals == [(sid, "elicit_9", True)]        # accept
    rec = mgr.store.get("CPL-1")
    assert rec.waiting is False and rec.elicitation_id is None


def test_no_reply_resolves_as_decline():
    bridge, mgr, jira, omni, sid = _running_task_blocked_on(("elicit_9", "Wipe the DB?"))
    r = bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="no thanks"))
    assert r == "declined"
    assert omni.approvals == [(sid, "elicit_9", False)]       # decline


def test_freeform_reply_is_a_message_not_an_approval():
    bridge, mgr, jira, omni, sid = _running_task_blocked_on(("elicit_9", "Which region?"))
    r = bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="use us-west-2 and add a test"))
    assert r == "reiterated"
    assert omni.approvals == []                               # not a verdict
    assert omni._sessions[sid]["messages"] == ["use us-west-2 and add a test"]  # sent as a message


# ---- halt label (session_state=="idle" AND the FINISH hand-off marker) ----

_FINISH_MSG = "Done. Your action: reply `/holodeck finalize`."


def test_session_idle_with_finish_marker_swaps_trigger_label_for_halt_label():
    bridge, mgr, jira, omni = _bridge()
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    jira.add_label("CPL-1", "holodeck")
    sid = mgr.store.get("CPL-1").session_id
    omni._sessions[sid]["state"] = "idle"
    omni.force_latest_message[sid] = _FINISH_MSG
    mgr.refresh()  # first sighting: not yet "stable"
    bridge.sync()
    assert jira.labels_of("CPL-1") == {"holodeck"}          # unchanged so far
    mgr.refresh()  # unchanged across this poll -> now stable
    bridge.sync()
    assert jira.labels_of("CPL-1") == {"holodeck:halt"}


def test_halt_not_applied_when_idle_without_the_finish_marker():
    # Regression (COMP-4954): the orchestrator's own session goes genuinely
    # idle right after dispatching work to a sub-agent, posting only an
    # interim "I'll report back" message — well before the real work is done.
    # session_state alone must not be enough to halt.
    bridge, mgr, jira, omni = _bridge()
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    sid = mgr.store.get("CPL-1").session_id
    omni._sessions[sid]["state"] = "idle"
    omni.force_latest_message[sid] = "Kicked off implementation. I'll report back once it's done."
    mgr.refresh(); bridge.sync()
    mgr.refresh(); bridge.sync()  # idle AND stable, but no finish marker
    assert mgr.store.get("CPL-1").halted is False
    assert "holodeck:halt" not in jira.labels_of("CPL-1")
    # later: the real completion message lands
    omni.force_latest_message[sid] = "All done. " + _FINISH_MSG
    mgr.refresh(); bridge.sync()
    mgr.refresh(); bridge.sync()
    assert "holodeck:halt" in jira.labels_of("CPL-1")


def test_halt_label_not_reswapped_while_still_idle():
    bridge, mgr, jira, omni = _bridge()
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    sid = mgr.store.get("CPL-1").session_id
    omni._sessions[sid]["state"] = "idle"
    omni.force_latest_message[sid] = _FINISH_MSG
    mgr.refresh(); bridge.sync()
    mgr.refresh(); bridge.sync()  # now stable, halt fires
    mgr.refresh(); bridge.sync()  # still idle+stable -> must not re-fire
    assert jira.labels_of("CPL-1") == {"holodeck:halt"}


def test_halt_clears_and_can_reapply_after_a_new_turn():
    bridge, mgr, jira, omni = _bridge()
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    sid = mgr.store.get("CPL-1").session_id
    omni._sessions[sid]["state"] = "idle"
    omni.force_latest_message[sid] = _FINISH_MSG
    mgr.refresh(); bridge.sync()
    mgr.refresh(); bridge.sync()
    assert "holodeck:halt" in jira.labels_of("CPL-1")
    omni._sessions[sid]["state"] = "running"   # a new turn started
    mgr.refresh(); bridge.sync()
    assert mgr.store.get("CPL-1").halted is False   # re-armed
    omni._sessions[sid]["state"] = "idle"      # finished again
    omni.force_latest_message[sid] = "Done again. " + _FINISH_MSG
    mgr.refresh(); bridge.sync()
    mgr.refresh(); bridge.sync()
    assert "holodeck:halt" in jira.labels_of("CPL-1")


def test_halt_not_applied_while_a_formal_elicitation_is_open():
    # Omnigent's own "waiting" status (the agent loop parked internally, e.g.
    # on a sub-agent) is a DIFFERENT concept from rec.waiting (a human
    # elicitation is pending) — a pending elicitation must never also get the
    # halt label even if the underlying session looks idle-adjacent; that's
    # the existing waiting-input flow's job.
    bridge, mgr, jira, omni = _bridge()
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    sid = mgr.store.get("CPL-1").session_id
    omni.force_elicitation[sid] = ("e1", "Pick a region?")
    omni._sessions[sid]["state"] = "idle"
    omni.force_latest_message[sid] = "Your action: pick a region."
    mgr.refresh(); bridge.sync()
    mgr.refresh(); bridge.sync()
    assert mgr.store.get("CPL-1").waiting is True
    assert "holodeck:halt" not in jira.labels_of("CPL-1")


def test_answering_clears_the_halt_label():
    bridge, mgr, jira, omni = _bridge()
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    sid = mgr.store.get("CPL-1").session_id
    omni._sessions[sid]["state"] = "idle"
    omni.force_latest_message[sid] = _FINISH_MSG
    mgr.refresh(); bridge.sync()
    mgr.refresh(); bridge.sync()
    assert "holodeck:halt" in jira.labels_of("CPL-1")
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="looks good, add a test too"))
    assert "holodeck:halt" not in jira.labels_of("CPL-1")


def test_finalize_clears_the_halt_label():
    bridge, mgr, jira, omni = _bridge()
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    sid = mgr.store.get("CPL-1").session_id
    omni._sessions[sid]["state"] = "idle"
    omni.force_latest_message[sid] = _FINISH_MSG
    mgr.refresh(); bridge.sync()
    mgr.refresh(); bridge.sync()
    assert "holodeck:halt" in jira.labels_of("CPL-1")
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck finalize"))
    assert "holodeck:halt" not in jira.labels_of("CPL-1")


# ---- finalize (the notary, human-triggered) ----

def test_finalize_command_calls_real_notary_not_agent_report():
    bridge, mgr, jira, omni = _bridge()
    bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck run"))
    lease_id = mgr.store.get("CPL-1").lease_id
    mgr.client.force_evidence[lease_id] = {
        "lease_id": lease_id, "readiness": "ok", "readiness_ok": True, "seed_rows": 2,
        "test_cmd": "pytest -k arch", "test_exit": 0, "test_output": "15 passed",
        "test_timed_out": False, "diff": "diff --git a/x b/x\n+1\n", "golden_head": "abc123",
        "schema_rev": "head1", "services_booted": ["web"], "services_absent": [],
        "finalized_at": 1.0, "pr_url": "https://github.com/org/repo/pull/7",
    }
    r = bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck finalize"))
    assert r == "finalized"
    assert mgr.store.get("CPL-1").pr_url == "https://github.com/org/repo/pull/7"
    body = [b for _, b in jira.comments if "🔏" in b][0]
    assert "✓ ready" in body and "2 seeded rows" in body and "tests: exit 0" in body


def test_finalize_no_active_task_errors():
    bridge, _, jira, _ = _bridge()
    r = bridge.handle(JiraEvent("comment", "CPL-1", "u1", body="/holodeck finalize"))
    assert r == "error"
    assert any("finalize failed" in b for _, b in jira.comments)


# ---- inbound by polling (no webhook) ----

def _poll_bridge(labels=None, issues=None):
    # bot id on the fake client MUST match cfg.jira_bot_account, or the loop guard
    # can't tell our own comments from a human reply.
    cfg = Config(provider="fake", console_driver="omnigent", jira_enabled=True,
                 jira_fake=True, jira_bot_account="bot-1", jira_project="CPL")
    lease = FakeLeaseClient()
    omni = FakeOmnigentClient(lease)
    mgr = ConsoleManager(cfg, ConsoleStore(), lease, OmnigentDriver(lease, omni, cfg))
    jira = FakeJiraClient(issues=issues or {}, labels=labels or {}, bot="bot-1")
    return JiraBridge(mgr, jira, cfg), mgr, jira, omni


def test_poll_triggers_labeled_ticket_once():
    bridge, mgr, jira, _ = _poll_bridge(labels={"CPL-1": ["holodeck"]})
    bridge.poll_inbound()
    assert mgr.store.get("CPL-1").status == "ready"
    sid = mgr.store.get("CPL-1").session_id
    bridge.poll_inbound()                       # label still present -> must NOT re-fire
    assert mgr.store.get("CPL-1").session_id == sid


def test_poll_pulls_reply_and_resolves_current_elicitation():
    bridge, mgr, jira, omni = _poll_bridge(labels={"CPL-1": ["holodeck"]})
    bridge.poll_inbound()                        # trigger
    sid = mgr.store.get("CPL-1").session_id
    omni.force_elicitation[sid] = ("elicit_1", "Deploy to prod?")
    mgr.refresh()
    bridge.sync()                                # posts the question -> sets the watermark
    jira.reply("CPL-1", "yes", author="human")   # the human answers IN JIRA
    bridge.poll_inbound()                        # pulls it -> approval to the session
    assert omni.approvals == [(sid, "elicit_1", True)]
    assert mgr.store.get("CPL-1").waiting is False


def test_poll_maps_current_reply_never_a_stale_one_across_iterations():
    # the load-bearing requirement: only the comment AFTER the current question
    # counts, so multiple round-trips don't cross-wire.
    bridge, mgr, jira, omni = _poll_bridge(labels={"CPL-1": ["holodeck"]})
    bridge.poll_inbound()
    sid = mgr.store.get("CPL-1").session_id

    # a stale "yes" typed BEFORE any question exists (lower comment id)
    jira.reply("CPL-1", "yes", author="human")

    # round 1: agent asks -> we post the question (watermark now above the stale yes)
    omni.force_elicitation[sid] = ("elicit_1", "Q1?")
    mgr.refresh(); bridge.sync()
    bridge.poll_inbound()
    assert omni.approvals == [] and mgr.store.get("CPL-1").waiting is True   # stale yes ignored

    jira.reply("CPL-1", "no", author="human")    # the real round-1 answer
    bridge.poll_inbound()
    assert omni.approvals == [(sid, "elicit_1", False)]

    # round 2: agent asks again. The round-1 "no" must NOT auto-answer it.
    omni.approvals.clear()
    omni.force_elicitation[sid] = ("elicit_2", "Q2?")
    mgr.refresh(); bridge.sync()
    bridge.poll_inbound()
    assert omni.approvals == []                                              # no stale reuse
    jira.reply("CPL-1", "yes", author="human")
    bridge.poll_inbound()
    assert omni.approvals == [(sid, "elicit_2", True)]


def test_poll_ignores_our_own_bot_comments_as_replies():
    bridge, mgr, jira, omni = _poll_bridge(labels={"CPL-1": ["holodeck"]})
    bridge.poll_inbound()
    sid = mgr.store.get("CPL-1").session_id
    omni.force_elicitation[sid] = ("elicit_1", "Q?")
    mgr.refresh(); bridge.sync()
    jira.post_comment("CPL-1", "Holodeck: still working…")  # our own comment, id > watermark
    bridge.poll_inbound()
    assert omni.approvals == [] and mgr.store.get("CPL-1").waiting is True


def test_poll_dispatches_prefixed_command_even_when_not_waiting():
    # Regression: a halted (not elicitation-waiting) ticket's `/holodeck finalize`
    # reply used to be silently dropped by polling — _poll_replies() only ever
    # looked at rec.waiting tickets. It must now reach _command() like the
    # webhook path already does.
    bridge, mgr, jira, omni = _poll_bridge(labels={"CPL-1": ["holodeck"]})
    bridge.poll_inbound()  # trigger + establish the comment-id baseline
    rec = mgr.store.get("CPL-1")
    assert rec.waiting is False
    mgr.client.force_evidence[rec.lease_id] = {
        "lease_id": rec.lease_id, "readiness": "ok", "readiness_ok": True, "seed_rows": 1,
        "test_cmd": None, "test_exit": None, "test_output": "", "test_timed_out": False,
        "diff": "+1", "golden_head": "abc", "schema_rev": None, "services_booted": [],
        "services_absent": [], "finalized_at": 1.0, "pr_url": "https://github.com/org/repo/pull/9",
    }
    jira.reply("CPL-1", "/holodeck finalize", author="human")
    bridge.poll_inbound()
    assert mgr.store.get("CPL-1").pr_url == "https://github.com/org/repo/pull/9"
    assert any("Finalized" in b for _, b in jira.comments)


def test_poll_still_routes_plain_replies_through_answer():
    # non-command replies on an active (not necessarily waiting) ticket still
    # work as freeform guidance, same as before this fix.
    bridge, mgr, jira, omni = _poll_bridge(labels={"CPL-1": ["holodeck"]})
    bridge.poll_inbound()
    sid = mgr.store.get("CPL-1").session_id
    jira.reply("CPL-1", "also handle the empty-list case", author="human")
    bridge.poll_inbound()
    assert omni._sessions[sid]["messages"] == ["also handle the empty-list case"]


# ---- webhook route + secret ----

def test_webhook_route_secret_and_dispatch():
    cfg = Config(provider="fake", console_driver="omnigent", jira_enabled=True,
                 jira_fake=True, jira_webhook_secret="s3cret")
    lease = FakeLeaseClient()
    omni = FakeOmnigentClient(lease)
    jira = FakeJiraClient()
    app = FastAPI()
    mount_console(app, cfg, lease_client=lease, omni_client=omni, jira_client=jira, start_poller=False)
    c = TestClient(app)

    payload = {"webhookEvent": "comment_created", "issue": {"key": "CPL-9"},
               "comment": {"author": {"accountId": "u1"}, "body": "/holodeck run"}}
    assert c.post("/jira/webhook", json=payload).status_code == 401          # missing secret
    r = c.post("/jira/webhook?secret=s3cret", json=payload)
    assert r.status_code == 200 and r.json()["result"] == "started"
