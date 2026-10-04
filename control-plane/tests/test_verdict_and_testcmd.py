"""Two demo-hardening fixes:
  - parse_verdict tolerates natural punctuation ("yes, go ahead" -> approve)
  - HOLO_TEST_CMD is resolved per-app from the manifest (via the provider) at
    acquire, captured on the lease, and run by the notary at finalize (D1)."""

from __future__ import annotations

import pytest

from holodeck.console.manager import parse_verdict


@pytest.mark.parametrize("text,expected", [
    ("yes", True), ("approve", True), ("ship it", True), ("👍", True),
    ("yes, go ahead", True),          # the bug: trailing comma on the first word
    ("Yes! do the thing.", True),
    ('"approve".', True),
    ("no", False), ("nope.", False), ("no, not yet", False), ("decline!", False),
    ("looks good", None),             # genuine freeform guidance stays None
    ("also handle the empty list", None),
    ("", None),
])
def test_parse_verdict(text, expected):
    assert parse_verdict(text) is expected


# ---- D1: manifest HOLO_TEST_CMD -> lease -> notary evidence ----

def test_test_cmd_resolved_from_provider_and_captured(client, provider):
    provider.manifest_test_cmd = "pytest tests/test_architecture.py -q"
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    ev = client.post("/leases/cpl-1/finalize").json()
    assert ev["test_cmd"] == "pytest tests/test_architecture.py -q"
    assert ev["test_exit"] == 0            # FakeProvider runs it green when set


def test_test_cmd_absent_when_manifest_unset(client, provider):
    assert provider.manifest_test_cmd is None   # default
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-2"})
    ev = client.post("/leases/cpl-2/finalize").json()
    assert ev["test_cmd"] is None


def test_repo_specific_test_cmd_resolved(client, provider):
    provider.composite_repos["full-stack-application"] = {"test_backend", "test_frontend"}
    provider.manifest_test_cmd_by_repo = {
        "test_backend": "ruff check . && pytest tests/",
        "test_frontend": "npm run lint && tsc -b",
    }
    # Strike backend
    r_be = client.post("/leases", json={
        "app": "full-stack-application", "ticket": "FSA-10", "target_repo": "test_backend"
    })
    assert r_be.status_code == 201
    ev_be = client.post("/leases/fsa-10/finalize").json()
    assert ev_be["test_cmd"] == "ruff check . && pytest tests/"

    # Strike frontend
    r_fe = client.post("/leases", json={
        "app": "full-stack-application", "ticket": "FSA-11", "target_repo": "test_frontend"
    })
    assert r_fe.status_code == 201
    ev_fe = client.post("/leases/fsa-11/finalize").json()
    assert ev_fe["test_cmd"] == "npm run lint && tsc -b"


def test_base_overrides_captured_and_validated(client, provider):
    provider.composite_repos["full-stack-application"] = {"test_backend", "test_frontend"}
    # Valid base_overrides
    r = client.post("/leases", json={
        "app": "full-stack-application",
        "ticket": "FSA-12",
        "target_repo": "test_frontend",
        "base_overrides": {"test_backend": "agent/fsa-10"},
    })
    assert r.status_code == 201
    lease = r.json()
    assert lease["base_overrides"] == {"test_backend": "agent/fsa-10"}

    # Invalid repo in base_overrides -> 422
    r_bad = client.post("/leases", json={
        "app": "full-stack-application",
        "ticket": "FSA-13",
        "target_repo": "test_frontend",
        "base_overrides": {"nonexistent_repo": "agent/fsa-10"},
    })
    assert r_bad.status_code == 422


def test_jira_bridge_base_overrides_parsing():
    from holodeck.console.bridge import JiraBridge
    description = (
        "Add UI analytics dashboard widget.\n"
        "Repo: test_frontend\n"
        "Base: test_backend@agent/fsa-10\n"
    )
    overrides = JiraBridge._extract_base_overrides(description)
    assert overrides == {"test_backend": "agent/fsa-10"}

    # Depends-On alias
    desc_alias = (
        "Fix navbar layout.\n"
        "Depends-On: test_backend@feat/v2-auth\n"
    )
    overrides_alias = JiraBridge._extract_base_overrides(desc_alias)
    assert overrides_alias == {"test_backend": "feat/v2-auth"}
