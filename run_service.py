"""Entrypoint for the standalone Resolution Finder service (VM deployment).

    RF_GIST_ID=<gist id> RF_GIST_FILE=rf_markets.json \\
    RF_GITHUB_TOKEN=<token> RF_SERVICE_TOKEN=<bearer secret> \\
    python run_service.py

See docs/SERVICE.md for the full API, schedule-tag grammar, and a
systemd unit. Serves on RF_HOST:RF_PORT (default 127.0.0.1:8700 —
loopback by default on purpose; front it with a reverse proxy or an
SSH tunnel rather than exposing Flask directly).
"""
import logging
import os

from resolution_finder.config import DB_PATH
from resolution_finder.service.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

if __name__ == "__main__":
    db_path = os.environ.get("RF_DB_PATH", DB_PATH)
    app = create_app(db_path, start_scheduler=True)
    host = os.environ.get("RF_HOST", "127.0.0.1")
    port = int(os.environ.get("RF_PORT", "8700"))
    app.run(host=host, port=port)
