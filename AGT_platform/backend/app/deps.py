from __future__ import annotations

from typing import Generator

from sqlalchemy.orm import Session

from .access import get_user_from_token
from .database.init_db import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """Per-request SQLAlchemy session; always closed when the request finishes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user() -> dict:
    """
    Mirrors the old ``@require_auth`` decorator: always resolves to a user context dict
    (``id`` / ``email`` / ``role``). See :func:`app.access.get_user_from_token` — real
    authorization is intentionally disabled; every request resolves to (or creates) a
    persisted guest account.
    """
    return get_user_from_token()


def require_role(*_roles: str):
    """
    Mirrors the old ``@require_role(...)`` decorator from :mod:`app.access`. ``_roles`` is
    accepted so call sites keep documenting the intended roles, but — matching the pre-existing
    behavior being ported here — it is **not** enforced: ``app.access`` intentionally disables
    authorization and always returns the same guest/admin context. Tightening this to actually
    check ``_roles`` would be a behavior change beyond this migration.
    """

    def _dependency() -> dict:
        return get_user_from_token()

    return _dependency
