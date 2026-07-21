import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .config import Config
from .errors import register_error_handlers
from .database.init_db import init_db
from .database.models import Base
from .tasks import init_celery
from .routes.health import router as health_router
from .routes.submissions import router as submissions_router
from .routes.admin import router as admin_router
from .routes.courses import router as courses_router
from .routes.standalone import router as standalone_router
from .routes_assignments import router as assignments_router
from .routes.assignment_materials import router as assignment_materials_router

_log = logging.getLogger(__name__)


def _init_database_strict(cfg: Config):
    """Initialize SQLAlchemy and fail fast when DB is unreachable."""
    engine = init_db(cfg.DATABASE_URL)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    _log.info("Database connected: %s", cfg.DATABASE_URL)

    # Ensure required tables exist only after successful connectivity probe.
    Base.metadata.create_all(bind=engine)
    return engine


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cfg = Config()
    if (
        cfg.MINIO_ENDPOINT
        and "minio" in cfg.MINIO_ENDPOINT.lower()
        and cfg.MINIO_BUCKET
        and cfg.MINIO_BUCKET != "ai-grader"
    ):
        _log.warning(
            "MINIO_ENDPOINT targets MinIO but MINIO_BUCKET=%r is not the default dev bucket "
            "'ai-grader'. Requests go to MinIO, which returns NoSuchBucket if that name "
            "was never created there. For local Docker use MINIO_BUCKET=ai-grader. See "
            "AGT_platform/docs/s3_bucket_and_presigned_uploads_setup.md",
            cfg.MINIO_BUCKET,
        )

    _init_database_strict(cfg)
    init_celery(cfg)
    yield


def create_app() -> FastAPI:
    cfg = Config()
    app = FastAPI(title="AI Automatic Grader Tool API", lifespan=lifespan)

    # Production: keep API bodies small (JSON + presign metadata only). Large files go to
    # MinIO. Enforced via Starlette's max upload size where multipart is allowed locally.
    max_body_bytes = (
        cfg.MAX_UPLOAD_BYTES if cfg.ALLOW_FLASK_MULTIPART_UPLOAD else cfg.WEB_MAX_BODY_BYTES
    )
    app.state.max_body_bytes = max_body_bytes

    # Explicit allow_headers so preflight allows Authorization (cross-origin SPA → API).
    # See docs/BUG_REPORT_ADMIN_WRITE_401.md
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    register_error_handlers(app)

    app.include_router(health_router)
    app.include_router(assignments_router)
    app.include_router(assignment_materials_router)
    app.include_router(submissions_router)
    app.include_router(admin_router)
    app.include_router(courses_router)
    app.include_router(standalone_router)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=Config.FLASK_PORT, reload=True)
