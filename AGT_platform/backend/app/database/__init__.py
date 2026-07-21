"""
Database layer: SQLAlchemy ORM schema (:mod:`models`), engine/session lifecycle
(:mod:`extensions`), and object storage (:mod:`storage`, MinIO/S3 via boto3).

Everything here is infrastructure the rest of the app depends on, not grading logic: routes,
Celery tasks, and the grading pipeline import from this package for persistence and file storage.
"""

from __future__ import annotations
