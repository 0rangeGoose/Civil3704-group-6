#!/usr/bin/env python3
"""Installs (or removes) a launchd agent that keeps the server running.

- Starts scripts/run_publish.py when you log in, and again whenever it exits
  (a crash, or `kill`), after at most THROTTLE_SECONDS. Under `caffeinate -i`
  so the Mac doesn't idle-sleep while it's serving.
- It's a user agent, so after a restart it only starts once you've logged in.
- While it's installed, `kill` no longer stops the server for good (launchd
  starts it again). Stop it with --uninstall, or for a moment with
  `launchctl kickstart -k gui/$(id -u)/com.pulsecbd.server` (restart).

    python scripts/install_server_service.py            # install and start
    python scripts/install_server_service.py --uninstall
"""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LABEL = "com.pulsecbd.server"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = REPO_ROOT / "data" / "recordings" / "server.log"
THROTTLE_SECONDS = 30  # also keeps a failing start (e.g. port in use) from looping fast


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def install() -> None:
    python = REPO_ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        raise SystemExit(f"error: {python} not found -- set up the venv first (see README)")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            "/usr/bin/caffeinate", "-i",
            str(python), str(REPO_ROOT / "scripts" / "run_publish.py"),
        ],
        "WorkingDirectory": str(REPO_ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_SECONDS,
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "bootout", _launchd_domain(), str(PLIST_PATH)], capture_output=True)
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["launchctl", "bootstrap", _launchd_domain(), str(PLIST_PATH)], check=True)
    print(f"installed {PLIST_PATH}; the server starts now and at every login")
    print(f"server log: {LOG_PATH}")


def uninstall() -> None:
    subprocess.run(["launchctl", "bootout", _launchd_domain(), str(PLIST_PATH)], capture_output=True)
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
    print(f"removed {PLIST_PATH}; the server is stopped")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if args.uninstall:
        uninstall()
    else:
        install()
    return 0


if __name__ == "__main__":
    sys.exit(main())
