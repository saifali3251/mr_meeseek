"""JiraBridge — turns Jira events into console actions and posts back.

Inbound:
  - label `<trigger_label>` added, or comment `<prefix> run`  -> start the task
  - comment `<prefix> retry <text>` / `<prefix> feedback <text>`, or any plain
    comment on a ticket with an ACTIVE task                    -> reiterate
  - comment `<prefix> stop` / `release`                        -> release
  Comments authored by our own bot account are ignored (loop guard).

Outbound (via the poller's on_tick -> sync):
  - when a task is waiting-for-input, comment once asking for it.

The bridge holds no state of its own — it drives the ConsoleManager and reads
the ticket spec from Jira to seed the agent's prompt.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from holodeck.console.jira import JiraClient, JiraEvent
from holodeck.console.manager import ConsoleConflict, ConsoleError, ConsoleManager
from holodeck.models import diff_stat

log = logging.getLogger("holodeck.console.jira")


def _as_int(v) -> int:
    """Jira comment ids are a monotonic numeric sequence; compare them as ints so
    'newer than the question' is a simple >. Unparseable -> -1 (sorts oldest)."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return -1


# Behavioral instructions wrapped around every Jira-triggered ticket's seed
# prompt. Independent asks, all aimed at the Jira comment thread being the
# record of what happened, not a debug log:
#   1. Share a plan before/while implementing — still visible via the idle-only
#      relay (JiraBridge.sync() posts whatever the agent says once it goes
#      idle) — but NOT a blocking gate. The agent proceeds straight through;
#      review happens on the PR, not before implementation starts.
#   2. Never push a branch or open the PR itself — that's Holodeck's job. The
#      whole point of the notary (LeaseService.finalize(), re-derives every
#      claim host-side) is a trust boundary the agent can't cross by just
#      running `gh pr create` on its own worktree; see
#      docs/JIRA_CONSOLE_FLOW.md / omnigent-model-a-open-points.md "Who
#      creates the PR". A human triggers finalize with `<prefix> finalize`
#      once the change looks ready.
#   3. Keep those relayed updates about the ticket, not the harness — without
#      this, status updates leak internal tooling detail (which CLIs/sub-agents
#      are on the box, roster preflight checks) that means nothing to a
#      ticket reporter and was never asked for.
#   4. Commit on the branch already checked out — never create/switch to a new
#      one. finalize()'s diff is `git diff golden_head..HEAD` in that same
#      worktree; strike.sh/pool.py already cut `agent/<lease_id>` and checked
#      it out before the agent's session ever starts, specifically so this
#      works with zero agent-side branch bookkeeping. Found for real
#      2026-08-30: an agent created and committed to its own descriptively-
#      named branch (`crlt-910-label-fix`) instead, leaving HEAD back on
#      `agent/crlt-910` unchanged — finalize reported `diff +0 -0` and never
#      opened a PR despite the real, already-implemented change sitting one
#      `git checkout` away. This instruction alone can't be a 100% guarantee
#      (an agent can still not follow it) — see finalize()'s own multi-branch
#      detection below for the actual host-side backstop.
_BEHAVIOR_PREAMBLE = """\
You are working on Jira ticket {key}.

Scope the ticket and share your implementation plan, then proceed directly to \
implementing it — no need to stop and wait for approval first.

Do not push a branch or open a pull request yourself, even a draft — that step \
belongs to Holodeck, not you. Commit your work on the branch that's already \
checked out in this session — do not create or switch to a new branch, even one \
named after the ticket; Holodeck's own verification diffs the branch that was \
already prepared for you, not whichever one you happen to leave checked out. \
Once your change is implemented and you've run the \
targeted test in this session to self-verify it, stop and post a summary of what \
changed, referencing the Jira key `{key}`, ending with this exact line so the \
reporter knows precisely what to do next:

Your action: reply `{prefix} finalize` and Holodeck will independently re-verify \
this host-side (readiness, the real test run, the diff) and open the pull \
request itself from that evidence.

When you post a status update that reaches the ticket reporter, keep it focused \
on the ticket itself: what you found, what you're doing, and what you need from \
them. Leave out internal tooling/process details (which CLIs or sub-agents are \
available, roster/preflight checks, etc.) unless it's something that actually \
blocks their decision.

--- Ticket {key} ---
"""


# The FINISH hand-off convention _BEHAVIOR_PREAMBLE mandates the agent write
# once it's actually done (not on interim status updates). Used by
# JiraBridge._check_halt as a second, narrow gate alongside session_state ==
# "idle" — a fixed phrase we ourselves require, not a guess at wording.
_FINISH_MARKER = "Your action:"


def _finalize_summary(ev: dict) -> str:
    """Render the notary's evidence as a short, human-readable comment — every
    field here was re-derived host-side, none of it came from the agent."""
    readiness = "✓ ready" if ev.get("readiness_ok") else "✕ not ready"
    rows = ev.get("seed_rows")
    seed = f"{rows} seeded rows" if rows is not None else "seed check skipped"
    if ev.get("test_cmd"):
        tests = f"tests: exit {ev.get('test_exit')}" + (" (timed out)" if ev.get("test_timed_out") else "")
    else:
        tests = "no test configured for this app"
    diff = diff_stat(ev.get("diff") or "")
    summary = (f"Holodeck 🔏 **Finalized** — verified independently, host-side "
              f"(not self-reported by the agent):\n\n"
              f"{readiness} · {seed} · {tests} · diff {diff}")
    branch_note = ev.get("branch_note")
    if branch_note:
        # ComposeProvider._recover_stray_branch had something to say — either it
        # found and used a stray branch instead of an initially-empty diff, or
        # found an ambiguous multi-branch situation it refused to guess at. Either
        # way this is exactly the kind of thing that must NOT be silent (see that
        # method's own docstring for the 2026-08-30 incident this backstops).
        summary += f"\n\n⚠️ {branch_note}"
    return summary


def _wrap_prompt(spec: Optional[str], key: str, prefix: str) -> str:
    """Build the actual seed prompt from a ticket's raw Jira description,
    wrapping it with the behavioral instructions above. Always wraps, even
    when the ticket has no description, so every Jira-triggered run gets the
    same plan/approve discipline regardless of how well-specified the ticket is."""
    body = spec or f"(no description on {key} — check the ticket directly for context)"
    return _BEHAVIOR_PREAMBLE.format(key=key, prefix=prefix) + body


def _is_bot_comment(body: str) -> bool:
    """Returns True if the comment is authored by our own bot automation.
    Recognizes all bot notification headers regardless of author account ID,
    preventing infinite feedback loops when the human reporter and the API
    token belong to the same Jira user."""
    b = (body or "").strip()
    if b.startswith(("/", "!", "#")):
        return False
    lower = b.lower()
    return lower.startswith(("holodeck", "meeseek"))


class JiraBridge:
    def __init__(self, manager: ConsoleManager, jira: JiraClient, cfg) -> None:
        self.manager = manager
        self.jira = jira
        self.cfg = cfg
        self.prefix = cfg.jira_command_prefix
        # Tickets blocked on a bad/missing `Repo:` line never get a TaskRecord,
        # so _poll_triggers() would otherwise re-attempt (and re-comment) every
        # tick forever while the trigger label sits there unaddressed. Process-
        # local edge-trigger, same idea as TaskRecord.halted: nag once, not on
        # a loop. Lost on restart, which just means one extra nag — acceptable.
        self._repo_blocked: set[str] = set()
        self._bot_comment_ids: set[str] = set()

    def _say(self, key: str, body: str, *,
             links: Optional[list[tuple[str, str]]] = None) -> Optional[str]:
        """Best-effort comment back (returns the new comment id, or None on failure)
        — a Jira outage must not fail the webhook or the action that already
        succeeded."""
        try:
            cid = self.jira.post_comment(key, body, links=links)
            if cid:
                cid_str = str(cid)
                self._bot_comment_ids.add(cid_str)
                rec = self.manager.store.get(key)
                if rec is not None:
                    if rec.jira_watermark is None or _as_int(cid_str) > _as_int(rec.jira_watermark):
                        rec.jira_watermark = cid_str
                        self.manager.store.put(rec)
            return cid
        except Exception:
            log.exception("jira post_comment failed for %s", key)
            return None

    # ---- inbound -------------------------------------------------------
    def handle(self, event: Optional[JiraEvent]) -> str:
        if event is None:
            return "ignored"
        if self.cfg.jira_bot_account and event.author == self.cfg.jira_bot_account:
            return "ignored (self)"  # don't react to our own comments

        if event.kind == "label":
            if event.label == self.cfg.jira_trigger_label:
                return self._start(event.issue_key)
            if event.label == self.cfg.jira_reset_label:
                return self._reset(event.issue_key)
            return "ignored (label)"

        if event.kind == "comment":
            body = event.body.strip()
            if _is_bot_comment(body):
                return "ignored (bot comment)"
            matched_prefix = None
            for p in (self.prefix, "/meeseek", "/holodeck"):
                if p and body.startswith(p):
                    matched_prefix = p
                    break
            if matched_prefix:
                return self._command(event.issue_key, body[len(matched_prefix):].strip())
            # a plain comment is a reply iff there's an active task for the ticket
            rec = self.manager.store.get(event.issue_key)
            if rec is not None and not rec.is_terminal:
                return self._answer(event.issue_key, body)
            return "ignored (no active task)"

        return "ignored"

    def _command(self, key: str, cmd: str) -> str:
        if cmd.startswith("run"):
            return self._start(key)
        if cmd.startswith(("retry", "feedback")):
            parts = cmd.split(None, 1)
            return self._answer(key, parts[1] if len(parts) > 1 else "")
        if cmd.startswith(("approve", "yes")):
            return self._answer(key, "yes")
        if cmd.startswith(("decline", "reject", "no")):
            return self._answer(key, "no")
        if cmd.startswith("finalize"):
            return self._finalize(key)
        if cmd.startswith(("stop", "release")):
            return self._release(key)
        self._say(
            key, f"Holodeck: unknown command `{cmd}`. "
                 f"Try `{self.prefix} run | retry <text> | finalize | stop`.")
        return "unknown-command"

    def _start(self, key: str) -> str:
        # One session per Jira id: if we've EVER started this ticket — active OR
        # already released/failed — don't create another. This avoids the messy
        # edge cases of a second run for the same ticket (old destroyed + new both
        # on the board, stitching ambiguity). The operator can still deliberately
        # re-strike from the console; this only guards the Jira-driven flow.
        existing = self.manager.store.get(key)
        if existing is not None:
            if existing.is_terminal:
                self._say(key, f"Holodeck: a session already ran for this ticket "
                               f"({existing.status}); a new one was not started.")
                return "exists"
            self._say(key, "Holodeck: a task is already active for this ticket.")
            return "conflict"
        try:
            issue = self.jira.get_issue(key)
        except Exception:
            log.exception("jira get_issue failed for %s", key)
            issue = {}
        spec = issue.get("description") or None
        app = self.manager.cfg.app_key(self.manager.cfg.default_app)
        target_repo, repo_error = self._extract_target_repo(app, spec or "")
        if repo_error:
            if key not in self._repo_blocked:
                self._say(key, f"Holodeck: {repo_error}")
                self._repo_blocked.add(key)
            return "invalid-target-repo"
        try:
            rec = self.manager.trigger(
                key, prompt=_wrap_prompt(spec, key, self.prefix), target_repo=target_repo)
        except ConsoleConflict:  # race safety net (a concurrent start slipped in)
            self._say(key, "Holodeck: a task is already active for this ticket.")
            return "conflict"
        except ConsoleError as e:
            self._say(key, f"Holodeck: failed to start — {e}")
            return "error"
        self._repo_blocked.discard(key)  # started fine after an earlier nag (edited + re-triggered)
        # Session link deliberately omitted: the Omnigent session URL is an internal
        # tooling link, not something a ticket reporter needs — same reasoning
        # _BEHAVIOR_PREAMBLE already gives the agent for its own status updates
        # ("leave out internal tooling/process details").
        links = [(label, url) for label, url in
                 (("Preview", rec.preview_url),) if url]
        self._say(key, f"Holodeck ▶ started — lease `{rec.lease_id}`. "
                       f"Updates will be posted here as the agent progresses.",
                  links=links or None)
        return "started"

    # A composite ticket names its target repo with a `Repo: <name>` line
    # anywhere in the description — the "golden ticket" convention, kept
    # separate from Jira labels (which only ever carry the fixed
    # trigger/halt labels). Case-insensitive on the key, exactly one bare
    # token as the value (repo names have no spaces).
    _REPO_LINE_RE = re.compile(r"^\s*repo\s*:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)

    def _extract_target_repo(self, app: str, description: str) -> tuple[Optional[str], Optional[str]]:
        """Parse target_repo out of the ticket description. Returns
        (target_repo, error_message); error_message is set (and target_repo is
        None) iff the ticket can't be started as-is: the description names a
        repo that isn't valid for this app, names more than one distinct repo,
        or — for a composite app — names none at all. A composite app has no
        sensible default (which of its N repos would a bare ticket mean?), so
        a missing `Repo:` line blocks the start rather than silently guessing;
        wasting a real lease/sandbox strike on the wrong repo is expensive to
        walk back. Checked HERE, before trigger() ever runs, so this is
        reported to the ticket immediately instead of surfacing as a
        downstream 422 the OmnigentDriver path can't relay (its real
        acquire() call happens async inside Omnigent's own process)."""
        found = sorted(set(m.group(1) for m in self._REPO_LINE_RE.finditer(description)))
        try:
            valid = self.manager.client.valid_target_repos(app)
        except Exception:
            log.exception("valid_target_repos failed for app %s", app)
            return None, None  # don't block a start over a lookup hiccup
        if not valid:
            if found:
                return None, f"app {app!r} is not a composite app; `Repo:` cannot be specified."
            return None, None  # single-repo app, nothing to extract
        if not found:
            return None, (f"this is a composite app ({', '.join(sorted(valid))}) — add a "
                          f"`Repo: <name>` line to the description and re-add the trigger "
                          f"label to start.")
        if len(found) > 1:
            return None, (f"multiple `Repo:` lines found ({', '.join(found)}) in the "
                          f"description — the ticket must name exactly one.")
        repo = found[0]
        if repo not in valid:
            return None, (f"`Repo: {repo}` is not valid for app {app!r} "
                          f"— must be one of {sorted(valid)}.")
        return repo, None

    def _clear_halt(self, rec) -> None:
        """A human acted on the ticket — it's no longer 'waiting on you', so
        drop the halt label immediately (optimistic — `_check_halt` will also
        self-correct on the next poll once `session_state` actually moves off
        `idle`). Best-effort: a labeling hiccup must never surface as a failure
        of the action that actually happened (answer/finalize)."""
        rec.halted = False
        self.manager.store.put(rec)
        try:
            self.jira.set_labels(rec.ticket, remove=[self.cfg.jira_halt_label])
        except Exception:
            log.exception("jira set_labels (clear halt) failed for %s", rec.ticket)

    def _answer(self, key: str, text: str) -> str:
        """Route a human reply: a yes/no on a pending elicitation becomes an
        approval; anything else becomes open-ended guidance (a message)."""
        try:
            rec, verdict = self.manager.answer(key, text)
        except ConsoleError as e:
            self._say(key, f"Holodeck: could not apply that reply — {e}")
            return "error"
        self._clear_halt(rec)
        if verdict is True:
            self._say(key, "Holodeck ✅ Approved. Continuing.")
            return "approved"
        if verdict is False:
            self._say(key, "Holodeck ❌ Declined. Notifying the agent.")
            return "declined"
        self._say(key, "Holodeck 🔁 Guidance received. Continuing.")
        return "reiterated"

    def _finalize(self, key: str) -> str:
        """The explicit human trigger for the notary. Never automatic — the human
        decides the change is ready, then Holodeck (not the agent) re-verifies it
        host-side and opens the PR from that evidence."""
        # Best-effort, same pattern as _start(): a Jira hiccup here degrades to the
        # old bare-ticket PR title/no-narrative body, it never fails finalize itself.
        ticket_summary, issue_type = None, None
        try:
            issue = self.jira.get_issue(key)
            ticket_summary = issue.get("summary") or None
            issue_type = issue.get("issuetype") or None
        except Exception:
            log.exception("jira get_issue failed for %s (finalize)", key)
        try:
            rec, evidence = self.manager.finalize(
                key, ticket_summary=ticket_summary, issue_type=issue_type)
        except ConsoleError as e:
            self._say(key, f"Holodeck: finalize failed — {e}")
            return "error"
        self._clear_halt(rec)
        pr = evidence.get("pr_url")
        body = _finalize_summary(evidence)
        if not pr:
            body += ("\n\nNo pull request was opened — there was nothing to diff against golden."
                      if not (evidence.get("diff") or "").strip()
                      else "\n\nNo pull request was opened — PR creation is disabled or failed; "
                           "the evidence above is still recorded.")
        self._say(key, body, links=[("PR", pr)] if pr else None)
        return "finalized"

    def _release(self, key: str) -> str:
        try:
            self.manager.release(key)
        except ConsoleError as e:
            self._say(key, f"Holodeck: failed to release — {e}")
            return "error"
        self._say(key, "Holodeck: the environment has been released.")
        return "released"

    def _reset(self, key: str) -> str:
        """Deliberate cleanup label (default `holodeck:destroy`), separate from
        the trigger/halt labels — lets the same Jira ticket be re-run end to end
        for testing. _start()'s one-session-per-ticket guard blocks on ANY
        record at all in self.manager.store (terminal or not); this is the
        supported way to clear it, rather than a manual DB edit, so a later
        `holodeck` label add starts a genuinely new session instead of being
        silently ignored. Releases the underlying lease first if it's somehow
        still active (halted only means the agent is paused, not that the
        workspace was torn down) — otherwise deleting the record here would
        leak a running workspace nothing references anymore."""
        rec = self.manager.store.get(key)
        if rec is None:
            self._say(key, "Holodeck: nothing to reset — no session was ever "
                           "started for this ticket.")
        else:
            if not rec.is_terminal:
                try:
                    self.manager.release(key)
                except ConsoleError as e:
                    log.warning("reset: release failed for %s (%s) — clearing "
                               "the record anyway", key, e)
            self.manager.store.delete(key)
            self._repo_blocked.discard(key)
            self._say(key, "Holodeck: this ticket's session has been reset — "
                           f"add the `{self.cfg.jira_trigger_label}` label again "
                           "to start a new one.")
        try:
            self.jira.set_labels(
                key, remove=[self.cfg.jira_reset_label, self.cfg.jira_halt_label])
        except Exception:
            log.exception("jira set_labels (reset cleanup) failed for %s", key)
        return "reset"

    def _check_halt(self, rec) -> None:
        """Passive status flag, not an automation trigger: swap the trigger
        label for the halt label once Omnigent's OWN session state machine
        says the CURRENT turn is over (`rec.session_state == "idle"`) AND the
        agent's own narration contains the FINISH hand-off convention baked
        into _BEHAVIOR_PREAMBLE ("Your action: reply ..."). So the ticket is
        filterable/board-visible as 'waiting on a human' without anyone having
        to read the comment thread.

        Why idle alone isn't enough (confirmed on real tickets, e.g.
        COMP-4954, not just a theoretical worry): the orchestrator's own
        session goes genuinely `idle` right after it dispatches work to a
        sub-agent and posts an interim "I've kicked this off, I'll report
        back" message — well before that sub-agent's actual implementation is
        done. `session_state == "idle"` reliably means "the orchestrator's
        CURRENT turn ended," not "all dispatched work is done," for this
        multi-session dispatch pattern. Requiring the FINISH marker too is a
        second, narrow, convention-based check — not a return to guessing at
        arbitrary agent wording: it's a fixed phrase we ourselves mandate in
        the preamble, so it only matches the deliberate hand-off, not an
        interim status update.

        Omnigent's `status` is one of idle/running/waiting/failed (see server
        API docs on the session lifecycle). "idle" is defined as "no agent
        loop running... the terminal state after a turn finishes" — the real
        `response.completed` signal, not a proxy for it; it's just not a
        signal for "every dispatched sub-agent has also finished."

        Gotcha: Omnigent's own "waiting" status means the agent loop itself is
        internally parked (a sub-agent, a background tool) — NOT the same
        thing as `rec.waiting`, which is Holodeck's own concept for "a human
        decision (elicitation) is pending". The two are deliberately checked
        separately: a formal elicitation is handled by the waiting-input flow
        below and must never also get the halt label, even though the agent
        loop is technically idle-adjacent while parked on it.

        `rec.halted` is the edge-trigger: set once when we swap the label,
        cleared the moment `session_state` moves off "idle" again (a new turn
        started) so the ticket correctly re-halts the next time it goes idle."""
        if rec.session_state != "idle" or rec.waiting:
            if rec.halted:  # left "idle" (or picked up an elicitation) -> re-arm
                rec.halted = False
                self.manager.store.put(rec)
            return
        if not rec.stable_agent_message or _FINISH_MARKER not in rec.stable_agent_message:
            return  # idle, but hasn't actually handed off yet (e.g. mid-dispatch)
        if rec.halted:
            return  # already signaled for this idle period
        try:
            self.jira.set_labels(rec.ticket, add=[self.cfg.jira_halt_label],
                                  remove=[self.cfg.jira_trigger_label])
        except Exception:
            log.exception("jira set_labels (halt) failed for %s", rec.ticket)
            return  # retry next tick
        rec.halted = True
        self.manager.store.put(rec)

    # ---- outbound (poller on_tick) ------------------------------------
    def sync(self) -> None:
        """Comment once on each ticket whose agent has newly paused for input,
        posting the agent's ACTUAL question. The id of that comment becomes the
        reply watermark: only a comment posted AFTER it counts as the answer, so
        a later iteration never picks up an earlier round's reply. Also relays
        the agent's latest message once it has stopped changing across polls —
        this is for INTERMEDIATE progress narration, where low latency matters
        more than exact turn-completion (a still-running session, e.g. one
        waiting on a dispatched sub-agent, can go a long time without its
        `session_state` reaching "idle", so text-stability is the right signal
        here specifically). Compare `_check_halt` below, which needs the
        opposite property — exact turn completion, not just "hasn't changed
        recently" — and uses `session_state == "idle"` instead, deliberately
        not text-based."""
        for rec in self.manager.store.all():
            if (rec.stable_agent_message
                    and rec.stable_agent_message != rec.posted_agent_message):
                cid = self._say(rec.ticket, f"Holodeck 💬 {rec.stable_agent_message}")
                if cid is not None:
                    rec.posted_agent_message = rec.stable_agent_message
                    rec.jira_watermark = str(cid)
                    self.manager.store.put(rec)
            self._check_halt(rec)
            if rec.waiting and not rec.notified_waiting:
                question = rec.question or "The agent needs your input to continue."
                cid = self._say(
                    rec.ticket,
                    f"Holodeck ⏸ **Input needed:**\n\n> {question}\n\n"
                    f"Reply with `yes` / `no`, or with your guidance — "
                    f"it will be relayed to the agent.")
                if cid is None:
                    continue  # post failed; retry next tick (don't mark notified)
                rec.notified_waiting = True
                rec.jira_watermark = str(cid)
                self.manager.store.put(rec)

    # ---- inbound by polling (no webhook) -------------------------------
    def poll_inbound(self) -> None:
        """One inbound tick when we can't register a webhook: clear any reset-
        labelled tickets FIRST (so a trigger label added in the same tick starts
        a genuinely fresh session, not a no-op against a record that's about to
        be deleted anyway), then start newly-labelled tickets, then pull new
        comments on every active ticket (commands and replies alike — see
        _poll_replies). Guarded — a Jira blip must never take down the poll."""
        self._poll_resets()
        self._poll_triggers()
        self._poll_replies()

    def _poll_triggers(self) -> None:
        if not self.cfg.jira_project:
            return
        try:
            keys = self.jira.search_labeled(self.cfg.jira_project, self.cfg.jira_trigger_label)
        except Exception:
            log.exception("jira label search failed")
            return
        for key in keys:
            # a task record (active OR past) means we already fired for this ticket;
            # don't re-trigger a labelled-but-released ticket every minute.
            if self.manager.store.get(key) is None:
                self._start(key)

    def _poll_resets(self) -> None:
        """Same polling shape as _poll_triggers, for the destroy/reset label.
        _reset() removes the label itself once it's handled (see its own
        docstring), so this self-corrects tick to tick with no separate
        edge-trigger tracking needed — same reasoning _check_halt's label
        swap already relies on. This deployment has no registered Atlassian
        webhook, so handle()'s reset-label branch (the webhook path) never
        actually fires here — this poll is what makes the label real."""
        if not self.cfg.jira_project:
            return
        try:
            keys = self.jira.search_labeled(self.cfg.jira_project, self.cfg.jira_reset_label)
        except Exception:
            log.exception("jira label search failed (reset)")
            return
        for key in keys:
            self._reset(key)

    def _poll_replies(self) -> None:
        """Pull new comments on every ACTIVE ticket (not just ones with a formal
        elicitation open) — parity with the webhook path's handle(), which never
        gated command/reply dispatch on rec.waiting either. A `{prefix} <cmd>`
        reply runs the same command dispatch as run/retry/approve/finalize/stop;
        anything else is routed through _answer() exactly as before (yes/no
        resolves a pending elicitation, freeform becomes guidance)."""
        for rec in self.manager.store.all():
            if rec.is_terminal:
                continue
            try:
                comments = self.jira.list_comments(rec.ticket)
            except Exception:
                log.exception("jira list_comments failed for %s", rec.ticket)
                continue
            if rec.jira_watermark is None:
                # First poll to ever look at this ticket's comments (e.g. it was
                # only ever notified via a "needs input" post before, or this
                # ticket predates this polling path): establish a baseline at the
                # highest comment id seen so far, so history isn't replayed as a
                # fresh command/reply. Nothing to dispatch yet on this tick.
                ids = [_as_int(c.get("id")) for c in comments]
                rec.jira_watermark = str(max(ids)) if ids else "0"
                self.manager.store.put(rec)
                continue
            wm = _as_int(rec.jira_watermark)
            replies = [c for c in comments
                       if _as_int(c.get("id")) > wm
                       and str(c.get("id")) not in self._bot_comment_ids
                       and (not self.cfg.jira_bot_account or c.get("author") != self.cfg.jira_bot_account)
                       and not _is_bot_comment(c.get("body") or "")]
            if not replies:
                if comments:
                    max_id = max(_as_int(c.get("id")) for c in comments)
                    if max_id > wm:
                        rec.jira_watermark = str(max_id)
                        self.manager.store.put(rec)
                continue
            latest = max(replies, key=lambda c: _as_int(c.get("id")))  # the CURRENT answer
            body = (latest.get("body") or "").strip()
            if body.startswith(self.prefix):
                status = self._command(rec.ticket, body[len(self.prefix):].strip())
            else:
                status = self._answer(rec.ticket, body)
            if status != "error":
                # advance the cursor on the fresh record (answer() cleared `waiting`)
                fresh = self.manager.store.get(rec.ticket)
                if fresh is not None:
                    fresh.jira_watermark = str(latest.get("id"))
                    self.manager.store.put(fresh)
