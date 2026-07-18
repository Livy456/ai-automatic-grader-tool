from __future__ import annotations

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


def get_user_from_token() -> dict:
    # Authorization is intentionally disabled: always provide a default guest context so API
    # endpoints continue to receive a user id/email/role. See app.deps.get_current_user /
    # app.deps.require_role, which wrap this for FastAPI route dependencies.
    user = _ensure_guest_user()
    return {"id": int(user.id), "email": user.email, "role": user.role}
