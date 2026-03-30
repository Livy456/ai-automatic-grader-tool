import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
load_dotenv(BASE_DIR / ".env.local")
load_dotenv(BASE_DIR / ".env", override=True)


def _env_str(key: str) -> str:
    """Always use empty-string default with os.getenv (never None)."""
    return os.getenv(key, "")


def _env_int(key: str, *, default: int) -> int:
    v = os.getenv(key, "").strip()
    return default if not v else int(v, 10)


def _env_bool(key: str) -> bool:
    return os.getenv(key, "").strip().lower() == "true"


class Config:
    SECRET_KEY = _env_str("SECRET_KEY")
    JWT_EXPIRATION_SECONDS = _env_int("JWT_EXPIRATION_SECONDS", default=8 * 3600)
    DATABASE_URL = _env_str("DATABASE_URL")
    REDIS_URL = _env_str("REDIS_URL")
    FRONTEND_BASE_URL = _env_str("FRONTEND_BASE_URL")

    # S3-compatible storage (AWS S3 or MinIO). Empty S3_ENDPOINT = native AWS.
    S3_ENDPOINT = _env_str("S3_ENDPOINT").strip()
    S3_ACCESS_KEY = _env_str("S3_ACCESS_KEY")
    S3_SECRET_KEY = _env_str("S3_SECRET_KEY")
    S3_BUCKET = _env_str("S3_BUCKET")
    S3_REGION = _env_str("S3_REGION")
    S3_SECURE = _env_bool("S3_SECURE")
    S3_ADDRESSING_STYLE = _env_str("S3_ADDRESSING_STYLE").strip()

    MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", default=1024)
    MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

    S3_INLINE_UPLOAD_MAX_BYTES = _env_int(
        "S3_INLINE_UPLOAD_MAX_BYTES", default=32 * 1024 * 1024
    )
    S3_UPLOAD_SPOOL_MAX_MEMORY_BYTES = _env_int(
        "S3_UPLOAD_SPOOL_MAX_MEMORY_BYTES", default=16 * 1024 * 1024
    )

    OIDC_CLIENT_ID = _env_str("OIDC_CLIENT_ID")
    OIDC_CLIENT_SECRET = _env_str("OIDC_CLIENT_SECRET")
    OIDC_DISCOVERY_URL = _env_str("OIDC_DISCOVERY_URL")
    OIDC_REDIRECT_URI = _env_str("OIDC_REDIRECT_URI")

    MICROSOFT_CLIENT_ID = _env_str("MICROSOFT_CLIENT_ID")
    MICROSOFT_CLIENT_SECRET = _env_str("MICROSOFT_CLIENT_SECRET")

    GOOGLE_CLIENT_ID = _env_str("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = _env_str("GOOGLE_CLIENT_SECRET")

    OLLAMA_BASE_URL = _env_str("OLLAMA_BASE_URL")
    OLLAMA_MODEL = _env_str("OLLAMA_MODEL")
    OPENAI_API_KEY = _env_str("OPENAI_API_KEY")
    WHISPER_ENABLED = _env_bool("WHISPER_ENABLED")

    _cors = _env_str("CORS_ORIGINS").strip()
    if not _cors:
        _cors = (
            "http://localhost:5173,http://127.0.0.1:5173,"
            "https://dia-ai-grader.com,https://www.dia-ai-grader.com,"
            "https://api.dia-ai-grader.com"
        )
    CORS_ORIGINS = [o.strip() for o in _cors.split(",") if o.strip()]
