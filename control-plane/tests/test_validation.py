"""Issue #1 + #7: input validation and id collisions at the HTTP boundary."""

from __future__ import annotations

import pytest

from holodeck.models import holo_id


@pytest.mark.parametrize("ticket,expected", [
    ("FSA-101", "fsa-101"),
    ("CPL-1", "cpl-1"),
    ("CPL/1", "cpl-1"),      # slash -> dash, same id as CPL-1 (collision, issue #7)
    ("A B", "a-b"),
    ("x!!y", "xy"),          # non-alnum stripped
])
def test_holo_id_matches_shell(ticket, expected):
    assert holo_id(ticket) == expected


def test_unknown_app_rejected_before_shellout(client):
    r = client.post("/leases", json={"app": "../../etc/passwd", "ticket": "CPL-1"})
    # path-traversal shape fails the regex validator -> 422, never reaches a script.
    assert r.status_code == 422


def test_app_not_in_allowlist_rejected(client):
    r = client.post("/leases", json={"app": "notanapp", "ticket": "CPL-1"})
    assert r.status_code == 422
    assert "unknown app" in r.json()["detail"]


def test_demo_service_app_is_auto_discovered(client):
    """The manifest is picked up by the on-disk allowlist (manifests/*.sh)
    with no API change: a lease for app=demo-service is accepted, not 422'd."""
    r = client.post("/leases", json={"app": "demo-service", "ticket": "CPL-1"})
    assert r.status_code == 201, r.text
    assert r.json()["app"] == "demo-service"
    assert r.json()["compose_project"] == "ws-cpl-1"


@pytest.mark.parametrize("ticket", ["x; rm -rf /opt/holo", "a b c", "../evil", ""])
def test_shell_metachar_tickets_rejected(client, ticket):
    r = client.post("/leases", json={"app": "full-stack-application", "ticket": ticket})
    assert r.status_code == 422


def test_valid_acquire_succeeds(client):
    r = client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["lease_id"] == "cpl-1"
    assert body["status"] == "ready"
    assert body["compose_project"] == "ws-cpl-1"
    assert body["preview_port"] is not None


def test_ticket_collision_is_409(client):
    assert client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"}).status_code == 201
    # 'Cpl-1' is a DISTINCT valid ticket that collapses to the same id 'cpl-1'
    # -> clean 409, not a 500 with shell output. (A '/' ticket is rejected 422
    #  earlier by the regex, so case-folding is the collision that reaches here.)
    r = client.post("/leases", json={"app": "full-stack-application", "ticket": "Cpl-1"})
    assert r.status_code == 409
    assert "cpl-1" in r.json()["detail"]


def test_finalize_has_no_command_field(client):
    """confirmation #2: an agent cannot supply a test command. Even if a body is
    posted, it is ignored — the endpoint takes no command input."""
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    r = client.post("/leases/cpl-1/finalize", json={"test_cmd": "rm -rf /"})
    assert r.status_code == 200
    # the fake echoes the resolved (env-derived) cmd, which is None here — not our injection.
    assert r.json()["test_cmd"] != "rm -rf /"
