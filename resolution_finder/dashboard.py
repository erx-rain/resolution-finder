# resolution_finder/dashboard.py
from flask import Flask, render_template, request, redirect, url_for
from resolution_finder.storage import get_latest_findings, set_review_status


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

    return app
