"""ConsoleManager — trigger, correlate, refresh, reiterate, release. Thin: it
delegates truth to the lease API (via the client) and the driver; it stores only
the correlation + a cached rollup."""

from __future__ import annotations

import logging
import re
import string
import time
from typing import Optional

from holodeck.console.driver import DriverError
from holodeck.console.leaseclient import LeaseClient, LeaseClientError
from holodeck.console.store import ConsoleStore, TaskRecord

log = logging.getLogger("holodeck.console")

# lease status -> pipeline rollup shown on the board
_PIPELINE = {"pending": "provisioning", "ready": "ready",
             "released": "released", "failed": "failed",
             "queued": "queued"}

# GitHub PR URL, for recovering the agent's PR link from its narration when the
# session-item scrape doesn't yield it (see ConsoleManager.refresh()).
_PR_URL_RE = re.compile(r"https?://github\.com/[^/\s]+/[^/\s]+/pull/\d+")

# Map a free-text human reply to an approve/decline verdict, or None for
# "this isn't a yes/no — treat it as open-ended guidance (a message)".
_YES = {"yes", "y", "yeah", "yep", "approve", "approved", "ok", "okay", "lgtm",
        "proceed", "go", "accept", "accepted", "👍", "ship", "do it"}
_NO = {"no", "n", "nope", "reject", "rejected", "deny", "denied", "decline",
       "declined", "stop", "abort", "cancel", "👎", "don't", "dont"}


def _clean(tok: str) -> str:
    """Strip surrounding punctuation/quotes so 'yes,' or '"approve".' still match
    (emoji have no punctuation to strip, so 👍/👎 survive)."""
    return tok.strip(string.punctuation + "…—–")


def parse_verdict(text: str) -> Optional[bool]:
    """True=approve, False=decline, None=not a verdict (freeform guidance).

    Matches the whole reply or its FIRST word, each punctuation-stripped — so a
    natural "yes, go ahead" reads as approve, not freeform guidance."""
    t = (text or "").strip().lower()
    whole = _clean(t)
    if whole in _YES:
        return True
    if whole in _NO:
        return False
    toks = t.split()
    first = _clean(toks[0]) if toks else ""
    if first in _YES:
        return True
    if first in _NO:
        return False
    return None


class ConsoleError(RuntimeError):
    status_code = 400


class ConsoleConflict(ConsoleError):
    status_code = 409


class ConsoleNotFound(ConsoleError):
    status_code = 404


class ConsoleManager:
    def __init__(self, cfg, store: ConsoleStore, lease_client: LeaseClient, driver) -> None:
        self.cfg = cfg
        self.store = store
        self.client = lease_client
        self.driver = driver
        self._change_listeners: list = []

    def add_change_listener(self, fn) -> None:
        if fn not in self._change_listeners:
            self._change_listeners.append(fn)

    def _notify_change(self) -> None:
        for fn in list(self._change_listeners):
            try:
                fn()
            except Exception:
                log.exception("console change listener failed")

    def trigger(self, ticket: str, app: Optional[str] = None,
                prompt: Optional[str] = None, target_repo: Optional[str] = None,
                base_overrides: Optional[dict[str, str]] = None,
                plan_only: bool = False) -> TaskRecord:
        # Resolve a display alias to the real manifest key BEFORE the lease API sees it.
        app = self.cfg.app_key(app or self.cfg.default_app)
        existing = self.store.get(ticket)
        if existing is not None and not existing.is_terminal:
            raise ConsoleConflict(f"task {ticket!r} already active ({existing.status})")
        if target_repo is not None:
            # Validate HERE, before the driver starts anything — the lease API's
            # own 422 for a bad target_repo happens too late for OmnigentDriver:
            # its acquire() call runs asynchronously inside Omnigent's own
            # process, so a rejection there would never make it back to this
            # call (and so never back to the Jira thread that triggered it).
            try:
                valid = self.client.valid_target_repos(app)
            except LeaseClientError as e:
                raise ConsoleError(str(e))
            if target_repo not in valid:
                if not valid:
                    raise ConsoleError(f"app {app!r} is not a composite app; target_repo cannot be set")
                raise ConsoleError(
                    f"invalid target_repo {target_repo!r} for app {app!r} "
                    f"— must be one of {sorted(valid)}")
        try:
            res = self.driver.start(ticket, app, prompt, target_repo, base_overrides=base_overrides)
        except LeaseClientError as e:
            raise ConsoleError(str(e))
        preview_url = res.preview_url
        if res.preview_port:
            preview_url = self.cfg.workspace_preview_url(res.preview_port, app=app, ticket=ticket) or res.preview_url
        rec = TaskRecord(
            ticket=ticket, app=app, lease_id=res.lease_id, session_id=res.session_id,
            status=_PIPELINE.get(res.status, res.status),
            preview_url=preview_url, preview_port=res.preview_port,
            session_url=res.session_url,
            workflow_state="QUEUED" if res.status == "queued" else "PROVISIONING",
            plan_only=plan_only,
        )
        self.store.put(rec)
        self._notify_change()
        return rec

    def refresh(self) -> None:
        """Re-derive each live task's state from the lease API (+ Omnigent). Never
        raises — the poller must survive a bad tick."""
        changed = False
        for rec in self.store.all():
            if rec.is_terminal:
                continue
            try:
                lease = self.client.get(rec.lease_id)
                if lease is None:
                    # HttpLeaseClient.get() returns None for ANY non-200 response, so
                    # this fires identically for "the lease is genuinely gone" and for
                    # "the lease API hiccuped once" — a single miss must NOT be treated
                    # as released (that's a terminal status; refresh() skips terminal
                    # tasks forever after, silently killing the relay for a still-live
                    # session). Require a few consecutive misses before believing it.
                    rec.missing_lease_polls += 1
                    if rec.missing_lease_polls >= 3:
                        log.warning("lease %s missing for %d consecutive polls (task %s) — "
                                    "treating as released", rec.lease_id,
                                    rec.missing_lease_polls, rec.ticket)
                        rec.status = "released"
                else:
                    rec.missing_lease_polls = 0
                    rec.status = _PIPELINE.get(lease.get("status", ""), rec.status)
                    port = lease.get("preview_port")
                    if port:
                        rec.preview_port = port
                        rec.preview_url = self.cfg.workspace_preview_url(port, app=rec.app, ticket=rec.ticket)
                    rec.error = lease.get("error")
                st = self.driver.session_status(rec.session_id)
                rec.waiting = st.waiting
                rec.elicitation_id = st.elicitation_id
                rec.question = st.question
                if st.agent_name:
                    rec.agent_name = st.agent_name
                if st.session_url:
                    rec.session_url = st.session_url
                rec.session_state = st.state
                if st.latest_message:
                    # A still-running session (e.g. waiting on a dispatched sub-agent)
                    # never goes "idle" until the whole turn ends, so relay-readiness
                    # comes from the message having stopped changing across polls,
                    # not from session_state — unchanged since last refresh() means
                    # it's sat there through at least one full poll interval.
                    if st.latest_message == rec.agent_message:
                        rec.stable_agent_message = st.latest_message
                    rec.agent_message = st.latest_message
                if st.workspace:
                    rec.workspace = st.workspace   # the managed sandbox id, for lease<->ticket mapping
                if st.pr_url:
                    rec.pr_url = st.pr_url          # PR the agent opened itself (from the transcript)
                if not rec.pr_url:
                    # Belt-and-suspenders: recover the PR link from the agent's own
                    # message text — the same "PR is up: <url>" line we relay to Jira,
                    # which is stored on the record. Independent of the session-item
                    # shape / driver, so a PR that's plainly narrated (and visible in
                    # the Jira thread) still lights up the console. Sticky once found.
                    for _txt in (rec.agent_message, rec.stable_agent_message, rec.posted_agent_message):
                        _m = _PR_URL_RE.search(_txt or "")
                        if _m:
                            rec.pr_url = _m.group(0)
                            break
                if rec.waiting:
                    rec.workflow_state = "WAITING_INPUT"
                    if rec.status == "ready":
                        rec.status = "waiting-input"
                else:
                    if rec.workflow_state in ("PROVISIONING", "WAITING_INPUT", "QUEUED") and rec.status == "ready":
                        rec.workflow_state = "CODING"
                if not rec.waiting:
                    # cleared: re-arm the notice + drop the stale elicitation
                    rec.notified_waiting = False
                    rec.elicitation_id = None
                    rec.question = None
                self.store.put(rec)
                changed = True
            except Exception:
                log.exception("refresh failed for task %s", rec.ticket)
        if changed:
            self._notify_change()

    def answer(self, ticket: str, text: str) -> tuple[TaskRecord, Optional[bool]]:
        """Human reply from Jira. If the agent is blocked on an elicitation and
        the reply is a yes/no, resolve it as an approval; otherwise send it as
        open-ended guidance (a message). Returns (record, verdict) where verdict
        is True/False for an approval or None for a freeform message."""
        rec = self.store.get(ticket)
        if rec is None:
            raise ConsoleNotFound(f"no task {ticket!r}")
        verdict = parse_verdict(text)
        is_plan_approval = rec.plan_only and (verdict is True or text.strip().lower() in ("approve", "yes", "proceed"))
        msg_to_send = text
        if is_plan_approval:
            msg_to_send = "Plan approved. You may now proceed directly with implementing the plan."
            rec.plan_only = False
            verdict = True
        try:
            if rec.elicitation_id and verdict is not None:
                self.driver.answer(rec.session_id, rec.elicitation_id, verdict)
            else:
                self.driver.reiterate(rec.session_id, msg_to_send)
                if not is_plan_approval:
                    verdict = None  # routed as a message, not an approval
        except DriverError as e:
            raise ConsoleError(str(e))
        rec.waiting = False
        rec.notified_waiting = False
        rec.elicitation_id = None
        rec.question = None
        rec.last_action = ("approved" if verdict else "declined") if verdict is not None else "guided"
        rec.last_action_at = time.time()
        if rec.status == "waiting-input":
            rec.status = "ready"
        rec.workflow_state = "CODING"
        self.store.put(rec)
        self._notify_change()
        return rec, verdict

    def reiterate(self, ticket: str, feedback: str) -> TaskRecord:
        """Open-ended guidance (always a message), regardless of yes/no shape.
        Used by the /console reiterate endpoint; the Jira bridge uses answer()
        so a bare "yes"/"no" resolves a pending approval instead."""
        rec = self.store.get(ticket)
        if rec is None:
            raise ConsoleNotFound(f"no task {ticket!r}")
        try:
            self.driver.reiterate(rec.session_id, feedback)
        except DriverError as e:
            raise ConsoleError(str(e))
        rec.waiting = False
        rec.notified_waiting = False
        rec.elicitation_id = None
        rec.question = None
        rec.last_action = "guided"
        rec.last_action_at = time.time()
        if rec.status == "waiting-input":
            rec.status = "ready"
        rec.workflow_state = "CODING"
        self.store.put(rec)
        self._notify_change()
        return rec

    def finalize(self, ticket: str, *, ticket_summary: Optional[str] = None,
                 issue_type: Optional[str] = None) -> tuple[TaskRecord, dict]:
        """Human-triggered notary step. Calls the real `LeaseService.finalize()`
        host-side — re-derives readiness/tests/diff and opens the PR itself
        (if enabled) — instead of trusting anything the agent self-reported.

        ticket_summary/issue_type (Jira issue fields — only JiraBridge has a
        JiraClient to fetch them, hence passed in rather than looked up here)
        and agent_summary (derived below from this ticket's OWN stable_agent_message,
        already in scope) are PR title/body cosmetics ONLY — see service.py's
        _pr_title/_pr_body docstrings for why that's still true even though one
        of them is agent-authored text."""
        rec = self.store.get(ticket)
        if rec is None:
            raise ConsoleNotFound(f"no task {ticket!r}")
        agent_summary = None
        if rec.stable_agent_message:
            idx = rec.stable_agent_message.find("Your action:")
            agent_summary = (rec.stable_agent_message[:idx] if idx != -1
                            else rec.stable_agent_message).strip() or None
        try:
            evidence = self.client.finalize(
                rec.lease_id, agent_summary=agent_summary,
                ticket_summary=ticket_summary, issue_type=issue_type)
        except LeaseClientError as e:
            raise ConsoleError(str(e))
        if evidence.get("pr_url"):
            rec.pr_url = evidence["pr_url"]  # overwrite: the verified PR wins over any
                                              # self-reported one scraped from the transcript
            rec.workflow_state = "CERTIFIED_PR"
        elif not evidence.get("guardrail_passed", True):
            rec.workflow_state = "GUARDRAIL_BLOCKED"
        elif evidence.get("test_cmd") and (evidence.get("test_exit") != 0 or evidence.get("test_timed_out")):
            rec.workflow_state = "NOTARY_FAILED"
        else:
            rec.workflow_state = "NOTARY_TESTING"
        rec.last_action = "finalized"
        rec.last_action_at = time.time()
        self.store.put(rec)
        self._notify_change()
        return rec, evidence

    def release(self, ticket: str) -> TaskRecord:
        rec = self.store.get(ticket)
        if rec is None:
            raise ConsoleNotFound(f"no task {ticket!r}")
        try:
            self.client.release(rec.lease_id)
        except LeaseClientError:
            log.exception("release failed for task %s", ticket)
        rec.status = "released"
        rec.workflow_state = "RELEASED"
        self.store.put(rec)
        self._notify_change()
        return rec

    def _tunnel_cmd(self, rec: TaskRecord) -> Optional[str]:
        """The ssh port-forward the author runs to reach the live workspace app
        on their own localhost (dev-testing preview — §14)."""
        if not rec.preview_port:
            return None
        host = self.cfg.console_ssh_host or "<ec2-box>"
        return f"ssh -L {rec.preview_port}:127.0.0.1:{rec.preview_port} {host}"

    def board(self) -> list[dict]:
        rows = []
        for rec in sorted(self.store.all(), key=lambda r: r.created_at):
            d = rec.as_dict()
            if rec.preview_port:
                d["tunnel_cmd"] = self._tunnel_cmd(rec)
                d["local_url"] = f"http://localhost:{rec.preview_port}"
            rows.append(d)
        return rows
