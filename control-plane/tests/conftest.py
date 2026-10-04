"""Shared fixtures: a fully-wired app around the FakeProvider (no Docker)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from holodeck.api import create_app
from holodeck.config import Config
from holodeck.providers.fake import FakeProvider
from holodeck.reaper import Reaper
from holodeck.service import LeaseService
from holodeck.store import LeaseStore, PortPool


@pytest.fixture
def manifests_dir(tmp_path: Path) -> Path:
    """A fake HOLO_DIR with manifests/{full-stack-application,demo-service,sample-app}.sh so the
    on-disk allowlist resolves to {'full-stack-application','demo-service','sample-app'}."""
    m = tmp_path / "manifests"
    m.mkdir()
    (m / "full-stack-application.sh").write_text("HOLO_APP=full-stack-application\n")
    (m / "demo-service.sh").write_text("HOLO_APP=demo-service\n")
    (m / "sample-app.sh").write_text("HOLO_APP=sample-app\n")
    (m / "README.md").write_text("# manifests\n")
    (tmp_path / "scripts").mkdir()
    return tmp_path


@pytest.fixture
def cfg(manifests_dir: Path) -> Config:
    c = Config(holo_dir=manifests_dir, provider="fake", token="")
    c.port_probe = False
    c.provision_async = False  # synchronous strike in tests -> deterministic acquire()
    c.max_leases = 3
    c.port_pool_start = 18000
    c.port_pool_end = 18002
    c.default_ttl_s = 1800
    return c


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def service(cfg: Config, provider: FakeProvider) -> LeaseService:
    # probe=False: unit tests must not depend on which host ports are free.
    store = LeaseStore(PortPool(cfg.port_pool_start, cfg.port_pool_end, probe=False))
    return LeaseService(cfg, store, provider)


@pytest.fixture
def reaper(service: LeaseService, cfg: Config) -> Reaper:
    return Reaper(service, cfg.reaper_interval_s)


@pytest.fixture
def client(cfg: Config, service: LeaseService) -> TestClient:
    return TestClient(create_app(cfg, service))
