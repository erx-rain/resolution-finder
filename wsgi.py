"""WSGI entrypoint for hosted deployments (gunicorn wsgi:app).

Same app as run_service.py, plus an optional model warm-up: with
RF_WARM_MODELS=1 both local models (the embedding model and the NLI
verifier) load once in a background thread at boot, so the first scan does
not pay the load cost mid-request and a memory problem shows up at startup
in the logs instead of halfway through someone's scan.
"""
import logging
import os
import threading

from resolution_finder.config import DB_PATH
from resolution_finder.service.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("rf.wsgi")

db_path = os.environ.get("RF_DB_PATH", DB_PATH)
db_dir = os.path.dirname(db_path)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)

app = create_app(db_path, start_scheduler=True)


def _warm_models() -> None:
    try:
        from resolution_finder.relevance_ranker import _get_model
        from resolution_finder.verdict_engine import _get_nli_classifier

        _get_model()
        logger.info("Embedding model loaded")
        _get_nli_classifier()
        logger.info("NLI verifier loaded — scans are ready")
    except Exception:  # noqa: BLE001 — a warm-up failure must not kill the API
        logger.exception("Model warm-up failed; models will load on first scan instead")


if os.environ.get("RF_WARM_MODELS") == "1":
    threading.Thread(target=_warm_models, name="rf-warmup", daemon=True).start()
