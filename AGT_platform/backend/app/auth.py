# backend/app/auth.py
from flask import Blueprint, redirect, request, jsonify, current_app
from authlib.integrations.flask_client import OAuth
import jwt, os, time
from .extensions import SessionLocal
from .models import User  # you’ll add fields below
from datetime import datetime

bp = Blueprint("auth", __name__)
oauth = OAuth()

def init_oauth(app):
    oauth.init_app(app)

def _issuer_for_domain(domain: str) -> str | None:
    """
    MVP: map known domains -> discovery url.
    Replace with DB lookup later.
    """
    domain = domain.lower().strip()

    mapping = {
        "mit.edu": "",
        "gsu.edu": "",
        "qcc.edu": "",
        "harvard.edu": ""
        # "mit.edu": "https://<mit-issuer>/.well-known/openid-configuration",
        # "gsu.edu": "https://<gsu-issuer>/.well-known/openid-configuration",
    }
    return mapping.get(domain)

def _register_dynamic_client(discovery_url: str):
    """
    Authlib needs a client name; we can reuse 'campus' but re-register with new metadata.
    """
    oauth.register(
        name="campus",
        server_metadata_url=discovery_url,
        client_id=current_app.config["OIDC_CLIENT_ID"],
        client_secret=current_app.config["OIDC_CLIENT_SECRET"],
        client_kwargs={"scope": "openid email profile"},
    )

def _issue_token(user: User):
    payload = {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "iat": int(time.time()),
        "exp": int(time.time()) + 60 * 60 * 8,
    }
    return jwt.encode(payload, os.getenv("SECRET_KEY", "dev_secret"), algorithm="HS256")

@bp.post("/api/auth/discover")
def discover():
    """
    Frontend sends { email }.
    We derive domain and return whether we can SSO it.
    """
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip().lower()
    if "@" not in email:
        return jsonify({"error": "valid email required"}), 400

    domain = email.split("@", 1)[1]
    discovery_url = _issuer_for_domain(domain)

    if not discovery_url:
        # fallback path: Google/Microsoft, or request manual admin add
        return jsonify({
            "supported": False,
            "domain": domain,
            "message": "School not configured yet. Use Google/Microsoft login or ask admin to add your school."
        }), 200

    return jsonify({"supported": True, "domain": domain}), 200

@bp.get("/api/auth/login")
def login():
    """
    Start OIDC. Requires ?domain=mit.edu OR ?email=x@mit.edu
    """
    domain = request.args.get("domain")
    email = request.args.get("email")

    if not domain and email and "@" in email:
        domain = email.split("@", 1)[1]

    if not domain:
        return jsonify({"error": "missing domain or email"}), 400

    discovery_url = _issuer_for_domain(domain)
    if not discovery_url:
        return jsonify({"error": f"unknown institution domain: {domain}"}), 400

    _register_dynamic_client(discovery_url)

    redirect_uri = request.host_url.rstrip("/") + "/api/auth/callback"
    # persist domain in state via session param
    return oauth.campus.authorize_redirect(redirect_uri, domain=domain)

@bp.get("/api/auth/callback")
def callback():
    token = oauth.campus.authorize_access_token()
    userinfo = token.get("userinfo") or oauth.campus.parse_id_token(token)

    email = (userinfo.get("email") or "").lower().strip()
    name = userinfo.get("name") or userinfo.get("preferred_username") or email
    if not email:
        return jsonify({"error": "missing email from idp"}), 400

    domain = email.split("@", 1)[1] if "@" in email else None

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(email=email).one_or_none()
        now = datetime.utcnow()

        if not user:
            # First login provisioning
            user = User(
                email=email,
                name=name,
                role="student",                 # default
                institution_domain=domain,
                first_login_at=now,
                last_login_at=now,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            user.last_login_at = now
            if not getattr(user, "institution_domain", None):
                user.institution_domain = domain
            db.commit()

        jwt_token = _issue_token(user)
    finally:
        db.close()

    frontend_base = current_app.config.get("FRONTEND_BASE_URL", "http://localhost:5173")
    return redirect(f"{frontend_base}/login#token={jwt_token}")