"""Track E — operator console: the B11 read endpoints on the lease API, plus the
/ops UI and its browser-facing actions. All against the FakeProvider (conftest)."""

from __future__ import annotations


# ---- B11 read endpoints (authed lease API) ----

def test_list_leases_excludes_released_by_default(client):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-2"})
    client.delete("/leases/cpl-2")
    ids = [l["lease_id"] for l in client.get("/leases").json()["leases"]]
    assert "cpl-1" in ids and "cpl-2" not in ids
    with_rel = [l["lease_id"] for l in client.get("/leases?include_released=true").json()["leases"]]
    assert "cpl-2" in with_rel


def test_list_leases_newest_first(client):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-2"})
    ids = [l["lease_id"] for l in client.get("/leases").json()["leases"]]
    assert ids[0] == "cpl-2"  # most recently created first


def test_environments_lists_apps_with_readiness(client):
    body = client.get("/environments").json()
    assert body["provider"] == "fake"
    apps = {e["app"]: e for e in body["environments"]}
    assert {"full-stack-application", "demo-service", "sample-app"} <= set(apps)
    assert apps["full-stack-application"]["ready"] is True          # fake prepare() is clean
    assert apps["full-stack-application"]["problems"] == []


def test_environments_counts_active_leases(client):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    apps = {e["app"]: e for e in client.get("/environments").json()["environments"]}
    assert apps["full-stack-application"]["active_leases"] == 1
    assert apps["sample-app"]["active_leases"] == 0


def test_evidence_404_before_finalize_then_bundle(client):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    assert client.get("/leases/cpl-1/evidence").status_code == 404   # not finalized yet
    client.post("/leases/cpl-1/finalize")
    ev = client.get("/leases/cpl-1/evidence")
    assert ev.status_code == 200
    assert ev.json()["seed_rows"] == 42


def test_evidence_unknown_lease_404(client):
    assert client.get("/leases/nope/evidence").status_code == 404


# ---- the /ops UI ----

def test_ops_page_is_html(client):
    r = client.get("/ops")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Operator Console" in r.text


def test_root_redirects_to_ops(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/ops"


def test_ops_state_snapshot(client):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    s = client.get("/ops/state").json()
    assert s["provider"] == "fake"
    assert s["kpis"]["live"] == 1
    assert s["kpis"]["environments_ready"] == s["kpis"]["environments_total"]
    assert any(l["lease_id"] == "cpl-1" for l in s["leases"])
    assert {"full-stack-application", "sample-app"} <= {e["app"] for e in s["environments"]}


def test_ops_state_exposes_jira_base_for_ticket_links(client, cfg):
    # the page turns a ticket into a Jira issue link from this base (empty by default)
    assert client.get("/ops/state").json()["jira_base"] == ""
    cfg.jira_base_url = "https://acme.atlassian.net"
    assert client.get("/ops/state").json()["jira_base"] == "https://acme.atlassian.net"


def test_ops_state_exposes_static_preview_url(client, cfg):
    # empty by default (page falls back to the per-lease loopback link)
    assert client.get("/ops/state").json()["preview_url"] == ""
    cfg.preview_url = "https://preview.aibuildercup.io"
    assert client.get("/ops/state").json()["preview_url"] == \
        "https://preview.aibuildercup.io"


# ---- ops actions (no-auth, in-process) ----

def test_ops_state_shows_seeded_rows_without_finalize(client):
    # the seeded-row count is captured at strike and surfaced on the workspace row,
    # so the console shows live status (seeded rows + golden) with no manual finalize.
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    row = next(l for l in client.get("/ops/state").json()["leases"] if l["lease_id"] == "cpl-1")
    assert row["seed_rows"] == 42
    assert row["golden_head"] == "deadbeef"


def test_ops_strike_creates_lease(client):
    r = client.post("/ops/strike", json={"app": "full-stack-application", "ticket": "CPL-42"})
    assert r.status_code == 200, r.text
    assert r.json()["lease_id"] == "cpl-42"
    assert any(l["lease_id"] == "cpl-42" for l in client.get("/ops/state").json()["leases"])


def test_ops_strike_defaults_app(client):
    r = client.post("/ops/strike", json={"ticket": "CPL-7"})   # no app -> cfg.default_app
    assert r.status_code == 200
    assert r.json()["app"] == "full-stack-application"


def test_ops_strike_rejects_unknown_app(client):
    assert client.post("/ops/strike", json={"app": "nope", "ticket": "X"}).status_code == 422


def test_ops_strike_rejects_bad_ticket(client):
    assert client.post("/ops/strike", json={"app": "full-stack-application", "ticket": "bad ticket!"}).status_code == 422


def test_ops_strike_conflict(client):
    client.post("/ops/strike", json={"app": "full-stack-application", "ticket": "CPL-1"})
    assert client.post("/ops/strike", json={"app": "full-stack-application", "ticket": "CPL-1"}).status_code == 409


def test_ops_finalize_shows_evidence_in_state(client):
    client.post("/ops/strike", json={"app": "full-stack-application", "ticket": "CPL-1"})
    assert client.post("/ops/leases/cpl-1/finalize").status_code == 200
    row = next(l for l in client.get("/ops/state").json()["leases"] if l["lease_id"] == "cpl-1")
    assert row["evidence"]["seed_rows"] == 42


def test_ops_extend_and_release(client):
    client.post("/ops/strike", json={"app": "full-stack-application", "ticket": "CPL-1"})
    assert client.post("/ops/leases/cpl-1/extend", json={"ttl_s": 999}).status_code == 200
    assert client.delete("/ops/leases/cpl-1").status_code == 200
    assert not any(l["lease_id"] == "cpl-1" for l in client.get("/ops/state").json()["leases"])


def test_ops_finalize_unknown_lease_404(client):
    assert client.post("/ops/leases/nope/finalize").status_code == 404


# ---- environment allowlist + display alias (one bundle shown as `demo-alias`) ----

def _client_with(manifests_dir, allow, aliases, default_app=None):
    from fastapi.testclient import TestClient

    from holodeck.api import create_app
    from holodeck.config import Config
    from holodeck.providers.fake import FakeProvider
    from holodeck.service import LeaseService
    from holodeck.store import LeaseStore, PortPool

    c = Config(holo_dir=manifests_dir, provider="fake", token="")
    c.port_probe = False
    c.provision_async = False
    c.apps_allow = set(allow)
    c.app_aliases = dict(aliases)
    if default_app is not None:
        c.default_app = default_app
    store = LeaseStore(PortPool(18000, 18002, probe=False))
    return TestClient(create_app(c, LeaseService(c, store, FakeProvider())))


def test_allowlist_and_alias_show_single_env(manifests_dir):
    c = _client_with(manifests_dir, {"demo-service"}, {"demo-service": "demo-alias"})
    s = c.get("/ops/state").json()
    assert [e["app"] for e in s["environments"]] == ["demo-alias"]
    assert s["apps"] == ["demo-alias"]
    assert "demo-service" not in s["apps"] and "sample-app" not in s["apps"]
    assert s["kpis"]["environments_total"] == 1


def test_strike_via_alias_maps_to_real_manifest(manifests_dir):
    c = _client_with(manifests_dir, {"demo-service"}, {"demo-service": "demo-alias"}, default_app="demo-alias")
    # operator strikes the displayed name 'demo-alias' -> real manifest demo-service
    r = c.post("/ops/strike", json={"app": "demo-alias", "ticket": "CPL-1"})
    assert r.status_code == 200, r.text
    assert r.json()["app"] == "demo-alias"          # lease shown under the alias
    row = next(l for l in c.get("/ops/state").json()["leases"] if l["lease_id"] == "cpl-1")
    assert row["app"] == "demo-alias"
    # a no-app strike falls back to default_app ('demo-alias') and still resolves
    assert c.post("/ops/strike", json={"ticket": "CPL-9"}).status_code == 200
    # the hidden per-repo manifests are not strikeable
    assert c.post("/ops/strike", json={"app": "sample-app", "ticket": "CPL-2"}).status_code == 422


# ---- E2: finalize -> PR (guarded) ----

def test_finalize_opens_pr_when_enabled(client, service):
    service.cfg.pr_enabled = True   # off by default; opening a real PR is outward-facing
    r = client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    port = r.json()["preview_port"]
    ev = client.post("/leases/cpl-1/finalize").json()
    assert ev["pr_url"] and "pull" in ev["pr_url"]
    pr = service.provider.last_pr
    # PR body is a Jira-linked description + live sandbox, not an evidence dump
    # (that moved to the console dashboard) — still marked as Holodeck-opened
    assert "🤖 Opened by Holodeck" in pr["body"]
    assert pr["branch"] == "agent/cpl-1"
    # the reviewer-facing extras: the live sandbox link + the holodeck_preview label
    assert port == 18000
    assert ("https://admin.workspace-one.aibuildercup.io/login" in pr["body"]
            or "18000" in pr["body"])
    assert pr["label"] == "holodeck_preview"


def test_pr_body_uses_real_diff_stat_not_raw_newline_count():
    from holodeck.models import diff_stat
    diff = (
        "diff --git a/x.py b/x.py\n"
        "index abc..def 100644\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,2 +1,3 @@\n"
        " unchanged line\n"
        "+added line one\n"
        "+added line two\n"
        "-removed line\n"
    )
    # 9 raw lines here, but only 2 additions + 1 removal are real content — a
    # raw newline count (the old behavior) would report "9 lines" next to
    # GitHub's own "+2 -1", which is what confused a reviewer in practice.
    assert diff_stat(diff) == "+2 -1"


def test_finalize_pr_body_links_the_jira_ticket_when_configured(client, service):
    service.cfg.pr_enabled = True
    service.cfg.jira_base_url = "https://acme.atlassian.net"
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    client.post("/leases/cpl-1/finalize")
    pr = service.provider.last_pr
    assert "[CPL-1](https://acme.atlassian.net/browse/CPL-1)" in pr["body"]


def test_finalize_pr_label_is_overridable(client, service):
    service.cfg.pr_enabled = True
    service.cfg.pr_label = "custom_label"
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    client.post("/leases/cpl-1/finalize")
    assert service.provider.last_pr["label"] == "custom_label"


def test_finalize_survives_pr_step_failure(client, service):
    # a missing `gh` (or any PR failure) must NOT 500 finalize — evidence still stamps.
    service.cfg.pr_enabled = True
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})

    def _boom(*a, **k):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'gh'")
    service.provider.open_pr = _boom

    r = client.post("/leases/cpl-1/finalize")
    assert r.status_code == 200                 # not a 500
    assert r.json()["pr_url"] is None           # PR failed; evidence still returned
    assert r.json()["seed_rows"] == 42


def test_finalize_no_pr_when_disabled(client):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    ev = client.post("/leases/cpl-1/finalize").json()
    assert ev["pr_url"] is None


def test_pr_url_surfaces_in_ops_state(client, service):
    service.cfg.pr_enabled = True
    client.post("/ops/strike", json={"app": "full-stack-application", "ticket": "CPL-1"})
    client.post("/ops/leases/cpl-1/finalize")
    row = next(l for l in client.get("/ops/state").json()["leases"] if l["lease_id"] == "cpl-1")
    assert "pull" in row["evidence"]["pr_url"]
