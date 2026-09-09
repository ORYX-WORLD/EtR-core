from __future__ import annotations

import os
from typing import Any

import requests
from flask import Flask, jsonify, render_template, request
from urllib.parse import urlsplit, urlunsplit

DASHBOARD_VERSION = "1.3.0"
DEFAULT_API_URL = "http://127.0.0.1:8080/api/v1/status"
# Le dashboard n'est accessible que sur la boucle locale. Il est affiché dans
# l'iframe du portail tactile EtR servi sur le port 8090. Toute autre origine
# reste interdite par la directive frame-ancestors.
TOUCH_PORTAL_ORIGIN = "http://127.0.0.1:8090"


def create_app(config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        ETR_API_URL=os.getenv("ETR_LOCAL_API_URL", DEFAULT_API_URL),
        ETR_API_TIMEOUT=float(os.getenv("ETR_LOCAL_API_TIMEOUT", "2.5")),
        JSON_SORT_KEYS=False,
    )
    if config:
        app.config.update(config)
    app.config["MAX_CONTENT_LENGTH"] = 8192

    @app.route("/api/hardware", methods=["GET", "PUT"])
    def api_hardware():
        if request.method == "PUT" and (
            request.headers.get("X-ETR-Local-Write") != "1" or not request.is_json
            or (request.headers.get("Origin") and request.headers["Origin"] != request.host_url.rstrip("/"))
        ):
            return jsonify({"error": "Écriture locale autorisée uniquement."}), 403
        base = urlsplit(app.config["ETR_API_URL"])
        url = urlunsplit((base.scheme, base.netloc, "/api/v1/hardware", "", ""))
        try:
            response = requests.request(request.method, url,
                json=request.get_json() if request.method == "PUT" else None,
                headers={"X-ETR-Local-Write": "1"},
                timeout=app.config["ETR_API_TIMEOUT"], allow_redirects=False)
            if response.status_code not in {200, 400, 409, 503}:
                raise ValueError("Unexpected hardware response")
            return jsonify(response.json()), response.status_code
        except (requests.RequestException, ValueError):
            return jsonify({"error": "Configuration du Raspberry inaccessible."}), 503

    @app.after_request
    def secure_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; form-action 'none'; "
            f"frame-ancestors {TOUCH_PORTAL_ORIGIN}"
        )
        return response

    @app.get("/")
    def index():
        return render_template("index.html", dashboard_version=DASHBOARD_VERSION)

    @app.get("/healthz")
    def health():
        return jsonify(
            {
                "ok": True,
                "service": "etr-dashboard",
                "version": DASHBOARD_VERSION,
                "embedded_by": TOUCH_PORTAL_ORIGIN,
            }
        )

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
