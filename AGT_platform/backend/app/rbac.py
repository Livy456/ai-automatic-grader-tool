from functools import wraps
from flask import request, jsonify
import jwt
import os

def get_user_from_token():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, os.getenv("SECRET_KEY","dev_secret"), algorithms=["HS256"])
        return payload  # {id,email,role}
    except Exception:
        return None

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
