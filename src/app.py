import json
import os
from pathlib import Path

import psutil
from flask import Flask

app = Flask(__name__)


def build_metadata():
    metadata = {
        "version": os.getenv("ETR_VERSION", "unknown"),
        "commit": os.getenv("ETR_COMMIT", "unknown"),
        "deployed_at": os.getenv("ETR_DEPLOYED_AT", "unknown"),
    }
    try:
        persisted = json.loads(Path("/var/lib/etr-core/release.json").read_text(encoding="utf-8"))
        metadata["version"] = persisted.get("version", metadata["version"])
        metadata["commit"] = persisted.get("commit", metadata["commit"])
        metadata["deployed_at"] = persisted.get(
            "deployed_at_utc",
            persisted.get("deployed_at", metadata["deployed_at"]),
        )
    except (OSError, ValueError, TypeError):
        pass
    return metadata

@app.route("/")
def root():
    return {
        "service": "EtR",
        "cpu": psutil.cpu_percent(interval=0.2),
        **build_metadata(),
    }

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
