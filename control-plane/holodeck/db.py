"""SQLite persistence for the one-service Holodeck.

Both the lease store (lease_id / holo_id, status, port, handle, evidence…) and the
console store (ticket ↔ lease_id ↔ session_id, waiting…) live in ONE file DB, so
the service is a single runnable process whose state survives restarts. One
connection + a lock (the API is single-worker anyway). `:memory:` gives an
ephemeral DB for tests/fake runs. Postgres is the later swap for multi-replica
scale (see README "Scaling to 10K") — the store interfaces don't change.
"""

from __future__ import annotations

import sqlite3
import threading

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leases (
    lease_id        TEXT PRIMARY KEY,   -- == holo_id(ticket)
    app             TEXT NOT NULL,
    ticket          TEXT NOT NULL,
    status          TEXT NOT NULL,
    preview_port    INTEGER,
    ticket_test_cmd TEXT,
    token           TEXT,               -- per-lease exec-gateway capability token
    handle_json     TEXT,               -- WorkspaceHandle
    evidence_json   TEXT,               -- Evidence (finalize)
    error           TEXT,
    created_at      REAL,
    expires_at      REAL
);
CREATE TABLE IF NOT EXISTS console_tasks (
    ticket           TEXT PRIMARY KEY,  -- the Jira/correlation key
    app              TEXT NOT NULL,
    lease_id         TEXT NOT NULL,     -- ticket -> holo_id mapping
    session_id       TEXT,              -- Omnigent session
    status           TEXT NOT NULL,
    preview_url      TEXT,
    preview_port     INTEGER,
    waiting          INTEGER NOT NULL DEFAULT 0,
    notified_waiting INTEGER NOT NULL DEFAULT 0,
    elicitation_id   TEXT,               -- outstanding decision the human must answer
    question         TEXT,               -- the agent's question (posted to Jira)
    agent_name       TEXT,               -- bound Omnigent agent (display)
    session_url      TEXT,               -- deep link into the Omnigent session
    last_action      TEXT,               -- last human verdict: approved/declined/guided
    last_action_at   REAL,               -- when that verdict landed (epoch seconds)
    jira_watermark   TEXT,               -- id of our "needs input" comment (reply cursor)
    first_message    TEXT,               -- unused; superseded by agent_message below
    notified_first_message INTEGER NOT NULL DEFAULT 0,  -- unused; superseded by posted_agent_message
    session_state    TEXT,               -- Omnigent's own session status: idle/running/failed/unknown
    agent_message    TEXT,               -- agent's latest authored turn, as of the last refresh
    stable_agent_message TEXT,           -- agent_message once unchanged across polls (relay-ready)
    missing_lease_polls INTEGER NOT NULL DEFAULT 0,  -- consecutive lease-lookup misses (threshold
                                          -- before treating as released — see manager.refresh())
    posted_agent_message TEXT,           -- last stable_agent_message value relayed to Jira (watermark)
    workspace        TEXT,               -- bound sandbox id (managed-xxxx) -> lease<->ticket map
    pr_url           TEXT,               -- PR the agent opened itself (scraped from the session)
    error            TEXT,
    created_at       REAL,
    updated_at       REAL,
    halted           INTEGER NOT NULL DEFAULT 0  -- holodeck:halt label edge-trigger:
                                          -- set while session_state == "idle" and clears
                                          -- the moment it moves off idle (a new turn started)
);
CREATE TABLE IF NOT EXISTS teams (
    slug        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    contact     TEXT,
    token       TEXT NOT NULL,   -- Option A "team token/link" (docs/AUTOMATIC_ONBOARDING.md) — a
                                  -- bearer secret, not a password; swappable for real SSO later
    created_at  REAL
);
CREATE TABLE IF NOT EXISTS app_teams (
    app         TEXT PRIMARY KEY,   -- manifest key -> the team that onboarded it
    team_slug   TEXT NOT NULL,
    created_at  REAL
);
CREATE TABLE IF NOT EXISTS onboarding_requests (
    request_id   TEXT PRIMARY KEY,
    team_slug    TEXT NOT NULL,
    app_name     TEXT NOT NULL,
    status       TEXT NOT NULL,
    payload_json TEXT NOT NULL,   -- repos, draft manifest fields, trial log/result, reject reason
    created_at   REAL,
    updated_at   REAL
);
"""

# Columns added after the table first shipped — ALTER them in on an existing DB
# file (CREATE TABLE IF NOT EXISTS won't add columns to a pre-existing table).
_MIGRATIONS = [
    "ALTER TABLE console_tasks ADD COLUMN elicitation_id TEXT",
    "ALTER TABLE console_tasks ADD COLUMN question TEXT",
    "ALTER TABLE console_tasks ADD COLUMN agent_name TEXT",
    "ALTER TABLE console_tasks ADD COLUMN session_url TEXT",
    "ALTER TABLE console_tasks ADD COLUMN last_action TEXT",
    "ALTER TABLE console_tasks ADD COLUMN last_action_at REAL",
    "ALTER TABLE console_tasks ADD COLUMN jira_watermark TEXT",
    "ALTER TABLE console_tasks ADD COLUMN first_message TEXT",
    "ALTER TABLE console_tasks ADD COLUMN notified_first_message INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE console_tasks ADD COLUMN workspace TEXT",
    "ALTER TABLE console_tasks ADD COLUMN pr_url TEXT",
    "ALTER TABLE console_tasks ADD COLUMN session_state TEXT",
    "ALTER TABLE console_tasks ADD COLUMN agent_message TEXT",
    "ALTER TABLE console_tasks ADD COLUMN stable_agent_message TEXT",
    "ALTER TABLE console_tasks ADD COLUMN missing_lease_polls INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE console_tasks ADD COLUMN posted_agent_message TEXT",
    "ALTER TABLE console_tasks ADD COLUMN halted INTEGER NOT NULL DEFAULT 0",
]


class Db:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            for stmt in _MIGRATIONS:
                try:
                    self._conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # duplicate column -> already migrated
            self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()
