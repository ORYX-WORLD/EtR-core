from __future__ import annotations

import json
import os
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil
from flask import Flask, jsonify

SCHEMA_VERSION = "1.0"
SERVICE_VERSION = "3.1.0"
DEFAULT_STATE_FILE = "/var/lib/etr-core/telemetry.json"
DEFAULT_ENROLLMENT_FILE = "/var/lib/etr-core/enrollment.json"
DEFAULT_TOKEN_FILE = "/var/lib/etr-core/firebase-auth.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}


def read_telemetry_state(path: Path) -> tuple[dict[str, Any], str | None]:
    """Read the latest normalized field state without inventing measurements."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, "state_file_missing"
    except (OSError, ValueError, TypeError):
        return {}, "state_file_invalid"

    if not isinstance(raw, dict):
        return {}, "state_payload_invalid"

    measurements = raw.get("measurements") if isinstance(raw.get("measurements"), dict) else {}
    states = raw.get("states") if isinstance(raw.get("states"), dict) else {}
    alerts = raw.get("alerts") if isinstance(raw.get("alerts"), list) else []

    return {
        "updated_at": raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else None,
        "source": raw.get("source") if isinstance(raw.get("source"), str) else "local_state_file",
        "measurements": measurements,
        "states": states,
        "alerts": alerts[:100],
    }, None


def _display_activation_code(value: Any) -> str | None:
    code = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    if len(code) != 20:
        return None
    return "-".join(code[index : index + 5] for index in range(0, 20, 5))


def read_enrollment_state(enrollment_path: Path, token_path: Path) -> dict[str, Any]:
    token_state = _read_json(token_path)
    if token_state.get("refreshToken"):
        return {
            "required": False,
            "status": "enrolled",
            "activation_code": None,
            "installation_id": os.getenv("ETR_INSTALLATION_ID", "").strip() or None,
            "expires_at": None,
            "expires_in_seconds": None,
        }

    state = _read_json(enrollment_path)
    activation_code = _display_activation_code(state.get("activationCode"))
    expires_epoch = float(state.get("expiresEpoch") or 0)
    expires_in = max(0, int(expires_epoch - time.time())) if expires_epoch else None
    status = str(state.get("status") or ("pending" if activation_code else "unconfigured"))
    if expires_in == 0 and activation_code:
        status = "expired"

    return {
        "required": True,
        "status": status,
        "activation_code": activation_code,
        "installation_id": str(state.get("installationId") or os.getenv("ETR_INSTALLATION_ID", "").strip() or "") or None,
        "expires_at": str(state.get("expiresAt") or "") or None,
        "expires_in_seconds": expires_in,
    }


def system_status() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {
        "cpu_percent": round(float(psutil.cpu_percent(interval=None)), 1),
        "memory_percent": round(float(memory.percent), 1),
        "disk_percent": round(float(disk.percent), 1),
        "uptime_seconds": max(0, int(time.time() - psutil.boot_time())),
    }


def build_status() -> dict[str, Any]:
    state_file = Path(os.getenv("ETR_TELEMETRY_FILE", DEFAULT_STATE_FILE))
    enrollment_file = Path(os.getenv("ETR_ENROLLMENT_FILE", DEFAULT_ENROLLMENT_FILE))
    token_file = Path(os.getenv("ETR_TOKEN_FILE", DEFAULT_TOKEN_FILE))
    telemetry, telemetry_error = read_telemetry_state(state_file)
    enrollment = read_enrollment_state(enrollment_file, token_file)
    system = system_status()
    measurements = telemetry.get("measurements", {})
    states = telemetry.get("states", {})
    alerts = telemetry.get("alerts", [])

    status: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "service": "EtR",
        "service_version": SERVICE_VERSION,
        "timestamp": utc_now(),
        "health": "ok" if telemetry_error is None else "degraded",
        "device": {
            "hostname": socket.gethostname(),
            "installation_id": os.getenv("ETR_INSTALLATION_ID", "").strip() or enrollment.get("installation_id"),
        },
        "system": system,
        "enrollment": enrollment,
        "telemetry": {
            "online": telemetry_error is None,
            "error": telemetry_error,
            "source": telemetry.get("source"),
            "updated_at": telemetry.get("updated_at"),
            "measurements": measurements,
            "states": states,
            "alerts": alerts,
        },
        "capabilities": {
            "local_dashboard": True,
            "wifi_onboarding": True,
            "firebase_bridge": True,
            "secure_enrollment": True,
            "remote_screen": True,
            "telemetry_contract": SCHEMA_VERSION,
        },
    }

    # Compatibility fields used by the current Firebase bridge and web console.
    status["cpu"] = system["cpu_percent"]
    status["memory_percent"] = system["memory_percent"]
    status["disk_percent"] = system["disk_percent"]
    for key in ("pressure_bar", "temperature_c", "suction_pressure", "discharge_pressure"):
        value = _number(measurements.get(key))
        if value is not None:
            status[key] = value
    if "compressor_state" in states:
        status["compressor_state"] = states["compressor_state"]
    elif "compressor_on" in states:
        status["compressor_on"] = bool(states["compressor_on"])
    status["alerts_active"] = len(alerts)
    return status


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.update(JSON_SORT_KEYS=False)

    @app.after_request
    def secure_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.get("/")
    @app.get("/api/v1/status")
    def status_endpoint():
        return jsonify(build_status())

    @app.get("/api/v1/telemetry")
    def telemetry_endpoint():
        status = build_status()
        return jsonify(
            {
                "schema_version": status["schema_version"],
                "timestamp": status["timestamp"],
                "telemetry": status["telemetry"],
            }
        )

    @app.get("/api/v1/enrollment")
    def enrollment_endpoint():
        status = build_status()
        return jsonify(
            {
                "schema_version": status["schema_version"],
                "timestamp": status["timestamp"],
                "enrollment": status["enrollment"],
            }
        )

    @app.get("/healthz")
    def health_endpoint():
        status = build_status()
        return jsonify({"ok": True, "health": status["health"], "service_version": SERVICE_VERSION})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("ETR_API_HOST", "127.0.0.1"),
        port=int(os.getenv("ETR_API_PORT", "8080")),
        debug=False,
    )
