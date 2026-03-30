from flask import Blueprint, jsonify

bp = Blueprint("health", __name__)


@bp.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@bp.get("/api/healthz")
def healthz():
    """ALB-friendly alias; same payload as /api/health."""
    return jsonify({"status": "ok"})
