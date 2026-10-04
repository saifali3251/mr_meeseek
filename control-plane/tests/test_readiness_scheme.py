"""ComposeProvider.finalize readiness is scheme-aware (TLS entrypoints).

The frontend or gateway may be fronted by HTTPS with a self-signed cert. finalize must
poll it with `curl -fsS -k https://...`, not plain HTTP — otherwise the notary reports a
false "not ready" for a healthy stack.

These tests stub _manifest and _run so no Docker/scripts are needed; they assert the
exact curl argv finalize builds for each scheme.
"""

from __future__ import annotations

import subprocess
import types
from pathlib import Path

from holodeck.config import Config
from holodeck.models import WorkspaceHandle
from holodeck.providers.compose import ComposeProvider


def _provider(tmp_path: Path) -> ComposeProvider:
    (tmp_path / "scripts").mkdir(exist_ok=True)
    return ComposeProvider(Config(holo_dir=tmp_path, provider="compose", token=""))


def _handle() -> WorkspaceHandle:
    return WorkspaceHandle(
        lease_id="cpl-1", app="test-tls-app", ticket="CPL-1", preview_port=8989,
        compose_project="ws-cpl-1", ws_dir="/tmp/ws/cpl-1", golden_head=None,
    )


def _base_manifest(scheme: str) -> dict:
    return {
        "HOLO_COMPOSE_FILE": "compose.yaml",
        "HOLO_PGDATA_OVERRIDE": "",
        "HOLO_APP_SERVICE": "gateway",
        "HOLO_PG_SERVICE": "db", "HOLO_PG_USER": "postgres", "HOLO_PG_DB": "test_db",
        "HOLO_READINESS_PATH": "/readyz", "HOLO_READINESS_SCHEME": scheme,
        "HOLO_SEED_PROOF_SQL": "SELECT count(*) FROM arena;",
        "WS_SERVICES": "gateway",
    }


def _capture_readiness_argv(provider: ComposeProvider, scheme: str) -> list[str]:
    """Run finalize with stubs and return the argv of the readiness curl call."""
    provider._manifest = types.MethodType(lambda self, app: _base_manifest(scheme), provider)
    captured: dict[str, list[str]] = {}

    def fake_run(self, argv, cwd, env, timeout):
        # the readiness call is the only curl to a scheme://127.0.0.1 URL
        if argv and argv[0] == "curl" and any("127.0.0.1:8989" in a for a in argv):
            captured["readiness"] = argv
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")  # non-zero: skip parsing

    provider._run = types.MethodType(fake_run, provider)
    provider.finalize(_handle(), test_cmd=None)
    return captured.get("readiness", [])


def test_https_scheme_uses_k_and_https_url(tmp_path):
    argv = _capture_readiness_argv(_provider(tmp_path), "https")
    assert argv[:3] == ["curl", "-fsS", "-k"]
    assert argv[-1] == "https://127.0.0.1:8989/readyz"


def test_http_scheme_has_no_k_and_http_url(tmp_path):
    argv = _capture_readiness_argv(_provider(tmp_path), "http")
    assert "-k" not in argv
    assert argv[-1] == "http://127.0.0.1:8989/readyz"
