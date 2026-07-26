from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil
from flask import Flask, jsonify

SCHEMA_VERSION = "1.0"
SERVICE_VERSION = "3.0.0"
DEFAULT_STATE_FILE = "/var/lib/etr-core/telemetry.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


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
    telemetry, telemetry_error = read_telemetry_state(state_file)
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
            "installation_id": os.getenv("ETR_INSTALLATION_ID", "").strip() or None,
        },
        "system": system,
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
