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

Before planning or modifying any code, first inspect `CLAUDE.md` (or `AGENT_RULES.md`) in the repository root. \
You must strictly adhere to the architecture conventions, typing rules, and pre-completion verification commands defined there.

Scope the ticket and share your implementation plan, then proceed directly to \
implementing it — no need to stop and wait for approval first.

EXECUTION DIRECTIVE (TWO-TIER HIERARCHY):
1. Preferred: If a coding sub-agent CLI (e.g. claude, codex, opencodex, cursor-agent, hermes, pi, or agy) is installed in this machine's PATH, you may dispatch the implementation plan to it.
2. Fallback: If no external coding CLI is present, you are FULLY AUTHORIZED to author, edit, and implement all source code directly using your available file and shell tools. Do NOT halt or ask the reporter for permission to write code or install CLIs — proceed immediately to direct implementation.

Do not push a branch or open a pull request yourself, even a draft — that step \
belongs to Meeseek, not you. Commit your work on the branch that's already \
checked out in this session — do not create or switch to a new branch, even one \
named after the ticket; Meeseek's own verification diffs the branch that was \
already prepared for you, not whichever one you happen to leave checked out. \
Once your change is implemented and you've run the \
targeted test in this session to self-verify it, stop and post a summary of what \
changed, referencing the Jira key `{key}`, ending with this exact line so the \
reporter knows precisely what to do next:

Your action: reply `{prefix} finalize` and Meeseek will independently re-verify \
this host-side (readiness, the real test run, the diff) and open the pull \
request itself from that evidence.

When you post a status update that reaches the ticket reporter, keep it focused \
on the ticket itself: what you found, what you're doing, and what you need from \
them. Leave out internal tooling/process details (which CLIs or sub-agents are \
available, roster/preflight checks, etc.) unless it's something that actually \
blocks their decision.

--- Ticket {key} ---
"""


_PLAN_ONLY_PREAMBLE = """\
You are working on Jira ticket {key}.

Before planning, first inspect `CLAUDE.md` (or `AGENT_RULES.md`) in the repository root. \
You must strictly adhere to the architecture conventions, typing rules, and verification patterns defined there.

IMPORTANT PLAN-ONLY MANDATE:
Do NOT modify any code, create any files, run git commits, or implement changes yet.
Scope the ticket, inspect the relevant files, and provide a clear, structured implementation plan outlining:
1. Target files and components to be modified.
2. Estimated changes and blast radius.
3. Verification and test plan.

Once your plan is detailed, stop immediately and conclude with this exact line:

Your action: reply `{prefix} approve` to approve this plan and authorize Meeseek to begin code implementation.

When you post your plan that reaches the ticket reporter, keep it focused on the ticket itself: what you found, proposed changes, and risks.

--- Ticket {key} ---
"""


# The FINISH hand-off convention _BEHAVIOR_PREAMBLE mandates the agent write
# once it's actually done (not on interim status updates). Used by
# JiraBridge._check_halt as a second, narrow gate alongside session_state ==
# "idle" — fixed phrases we check for hand-off / blocker states.
_FINISH_MARKER = "Your action:"
_HANDOFF_MARKERS = ("Your action:", "What I need from you", "Blocker.")


def _finalize_summary(ev: dict, prefix: str = "#meeseek") -> str:
    """Render the notary's evidence as a short, human-readable comment — every
    field here was re-derived host-side, none of it came from the agent."""
    readiness = "✓ ready" if ev.get("readiness_ok") else "✕ not ready"
    rows = ev.get("seed_rows")
    seed = f"{rows} seeded rows" if rows is not None else "seed check skipped"
    test_exit = ev.get("test_exit")
    test_timed_out = ev.get("test_timed_out")
    if ev.get("test_cmd"):
        tests = f"exit {test_exit}" + (" (timed out)" if test_timed_out else "")
    else:
        tests = "no test configured for this app"
    diff = diff_stat(ev.get("diff") or "")

    test_failed = ev.get("test_cmd") and (test_exit != 0 or test_timed_out)
    guardrail_failed = not ev.get("guardrail_passed", True)

    if guardrail_failed:
        reason = ev.get("guardrail_reason") or "Blast radius or AST test integrity check failed."
        summary = (
            f"🛑 **Meeseek Guardrail: Blast Radius / AST Violation**\n\n"
            f"Pull Request creation has been blocked by host guardrails.\n"
            f"**Violation**: {reason}\n\n"
            f"• **Readiness**: `{readiness}`\n"
            f"• **Database Integrity**: `✓ {seed}`\n"
            f"• **Git Diff**: `{diff}`\n\n"
            f"🛠️ **To Authorize**: If this blast radius is intentional, reply `{prefix} approve` or adjust the scope."
        )
    elif test_failed:
        summary = (
            f"⚠️ **Meeseek Notary: Verification Failed**\n\n"
            f"The test command returned non-zero exit code ({tests}).\n"
            f"Pull Request creation has been blocked to protect the repository.\n\n"
            f"• **Readiness**: {readiness}\n"
            f"• **Database Integrity**: {seed}\n"
            f"• **Git Diff**: {diff}"
        )
        if ev.get("test_output"):
            summary += f"\n\n**Failure Traceback**:\n```text\n{ev['test_output'][-1500:]}\n```"
    else:
        summary = (
            f"🎯 **Task Completed! Meeseek Certified Delivery**\n"
            f"*\"Ooo yeah, can-do! All tests passed and code is verified!\"*\n\n"
            f"🔏 **Host Notary Proof of Correctness**:\n\n"
            f"• **Test Suite**: `✓ Passed` ({tests})\n"
            f"• **HTTP Readiness**: `{readiness}`\n"
            f"• **Database Integrity**: `✓ {seed}`\n"
            f"• **Git Diff**: `{diff}`"
        )

    branch_note = ev.get("branch_note")
    if branch_note:
        summary += f"\n\n⚠️ {branch_note}"
    return summary


def _wrap_prompt(spec: Optional[str], key: str, prefix: str, plan_only: bool = False) -> str:
    """Build the actual seed prompt from a ticket's raw Jira description,
    wrapping it with the behavioral instructions above. Always wraps, even
    when the ticket has no description, so every Jira-triggered run gets the
    same plan/approve discipline regardless of how well-specified the ticket is."""
    body = spec or f"(no description on {key} — check the ticket directly for context)"
    preamble = _PLAN_ONLY_PREAMBLE if plan_only else _BEHAVIOR_PREAMBLE
    return preamble.format(key=key, prefix=prefix) + body


def _is_bot_comment(body: str) -> bool:
    """Returns True if the comment is authored by our own bot automation.
    Recognizes all bot notification headers regardless of author account ID,
    preventing infinite feedback loops when the human reporter and the API
    token belong to the same Jira user."""
    b = (body or "").strip()
    if b.startswith(("/", "!", "#")):
        return False
    lower = b.lower()
    if lower.startswith(("holodeck", "meeseek")):
        return True
    # Emoji headers used by Meeseek
    if any(b.startswith(prefix) for prefix in ("🚀", "💬", "⏸️", "🧪", "⚠️", "🎯", "🛑")):
        return True
    return False


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
            for p in (self.prefix, "/meeseek", "#meeseek", "/holodeck", "#holodeck"):
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
            key, f"Meeseek: unknown command `{cmd}`. "
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
                self._say(key, f"Meeseek: a session already ran for this ticket "
                               f"({existing.status}); a new one was not started.")
                return "exists"
            if existing.halted:
                # Re-triggering on a halted ticket acts as resume/approval
                return self._answer(key, "approve")
            self._say(key, "Meeseek: a task is already active for this ticket.")
            return "conflict"
        try:
            issue = self.jira.get_issue(key)
        except Exception:
            log.exception("jira get_issue failed for %s", key)
            issue = {}
        spec = issue.get("description") or None
        labels = issue.get("labels") or []
        plan_label = getattr(self.cfg, "jira_plan_label", "meeseek:plan-only")
        plan_only = bool((spec and "/plan" in spec) or (plan_label in labels))
        app = self.manager.cfg.app_key(self.manager.cfg.default_app)
        target_repo, repo_error = self._extract_target_repo(app, spec or "")
        if repo_error:
            if key not in self._repo_blocked:
                self._say(key, f"Meeseek: {repo_error}")
                self._repo_blocked.add(key)
            return "invalid-target-repo"
        base_overrides = self._extract_base_overrides(spec or "")
        try:
            rec = self.manager.trigger(
                key, prompt=_wrap_prompt(spec, key, self.prefix, plan_only=plan_only),
                target_repo=target_repo,
                base_overrides=base_overrides,
                plan_only=plan_only)
        except ConsoleConflict:  # race safety net (a concurrent start slipped in)
            self._say(key, "Meeseek: a task is already active for this ticket.")
            return "conflict"
        except ConsoleError as e:
            self._say(key, f"Meeseek: failed to start — {e}")
            return "error"
        if rec.status == "queued":
            max_limit = getattr(self.manager.cfg, "max_app_leases", 3)
            msg = (
                f"⏳ **Meeseek on Standby (Queued)**\n"
                f"*\"I'm Mr. Meeseeks, look at me! Application capacity reached ({max_limit} active workspaces). "
                f"Ticket **{key}** is queued and will boot automatically once an active workspace is released.\"*\n\n"
                f"• **Workspace**: `{rec.lease_id}` (Target Repo: `{target_repo or 'default'}`)\n"
                f"• **Status**: Queued in FIFO workspace line"
            )
            self._say(key, msg)
            return "queued"

        links = [(label, url) for label, url in
                 (("Preview", rec.preview_url),) if url]
        mode_hint = "Plan-only mode (`/plan` mandate active: formulating plan without code edits)" if plan_only else "In-sandbox coding underway..."
        msg = (
            f"🚀 **Meeseek on the job!**\n"
            f"*\"I'm Mr. Meeseeks, look at me! I've claimed ticket **{key}** and booted an isolated sandbox.\"*\n\n"
            f"• **Workspace**: `{rec.lease_id}` (Target Repo: `{target_repo or 'default'}`)\n"
            f"• **Live Preview**: [{rec.preview_url}]({rec.preview_url})\n"
            f"• **Guardrails Active**: `CLAUDE.md` repository conventions pre-grounded\n"
            f"• **Status**: {mode_hint}"
        )
        self._say(key, msg, links=links or None)
        return "started"

    # A composite ticket names its target repo with a `Repo: <name>` line
    # anywhere in the description — the "golden ticket" convention, kept
    # separate from Jira labels (which only ever carry the fixed
    # trigger/halt labels). Case-insensitive on the key, exactly one bare
    # token as the value (repo names have no spaces).
    _REPO_LINE_RE = re.compile(r"^\s*repo\s*:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
    _BASE_LINE_RE = re.compile(r"^\s*(?:base|depends-on)\s*:\s*(\S+)@(\S+)\s*$", re.IGNORECASE | re.MULTILINE)

    @classmethod
    def _extract_base_overrides(cls, description: str) -> Optional[dict[str, str]]:
        """Parse upstream base/dependency branch overrides from lines like:
        `Base: test_backend@agent/fsa-10` or `Depends-On: test_backend@agent/fsa-10`.
        Returns a dict of {repo: ref} or None if none found."""
        overrides = {m.group(1): m.group(2) for m in cls._BASE_LINE_RE.finditer(description)}
        return overrides or None

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
        remove_labels = [self.cfg.jira_halt_label]
        plan_label = getattr(self.cfg, "jira_plan_label", "meeseek:plan-only")
        if plan_label:
            remove_labels.append(plan_label)
        try:
            self.jira.set_labels(rec.ticket, remove=remove_labels, add=[self.cfg.jira_trigger_label])
        except Exception:
            log.exception("jira set_labels (clear halt) failed for %s", rec.ticket)

    def _answer(self, key: str, text: str) -> str:
        """Route a human reply: a yes/no on a pending elicitation becomes an
        approval; anything else becomes open-ended guidance (a message)."""
        try:
            rec, verdict = self.manager.answer(key, text)
        except ConsoleError as e:
            self._say(key, f"Meeseek: could not apply that reply — {e}")
            return "error"
        self._clear_halt(rec)
        if verdict is True:
            self._say(key, "Meeseek ✅ Approved. Continuing.")
            return "approved"
        if verdict is False:
            self._say(key, "Meeseek ❌ Declined. Notifying the agent.")
            return "declined"
        self._say(key, "Meeseek 🔁 Guidance received. Continuing.")
        return "reiterated"

    def _finalize(self, key: str) -> str:
        """The explicit human trigger for the notary. Never automatic — the human
        decides the change is ready, then Meeseek (not the agent) re-verifies it
        host-side and opens the PR from that evidence."""
        ticket_summary, issue_type = None, None
        try:
            issue = self.jira.get_issue(key)
            ticket_summary = issue.get("summary") or None
            issue_type = issue.get("issuetype") or None
        except Exception:
            log.exception("jira get_issue failed for %s (finalize)", key)

        # Notify Jira that notary verification has started
        self._say(
            key,
            "🧪 **Meeseek Notary: Running Independent Verification**\n\n"
            "The agent has concluded its implementation. Meeseek is now independently "
            "executing the verification suite inside the container before opening a PR..."
        )

        try:
            rec, evidence = self.manager.finalize(
                key, ticket_summary=ticket_summary, issue_type=issue_type)
        except ConsoleError as e:
            self._say(key, f"Meeseek: finalize failed — {e}")
            return "error"
        self._clear_halt(rec)
        pr = evidence.get("pr_url")
        body = _finalize_summary(evidence, prefix=self.prefix)
        if not pr:
            if not evidence.get("guardrail_passed", True):
                body += f"\n\n🛑 **Pull Request Blocked**: Host blast-radius or AST guardrails tripped ({evidence.get('guardrail_reason')})."
            elif evidence.get("test_cmd") and (evidence.get("test_exit") != 0 or evidence.get("test_timed_out")):
                body += "\n\n❌ **Pull Request Blocked**: The test suite did not pass with Exit 0. PR creation has been withheld."
            elif not (evidence.get("diff") or "").strip():
                body += "\n\nNo pull request was opened — there was nothing to diff against golden."
            else:
                body += "\n\nNo pull request was opened — PR creation is disabled or failed; the evidence above is still recorded."
        links = []
        if pr:
            links.append(("PR", pr))
        if rec.preview_url:
            links.append(("Preview", rec.preview_url))
        self._say(key, body, links=links or None)
        return "finalized"

    def _release(self, key: str) -> str:
        try:
            self.manager.release(key)
        except ConsoleError as e:
            self._say(key, f"Meeseek: failed to release — {e}")
            return "error"
        self._say(key, "Meeseek: the environment has been released.")
        return "released"

    def _reset(self, key: str) -> str:
        rec = self.manager.store.get(key)
        if rec is None:
            self._say(key, "Meeseek: nothing to reset — no session was ever "
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
            self._say(key, "Meeseek: this ticket's session has been reset — "
                           f"add the `{self.cfg.jira_trigger_label}` label again "
                           "to start a new one.")
        try:
            self.jira.set_labels(
                key, remove=[self.cfg.jira_reset_label, self.cfg.jira_halt_label, self.cfg.jira_trigger_label])
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
        if (rec.workflow_state in ("CERTIFIED_PR", "PR_OPENED", "RELEASED", "FAILED", "NOTARY_FAILED", "GUARDRAIL_BLOCKED")
                or rec.last_action == "finalized"
                or rec.pr_url):
            return  # Already finalized or delivered — never regress back to halted

        if rec.session_state != "idle" or rec.waiting:
            if rec.halted:  # left "idle" (or picked up an elicitation) -> re-arm
                rec.halted = False
                self.manager.store.put(rec)
            return
        has_handoff = any(m in (rec.stable_agent_message or "") for m in _HANDOFF_MARKERS)
        if not rec.stable_agent_message or not has_handoff:
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
        rec.workflow_state = "WAITING_INPUT"
        msg = rec.stable_agent_message or ""
        if rec.plan_only or "Blocker." in msg or "What I need from you" in msg:
            self._say(
                rec.ticket,
                f"⏸️ **Meeseek Implementation Plan Ready for Review**\n\n"
                f"The plan-only mandate (`/plan`) was requested. The agent has prepared the implementation strategy above.\n\n"
                f"Reply `{self.prefix} approve` to approve the plan and authorize Meeseek to begin code implementation."
            )
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
                cid = self._say(rec.ticket, f"💬 **Meeseek Update**\n\n{rec.stable_agent_message}")
                if cid is not None:
                    rec.posted_agent_message = rec.stable_agent_message
                    rec.jira_watermark = str(cid)
                    self.manager.store.put(rec)
            self._check_halt(rec)
            if rec.waiting and not rec.notified_waiting:
                question = rec.question or "The agent needs your input to continue."
                cid = self._say(
                    rec.ticket,
                    f"⏸️ **Meeseek Needs Your Guidance**\n\n> {question}\n\n"
                    f"Reply with `yes` / `no`, or with your guidance — "
                    f"it will be relayed to the agent in the sandbox.")
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
