"""In-memory correlation store — the one piece of state the console owns.

Keyed by ticket id (the correlation key: the lease id is derived from it, and
the Omnigent session is tagged with it). Everything else is read from the lease
API / Omnigent on refresh, never duplicated as a source of truth here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from holodeck.db import Db

# Terminal pipeline states the poller stops refreshing.
TERMINAL = {"released", "failed"}


@dataclass
class TaskRecord:
    ticket: str
    app: str
    lease_id: str
    session_id: Optional[str] = None  # Omnigent session (None for the direct driver)
    status: str = "triggered"  # pipeline rollup: triggered/provisioning/ready/waiting-input/released/failed
    preview_url: Optional[str] = None
    preview_port: Optional[int] = None  # host port — for the author's ssh tunnel
    waiting: bool = False  # agent paused for human input (from Omnigent)
    notified_waiting: bool = False  # already posted a "needs input" comment to Jira
    elicitation_id: Optional[str] = None  # the outstanding decision the human must answer
    question: Optional[str] = None        # the agent's question text (posted to Jira)
    agent_name: Optional[str] = None      # the bound Omnigent agent, e.g. "debby"
    session_url: Optional[str] = None     # deep link into the Omnigent session UI
    last_action: Optional[str] = None     # last human verdict: approved/declined/guided
    last_action_at: Optional[float] = None  # when that verdict landed (epoch seconds)
    jira_watermark: Optional[str] = None  # id of the "needs input" comment we posted;
                                          # a Jira reply counts only if its id is higher
    session_state: Optional[str] = None   # Omnigent's own session status: idle/running/failed/unknown
    agent_message: Optional[str] = None   # the agent's latest authored turn, as of the last refresh
    stable_agent_message: Optional[str] = None  # agent_message once it's stopped changing across
                                          # polls — a still-running session (e.g. waiting on a
                                          # dispatched sub-agent) never goes "idle" until the whole
                                          # turn ends, so relay readiness comes from stability instead
    missing_lease_polls: int = 0          # consecutive polls where the lease API returned nothing —
                                          # a transient HTTP blip looks identical to "actually gone",
                                          # so this must clear a threshold before we treat it as
                                          # released (see manager.refresh()), not fire on the first miss
    posted_agent_message: Optional[str] = None  # the last stable_agent_message value we relayed to
                                          # Jira — relay again only once it moves past this
    halted: bool = False                  # edge-trigger for the holodeck:halt label swap: set once
                                          # when session_state goes idle, cleared the moment it moves
                                          # off idle again — see JiraBridge._check_halt
    workspace: Optional[str] = None       # the sandbox id the server bound (managed-xxxx);
                                          # maps a managed lease row back to this ticket
    pr_url: Optional[str] = None          # PR link. Initially the agent's own self-reported
                                          # one (scraped from the transcript); overwritten with
                                          # the host-side notary's verified PR once finalize() runs
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "ticket": self.ticket, "app": self.app, "lease_id": self.lease_id,
            "session_id": self.session_id, "status": self.status,
            "preview_url": self.preview_url, "preview_port": self.preview_port,
            "waiting": self.waiting, "elicitation_id": self.elicitation_id,
            "question": self.question, "agent_name": self.agent_name,
            "session_url": self.session_url, "last_action": self.last_action,
            "last_action_at": self.last_action_at, "session_state": self.session_state,
            "agent_message": self.agent_message, "stable_agent_message": self.stable_agent_message,
            "jira_watermark": self.jira_watermark, "workspace": self.workspace,
            "pr_url": self.pr_url, "error": self.error,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "halted": self.halted,
        }

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL


def _rec_to_row(r: TaskRecord) -> tuple:
    return (r.ticket, r.app, r.lease_id, r.session_id, r.status, r.preview_url,
            r.preview_port, int(r.waiting), int(r.notified_waiting),
            r.elicitation_id, r.question, r.agent_name, r.session_url,
            r.last_action, r.last_action_at, r.jira_watermark,
            r.session_state, r.agent_message, r.stable_agent_message,
            r.missing_lease_polls, r.posted_agent_message,
            r.workspace, r.pr_url, r.error, r.created_at, r.updated_at,
            int(r.halted))


def _row_to_rec(row) -> TaskRecord:
    return TaskRecord(
        ticket=row["ticket"], app=row["app"], lease_id=row["lease_id"],
        session_id=row["session_id"], status=row["status"], preview_url=row["preview_url"],
        preview_port=row["preview_port"], waiting=bool(row["waiting"]),
        notified_waiting=bool(row["notified_waiting"]),
        elicitation_id=row["elicitation_id"], question=row["question"],
        agent_name=row["agent_name"], session_url=row["session_url"],
        last_action=row["last_action"], last_action_at=row["last_action_at"],
        jira_watermark=row["jira_watermark"],
        session_state=row["session_state"],
        agent_message=row["agent_message"],
        stable_agent_message=row["stable_agent_message"],
        missing_lease_polls=row["missing_lease_polls"] or 0,
        posted_agent_message=row["posted_agent_message"],
        workspace=row["workspace"], pr_url=row["pr_url"],
        error=row["error"], created_at=row["created_at"], updated_at=row["updated_at"],
        halted=bool(row["halted"]),
    )


_COLS = ("ticket, app, lease_id, session_id, status, preview_url, preview_port, "
         "waiting, notified_waiting, elicitation_id, question, agent_name, session_url, "
         "last_action, last_action_at, jira_watermark, session_state, agent_message, "
         "stable_agent_message, missing_lease_polls, posted_agent_message, workspace, "
         "pr_url, error, created_at, updated_at, halted")
_UPSERT = f"""
INSERT INTO console_tasks ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(ticket) DO UPDATE SET
  app=excluded.app, lease_id=excluded.lease_id, session_id=excluded.session_id,
  status=excluded.status, preview_url=excluded.preview_url,
  preview_port=excluded.preview_port, waiting=excluded.waiting,
  notified_waiting=excluded.notified_waiting, elicitation_id=excluded.elicitation_id,
  question=excluded.question, agent_name=excluded.agent_name,
  session_url=excluded.session_url, last_action=excluded.last_action,
  last_action_at=excluded.last_action_at, jira_watermark=excluded.jira_watermark,
  session_state=excluded.session_state, agent_message=excluded.agent_message,
  stable_agent_message=excluded.stable_agent_message,
  missing_lease_polls=excluded.missing_lease_polls,
  posted_agent_message=excluded.posted_agent_message,
  workspace=excluded.workspace, pr_url=excluded.pr_url, error=excluded.error,
  created_at=excluded.created_at, updated_at=excluded.updated_at,
  halted=excluded.halted
"""


class ConsoleStore:
    def __init__(self, db: Optional[Db] = None) -> None:
        self.db = db or Db()  # default: private in-memory DB (tests / throwaway)

    def get(self, ticket: str) -> Optional[TaskRecord]:
        rows = self.db.query("SELECT * FROM console_tasks WHERE ticket=?", (ticket,))
        return _row_to_rec(rows[0]) if rows else None

    def put(self, rec: TaskRecord) -> None:
        rec.updated_at = time.time()
        self.db.execute(_UPSERT, _rec_to_row(rec))

    def all(self) -> list[TaskRecord]:
        return [_row_to_rec(r) for r in self.db.query("SELECT * FROM console_tasks")]

    def delete(self, ticket: str) -> None:
        """Drop this ticket's record entirely — used by JiraBridge._reset() so a
        finished ticket (halted, released) can be re-triggered from scratch via
        the reset label, without which _start()'s one-session-per-ticket guard
        (`self.manager.store.get(key)` returning anything at all) blocks it
        forever. Idempotent: deleting a ticket with no record is a no-op."""
        self.db.execute("DELETE FROM console_tasks WHERE ticket=?", (ticket,))
