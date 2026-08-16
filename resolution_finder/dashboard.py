# resolution_finder/dashboard.py
from flask import Flask, render_template, request, redirect, url_for
from resolution_finder.storage import get_latest_findings, set_review_status, get_setting, set_setting


def create_app(db_path: str) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        findings = get_latest_findings(db_path)
        return render_template("index.html", findings=findings)

    @app.route("/review/<int:finding_id>", methods=["POST"])
    def review(finding_id):
        status = request.form["status"]
        set_review_status(db_path, finding_id, status)
        return redirect(url_for("index"))

    @app.route("/settings", methods=["GET"])
    def settings():
        api_key = get_setting(db_path, "currents_api_key")
        masked_key = f"••••{api_key[-4:]}" if api_key else None
        remaining = get_setting(db_path, "currents_rate_limit_remaining")
        limit = get_setting(db_path, "currents_rate_limit_limit")
        updated_at = get_setting(db_path, "currents_rate_limit_updated_at")
        return render_template(
            "settings.html",
            masked_key=masked_key,
            remaining=remaining,
            limit=limit,
            updated_at=updated_at,
        )

    @app.route("/settings", methods=["POST"])
    def save_settings():
        new_key = request.form.get("currents_api_key", "").strip()
        if new_key:
            set_setting(db_path, "currents_api_key", new_key)
        return redirect(url_for("settings"))

    return app
