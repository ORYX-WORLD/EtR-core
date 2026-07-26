from __future__ import annotations

import os
from typing import Any

import requests
from flask import Flask, jsonify, render_template

DASHBOARD_VERSION = "1.0.0"
DEFAULT_API_URL = "http://127.0.0.1:8080/api/v1/status"


def create_app(config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        ETR_API_URL=os.getenv("ETR_LOCAL_API_URL", DEFAULT_API_URL),
        ETR_API_TIMEOUT=float(os.getenv("ETR_LOCAL_API_TIMEOUT", "2.5")),
        JSON_SORT_KEYS=False,
    )
    if config:
        app.config.update(config)

    @app.after_request
    def secure_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; form-action 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/")
    def index():
        return render_template("index.html", dashboard_version=DASHBOARD_VERSION)

    @app.get("/healthz")
    def health():
        return jsonify({"ok": True, "service": "etr-dashboard", "version": DASHBOARD_VERSION})

    @app.get("/api/status")
    def api_status():
        try:
            response = requests.get(
                app.config["ETR_API_URL"],
                timeout=app.config["ETR_API_TIMEOUT"],
                headers={"Accept": "application/json", "User-Agent": f"EtR-Dashboard/{DASHBOARD_VERSION}"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid_payload")
            return jsonify({"dashboard_online": True, "api_online": True, "data": payload})
        except (requests.RequestException, ValueError, TypeError):
            return jsonify(
                {
                    "dashboard_online": True,
                    "api_online": False,
                    "error": "local_api_unavailable",
                    "data": {},
                }
            )

    return app


app = create_app()
