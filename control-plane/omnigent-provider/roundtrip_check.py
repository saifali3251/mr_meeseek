"""Prove HolodeckSandboxLauncher drives the control plane through Omnigent's
REAL base classes. Run with the omnigent venv python (which has `omnigent`
installed) against a locally-running control plane:

    HOLODECK_URL=http://127.0.0.1:8099 \
      ~/.local/share/uv/tools/omnigent/bin/python roundtrip_check.py

Loads launcher.py by path so it uses the installed omnigent package (no
namespace shadowing, no install needed).
"""

from __future__ import annotations

import importlib.util
import pathlib
import tempfile

HERE = pathlib.Path(__file__).parent
LAUNCHER = HERE / "omnigent/community/sandbox/holodeck/launcher.py"

spec = importlib.util.spec_from_file_location("holodeck_launcher", LAUNCHER)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)  # imports omnigent.onboarding.sandboxes.base
HolodeckSandboxLauncher = mod.HolodeckSandboxLauncher

from omnigent.onboarding.sandboxes.base import ExecModelHostLauncher  # noqa: E402

launcher = HolodeckSandboxLauncher()
assert isinstance(launcher, ExecModelHostLauncher), "must subclass the real base"

print("provider          :", launcher.provider)
caps = launcher.capabilities
print("capabilities      :", {
    "cli_bootstrap": caps.cli_bootstrap,
    "managed_launch": caps.managed_launch,
    "file_copy": caps.file_copy,
    "programmatic_terminate": caps.programmatic_terminate,
    "streaming_exec": caps.streaming_exec,
    "local_port_forward": caps.local_port_forward,
    "resume_stopped": caps.resume_stopped,
})

print("prepare()         ...", end=" ")
launcher.prepare()
print("OK (substrate ready)")

# A TICKET, not a sandbox name — provision() derives the lease id (and hence the
# workspace dir / compose project / agent branch) from it. "omnigent-host" used
# here previously, which is exactly the shape that breaks console correlation.
sid = launcher.provision("HOLO-1")
print("provision()       -> sandbox id:", sid)

res = launcher.run(sid, "echo hello from omnigent && whoami")
print("run()             -> rc=%s stdout=%r" % (res.returncode, res.stdout.strip()))

with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
    f.write("patch content shipped via put()")
    local = f.name
launcher.put(sid, local, "/app/patch.txt")
print("put()             -> copied", local, "-> /app/patch.txt")

launcher.terminate(sid)
print("terminate()       -> released", sid)

print("\nROUND-TRIP OK — the provider drives the control plane end to end.")
