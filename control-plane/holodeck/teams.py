"""Team registry — Option A from docs/AUTOMATIC_ONBOARDING.md ("team token/link"):
a team is a slug + a shared token, not a real login. Visiting
`/ops?team=<slug>&token=<token>` once scopes that browser (via a cookie) to only
the apps that team owns; the plain `/ops` link with no team param stays the
platform-wide admin view, byte-identical to what it is today.

Deliberately a swappable seam, same shape as WorkspaceProvider: a real SSO/OIDC
resolver can replace how a Team is resolved later (PRODUCTION.md S3) without
touching anything downstream that just consumes a `Team`.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from holodeck.db import Db

if TYPE_CHECKING:
    from fastapi import Request, Response

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

# cookie names for the "visit the link once" flow: a team's share link carries
# ?team=&token= only on the first hit; after that the browser is scoped by
# these cookies, so subsequent bare /ops loads stay in that team's view.
COOKIE_TEAM = "holo_team"
COOKIE_TOKEN = "holo_token"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def slugify(name: str) -> str:
    """Best-effort slug from a display name ('Acme Team!' -> 'acme-team').
    Collisions are the caller's problem — TeamStore.create raises on a taken slug."""
    s = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")
    return s or "team"


def generate_token() -> str:
    return secrets.token_urlsafe(24)


@dataclass
class Team:
    slug: str
    name: str
    contact: str
    token: str
    created_at: float = field(default_factory=time.time)


class TeamExistsError(ValueError):
    """slug already taken — mapped to 409 at the route."""


class TeamStore:
    """SQLite-backed, same one-file-DB pattern as LeaseStore (holodeck/db.py):
    a private in-memory Db() by default (tests), the shared on-disk Db in
    production so team + app-ownership state survives restarts."""

    def __init__(self, db: Optional[Db] = None) -> None:
        self.db = db or Db()

    def create(self, name: str, contact: str, slug: Optional[str] = None) -> Team:
        slug = slug or slugify(name)
        if not SLUG_RE.match(slug):
            raise ValueError("slug must match ^[a-z0-9][a-z0-9-]{0,31}$")
        if self.get(slug) is not None:
            raise TeamExistsError(f"team '{slug}' already exists")
        team = Team(slug=slug, name=name, contact=contact, token=generate_token())
        self.db.execute(
            "INSERT INTO teams (slug, name, contact, token, created_at) VALUES (?,?,?,?,?)",
            (team.slug, team.name, team.contact, team.token, team.created_at),
        )
        return team

    def get(self, slug: str) -> Optional[Team]:
        rows = self.db.query("SELECT * FROM teams WHERE slug=?", (slug,))
        return self._row(rows[0]) if rows else None

    def get_by_token(self, slug: str, token: str) -> Optional[Team]:
        """Resolve AND authenticate in one call — never split into a separate
        lookup + compare, so a caller can't accidentally treat 'slug exists' as
        'token matched'. Constant-time compare: this token is a bearer secret."""
        team = self.get(slug)
        if team is None or not secrets.compare_digest(team.token, token or ""):
            return None
        return team

    def all(self) -> list[Team]:
        return [self._row(r) for r in self.db.query("SELECT * FROM teams ORDER BY created_at")]

    @staticmethod
    def _row(r) -> Team:
        return Team(slug=r["slug"], name=r["name"], contact=r["contact"],
                     token=r["token"], created_at=r["created_at"])

    # --- app ownership: manifest key -> the team that onboarded it ---

    def assign_app(self, app: str, team_slug: str) -> None:
        now = time.time()
        self.db.execute(
            "INSERT INTO app_teams (app, team_slug, created_at) VALUES (?,?,?) "
            "ON CONFLICT(app) DO UPDATE SET team_slug=excluded.team_slug",
            (app, team_slug, now),
        )

    def team_for_app(self, app: str) -> Optional[str]:
        rows = self.db.query("SELECT team_slug FROM app_teams WHERE app=?", (app,))
        return rows[0]["team_slug"] if rows else None

    def apps_for_team(self, team_slug: str) -> set[str]:
        rows = self.db.query("SELECT app FROM app_teams WHERE team_slug=?", (team_slug,))
        return {r["app"] for r in rows}

    def unassign_app(self, app: str) -> None:
        self.db.execute("DELETE FROM app_teams WHERE app=?", (app,))


def resolve_team(request: "Request", store: Optional[TeamStore]) -> Optional[Team]:
    """Query params win (the share link itself); the cookie is the fallback so
    a bare /ops load after the first visit stays scoped. Anything else —
    `store is None` (team-scoping not wired up), no slug/token given, or a bad
    token — resolves to None, the platform-wide admin view. This is a filter,
    never a hard-auth wall: an unresolved team just means "see everything",
    matching /ops's existing loopback-trust posture (ops_console.py's module
    docstring) rather than adding a new one."""
    if store is None:
        return None
    slug = request.query_params.get("team") or request.cookies.get(COOKIE_TEAM)
    token = request.query_params.get("token") or request.cookies.get(COOKIE_TOKEN)
    if not slug or not token:
        return None
    return store.get_by_token(slug, token)


def set_team_cookies(response: "Response", team: Team) -> None:
    response.set_cookie(COOKIE_TEAM, team.slug, max_age=_COOKIE_MAX_AGE,
                        httponly=True, samesite="lax")
    response.set_cookie(COOKIE_TOKEN, team.token, max_age=_COOKIE_MAX_AGE,
                        httponly=True, samesite="lax")
