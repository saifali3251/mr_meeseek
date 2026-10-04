"""Recon Agent (static-analysis half) — docs/AUTOMATIC_ONBOARDING.md §3 step 2.

Drafts a manifest by reading a repo's compose file, the same fields a human
fills in by hand per manifests/README.md's contract table. Every field is
tagged with where it came from (`ManifestField.source`) so the review screen
can show "inferred from compose.yaml" vs "needs your input" — nothing here is
silently trusted, and destructive fields (HOLO_MIGRATE_CMD/HOLO_SEED_CMD)
NEVER leave this module with a source other than "needs_review", regardless
of how confident a guess is (see models.DESTRUCTIVE_FIELDS and
OnboardingRequest.has_unreviewed_destructive_fields).

`clone_repo` shells out to `git` for real repo URLs; it also accepts a plain
local path (or `file://` URL) unchanged, which is how this gets exercised
without network access — the same "point it at a local checkout" pattern the
rest of Holodeck already uses (manifests/main.sh's `HOLO_SRC`).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import yaml

from holodeck.onboarding.models import DESTRUCTIVE_FIELDS, ManifestField, RepoSpec

_COMPOSE_FILENAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
_DB_IMAGE_HINTS = ("postgres", "postgresql")
_READINESS_PATTERN = re.compile(r"""["'](/(?:readyz|healthz|health_check|readiness|health))["']""")
_SEED_MAKE_TARGET = re.compile(r"^([A-Za-z][\w.-]*seed[\w.-]*|[A-Za-z][\w.-]*migrat[\w.-]*)\s*:", re.MULTILINE | re.IGNORECASE)
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}


class ReconError(RuntimeError):
    """Recon couldn't get far enough to draft anything useful (e.g. no compose
    file, clone failed) — surfaced to the team on the intake screen, not a 500."""


def clone_repo(spec: RepoSpec, dest: Path) -> Path:
    """Fetch `spec` into `dest` and return the checkout root.

    A local path (or file:// URL) is used as-is — no network needed — which is
    how this is exercised in tests/demos without reaching a real git host.
    """
    url = spec.url
    if url.startswith("file://"):
        url = url[len("file://"):]
    local = Path(url)
    if local.exists():
        return local
    if shutil.which("git") is None:
        raise ReconError("git is not available to clone repos")
    dest.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", spec.branch, spec.url, str(dest)],
            check=True, capture_output=True, text=True, timeout=120,
        )
    except subprocess.CalledProcessError as e:
        raise ReconError(f"git clone failed for {spec.url}: {e.stderr.strip()[:300]}") from e
    except subprocess.TimeoutExpired as e:
        raise ReconError(f"git clone timed out for {spec.url}") from e
    return dest


def _find_compose_file(repo_root: Path) -> Optional[Path]:
    for name in _COMPOSE_FILENAMES:
        p = repo_root / name
        if p.is_file():
            return p
    return None


def _parse_port(mapping) -> Optional[int]:
    """A compose port entry is "8080:80", "80", or the long form {published,target}.
    Return the CONTAINER-side port (what HOLO_APP_PORT wants)."""
    if isinstance(mapping, dict):
        return mapping.get("target")
    s = str(mapping)
    parts = s.split(":")
    try:
        return int(parts[-1].split("/")[0])
    except ValueError:
        return None


def _volume_mode(volumes: list, db_service: str) -> tuple[str, str]:
    """named-volume vs bind-mount for the db service's data dir — the one fact
    that decides whether golden-build must relocate pgdata (manifests/README.md's
    HOLO_PGDATA_MODE). Best-effort: looks at the FIRST volume entry only."""
    if not volumes:
        return "unknown", "needs_review"
    entry = volumes[0]
    source = entry.get("source", "") if isinstance(entry, dict) else str(entry).split(":")[0]
    if source.startswith(".") or source.startswith("/"):
        return "bind-mount", "inferred:compose"
    return "named-volume", "inferred:compose"


def _grep_readiness_path(repo_root: Path) -> Optional[str]:
    """Best-effort: the first health/readiness-looking route literal found in
    source files. A miss is common and fine — it just leaves the field for the
    team to fill in; nothing downstream trusts this without review."""
    for path in repo_root.rglob("*"):
        if path.is_dir() or any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in (".py", ".js", ".ts", ".go", ".rb"):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        m = _READINESS_PATTERN.search(text)
        if m:
            return m.group(1)
    return None


def _guess_seed_migrate_target(repo_root: Path) -> Optional[str]:
    makefile = repo_root / "Makefile"
    if not makefile.is_file():
        return None
    m = _SEED_MAKE_TARGET.search(makefile.read_text(errors="ignore"))
    return f"make {m.group(1)}" if m else None


def draft_manifest(repo_root: Path, primary: RepoSpec) -> dict[str, ManifestField]:
    """Draft every manifests/README.md field recon can attempt for `primary`'s
    checkout at `repo_root`. Raises ReconError if there's no compose file to
    read at all — that's the one thing every other field depends on."""
    compose_path = _find_compose_file(repo_root)
    if compose_path is None:
        raise ReconError(
            f"no {'/'.join(_COMPOSE_FILENAMES)} found at the repo root — "
            "recon needs a Compose file to draft anything")
    doc = yaml.safe_load(compose_path.read_text()) or {}
    services: dict = doc.get("services", {}) or {}
    top_volumes = set((doc.get("volumes") or {}).keys())

    def inferred(value, note: str = "compose") -> ManifestField:
        return ManifestField(value=str(value) if value is not None else None,
                             source=f"inferred:{note}", confidence="medium")

    def needs_review(value=None) -> ManifestField:
        return ManifestField(value=value, source="needs_review", confidence="low")

    fields: dict[str, ManifestField] = {}
    fields["HOLO_COMPOSE_FILE"] = inferred(compose_path.name)

    port_services = [name for name, svc in services.items() if (svc or {}).get("ports")]
    fields["HOLO_PORT_SERVICES"] = inferred(" ".join(sorted(port_services)) or None)

    # app service: prefer the one matching the repo's own name, else the first
    # service that builds from source (not a pulled image) and publishes a port.
    app_name = None
    if primary.name in services:
        app_name = primary.name
    else:
        for name, svc in services.items():
            if (svc or {}).get("build") and (svc or {}).get("ports"):
                app_name = name
                break
    if app_name:
        fields["HOLO_APP_SERVICE"] = inferred(app_name)
        ports = services[app_name].get("ports") or []
        container_port = _parse_port(ports[0]) if ports else None
        fields["HOLO_APP_PORT"] = (inferred(container_port) if container_port
                                   else needs_review())
    else:
        fields["HOLO_APP_SERVICE"] = needs_review()
        fields["HOLO_APP_PORT"] = needs_review()

    # db service: first one whose image looks like Postgres.
    db_name, db_svc = None, None
    for name, svc in services.items():
        image = str((svc or {}).get("image", "")).lower()
        if any(hint in image for hint in _DB_IMAGE_HINTS):
            db_name, db_svc = name, svc
            break
    if db_name:
        env = db_svc.get("environment") or {}
        if isinstance(env, list):  # compose allows "KEY=VALUE" list form too
            env = dict(kv.split("=", 1) for kv in env if "=" in kv)
        fields["HOLO_PG_SERVICE"] = inferred(db_name)
        fields["HOLO_PG_USER"] = inferred(env.get("POSTGRES_USER", "postgres"))
        fields["HOLO_PG_DB"] = (inferred(env["POSTGRES_DB"]) if env.get("POSTGRES_DB")
                                else needs_review(db_name))
        mode, source = _volume_mode(db_svc.get("volumes") or [], db_name)
        fields["HOLO_PGDATA_MODE"] = ManifestField(value=mode, source=source,
                                                   confidence="medium" if source != "needs_review" else "low")
    else:
        for name in ("HOLO_PG_SERVICE", "HOLO_PG_USER", "HOLO_PG_DB", "HOLO_PGDATA_MODE"):
            fields[name] = needs_review()
    # relocating pgdata into the checkout (pgdata override) is too
    # bespoke to auto-generate — always left for a human.
    fields["HOLO_PGDATA_OVERRIDE"] = needs_review()

    # minimal boot set: app + db (+ anything the app declares depends_on).
    minimal = {n for n in (app_name, db_name) if n}
    if app_name and app_name in services:
        minimal |= set((services[app_name].get("depends_on") or []))
    fields["WS_SERVICES"] = inferred(" ".join(sorted(minimal)) or None,
                                     note="compose (review — may include more than needed)")

    readiness = _grep_readiness_path(repo_root)
    fields["HOLO_READINESS_PATH"] = inferred(readiness, note="source scan") if readiness else needs_review()
    fields["HOLO_READINESS_SCHEME"] = ManifestField(value="http", source="default", confidence="low")

    # no static signal reliably says WHICH query proves seeded data — always human.
    fields["HOLO_SEED_PROOF_SQL"] = needs_review()

    # destructive fields: recon may propose a guess as a starting point, but the
    # source is ALWAYS "needs_review" — never auto-trusted (models.DESTRUCTIVE_FIELDS).
    guess = _guess_seed_migrate_target(repo_root)
    for name in DESTRUCTIVE_FIELDS:
        fields[name] = needs_review(guess)

    fields["HOLO_BUILD_CMD"] = (inferred(f"docker compose build {app_name}", note="default")
                                if app_name else needs_review())
    fields["HOLO_TOKEN_CMD"] = needs_review()  # auth/registry token refresh: never guessable

    return fields
