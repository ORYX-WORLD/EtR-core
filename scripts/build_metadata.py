#!/usr/bin/env python3
"""Create a machine-readable EtR deployment manifest."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def git_value(*args: str, fallback: str = "unknown") -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return fallback


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--status", default="unknown")
    parser.add_argument("--host", default=socket.gethostname())
    args = parser.parse_args()

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    manifest = {
        "schema_version": "1.0",
        "project": "EtR-core",
        "version": version,
        "commit": os.getenv("GITHUB_SHA") or git_value("rev-parse", "HEAD"),
        "branch": os.getenv("GITHUB_REF_NAME") or git_value("branch", "--show-current"),
        "target": args.target,
        "status": args.status,
        "deployed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "deployed_by": os.getenv("GITHUB_ACTOR") or os.getenv("USER", "unknown"),
        "workflow_run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "host": args.host,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
