#!/usr/bin/env python3
"""Validate the repository's release and traceability invariants."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
REQUIRED_FILES = (
    "VERSION",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/definition-of-done.md",
    "docs/adr/README.md",
    "docs/incidents/README.md",
    "docs/deployments/README.md",
    "docs/registers/device-fleet.csv",
    "docs/registers/risk-register.md",
)


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    missing = [name for name in REQUIRED_FILES if not (ROOT / name).is_file()]
    if missing:
        fail(f"required files missing: {', '.join(missing)}")

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not SEMVER.fullmatch(version):
        fail(f"VERSION is not valid SemVer: {version!r}")

    package = json.loads((ROOT / "gateway/package.json").read_text(encoding="utf-8"))
    if package.get("version") != version:
        fail(f"gateway/package.json version {package.get('version')!r} differs from VERSION {version!r}")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if "## [Unreleased]" not in changelog:
        fail("CHANGELOG.md has no [Unreleased] section")
    if f"## [{version}]" not in changelog:
        fail(f"CHANGELOG.md has no [{version}] release section")

    rules = ROOT / "firebase/database.rules.json"
    if rules.exists():
        json.loads(rules.read_text(encoding="utf-8"))

    print(f"Project metadata valid for EtR {version}")


if __name__ == "__main__":
    main()
