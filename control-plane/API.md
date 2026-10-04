# Lease API — endpoint reference

Base: `http://127.0.0.1:8099` · Live spec: `/docs` (FastAPI OpenAPI)
Auth: `authorization: Bearer $HOLODECK_TOKEN` on everything except `/healthz`.
Auth is **disabled when `HOLODECK_TOKEN` is empty** (logged loudly at startup).

**Prerequisite, not an endpoint:** the golden image is built by
`scripts/golden-build.<app>.sh` (nightly = **G1** in production). No endpoint creates it.
Every call below fails until `$HOLO_GOLDEN` exists.

---

## `GET /healthz`

Liveness + which substrate is active. **Runs no script.**

```json
{"status":"ok","provider":"compose","active_leases":0,"max_leases":50}
```

- No auth — deliberately, so a load balancer can probe it.
- `provider` proves compose-vs-eks without changing the client.

---

## `POST /leases` — acquire

Runs **`strike.sh <ticket> --preview <port>`** with `HOLO_APP=<app>` injected.

```json
// request
{"app":"full-stack-application","ticket":"FSA-101","preview":18081,"ttl_s":1800}
// 201
{"lease_id":"fsa-101","status":"ready","preview_port":18000,
 "compose_project":"ws-fsa-101","ws_dir":"/opt/holo/holodeck-data/ws/fsa-101",
 "golden_head":"224e36c0…","expires_at":1785846612.7}
```

`preview` and `ttl_s` are optional (port auto-allocated from 18000–18999).

| Code | Cause |
|---|---|
| **422** | `app` not a `manifests/*.sh` basename, or `ticket` fails `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` |
| **409** | `ticket`→`lease_id` already leased (`FSA-101` and `FSA/101` both → `fsa-101`) |
| **503** | `max_leases` reached |
| **502** | `strike.sh` failed — partial workspace is rolled back so the ticket stays reusable |

**Edge cases**
- Port allocation **probes the OS**, not just its own bookkeeping — so it skips ports held by
  the POC stack, another dev, or orphans from a previous run.
- A pinned `preview` that's already bound → 502 from `strike.sh` (`strike.sh` does no port check).
- ~14s local, ~29s on EC2. Client timeouts must exceed that.

---

## `GET /leases/{id}` — status

**Runs no script.** Pure read of the in-memory store: status, handle, port, `golden_head`,
`expires_at`. Use it to re-read a handle you lost.

- **404** if unknown — including *after an API restart*, because the store is in-memory. The
  workspace may still be running; startup reconcile logs it as an orphan.

---

## `GET /leases` — list (B11)

**Runs no script.** All leases newest-first; `?include_released=true` includes torn-down ones
(excluded by default so the list is "what's live now"). Feeds the Console.

## `GET /environments` — goldens + status (B11)

**Runs no script.** Per app (discovered from `manifests/*.sh`): `ready` (golden built + substrate
preflight clean), `problems`, and `active_leases`. No token — substrate-level, like `/readyz`.

## `GET /leases/{id}/evidence` — read the notary bundle (B11)

The stored `finalize` output, re-read. **404** if the lease was never finalized — `POST /finalize`
is what *derives* it; this only reads it back.

---

## Operator Console (Track E) — `GET /ops`

The single-screen **environment + workspace + evidence** UI.
Distinct from the agent run/task board at `/console`: a harness owns runs, this console owns
environments and proof.

- `GET /` → redirects to `/ops`.
- `GET /ops/state` — one JSON snapshot (`provider`, `kpis`, `environments`, `leases` with inline
  evidence) the page polls every 4s.
- `POST /ops/strike` `{ticket, app?, preview?, ttl_s?}` — lease a fresh env (the raw ops primitive;
  full agent runs live on `/console`).
- `POST /ops/leases/{id}/finalize` · `POST …/extend` · `DELETE /ops/leases/{id}` — the row actions.

Browser-facing `/ops*` routes call the service **in-process** (no shared token in the page),
matching the `/console` board's loopback posture. The tokened equivalents are the lease-API
endpoints above.

---

## `POST /leases/{id}/finalize` — the notary

**No request body.** There is no field for a test command, so an agent cannot supply one —
an absent field is a property, not a rule someone relaxes.

Re-derives every claim host-side; reads nothing the agent wrote:

| Field | How it's obtained |
|---|---|
| `readiness`, `readiness_ok` | `curl 127.0.0.1:<port><HOLO_READINESS_PATH>` |
| `seed_rows` | `psql -tAc "<HOLO_SEED_PROOF_SQL>"` in the workspace's own db |
| `test_cmd`, `test_exit`, `test_output` | runs `HOLO_TEST_CMD` (manifest) — **captured at acquire time** |
| `diff` | `git diff <golden_head>..HEAD` — total change vs the struck snapshot |
| `golden_head`, `schema_rev` | golden's git HEAD + `alembic current` (freshness, closes V12) |
| `services_booted` / `services_absent` | `docker compose ps` vs `WS_SERVICES` (scope of the claim) |
| `pr_url` | draft PR opened by finalize when `HOLODECK_PR=1` and there's a diff (else `null`) |

**Edge cases**
- `test_cmd: null` when no manifest sets `HOLO_TEST_CMD` — everything else still stamps.
- Test exceeding `HOLODECK_FINALIZE_TIMEOUT_S` (600s) → `test_timed_out: true`, no raise.
- **Idempotent**: re-calling re-derives and overwrites. Safe after a fix.
- `readiness_ok: false` if the lease has no `preview_port`.
- **PR (E2):** with `HOLODECK_PR=1`, finalize also pushes `agent/<id>` and opens a **draft PR**
  (`gh`) whose body is the evidence (incl. the **preview-env URL**), returning `pr_url`. The PR is
  tagged `HOLODECK_PR_LABEL` (default **`holodeck_preview`**) for reviewer triage — attached
  best-effort, so a missing/failed label never fails PR creation. **OFF by default** (outward-facing).
  A PR failure never fails finalize — the evidence still stamps. Idempotent: re-finalize returns the
  existing PR (and re-applies the label). Config: `HOLODECK_PR_BASE` (default `master`),
  `HOLODECK_PR_DRAFT`, `HOLODECK_PR_TITLE_PREFIX`, `HOLODECK_PR_LABEL`.
- **404** unknown lease · **502** provider failure.

---

## `POST /leases/{id}/extend`

`{"ttl_s": 3600}` → new `expires_at`. Review-without-persistence: same ephemeral workspace,
preview URL still live, still reaped later.

- **404** unknown. No upper bound on `ttl_s` — a caller can effectively pin a workspace.

---

## `DELETE /leases/{id}` — release

Runs **`destroy.sh <ticket>`**: compose down, delete the clone, free the port.

- Idempotent — releasing twice returns `released`.
- On Linux logs `retrying with sudo` (pgdata is uid-999/mode-700). **Expected, not an error.**
- The **TTL reaper** calls this automatically on expiry, so a hung agent can't leak a workspace.
- **404** unknown · **502** `destroy.sh` failed (workspace may still exist — check manually).

---

## `POST` / `GET /leases/{id}/exec` — **501, deferred**

Same-host by design (D3): the agent uses the returned handle directly.

```bash
docker compose -p ws-cpl-1 exec webserver <cmd>
```

`POST` returns 501 **with that command in the message**. `GET` reserves the WebSocket PTY path
(B3). Contract published now, implemented post-demo.

---

## Operational gotchas

- **Single worker only.** The store is process-local; `--workers 2` = two stores, double-allocated
  ports, half-reaped leases. `main.py` hard-codes `workers=1`.
- **Restart loses all leases.** Workspaces survive; startup reconcile lists them as orphans and
  reserves their ports. `HOLODECK_RECONCILE=reap` tears them down instead.
- **Manifest changes need a restart.** `_manifest(app)` is cached per process, and `HOLO_TEST_CMD`
  is captured onto the lease at acquire time — so in-flight leases keep the old test.
- **Loopback-only by default.** The process can reach `sudo rm -rf`; non-loopback binding warns
  and needs `HOLODECK_TOKEN` + a firewall.
- **Swagger + auth:** the token is a plain header, so `/docs` shows a per-request header field
  rather than a global *Authorize* button. Either paste it per request, or leave `HOLODECK_TOKEN`
  unset while testing over a loopback SSM tunnel.

## Known gaps

| Gap | Task |
|---|---|
| ~~`finalize` does not create a draft PR~~ → **done (E2)**: `HOLODECK_PR=1` pushes the branch + opens a draft PR with the evidence | — |
| ~~`HOLO_TEST_CMD` unset~~ → **done (D1)**: test suite runs at finalize; resolved **per-app from the manifest** via `provider.test_cmd(app)`, captured on the lease at acquire | — |
| `WS_SERVICES` is one fixed list per app — no per-lease service profile | future (`HOLO_SERVICE_PROFILES`) |
| HTTP/WS `exec` unimplemented | **B3** |
| No lease persistence (in-memory) | **C2** |
