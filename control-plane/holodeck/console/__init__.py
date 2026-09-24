"""The console — trigger bridge + status board, co-located with the control
plane but talking to it over HTTP (loopback) so it can be split out later with
only a URL change. It owns ONE thing: the correlation (ticket ↔ session ↔ lease)
plus a cached rollup. Truth stays in Jira / Omnigent / Holodeck.
"""

from holodeck.console.routes import mount_console

__all__ = ["mount_console"]
