#!/usr/bin/env python3
"""Discover the actual RTDB instance, publish rules and verify the readback.

The script receives a short-lived OAuth token from the Google Cloud WIF
workflow. It never reads a service-account private key and never writes the
access token to the report or logs.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "oryx-froid-industriel").strip()
ACCESS_TOKEN = os.getenv("GCP_ACCESS_TOKEN", "").strip()
CONFIGURED_URL = os.getenv(
    "FIREBASE_DATABASE_URL",
    "https://oryx-froid-industriel-default-rtdb.europe-west1.firebasedatabase.app",
).rstrip("/")
RULES_PATH = Path(sys.argv[1] if len(sys.argv) > 1 else "firebase/database.rules.json")
REPORT_PATH = Path(sys.argv[2] if len(sys.argv) > 2 else ".github/deployment/rtdb-rules-last.json")


def canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def digest(value: Any) -> str:
    raw = json.dumps(canonical(value), separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def safe_text(value: str, limit: int = 600) -> str:
    text = str(value or "")
    if ACCESS_TOKEN:
        text = text.replace(ACCESS_TOKEN, "[REDACTED_ACCESS_TOKEN]")
    return text.replace("\n", " ").strip()[:limit]


def request_json(url: str, *, method: str = "GET", payload: Any | None = None) -> tuple[int, Any, str]:
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Accept": "application/json",
            **({"Content-Type": "application/json; charset=utf-8"} if data is not None else {}),
            "User-Agent": "ORYX-EtR-RTDB-Rules/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw) if raw.strip() else None
            return response.status, parsed, raw
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        return error.code, parsed, raw


def normalized_database_url(value: str) -> str | None:
    try:
        parsed = urllib.parse.urlparse(str(value or "").strip())
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    if not parsed.hostname.endswith(("firebaseio.com", "firebasedatabase.app")):
        return None
    return f"https://{parsed.hostname}"


def discover_instances() -> tuple[list[str], dict[str, Any]]:
    endpoint = (
        "https://firebasedatabase.googleapis.com/v1beta/projects/"
        f"{urllib.parse.quote(PROJECT_ID, safe='')}/locations/-/instances?pageSize=100"
    )
    status, parsed, raw = request_json(endpoint)
    details: dict[str, Any] = {
        "endpoint": endpoint,
        "httpStatus": status,
        "error": None,
        "instances": [],
    }
    candidates: list[str] = []
    if status == 200 and isinstance(parsed, dict):
        for item in parsed.get("instances", []) or []:
            if not isinstance(item, dict):
                continue
            database_url = normalized_database_url(
                item.get("databaseUrl") or item.get("database_url") or item.get("url") or ""
            )
            details["instances"].append(
                {
                    "name": item.get("name"),
                    "state": item.get("state"),
                    "type": item.get("type"),
                    "databaseUrl": database_url,
                }
            )
            if database_url and str(item.get("state") or "ACTIVE").upper() not in {"DELETED", "DISABLED"}:
                candidates.append(database_url)
    else:
        details["error"] = safe_text(raw)
    fallback = normalized_database_url(CONFIGURED_URL)
    if fallback and fallback not in candidates:
        candidates.append(fallback)
    return candidates, details


def main() -> int:
    report: dict[str, Any] = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "commit": os.getenv("GITHUB_SHA"),
        "project": PROJECT_ID,
        "status": "failure",
        "method": "google_wif_management_discovery_and_rest_readback",
        "rulesSha256": None,
        "readbackSha256": None,
        "databaseUrl": None,
        "verified": False,
        "discovery": None,
        "attempts": [],
        "error": None,
    }
    try:
        if len(ACCESS_TOKEN) < 40:
            raise RuntimeError("Jeton OAuth Google Cloud absent ou invalide")
        local_rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        if not isinstance(local_rules, dict) or not isinstance(local_rules.get("rules"), dict):
            raise RuntimeError("Le fichier ne contient pas d'objet rules")
        report["rulesSha256"] = digest(local_rules)
        candidates, discovery = discover_instances()
        report["discovery"] = discovery
        if not candidates:
            raise RuntimeError("Aucune instance Realtime Database exploitable n'a été découverte")

        for database_url in candidates:
            endpoint = f"{database_url}/.settings/rules.json"
            put_status, _put_json, put_raw = request_json(endpoint, method="PUT", payload=local_rules)
            attempt: dict[str, Any] = {
                "databaseUrl": database_url,
                "putStatus": put_status,
                "getStatus": None,
                "error": None,
            }
            if put_status not in {200, 204}:
                attempt["error"] = safe_text(put_raw)
                report["attempts"].append(attempt)
                continue

            get_status, remote_rules, get_raw = request_json(endpoint)
            attempt["getStatus"] = get_status
            if get_status != 200 or not isinstance(remote_rules, dict):
                attempt["error"] = safe_text(get_raw)
                report["attempts"].append(attempt)
                continue

            remote_hash = digest(remote_rules)
            report["readbackSha256"] = remote_hash
            attempt["readbackSha256"] = remote_hash
            report["attempts"].append(attempt)
            if remote_hash != report["rulesSha256"]:
                raise RuntimeError("Les règles relues ne correspondent pas aux règles locales")
            report.update(
                {
                    "status": "success",
                    "databaseUrl": database_url,
                    "verified": True,
                    "error": None,
                }
            )
            break

        if not report["verified"]:
            statuses = ", ".join(
                f"{item['databaseUrl']}=PUT:{item['putStatus']}" for item in report["attempts"]
            )
            raise RuntimeError(f"Aucune instance n'a accepté la publication ({statuses})")
    except Exception as error:  # noqa: BLE001 - report must always be written.
        report["error"] = safe_text(str(error), 1200)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
