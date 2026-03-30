#!/usr/bin/env python3
"""
Create or update a user with a password hash and role (for JWT login at POST /api/auth/login/password).

Usage (from AGT_platform/backend, with DATABASE_URL in .env or environment):

  python scripts/create_password_user.py teacher@school.edu 'YourPassword' teacher

Roles: student | teacher | admin
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

load_dotenv(ROOT / ".env")

from app.config import Config  # noqa: E402
from app.extensions import SessionLocal, init_db  # noqa: E402
from app.models import User  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Create/update user for password + JWT login")
    p.add_argument("email", help="Login email (same as username)")
    p.add_argument("password", help="Plain password (only echoed to terminal—use a strong secret)")
    p.add_argument(
        "role",
        nargs="?",
        default="student",
        choices=("student", "teacher", "admin"),
        help="Role stored in users.role (default: student)",
    )
    p.add_argument("--name", default="", help="Display name (optional)")
    args = p.parse_args()

    email = args.email.strip().lower()
    cfg = Config()
    init_db(cfg.DATABASE_URL)
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(email=email).one_or_none()
        pw_hash = generate_password_hash(args.password)
        if user:
            user.password_hash = pw_hash
            user.role = args.role
            if args.name:
                user.name = args.name
            db.commit()
            print(f"Updated user id={user.id} email={email} role={args.role}")
        else:
            user = User(
                email=email,
                name=args.name or email.split("@")[0],
                role=args.role,
                password_hash=pw_hash,
            )
            db.add(user)
            db.commit()
            print(f"Created user id={user.id} email={email} role={args.role}")
        print("They can obtain a JWT with: POST /api/auth/login/password")
    finally:
        db.close()


if __name__ == "__main__":
    main()
