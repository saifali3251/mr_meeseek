"""The Omnigent seam — how the console starts / inspects / reiterates an agent
session.

`OmnigentDriver` drives Omnigent through this interface. `FakeOmnigentClient`
runs the whole path with no server (tests / local). `HttpOmnigentClient` is the
real client against the Omnigent server session API (v1), authed with a Bearer
token — see its docstring for the endpoints and what the server must have set up.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


class OmnigentError(RuntimeError):
    pass


def _extract_token(d) -> str:
    """Pull a bearer token out of a login response across the common shapes:
    a top-level `token` / `access_token` / `jwt` / `id_token`, or the same nested
    under `data` / `result` / `auth`. Omnigent's account login returns `token`."""
    if not isinstance(d, dict):
        return ""
    for k in ("token", "access_token", "accessToken", "jwt", "id_token"):
        v = d.get(k)
        if isinstance(v, str) and v:
            return v
    for wrap in ("data", "result", "auth"):
        inner = d.get(wrap)
        if isinstance(inner, dict):
            t = _extract_token(inner)
            if t:
                return t
    return ""


@dataclass
class SessionStatus:
    state: str  # idle | running | waiting | failed | unknown (Omnigent's own session
                # lifecycle status — "idle" == the turn is fully finished, the
                # deterministic signal JiraBridge._check_halt keys off)
    waiting: bool  # agent paused for human input (pending elicitation / approval)
    # When the agent is blocked on a decision, these carry the outstanding
    # elicitation so the bridge can post the actual question to Jira and answer
    # it with the right correlation id. None when the session isn't waiting.
    elicitation_id: Optional[str] = None
    question: Optional[str] = None
    # Surfaced on the console so a run reads as more than an id:
    agent_name: Optional[str] = None   # the bound agent, e.g. "debby"
    session_url: Optional[str] = None  # deep link into the Omnigent session UI
    # The agent's most recent authored turn, if any have landed yet. None until
    # the agent has replied at least once — see HttpOmnigentClient.status() for
    # how this is pulled out of the transcript. Always the LATEST assistant
    # item, not the first — the bridge relays whichever is newest once the
    # session goes idle (see JiraBridge.sync()).
    latest_message: Optional[str] = None
    workspace: Optional[str] = None    # the sandbox id the server bound (e.g. "managed-8f02ceb1"),
                                       # used to map a managed lease back to its ticket
    pr_url: Optional[str] = None       # the PR the agent opened itself (gh pr create in its
                                       # worktree), scraped from the session transcript


@runtime_checkable
class OmnigentClient(Protocol):
    def start(self, ticket: str, app: str, prompt: str,
              target_repo: Optional[str] = None) -> str:
        """Start a session using the holodeck sandbox; return the session id.
        (Omnigent's provider strikes the env as a side effect.)

        target_repo is carried as a session label (holodeck_target_repo) —
        omnigent-provider/sitecustomize.py's target-repo passthrough resolves it
        from there and injects it into the provider's own /leases POST, the same
        way holodeck_ticket already works for the ticket itself. None is the
        common case (a single-repo app, or a composite ticket that didn't pick
        a repo) and must reach the lease API as an absent field, not a guess."""
        ...

    def status(self, session_id: str) -> SessionStatus: ...

    def session_url(self, session_id: str) -> Optional[str]:
        """Deep link into the Omnigent session UI, available as soon as the
        session exists — unlike most of SessionStatus, callers don't need to
        wait for a status() round-trip just to get this."""
        ...

    def send(self, session_id: str, message: str) -> None:
        """Send a freeform user message into a live session (open-ended
        guidance / the reiterate path)."""
        ...

    def approve(self, session_id: str, elicitation_id: str, accept: bool,
                content: Optional[dict] = None) -> None:
        """Resolve an outstanding elicitation (a yes/no decision the agent is
        blocked on). accept=True -> "accept", False -> "decline". `content` is
        the form payload for schema elicitations (omit for binary yes/no)."""
        ...


class FakeOmnigentClient:
    """In-memory Omnigent stand-in. Simulates the provider striking the env by
    calling the lease API itself (what Omnigent's provider would do), so the
    whole console Omnigent path runs with no server/golden."""

    def __init__(self, lease_client) -> None:
        self.lease_client = lease_client
        self._sessions: dict[str, dict] = {}
        self._n = 0
        self.force_waiting: set[str] = set()  # test knob: mark a session waiting-for-input
        # test knob: session_id -> (elicitation_id, question) to simulate the
        # agent blocking on a yes/no decision.
        self.force_elicitation: dict[str, tuple[str, str]] = {}
        self.approvals: list[tuple[str, str, bool]] = []  # (session_id, elicitation_id, accept)
        # test knob: session_id -> text, simulating the agent's latest authored
        # turn (the real client pulls this from the transcript; the fake has no LLM).
        self.force_latest_message: dict[str, str] = {}
        self.force_pr_url: dict[str, str] = {}  # test knob: session_id -> PR url

    def start(self, ticket: str, app: str, prompt: str,
             target_repo: Optional[str] = None) -> str:
        self._n += 1
        session_id = f"s-{self._n:04d}"
        # simulate Omnigent → holodeck provider → strike
        workspace = None
        try:
            lease = self.lease_client.acquire(app, ticket, target_repo)
            workspace = lease.get("lease_id") if isinstance(lease, dict) else None
        except Exception:
            pass  # lease may already exist; the driver reads it back regardless
        self._sessions[session_id] = {"ticket": ticket, "prompt": prompt,
                                      "state": "running", "messages": [],
                                      "workspace": workspace}
        return session_id

    def status(self, session_id: str) -> SessionStatus:
        s = self._sessions.get(session_id)
        if s is None:
            return SessionStatus("unknown", False)
        el = self.force_elicitation.get(session_id)
        waiting = session_id in self.force_waiting or el is not None
        eid, q = el if el else (None, None)
        return SessionStatus(s["state"], waiting, elicitation_id=eid, question=q,
                             agent_name=s.get("agent_name", "fake-agent"),
                             session_url=self.session_url(session_id),
                             latest_message=self.force_latest_message.get(session_id),
                             workspace=s.get("workspace"),
                             pr_url=self.force_pr_url.get(session_id))

    def session_url(self, session_id: str) -> Optional[str]:
        return f"https://omni.fake/sessions/{session_id}"

    def send(self, session_id: str, message: str) -> None:
        s = self._sessions.get(session_id)
        if s is None:
            raise OmnigentError(f"no such session {session_id!r}")
        s["messages"].append(message)
        self.force_waiting.discard(session_id)  # feedback un-pauses the agent
        self.force_elicitation.pop(session_id, None)
        s["state"] = "running"

    def approve(self, session_id: str, elicitation_id: str, accept: bool,
                content: Optional[dict] = None) -> None:
        s = self._sessions.get(session_id)
        if s is None:
            raise OmnigentError(f"no such session {session_id!r}")
        self.approvals.append((session_id, elicitation_id, accept))
        self.force_elicitation.pop(session_id, None)  # decision resolves the pause
        self.force_waiting.discard(session_id)
        s["state"] = "running"


class HttpOmnigentClient:
    """Real client for Omnigent's BUILT-IN server session API (v1).

    This is Omnigent's own API (runs on the EKS server) — NOT the Holodeck lease
    API (/leases, on EC2). The bridge talks here; Omnigent's holodeck *provider*
    is what talks to /leases. Endpoints (from the 0.8.2 SessionCreateRequest /
    SessionEventInput schemas):

      start  : POST /v1/sessions  {host_type:"managed", agent_id, initial_items}
               -> the SERVER provisions the workspace via the holodeck sandbox
                  provider and binds the session to it. Returns {"session_id"|"id"}.
      status : GET  /v1/sessions/{id}          -> {status, pending_inputs, pending_elicitations}
      send   : POST /v1/sessions/{id}/events  {type:"message", data:{...}}  (reiterate)

    Auth: a static ``Authorization: Bearer <HOLODECK_OMNIGENT_TOKEN>`` if set;
    otherwise account login — ``POST /auth/login {username,password}`` (no OIDC),
    the returned ``token`` cached and re-fetched on 401.
    ``HOLODECK_OMNIGENT_AGENT`` is the durable agent *id* (not a display name).

    For ``host_type="managed"`` to resolve to a Holodeck env, the server must
    BOTH (a) have the holodeck provider wheel installed, AND (b) be wired with a
    ``ManagedSandboxConfig`` whose ``launcher_factory`` returns
    ``HolodeckSandboxLauncher`` — holodeck is a community provider, so the plain
    ``sandbox:`` YAML won't drive managed launch (its allowlist excludes it). See
    DEPLOY-MANAGED.md → "Managed-launch wiring".

    Body shapes are read from 0.8.2 source, not a live authed call; ``probe()``
    validates connectivity + auth, and any mismatch surfaces the server's own
    error so it's a one-field fix against your hosted/local server.
    """

    def __init__(self, cfg) -> None:
        self.base = (cfg.omnigent_url or "").rstrip("/")
        self._static_token = cfg.omnigent_token      # HOLODECK_OMNIGENT_TOKEN (wins if set)
        self.user = cfg.omnigent_user                # else account login (no OIDC here)
        self.password = cfg.omnigent_password
        self.login_path = cfg.omnigent_login_path
        self.agent = cfg.omnigent_agent
        self._token: Optional[str] = None            # cached account-login token

    def _auth_token(self) -> str:
        """Bearer token for a request. A static HOLODECK_OMNIGENT_TOKEN wins;
        otherwise account login (username/password), fetched lazily and cached."""
        if self._static_token:
            return self._static_token
        if self.user and self.password:
            if not self._token:
                self._token = self._login()
            return self._token
        return ""

    def _login(self) -> str:
        """POST {username,password} to the login path -> bearer token. Omnigent
        here uses account login, not OIDC (POST /auth/login, token in `token`)."""
        if not self.base:
            raise OmnigentError("HOLODECK_OMNIGENT_URL not set")
        payload = json.dumps({"username": self.user, "password": self.password}).encode()
        req = urllib.request.Request(
            self.base + self.login_path, data=payload, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise OmnigentError(
                f"omnigent login {self.login_path} -> {e.code}: {e.read().decode(errors='replace')[:300]}")
        except urllib.error.URLError as e:
            raise OmnigentError(f"omnigent server unreachable at {self.base}: {e.reason}")
        tok = _extract_token(d)
        if not tok:
            keys = list(d)[:8] if isinstance(d, dict) else type(d).__name__
            raise OmnigentError(f"omnigent login returned no token (response keys: {keys})")
        return tok

    def _ensure_agent(self) -> Optional[str]:
        """Agent id to bind the session to: HOLODECK_OMNIGENT_AGENT if set, else the
        first agent from GET /v1/agents (matches the smoke script's auto-discovery)."""
        if self.agent:
            return self.agent
        d = self._req("GET", "/v1/agents")
        agents = d.get("data", d) if isinstance(d, dict) else d
        if isinstance(agents, list) and agents and isinstance(agents[0], dict):
            self.agent = agents[0].get("id")  # cache for later calls
        return self.agent

    def _req(self, method: str, path: str, body: Optional[dict] = None, *, _retry: bool = True):
        if not self.base:
            raise OmnigentError("HOLODECK_OMNIGENT_URL not set")
        headers = {"Accept": "application/json"}
        tok = self._auth_token()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            # account-login token expired? drop it and re-login once (static tokens don't retry)
            if e.code == 401 and _retry and not self._static_token and self.user:
                self._token = None
                return self._req(method, path, body, _retry=False)
            raise OmnigentError(
                f"omnigent {method} {path} -> {e.code}: {e.read().decode(errors='replace')[:400]}")
        except urllib.error.URLError as e:
            raise OmnigentError(f"omnigent server unreachable at {self.base}: {e.reason}")

    def probe(self) -> int:
        """Connectivity + auth check: GET /v1/sessions. Returns the session count."""
        d = self._req("GET", "/v1/sessions?limit=1")
        return len(d.get("data", [])) if isinstance(d, dict) else 0

    @staticmethod
    def _message_item(text: str) -> dict:
        """A SessionEventInput 'message' item — the shape the server accepts both
        as an ``initial_items`` element on create and as the ``/events`` body."""
        return {"type": "message",
                "data": {"role": "user", "content": [{"type": "input_text", "text": text}]}}

    def start(self, ticket: str, app: str, prompt: str,
             target_repo: Optional[str] = None) -> str:
        # host_type="managed" is the whole trick: the SERVER provisions the
        # workspace via the holodeck sandbox provider (which calls our EC2 lease
        # API) and binds the session to it. Per the schema we must NOT send
        # host_id/workspace — the server chooses both. The ticket spec rides in
        # as the session's first user message.
        labels = {"holodeck_ticket": ticket, "holodeck_app": app}
        if target_repo:
            # Read by omnigent-provider/sitecustomize.py's target-repo passthrough
            # (a ContextVar set from this exact label, mirroring holodeck_ticket
            # above) and injected into the provider's own /leases POST — see that
            # file for why this couldn't be a parameter on provision() instead.
            labels["holodeck_target_repo"] = target_repo
        body: dict = {
            "host_type": "managed",
            "labels": labels,
        }
        agent = self._ensure_agent()
        if agent:
            body["agent_id"] = agent  # durable agent id (configured or auto-discovered)
        if prompt:
            body["initial_items"] = [self._message_item(prompt)]
        resp = self._req("POST", "/v1/sessions", body)
        sid = resp.get("session_id") or resp.get("id")
        if not sid:
            raise OmnigentError(f"create session returned no id: {resp}")
        return sid

    @staticmethod
    def _item_text(item: dict) -> str:
        """Flatten a conversation item's content parts into plain text.
        VERIFIED against a live session (agent "polly", plain AP session, no
        external_session_id) on 2026-08-20 — `role`/`content` are nested under
        `data`, e.g. `{"id": ..., "type": "message", "status": "completed",
        "data": {"role": "assistant", "content": [{"type": "output_text",
        "text": "..."}], "model": ...}}`. Mirrors what `_message_item()` writes.
        NOTE: server/API.md's generic Session Object / List Conversation Items
        examples document a FLAT shape (role/content as top-level keys, no
        `data` wrapper) — that does NOT match this deployment's actual
        behavior. Trust the verified shape here over that doc until proven
        otherwise for a given agent/harness; don't re-"fix" this without a
        fresh live sample to check against, since generic docs elsewhere in
        this same API have already been wrong once for a different agent."""
        data = item.get("data") or {}
        parts = data.get("content") or []
        if isinstance(parts, str):
            return parts
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()

    @classmethod
    def _latest_assistant_message(cls, items: list) -> Optional[str]:
        """The agent's most recent authored turn, if any — relayed to Jira once
        the session goes idle (see JiraBridge.sync()). None until the agent has
        produced at least one turn (items only has the seed user message so far).
        Deliberately the LAST match, not the first: a long-running ticket keeps
        relaying whatever is newest, not just its opening read."""
        latest = None
        for item in items:
            data = item.get("data") or {}
            if data.get("role") == "assistant":
                text = cls._item_text(item)
                if text:
                    latest = text
        return latest

    _PR_RE = re.compile(r"https?://github\.com/[^/\s]+/[^/\s]+/pull/\d+")

    @classmethod
    def _extract_pr_url(cls, items: list) -> Optional[str]:
        """The PR the agent opened itself — it runs `gh pr create` in its worktree
        and echoes the URL, so scrape it from the transcript. Last match wins (a
        re-run / re-push prints a fresh one)."""
        found = None
        for item in items:
            for m in cls._PR_RE.findall(cls._item_text(item) or ""):
                found = m
        return found

    @classmethod
    def _pr_from_text(cls, text: Optional[str]) -> Optional[str]:
        """Fallback PR-link source: the agent's own narration (the same text we
        relay to Jira, e.g. 'Done. PR is up: <url>'). Used when the structured
        item scrape comes up empty because a given agent/harness nests message
        content differently — the URL is still plainly in the message text.
        Last match wins, consistent with _extract_pr_url."""
        if not text:
            return None
        matches = cls._PR_RE.findall(text)
        return matches[-1] if matches else None

    @staticmethod
    def _elicit_prompt(e: dict) -> str:
        """Pull the human-readable question out of a `response.elicitation_request`
        dict — MCP puts it in params.message; AP policies add content_preview."""
        p = e.get("params") or {}
        return (p.get("message") or p.get("content_preview") or p.get("prompt")
                or "the agent needs your input to continue")

    def status(self, session_id: str) -> SessionStatus:
        # include_items defaults True server-side, so the latest assistant turn
        # (if any) rides along on this same call — no extra round-trip.
        s = self._req("GET", f"/v1/sessions/{urllib.parse.quote(session_id)}")
        elicits = s.get("pending_elicitations") or []
        eid = question = None
        if elicits:
            e = elicits[0]
            eid = e.get("elicitation_id") or e.get("id")
            question = self._elicit_prompt(e)
        # waiting-for-a-human = an outstanding elicitation (a decision). Note:
        # pending_inputs are just queued messages, NOT a human-decision pause,
        # so they don't gate `waiting` on their own.
        items = s.get("items") or []
        latest = self._latest_assistant_message(items)
        return SessionStatus(
            s.get("status", "unknown"), bool(elicits),
            elicitation_id=eid, question=question,
            agent_name=s.get("agent_name") or s.get("agent_id"),
            session_url=self.session_url(session_id),
            latest_message=latest,
            workspace=s.get("workspace"),  # the managed sandbox id -> maps a lease back to its ticket
            # Prefer the structured transcript scrape; fall back to the agent's own
            # message text (which reliably carries the "PR is up: <url>" line) so a
            # per-agent item-shape quirk can't hide a PR that's plainly narrated.
            pr_url=self._extract_pr_url(items) or self._pr_from_text(latest))

    def session_url(self, session_id: str) -> Optional[str]:
        return f"{self.base}/sessions/{urllib.parse.quote(session_id)}" if self.base else None

    def approve(self, session_id: str, elicitation_id: str, accept: bool,
                content: Optional[dict] = None) -> None:
        sid = urllib.parse.quote(session_id)
        data: dict = {"elicitation_id": elicitation_id,
                      "action": "accept" if accept else "decline"}
        if content is not None:
            data["content"] = content
        self._req("POST", f"/v1/sessions/{sid}/events", {"type": "approval", "data": data})

    def send(self, session_id: str, message: str) -> None:
        # reiterate = inject a new user-message event into the live session
        # (SessionEventInput is the /events body per the schema).
        sid = urllib.parse.quote(session_id)
        item = self._message_item(message)
        try:
            self._req("POST", f"/v1/sessions/{sid}/events", item)
            return
        except OmnigentError as e:
            if "no runner bound" not in str(e).lower():
                raise
            self._recover_runner_and_retry(session_id, sid, item, first_error=e)

    def _recover_runner_and_retry(self, session_id: str, sid: str, item: dict, *, first_error) -> None:
        """WORKAROUND for the server-side managed runner-bind race: the host
        registers (host_id/workspace populate, status 'ready') but the runner
        never binds on the replica that ran the managed launch, so /events 503s
        "no runner bound" forever with no failure status. We explicitly launch a
        runner (POST /v1/hosts/{host_id}/runners {session_id, workspace}) and retry
        the event — retried because each call may hit a different replica, one of
        which missed the host's in-memory registration. NOT a fix; remove once the
        server retries/repairs the bind itself (see the runner-bind bug report)."""
        sess = self._req("GET", f"/v1/sessions/{sid}") or {}
        host_id, workspace = sess.get("host_id"), sess.get("workspace")
        if not host_id or not workspace:
            raise OmnigentError(
                f"session {session_id}: no runner bound and can't self-heal "
                f"(host_id={host_id!r}, workspace={workspace!r}): {first_error}")
        hid = urllib.parse.quote(host_id)
        last = first_error
        for _ in range(5):
            try:
                self._req("POST", f"/v1/hosts/{hid}/runners",
                          {"session_id": session_id, "workspace": workspace})
            except OmnigentError as exc:
                last = exc  # a replica that already bound a runner may reject — keep trying
            try:
                self._req("POST", f"/v1/sessions/{sid}/events", item)
                return
            except OmnigentError as exc:
                last = exc
                if "no runner bound" not in str(exc).lower():
                    raise  # a different failure — surface it, don't mask
            time.sleep(1.5)
        raise OmnigentError(f"session {session_id}: runner never bound after 5 retries ({last})")
