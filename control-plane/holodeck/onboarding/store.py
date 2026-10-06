"""SQLite-backed store for onboarding requests — same one-file-DB pattern as
LeaseStore (holodeck/store.py). The nested bits (repos, manifest fields, trial
output) are still evolving, so they live in one JSON payload column rather than
a rigid schema, the same tradeoff store.py makes for handle_json/evidence_json.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Optional

from holodeck.db import Db
from holodeck.onboarding.models import (ManifestField, OnboardingRequest,
                                        OnboardingStatus, RepoSpec)


def _to_row(r: OnboardingRequest) -> tuple:
    payload = {
        "contact": r.contact,
        "repos": [asdict(x) for x in r.repos],
        "manifest": {k: asdict(v) for k, v in r.manifest.items()},
        "trial_log": r.trial_log,
        "trial_error": r.trial_error,
        "reject_reason": r.reject_reason,
        "jira_project": r.jira_project,
        "test_cmd": r.test_cmd,
        "preview_port": r.preview_port,
        "created_at": r.created_at,
    }
    return (r.request_id, r.team_slug, r.app_name, r.status.value,
            json.dumps(payload), r.created_at, r.updated_at)


def _from_row(row) -> OnboardingRequest:
    payload = json.loads(row["payload_json"])
    raw_repos = payload.get("repos", [])
    repos: list[RepoSpec] = []
    for x in raw_repos:
        repos.append(RepoSpec(
            name=x.get("name", ""),
            url=x.get("url", ""),
            branch=x.get("branch", "main"),
            role=x.get("role", "app"),
            test_cmd=x.get("test_cmd"),
            depends_on=x.get("depends_on", []),
            via=x.get("via"),
            env_var=x.get("env_var"),
        ))

    return OnboardingRequest(
        request_id=row["request_id"], team_slug=row["team_slug"], app_name=row["app_name"],
        contact=payload.get("contact", ""),
        repos=repos,
        status=OnboardingStatus(row["status"]),
        manifest={k: ManifestField(**v) for k, v in payload.get("manifest", {}).items()},
        trial_log=payload.get("trial_log", ""), trial_error=payload.get("trial_error"),
        reject_reason=payload.get("reject_reason"),
        jira_project=payload.get("jira_project"),
        test_cmd=payload.get("test_cmd"),
        preview_port=payload.get("preview_port"),
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


_UPSERT = """
INSERT INTO onboarding_requests
  (request_id, team_slug, app_name, status, payload_json, created_at, updated_at)
VALUES (?,?,?,?,?,?,?)
ON CONFLICT(request_id) DO UPDATE SET
  team_slug=excluded.team_slug, app_name=excluded.app_name, status=excluded.status,
  payload_json=excluded.payload_json, updated_at=excluded.updated_at
"""


class OnboardingStore:
    def __init__(self, db: Optional[Db] = None) -> None:
        self.db = db or Db()

    def put(self, req: OnboardingRequest) -> None:
        req.updated_at = time.time()
        self.db.execute(_UPSERT, _to_row(req))

    def get(self, request_id: str) -> Optional[OnboardingRequest]:
        rows = self.db.query("SELECT * FROM onboarding_requests WHERE request_id=?", (request_id,))
        return _from_row(rows[0]) if rows else None

    def list_for_team(self, team_slug: str) -> list[OnboardingRequest]:
        rows = self.db.query(
            "SELECT * FROM onboarding_requests WHERE team_slug=? ORDER BY created_at DESC",
            (team_slug,))
        return [_from_row(r) for r in rows]

    def list_by_status(self, status: OnboardingStatus) -> list[OnboardingRequest]:
        rows = self.db.query(
            "SELECT * FROM onboarding_requests WHERE status=? ORDER BY created_at",
            (status.value,))
        return [_from_row(r) for r in rows]

    def list_all(self) -> list[OnboardingRequest]:
        rows = self.db.query(
            "SELECT * FROM onboarding_requests ORDER BY created_at DESC"
        )
        return [_from_row(r) for r in rows]

    def delete(self, request_id: str) -> bool:
        self.db.execute("DELETE FROM onboarding_requests WHERE request_id=?", (request_id,))
        return True

    def count_active_leases_for_app(self, app_name: str) -> int:
        try:
            rows = self.db.query(
                "SELECT count(*) as cnt FROM leases WHERE app=? AND status IN ('ready', 'striking', 'allocated')",
                (app_name,)
            )
            return int(rows[0]["cnt"]) if rows else 0
        except Exception:
            return 0
