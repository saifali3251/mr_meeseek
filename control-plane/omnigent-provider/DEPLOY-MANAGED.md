# Adding the `holodeck` sandbox provider to a managed Omnigent server

**What this enables.** Omnigent sessions on the managed server can request a
`holodeck` sandbox — a warm, seeded compliance-backend stack — instead of a
blank box. The server calls this provider, which provisions the environment via
the Holodeck control plane (the lease API).

**Compatibility.** Built and verified against **omnigent 0.8.2**. It plugs in via
the standard `omnigent.sandbox_providers` entry-point group and lives under the
required `omnigent.community.sandbox` namespace — no core changes. Confirm the
server runs a compatible 0.8.x.

**Prerequisites (Holodeck-side — handled separately; listed so the boundary is clear).**
- The Holodeck control plane is reachable from the server (`HOLODECK_URL`).
- Provisioned workspaces have network egress back to this Omnigent server (for the
  `omnigent host` dial-back).
- The compliance golden exists and the workspace runs an `omnigent host` sidecar
  (model A) so `start_host` completes.
- The agent's model + GitHub credentials are injected at runtime.

---

## Step 1 — install the provider into the server's Python environment

Package: `omnigent-community-sandbox-holodeck` (repo path `control-plane/omnigent-provider/`).
It declares **no dependencies** — it uses the server's own `omnigent`.

Install from the repo subdirectory at the merged ref:

```bash
pip install --no-deps \
  "git+https://github.com/junipersquare/holodeck.git@<MERGED_REF>#subdirectory=control-plane/omnigent-provider"
```

Bake this into the server image build, however the deployment is managed.

> Alternatives on request: a CodeArtifact-published wheel (`pip install omnigent-community-sandbox-holodeck`),
> or a prebuilt `.whl` handed over directly.

### ⚠️ Do NOT hardcode the wheel version in the deploy (k8s init-container pattern)

The managed k8s deploy ships the wheel via a Secret/ConfigMap mounted at `/wheels`
and installs it from an init container. **Do not pin the exact filename** — a
version bump then silently deploys the old wheel until someone remembers to edit
the manifest (we hit exactly this: `…-0.1.0-…whl` stayed pinned while a newer
wheel sat unused). Instead:

- **Glob the install** so any version drops in with no manifest edit:
  ```sh
  pip install --no-deps --target /opt/holodeck /wheels/omnigent_community_sandbox_holodeck-*.whl
  ```
- **Ship exactly ONE wheel** in the Secret/ConfigMap (key = the versioned filename,
  e.g. `omnigent_community_sandbox_holodeck-0.1.4-py3-none-any.whl`). If the old
  key lingers, the glob installs both and the older can win.
- **The wheel BYTES must be the new build** — pip installs by the wheel's metadata,
  so renaming a file doesn't change the version; replace the actual base64/binary
  in the Secret source, then confirm `ls -d /opt/holodeck/*.dist-info` shows the
  expected version after rollout.

### ⚠️ Also install `psycopg2-binary` alongside the wheel

`sitecustomize.py`'s ticket-passthrough patch (`_install_holodeck_ticket_passthrough`)
opens its own `SqlAlchemyConversationStore(DATABASE_URL)` to resolve a managed
session's real ticket from `labels.holodeck_ticket`, so `provision()` uses it
instead of Omnigent's generated `managed-<uuid8>` host name. That needs a
Postgres driver — which the wheel deliberately does NOT declare as a dependency
(the install above is `--no-deps`, by design, since it otherwise "uses the
server's own omnigent"). **Confirmed missing on a real deployment**: the pod's
`/opt/venv` has no `psycopg2`, so every managed launch silently falls back to
the generated name — `except Exception: ticket = None`, no error surfaced
anywhere — meaning ticket<->lease correlation was completely broken (comment
relay, finalize, everything keyed by ticket) with nothing in the logs pointing
at why until logging was added. Install it into the same target:

```sh
pip install --no-deps --target /opt/holodeck /wheels/omnigent_community_sandbox_holodeck-*.whl
pip install --target /opt/holodeck psycopg2-binary
```

(Not `--no-deps` for this one — `psycopg2-binary` has no further deps to pull in
anyway, but there's no reason to risk it.) Confirm after rollout: `python -c
"from omnigent.stores.conversation_store.sqlalchemy_store import
SqlAlchemyConversationStore"` should succeed with `/opt/holodeck` on
`PYTHONPATH`, not raise `ModuleNotFoundError: psycopg2`.

## Step 2 — enable it in the server config + set its runtime config

1. Select the provider under the server's `sandbox:` config block (see Omnigent's
   `docs/extending/sandbox_providers.md` → "Server-managed sandboxes"):

   ```yaml
   sandbox:
     provider: holodeck
   ```

2. Provide the provider's runtime config as **environment variables** on the server
   process (the provider reads these):

   ```
   HOLODECK_URL=https://<holodeck-control-plane-host>   # the lease API base URL
   HOLODECK_TOKEN=<shared token>                        # control-plane auth token
   HOLODECK_APP=compliance                              # app / golden to strike
   ```

   (URL + token values provided by the Holodeck team.)

## Step 3 — verify discovery

After install + restart, the registry should list `holodeck` with no load errors:

```bash
python -c "from omnigent.onboarding.sandboxes import registry; \
print(registry.available_providers()); print(registry.plugin_state().load_errors)"
# expect '...holodeck...' in the list, and load_errors == {}
```

Discovery only proves the provider *loads* — it makes `omnigent sandbox create
--provider holodeck` work. **Managed sessions need one more step (below).**

## Step 4 — wire managed-launch (REQUIRED for `host_type="managed"`)

Server-managed sessions (`POST /v1/sessions` with `host_type:"managed"`) are how
the Holodeck bridge drives Omnigent. But the server's `sandbox:` YAML only accepts
a **built-in allowlist** of providers (`modal|daytona|boxlite|cwsandbox|islo|e2b|
openshell|kubernetes`) — `holodeck` is a *community* provider and is **not** in it,
so `sandbox: provider: holodeck` alone will NOT serve a managed session.

The supported path for an out-of-tree provider (documented in
`omnigent/server/managed_hosts.py`) is to construct a `ManagedSandboxConfig` with
our launcher and hand it to `create_app(..., sandbox_config=...)`. The server
builds that config via `parse_sandbox_config(cfg["sandbox"])`, which is imported
*lazily inside* the `server` command — so we can inject holodeck by monkeypatching
that function at process start, with **no edits to installed site-packages**
(upgrade-safe). That launcher is shipped here as **`holodeck_server.py`**.

**Run the server via the wrapper instead of `omnigent`** (same args):

```bash
# config.yaml:
#   sandbox:
#     provider: holodeck
#     server_url: https://<this-omnigent-server-public-url>   # workspace dials back here

HOLODECK_URL=http://<control-plane-host>:8099 \
HOLODECK_APP=compliance \
HOLODECK_TOKEN=<optional; required if the control plane is network-exposed> \
python /path/to/holodeck_server.py server --config /etc/omnigent/config.yaml
```

`holodeck_server.py` patches `parse_sandbox_config` so `provider: holodeck` yields
`ManagedSandboxConfig(launcher_factory=lambda: HolodeckSandboxLauncher(), …)`, then
hands off to the normal CLI. All other providers pass through unchanged.

Notes / connectivity:
- `server_url` must be reachable **from the provisioned workspace** (the `omnigent
  host` dial-back) — the REVERSE of the `HOLODECK_URL` direction. Both legs must be open:
  server → control plane (`HOLODECK_URL`, provision) **and** workspace → server (`server_url`).
- The launcher reads `HOLODECK_URL` / `HOLODECK_APP` / `HOLODECK_TOKEN` from the
  server process env (Step 2) — no creds in the config file.
- Verify it took: `GET /v1/info` should report `managed_sandboxes_enabled: true` and
  `sandbox_provider: "holodeck"`.

Then a managed session should provision — the Holodeck control plane shows a
`POST /leases` when it works. Coordinate a smoke test with us.

## Rollback

```bash
pip uninstall omnigent-community-sandbox-holodeck    # then remove the sandbox.provider config
```

Additive and reversible — removing it just drops `holodeck` from the provider
list; nothing else changes.

---

## Values to fill before running

| Placeholder | Provided by | Notes |
|---|---|---|
| `<MERGED_REF>` | Holodeck team | commit/tag once this provider PR is merged |
| `HOLODECK_URL` | Holodeck team | control-plane base URL, reachable from the server |
| `HOLODECK_TOKEN` | Holodeck team | shared control-plane auth token |
