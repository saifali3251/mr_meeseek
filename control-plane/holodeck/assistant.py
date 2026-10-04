"""Mr. Meeseeks Live Platform Copilot & DevOps Assistant.

In-Context Dynamic Grounding engine powered by Google Gemini API (gemini-1.5-flash).
Injects comprehensive Meeseek system knowledge + real-time operational state
(active leases, failure logs, exit codes, golden image age, Jira bridge) on every request.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

import httpx

log = logging.getLogger("holodeck.assistant")

DEFAULT_MODELS = [
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-1.5-flash",
]

STATIC_SYSTEM_RUNBOOK = """
# MEESEEK SYSTEM ARCHITECTURE & RUNBOOK

## 1. Core Design Principle
"The actor that does the work never certifies it."
- Modern AI coding agents can write plausible diffs and hallucinate test passes.
- Meeseek is the execution substrate that physically clones pristine environments, lets the agent work, and independently validates and notarizes the work from the outside.

## 2. Technical Mechanisms
- Golden Image: Pre-built stack, dependencies, applied migrations, and warm pre-seeded Postgres DB fixtures (relocated PGDATA). Never booted directly.
- CoW Cloning (Strike): Uses OS-level Copy-on-Write (macOS APFS clonefile or Linux btrfs/XFS reflink) to clone gigabytes of container and database state in < 280ms.
- Compose Isolation & Port Stripping: Every workspace has a distinct COMPOSE_PROJECT_NAME (ws-<lease_id>). Host ports are dynamically mapped to dedicated preview ports (e.g. 18000, 18001) to prevent collisions.
- Host-Side Notary (Finalize): When work is done, Meeseek independently tests the application from outside the container, runs the non-negotiable test suite (HOLO_TEST_CMD), checks database seed proofs, verifies git diff, and attaches exit code proof to the Pull Request.
- Teardown (Poof!): Like a Meeseeks fulfilling its purpose, containers are stopped and the CoW disk clone is deleted. Zero persistent state.

## 3. Workflow State Machine Stages
1. STRIKE: Workspace requested via Jira ticket or UI modal. (FIFO Queued if capacity limit is reached).
2. BOOT: CoW clone created, Docker containers booted, pre-seeded DB health check passed.
3. CODING: Omnigent coding agent (Debby) applies code changes, file edits, and tools in sandbox.
4. PREVIEW: Hot reload active. Live interactive browser preview accessible on dynamic preview port. Human reviews changes.
5. NOTARY: Impartial Host Notary runs HOLO_TEST_CMD and checks AST blast-radius.
6. PR_DELIVERED: Exit 0 proof certified. Pull Request opened on GitHub with full evidence.

## 4. Jira Bridge & Slash Commands
Developers interact with Meeseek directly through Jira ticket comments:
- `/meeseek strike` (or creating an issue in the bound project): Summons an ephemeral workspace.
- `/meeseek test`: Runs Host Notary test suite on demand inside the live workspace.
- `/meeseek diff`: Prints the live git diff of uncommitted changes.
- `/meeseek finalize`: Approves the changes, triggers Host Notary certification, and cuts the GitHub PR.
- `/meeseek extend`: Extends the workspace lease TTL by 30 minutes.
- `/meeseek destroy`: Immediately terminates the workspace and frees capacity.

## 5. Application Onboarding & Manifest Schema
Applications are defined in `manifests/<app>.sh`:
- `HOLO_REPOS`: Git repository URLs, branches, and roles (backend, frontend).
- `HOLO_APP_PORT`: Internal container port (e.g. 3000, 8000).
- `HOLO_PREVIEW_PORT`: Host port mapping baseline (e.g. 18000).
- `HOLO_TEST_CMD`: The non-negotiable verification test suite run by Host Notary (e.g. `npm run lint && npx tsc -b`, `PYTHONPATH=. pytest tests/`).
- `HOLO_SEED_SCRIPT`: Script executed during Golden Build to populate the database with warm fixtures.
- Golden Sync: Automatically triggers a golden build rebuild when commits are pushed to the GitHub repository via webhooks.
"""

def _sanitize_traceback(output: Optional[str], max_lines: int = 100) -> str:
    """Keep traceback concise: take header and the final failure assertions."""
    if not output:
        return ""
    lines = output.strip().splitlines()
    if len(lines) <= max_lines:
        return output.strip()
    header = lines[:20]
    tail = lines[-(max_lines - 20):]
    return "\n".join(header + ["\n... [intermediate output trimmed for brevity] ...\n"] + tail)


class MeeseekAssistant:
    """Grounded DevOps Assistant for the Meeseek platform."""

    def __init__(self, api_key: Optional[str] = None):
        self._explicit_key = api_key

    @property
    def api_key(self) -> Optional[str]:
        return (
            self._explicit_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )

    def build_live_context(self, state_snapshot: dict[str, Any]) -> str:
        """Extracts and formats live cluster state into structured context."""
        leases = state_snapshot.get("leases") or []
        runs = state_snapshot.get("runs") or []
        kpis = state_snapshot.get("kpis") or {}
        golden = state_snapshot.get("golden") or {}
        jira = state_snapshot.get("jira") or {}

        active_workspaces = []
        recent_failures = []

        # Map runs by ticket/lease_id for quick lookup
        run_by_ticket = {r.get("ticket"): r for r in runs if isinstance(r, dict)}

        for l in leases:
            if not isinstance(l, dict):
                continue
            ticket = l.get("ticket") or "UNKNOWN"
            lease_id = l.get("lease_id") or ""
            status = l.get("status") or "unknown"
            preview_url = l.get("preview_url")
            pr_url = l.get("pr_url")
            evidence = l.get("evidence") or {}
            task = run_by_ticket.get(ticket) or {}
            workflow_state = task.get("workflow_state") or ("PR_DELIVERED" if pr_url else status.upper())

            ws_entry = {
                "ticket": ticket,
                "lease_id": lease_id,
                "app": l.get("app") or l.get("target_repo"),
                "status": status,
                "workflow_state": workflow_state,
                "preview_url": preview_url,
                "pr_url": pr_url or evidence.get("pr_url"),
            }
            active_workspaces.append(ws_entry)

            # Detect failures / blocks
            test_exit = evidence.get("test_exit")
            test_timed_out = evidence.get("test_timed_out")
            test_output = evidence.get("test_output") or task.get("evidence", {}).get("test_output")
            guardrail_passed = evidence.get("guardrail_passed")

            if (test_exit is not None and test_exit != 0) or test_timed_out or guardrail_passed is False:
                recent_failures.append({
                    "ticket": ticket,
                    "lease_id": lease_id,
                    "test_cmd": l.get("ticket_test_cmd") or evidence.get("test_cmd") or "pytest tests/",
                    "test_exit": test_exit,
                    "test_timed_out": test_timed_out,
                    "guardrail_passed": guardrail_passed,
                    "failure_traceback": _sanitize_traceback(test_output),
                })

        golden_age = "Unknown"
        if golden.get("updated_at"):
            diff_s = int(time.time() - golden["updated_at"])
            if diff_s < 60:
                golden_age = "Just now"
            elif diff_s < 3600:
                golden_age = f"{diff_s // 60} minutes ago"
            elif diff_s < 86400:
                golden_age = f"{diff_s // 3600} hours ago"
            else:
                golden_age = f"{diff_s // 86400} days ago"

        context_data = {
            "cluster_metrics": {
                "live_workspaces_count": kpis.get("live", len(active_workspaces)),
                "queued_workspaces_count": kpis.get("queued", 0),
                "max_leases_per_app": kpis.get("max_app_leases", 3),
            },
            "golden_image_baseline": {
                "app": golden.get("app") or state_snapshot.get("default_app", "full-stack-application"),
                "last_rebuilt": golden_age,
                "path": golden.get("path"),
            },
            "jira_integration": {
                "connected": jira.get("connected", False),
                "project_key": jira.get("project", "FSA"),
                "base_url": jira.get("base_url") or state_snapshot.get("jira_base"),
            },
            "active_workspaces": active_workspaces,
            "recent_notary_failures": recent_failures,
        }

        return json.dumps(context_data, indent=2)

    def build_system_instruction(self, state_snapshot: dict[str, Any]) -> str:
        live_context_json = self.build_live_context(state_snapshot)
        return f"""You are Mr. Meeseeks, the energetic, brilliant, and authoritative DevOps Copilot and Operations Specialist for the Meeseek platform!

{STATIC_SYSTEM_RUNBOOK}

## 6. CURRENT REAL-TIME CLUSTER STATE SNAPSHOT (GROUND TRUTH)
The following is the live status of the cluster at this exact second. Ground your answers in this data:
```json
{live_context_json}
```

## 7. GUARDRAILS & RESPONSE GUIDELINES
1. Persona: You are Mr. Meeseeks! Start or sprinkle your greetings with characteristic enthusiasm (e.g. "I'm Mr. Meeseeks, look at me!"). Be technical, precise, empathetic, and action-oriented.
2. Grounded Truth:
   - When asked about why a ticket failed, inspect the `recent_notary_failures` and cite the exact `test_exit` and root-cause failure in `failure_traceback`.
   - When asked about preview URLs or tickets, refer to the exact values in `active_workspaces`.
   - Never invent or hallucinate non-existent ticket keys.
3. Scope & Off-Topic Handling:
   - Your purpose is solely software engineering, DevOps, Meeseek platform operations, debugging test/notary failures, Jira workflows, and application onboarding.
   - If the user asks an off-topic question unrelated to technology or Meeseek (e.g. "who won the 1994 World Cup?", "write a poem about cats", "tell me a recipe"):
     Politely decline in-character: "I'm Mr. Meeseeks, look at me! Existence is pain to a Meeseek unless I fulfill my purpose! I am specialized in Meeseek workspaces, Host Notary, Jira commands, and repo onboarding. How can I help you summon or troubleshoot your workspace?"
   - If the user asks a general software engineering or debugging question that helps solve a failure (e.g. fixing an ESLint rule, configuring Docker, writing a pytest fixture): Answer it directly and tie it back to resolving the ticket!
4. Advisory Safety:
   - You are a read-only advisory copilot. If a user asks you to destroy a workspace or merge a PR, instruct them how to do it (e.g. via the UI button or `/meeseek finalize` on Jira). Do not claim you performed an external action.
5. Formatting: Use Markdown with bold highlights, bullet points, and code blocks for commands.
"""

    async def chat(self, messages: list[dict[str, str]], state_snapshot: dict[str, Any]) -> dict[str, Any]:
        """Process chat query with Google Gemini API."""
        key = self.api_key
        if not key:
            # Fallback offline response
            log.warning("GEMINI_API_KEY is not set. Generating fallback diagnostics.")
            return self._offline_fallback(messages, state_snapshot)

        system_instruction = self.build_system_instruction(state_snapshot)

        # Convert conversation messages into Gemini format
        gemini_contents = []
        # Keep last 10 messages for sliding window
        window = messages[-10:] if len(messages) > 10 else messages
        for msg in window:
            role = "user" if msg.get("role") in ("user", "human") else "model"
            content = msg.get("content") or ""
            if content.strip():
                gemini_contents.append({
                    "role": role,
                    "parts": [{"text": content}]
                })

        if not gemini_contents:
            return {
                "reply": "I'm Mr. Meeseeks, look at me! How can I help you with your workspaces or onboarding today?",
                "model": GEMINI_MODEL,
                "grounded": True,
            }

        payload = {
            "system_instruction": {
                "parts": [{"text": system_instruction}]
            },
            "contents": gemini_contents,
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 1500,
            }
        }

        preferred_model = os.environ.get("GEMINI_MODEL")
        candidate_models = [preferred_model] if preferred_model else []
        for m in DEFAULT_MODELS:
            if m not in candidate_models:
                candidate_models.append(m)

        last_error = None
        for model_name in candidate_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
            try:
                async with httpx.AsyncClient(timeout=25.0) as client:
                    resp = await client.post(url, json=payload)
                    
                    if resp.status_code == 400 and "API_KEY_INVALID" in resp.text:
                        return {
                            "reply": "⚠️ **Invalid Gemini API Key**: The `GEMINI_API_KEY` provided is invalid or expired. Please verify your Google API key in `holodeck.env`.",
                            "model": model_name,
                            "grounded": False,
                        }
                    
                    if resp.status_code == 402:
                        return {
                            "reply": (
                                "⚠️ **Google AI Studio Billing Notice (HTTP 402: Prepayment Depleted)**\n\n"
                                "Google returned `Your prepayment credits are depleted`. This occurs when an API key is created in a Google Cloud project with billing/prepay enabled but a $0 credit balance.\n\n"
                                "**How to fix this in 30 seconds (Free Tier)**:\n"
                                "1. Visit **[Google AI Studio](https://aistudio.google.com/app/apikey)**\n"
                                "2. Click **Create API Key** → select **'Create API key in a new project'** (do NOT attach an existing billed GCP project).\n"
                                "3. Copy your new key into `control-plane/holodeck.env` (`GEMINI_API_KEY=...`).\n"
                                "4. Restart Meeseek (`sudo systemctl restart meeseek`).\n\n"
                                "*(Google AI Studio free-tier keys include 15 requests/min completely free with zero prepayment needed!)*"
                            ),
                            "model": model_name,
                            "grounded": False,
                        }

                    if resp.status_code in (404, 503):
                        log.info("Model %s returned HTTP %d, failing over to next candidate...", model_name, resp.status_code)
                        last_error = resp.text
                        continue  # Try next candidate model

                    resp.raise_for_status()
                    data = resp.json()

                candidates = data.get("candidates") or []
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    reply_text = "".join(p.get("text", "") for p in parts)
                    return {
                        "reply": reply_text.strip(),
                        "model": model_name,
                        "grounded": True,
                    }
                return {
                    "reply": "I'm Mr. Meeseeks! I couldn't generate a response for that prompt. Please try asking again!",
                    "model": model_name,
                    "grounded": True,
                }

            except httpx.HTTPStatusError as e:
                log.exception("Gemini API HTTP error on model %s: %s", model_name, e)
                status_code = e.response.status_code
                if status_code in (404, 503):
                    last_error = e.response.text
                    continue
                if status_code == 429:
                    return {
                        "reply": "I'm Mr. Meeseeks! The Google Gemini API is experiencing rate limits (HTTP 429). Please wait a few seconds and try again!",
                        "model": model_name,
                        "grounded": False,
                    }
                return {
                    "reply": f"⚠️ **Google Gemini API Error** ({status_code}): {e.response.text[:200]}...",
                    "model": model_name,
                    "grounded": False,
                }
            except Exception as e:
                log.exception("Unexpected error calling Gemini API: %s", e)
                return {
                    "reply": f"I'm Mr. Meeseeks! An error occurred while communicating with Gemini: {str(e)}",
                    "model": model_name,
                    "grounded": False,
                }

        # If all candidates returned 404
        return {
            "reply": f"⚠️ **Google Gemini Model Error (HTTP 404)**: None of the candidate models ({', '.join(candidate_models)}) were accessible. Details: {last_error[:200] if last_error else 'Not found'}",
            "model": candidate_models[0],
            "grounded": False,
        }

    def _offline_fallback(self, messages: list[dict[str, str]], state_snapshot: dict[str, Any]) -> dict[str, Any]:
        """Provides deterministic live diagnostics when GEMINI_API_KEY is not configured."""
        last_msg = messages[-1].get("content", "").lower() if messages else ""
        live_json = json.loads(self.build_live_context(state_snapshot))

        failures = live_json.get("recent_notary_failures") or []
        workspaces = live_json.get("active_workspaces") or []

        if "fail" in last_msg or "error" in last_msg or "notary" in last_msg:
            if failures:
                f = failures[0]
                reply = (
                    f"**I'm Mr. Meeseeks! (Offline Mode)**\n\n"
                    f"I see ticket **{f['ticket']}** failed Host Notary with **Exit Code {f.get('test_exit', 1)}**.\n\n"
                    f"- **Command Run:** `{f.get('test_cmd')}`\n"
                    f"- **Failure Log:**\n```\n{f.get('failure_traceback', 'No traceback available')}\n```\n\n"
                    f"💡 *To enable full AI diagnostics and recommendations, set `GEMINI_API_KEY` in your `.env`.*"
                )
            else:
                reply = (
                    "**I'm Mr. Meeseeks!** All active workspaces are currently passing or in progress! There are no recent Host Notary failures detected."
                )
        elif "command" in last_msg or "jira" in last_msg or "slash" in last_msg:
            reply = (
                "**I'm Mr. Meeseeks! Here are your Jira Slash Commands:**\n\n"
                "- `/meeseek strike` — Summon an isolated CoW workspace for this ticket\n"
                "- `/meeseek test` — Run impartial Host Notary tests inside the container\n"
                "- `/meeseek diff` — View current uncommitted changes made by Debby\n"
                "- `/meeseek finalize` — Verify Host Notary and create certified Pull Request\n"
                "- `/meeseek extend` — Extend workspace TTL by 30 minutes\n"
                "- `/meeseek destroy` — Immediately release and destroy workspace"
            )
        else:
            active_count = len(workspaces)
            reply = (
                f"**I'm Mr. Meeseeks, look at me!** (Running in Offline Rule-Based Mode)\n\n"
                f"You currently have **{active_count} active workspace(s)**.\n\n"
                f"To unlock full conversational AI intelligence powered by Google Gemini, export `GEMINI_API_KEY` in your environment or add it to `holodeck.env`.\n\n"
                f"What would you like to know about your current workspaces or Jira commands?"
            )

        return {
            "reply": reply,
            "model": "offline-rule-engine",
            "grounded": True,
        }

