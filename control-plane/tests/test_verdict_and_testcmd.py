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
    client.post("/leases", json={"app": "compliance", "ticket": "CPL-1"})
    ev = client.post("/leases/cpl-1/finalize").json()
    assert ev["test_cmd"] == "pytest tests/test_architecture.py -q"
    assert ev["test_exit"] == 0            # FakeProvider runs it green when set


def test_test_cmd_absent_when_manifest_unset(client, provider):
    assert provider.manifest_test_cmd is None   # default
    client.post("/leases", json={"app": "compliance", "ticket": "CPL-2"})
    ev = client.post("/leases/cpl-2/finalize").json()
    assert ev["test_cmd"] is None
