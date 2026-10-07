"""Git repository connectivity and branch existence validator for onboarding.

Verifies remote repository reachability, branch HEAD SHA, and authentication
without performing an expensive full clone.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

log = logging.getLogger("holodeck.onboarding.git_validator")


def _sanitize_output(text: str, token: Optional[str] = None) -> str:
    if not text:
        return ""
    if token:
        text = text.replace(token, "[REDACTED]")
    # Strip any basic-auth passwords if in output
    text = re.sub(r"://([^:@\s]+):([^@\s]+)@", "://[REDACTED]@", text)
    return text.strip()


def validate_git_repo(
    url: str,
    branch: str = "main",
    token: Optional[str] = None,
    timeout_s: int = 15,
) -> dict:
    """Validates that `url` exists, is reachable, and contains `branch`."""
    url = url.strip()
    branch = branch.strip() or "main"
    auth_token = token or os.environ.get("GITHUB_TOKEN", "") or os.environ.get("HOLODECK_GITHUB_PAT", "")

    # Local path handling
    raw_path = url[len("file://"):] if url.startswith("file://") else url
    local_p = Path(raw_path)
    if local_p.is_dir():
        if not shutil.which("git"):
            return {"valid": False, "error": "git executable not found on host"}
        res = subprocess.run(
            ["git", "-C", str(local_p), "rev-parse", "--verify", branch],
            capture_output=True, text=True, timeout=timeout_s,
        )
        if res.returncode == 0:
            sha = res.stdout.strip()
            return {
                "valid": True,
                "commit_sha": sha[:8],
                "full_sha": sha,
                "branch": branch,
                "message": f"Local repository branch '{branch}' verified ({sha[:8]})",
            }
        return {"valid": False, "error": f"Branch '{branch}' not found in local repo"}

    if not shutil.which("git"):
        return {"valid": False, "error": "git binary not found on host"}

    target_url = url
    if auth_token and ("github.com" in url or "gitlab.com" in url):
        parsed = urlparse(url)
        if not parsed.username and not parsed.password:
            # Inject token credentials safely
            target_url = urlunparse((
                parsed.scheme,
                f"x-access-token:{auth_token}@{parsed.netloc}",
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            ))

    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "echo",
    }

    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--heads", target_url, branch],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"valid": False, "error": f"Connection to repository timed out after {timeout_s}s"}
    except Exception as e:
        log.exception("git ls-remote failed unexpectedly")
        return {"valid": False, "error": _sanitize_output(str(e), auth_token)}

    if proc.returncode != 0:
        err = _sanitize_output(proc.stderr or proc.stdout, auth_token)
        return {
            "valid": False,
            "error": err or f"Repository cannot be reached (exit code {proc.returncode})",
        }

    lines = proc.stdout.strip().splitlines()
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[1].endswith(f"/{branch}"):
            sha = parts[0].strip()
            return {
                "valid": True,
                "commit_sha": sha[:8],
                "full_sha": sha,
                "branch": branch,
                "message": f"Repository and branch '{branch}' reachable (HEAD: {sha[:8]})",
            }

    # If ls-remote succeeded but branch ref was not in output
    return {
        "valid": False,
        "error": f"Branch '{branch}' does not exist on remote repository",
    }

