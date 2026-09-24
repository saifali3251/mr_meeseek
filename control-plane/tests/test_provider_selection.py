"""confirmation #4: provider switch fails fast on unknown; auth (#2)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from holodeck.api import create_app
from holodeck.config import Config
from holodeck.factory import build_provider
from holodeck.service import LeaseService
from holodeck.store import LeaseStore, PortPool


def test_unknown_provider_fails_fast(manifests_dir):
    cfg = Config(holo_dir=manifests_dir, provider="bogus")
    with pytest.raises(SystemExit):
        build_provider(cfg)


def test_eks_reserved(manifests_dir):
    cfg = Config(holo_dir=manifests_dir, provider="eks")
    with pytest.raises(SystemExit):
        build_provider(cfg)


def test_compose_and_fake_build(manifests_dir):
    assert build_provider(Config(holo_dir=manifests_dir, provider="fake")).name == "fake"
    assert build_provider(Config(holo_dir=manifests_dir, provider="compose")).name == "compose"


def _client_with_token(manifests_dir, provider, token):
    cfg = Config(holo_dir=manifests_dir, provider="fake", token=token)
    svc = LeaseService(cfg, LeaseStore(PortPool(18000, 18010)), provider)
    return TestClient(create_app(cfg, svc))


def test_auth_required_when_token_set(manifests_dir, provider):
    c = _client_with_token(manifests_dir, provider, "s3cret")
    assert c.post("/leases", json={"app": "compliance", "ticket": "CPL-1"}).status_code == 401
    ok = c.post("/leases", json={"app": "compliance", "ticket": "CPL-1"},
                headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 201


def test_healthz_open_without_token(manifests_dir, provider):
    c = _client_with_token(manifests_dir, provider, "s3cret")
    assert c.get("/healthz").status_code == 200  # health is unauthenticated
