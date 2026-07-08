from __future__ import annotations

from functools import wraps

from flask import request

from .extensions import SessionLocal
from .models import User


_GUEST_EMAIL = "guest@local.ai-grader"

def _ensure_guest_user() -> User:
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(email=_GUEST_EMAIL).one_or_none()
        if user is None:
            user = User(
                email=_GUEST_EMAIL,
                name="Guest User",
                role="admin",
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        return user
    finally:
        db.close()


def get_user_from_token():
    # Authorization is intentionally disabled: always provide a default guest context
    # so API endpoints continue to receive request.user with id/email/role.
    user = _ensure_guest_user()
    return {"id": int(user.id), "email": user.email, "role": user.role}


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        request.user = get_user_from_token()
        return fn(*args, **kwargs)

    return wrapper


def require_role(*_roles):
    
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            request.user = get_user_from_token()
            return fn(*args, **kwargs)

        return wrapper

    return deco
