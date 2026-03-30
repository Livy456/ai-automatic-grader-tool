from datetime import datetime
from functools import wraps

import jwt
from flask import jsonify, request

from .config import Config
from .extensions import SessionLocal
from .models import IssuedJwt


def get_user_from_token():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.split(" ", 1)[1]
    cfg = Config()
    secret = cfg.SECRET_KEY or "dev_secret"
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except Exception:
        return None

    jti = payload.get("jti")
    if not jti:
        return None

    db = SessionLocal()
    try:
        row = db.query(IssuedJwt).filter(IssuedJwt.jti == jti).one_or_none()
        if row is None or row.revoked_at is not None:
            return None
        if row.expires_at < datetime.utcnow():
            return None
        return payload
    finally:
        db.close()

def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_user_from_token()
        if not user:
            return jsonify({"error":"unauthorized"}), 401
        request.user = user
        return fn(*args, **kwargs)
    return wrapper

def require_role(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = getattr(request, "user", None) or get_user_from_token()
            if not user:
                return jsonify({"error":"unauthorized"}), 401
            if user.get("role") not in roles:
                return jsonify({"error":"forbidden"}), 403
            request.user = user
            return fn(*args, **kwargs)
        return wrapper
    return deco
