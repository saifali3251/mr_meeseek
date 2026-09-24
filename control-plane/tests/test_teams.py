"""TeamStore — Option A "team token/link" (docs/AUTOMATIC_ONBOARDING.md)."""

from __future__ import annotations

import pytest

from holodeck.teams import TeamExistsError, TeamStore


@pytest.fixture
def teams() -> TeamStore:
    return TeamStore()  # private in-memory Db()


def test_create_slugifies_name(teams):
    t = teams.create("Acme Team!", "eng@acme.com")
    assert t.slug == "acme-team"
    assert t.name == "Acme Team!"
    assert len(t.token) > 20


def test_create_rejects_duplicate_slug(teams):
    teams.create("Acme", "a@acme.com", slug="acme")
    with pytest.raises(TeamExistsError):
        teams.create("Acme Again", "b@acme.com", slug="acme")


def test_create_rejects_bad_explicit_slug(teams):
    with pytest.raises(ValueError):
        teams.create("Acme", "a@acme.com", slug="Not Valid!")


def test_get_by_token_requires_both_slug_and_token(teams):
    t = teams.create("Acme", "a@acme.com")
    assert teams.get_by_token(t.slug, t.token).slug == t.slug
    assert teams.get_by_token(t.slug, "wrong-token") is None
    assert teams.get_by_token("nope", t.token) is None
    assert teams.get_by_token(t.slug, "") is None


def test_all_lists_teams_oldest_first(teams):
    a = teams.create("A", "a@x.com")
    b = teams.create("B", "b@x.com")
    assert [t.slug for t in teams.all()] == [a.slug, b.slug]


def test_app_ownership_round_trip(teams):
    t = teams.create("Acme", "a@acme.com")
    assert teams.team_for_app("my-app") is None
    assert teams.apps_for_team(t.slug) == set()
    teams.assign_app("my-app", t.slug)
    assert teams.team_for_app("my-app") == t.slug
    assert teams.apps_for_team(t.slug) == {"my-app"}


def test_assign_app_reassignment_overwrites(teams):
    a = teams.create("A", "a@x.com")
    b = teams.create("B", "b@x.com")
    teams.assign_app("shared-app", a.slug)
    teams.assign_app("shared-app", b.slug)
    assert teams.team_for_app("shared-app") == b.slug
    assert teams.apps_for_team(a.slug) == set()
    assert teams.apps_for_team(b.slug) == {"shared-app"}
