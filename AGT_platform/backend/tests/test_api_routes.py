"""
FastAPI web-layer smoke tests (the Flask → FastAPI migration added test coverage for the HTTP
layer that did not exist before). Uses a throwaway file-based SQLite DB via ``Config`` class
attribute overrides — see ``_use_temp_sqlite`` — and mocks the only two forms of real network
I/O these routes would otherwise need (MinIO object storage, Celery task dispatch) so the
suite runs offline. ``app.access.get_user_from_token`` always resolves to a persisted local
guest account (role "admin"); see that module's docstring — real authorization is intentionally
disabled, so every request in these tests is implicitly an authenticated admin request.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class ApiRoutesTestCase(unittest.TestCase):
    """Base class: fresh sqlite file + FastAPI app + TestClient per test."""

    def setUp(self) -> None:
        from sqlalchemy import String

        from app.config import Config
        from app.database.models import AssignmentUpload

        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "test.db"
        self._orig_database_url = Config.DATABASE_URL
        self._orig_redis_url = Config.REDIS_URL
        Config.DATABASE_URL = f"sqlite:///{db_path}"
        Config.REDIS_URL = "redis://localhost:6379/0"

        # AssignmentUpload.id is a Postgres-native UUID column (production DB). The app only
        # ever handles it as a plain string (str(uuid.uuid4()) at creation, string path params
        # for reads), so swapping in a plain String column here is transparent to route
        # behavior — it just avoids SQLite's lack of a native UUID bind/result type in this
        # SQLite-backed test DB. Restored in tearDown so it can't leak into other test modules.
        self._orig_id_type = AssignmentUpload.__table__.c.id.type
        AssignmentUpload.__table__.c.id.type = String(36)

        from app.main import create_app

        self.app = create_app()
        self.client = TestClient(self.app)
        self.client.__enter__()  # trigger lifespan startup (DB init/create_all)

    def tearDown(self) -> None:
        from app.config import Config
        from app.database.models import AssignmentUpload

        self.client.__exit__(None, None, None)
        self._tmpdir.cleanup()
        Config.DATABASE_URL = self._orig_database_url
        Config.REDIS_URL = self._orig_redis_url
        AssignmentUpload.__table__.c.id.type = self._orig_id_type


class HealthRouteTests(ApiRoutesTestCase):
    def test_health_and_healthz(self):
        for path in ("/api/health", "/api/healthz"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json(), {"status": "ok"})


class AssignmentUploadRouteTests(ApiRoutesTestCase):
    def test_list_is_empty_then_create_then_get_round_trips(self):
        empty = self.client.get("/api/assignments")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json(), [])

        with patch(
            "app.routes_assignments.upload_from_fastapi_file",
            return_value="ingest/assignment-uploads/fake/blank.txt",
        ):
            res = self.client.post(
                "/api/assignments",
                files={"file": ("blank.txt", io.BytesIO(b"hello world"), "text/plain")},
            )
        self.assertEqual(res.status_code, 201, res.text)
        assignment_id = res.json()["id"]

        got = self.client.get(f"/api/assignments/{assignment_id}")
        self.assertEqual(got.status_code, 200)
        body = got.json()
        self.assertEqual(body["id"], assignment_id)
        self.assertEqual(body["filename"], "blank.txt")
        self.assertEqual(body["status"], "uploaded")

        listed = self.client.get("/api/assignments")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([a["id"] for a in listed.json()], [assignment_id])

    def test_get_missing_assignment_returns_legacy_error_shape(self):
        res = self.client.get("/api/assignments/does-not-exist")
        self.assertEqual(res.status_code, 404)
        # Custom exception handler flattens HTTPException(detail=...) back to a top-level
        # {"error": ...} body, matching the pre-migration Flask jsonify({"error": ...}) contract.
        self.assertEqual(res.json(), {"error": "Assignment not found"})

    def test_grade_without_openai_key_returns_503(self):
        with patch(
            "app.routes_assignments.upload_from_fastapi_file",
            return_value="ingest/assignment-uploads/fake/blank.txt",
        ):
            create = self.client.post(
                "/api/assignments",
                files={"file": ("blank.txt", io.BytesIO(b"hello"), "text/plain")},
            )
        assignment_id = create.json()["id"]

        with patch("app.routes_assignments.build_multimodal_grading_clients", return_value=[]):
            res = self.client.post(f"/api/assignments/{assignment_id}/grade")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["error"], "Multimodal grading unavailable")

    def test_grade_enqueues_celery_task_and_returns_202(self):
        with patch(
            "app.routes_assignments.upload_from_fastapi_file",
            return_value="ingest/assignment-uploads/fake/blank.txt",
        ):
            create = self.client.post(
                "/api/assignments",
                files={"file": ("blank.txt", io.BytesIO(b"hello"), "text/plain")},
            )
        assignment_id = create.json()["id"]

        with (
            patch(
                "app.routes_assignments.build_multimodal_grading_clients",
                return_value=[("fake-client", "openai:fake")],
            ),
            patch("app.routes_assignments.grade_assignment_upload") as mock_task,
        ):
            res = self.client.post(f"/api/assignments/{assignment_id}/grade")
        self.assertEqual(res.status_code, 202)
        self.assertEqual(res.json(), {"ok": True, "status": "queued"})
        mock_task.delay.assert_called_once_with(assignment_id)

        # Re-posting while already queued short-circuits (200, not re-enqueued) — same
        # idempotency contract as the pre-migration Flask route.
        with patch("app.routes_assignments.grade_assignment_upload") as mock_task_2:
            again = self.client.post(f"/api/assignments/{assignment_id}/grade")
        self.assertEqual(again.status_code, 200)
        mock_task_2.delay.assert_not_called()


class CoursesRouteTests(ApiRoutesTestCase):
    def test_list_courses_empty_for_admin_guest(self):
        res = self.client.get("/api/courses")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), [])

    def test_get_missing_course_returns_404(self):
        res = self.client.get("/api/courses/999")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json(), {"error": "not found"})

    def test_create_assignment_requires_valid_modality(self):
        from app.database.init_db import SessionLocal
        from app.database.models import Course

        db = SessionLocal()
        try:
            course = Course(code="6.100", title="Intro to CS")
            db.add(course)
            db.commit()
            db.refresh(course)
            course_id = course.id
        finally:
            db.close()

        bad = self.client.post(
            f"/api/courses/{course_id}/assignments",
            json={"title": "PSet 1", "modality": "not-a-real-modality"},
        )
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json(), {"error": "invalid modality"})

        ok = self.client.post(
            f"/api/courses/{course_id}/assignments",
            json={"title": "PSet 1", "modality": "written"},
        )
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertEqual(ok.json()["title"], "PSet 1")

        listed = self.client.get(f"/api/courses/{course_id}/assignments")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)


class AdminRouteTests(ApiRoutesTestCase):
    def test_list_users_includes_guest_admin(self):
        res = self.client.get("/api/admin/users")
        self.assertEqual(res.status_code, 200)
        emails = [u["email"] for u in res.json()]
        self.assertIn("guest@local.ai-grader", emails)


class StandaloneRouteTests(ApiRoutesTestCase):
    def test_list_standalone_submissions_empty(self):
        res = self.client.get("/api/standalone/submissions")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"items": [], "total": 0, "page": 1, "per_page": 20})

    def test_start_requires_title_and_files(self):
        res = self.client.post("/api/standalone/submissions/start", json={})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json(), {"error": "title is required"})

    def test_start_returns_presigned_uploads(self):
        with patch(
            "app.routes.standalone.presigned_put_url",
            return_value="https://minio.example/fake-presigned-url",
        ):
            res = self.client.post(
                "/api/standalone/submissions/start",
                json={
                    "title": "My Autograded Submission",
                    "files": [{"filename": "essay.pdf", "content_type": "application/pdf"}],
                },
            )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["status"], "uploading")
        self.assertEqual(len(body["uploads"]), 1)
        self.assertEqual(body["uploads"][0]["upload_url"], "https://minio.example/fake-presigned-url")


class AssignmentLibraryRouteTests(ApiRoutesTestCase):
    """Assignment Creation flow: upload-context (start/finalize) + editable Q&A chunks."""

    def _start(self):
        with patch(
            "app.routes.assignment_library.presigned_put_url",
            return_value="https://minio.example/fake-presigned-url",
        ):
            return self.client.post(
                "/api/assignment-library/start",
                json={
                    "title": "Problem Set 3",
                    "modality": "written",
                    "files": [
                        {"filename": "blank.pdf", "content_type": "application/pdf", "artifact_kind": "blank_assignment"},
                        {"filename": "key.pdf", "content_type": "application/pdf", "artifact_kind": "answer_key"},
                        {"filename": "rubric.json", "content_type": "application/json", "artifact_kind": "rubric"},
                    ],
                },
            )

    def test_start_requires_title_and_files(self):
        res = self.client.post("/api/assignment-library/start", json={})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json(), {"error": "title is required"})

    def test_start_returns_presigned_uploads_for_each_kind(self):
        res = self._start()
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["status"], "uploading")
        self.assertEqual(
            sorted(u["kind"] for u in body["uploads"]),
            ["answer_key", "blank_assignment", "rubric"],
        )
        for u in body["uploads"]:
            self.assertEqual(u["upload_url"], "https://minio.example/fake-presigned-url")

    def test_finalize_requires_all_three_context_kinds(self):
        with patch(
            "app.routes.assignment_library.presigned_put_url",
            return_value="https://minio.example/fake-presigned-url",
        ):
            start = self.client.post(
                "/api/assignment-library/start",
                json={
                    "title": "Missing pieces",
                    "files": [
                        {"filename": "blank.pdf", "content_type": "application/pdf", "artifact_kind": "blank_assignment"},
                    ],
                },
            )
        assignment_id = start.json()["assignment_id"]

        res = self.client.post(f"/api/assignment-library/{assignment_id}/finalize")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"], "missing required context")
        self.assertEqual(sorted(res.json()["missing"]), ["answer_key", "rubric"])

    def test_finalize_seeds_chunks_then_review_edit_and_history_round_trip(self):
        start = self._start()
        assignment_id = start.json()["assignment_id"]

        with (
            patch("app.routes.assignment_library.object_exists", return_value=True),
            patch("app.routes.assignment_library.get_object_bytes", return_value=b"n/a"),
            patch(
                "app.routes.assignment_library.parse_assignment_context",
                return_value=type(
                    "P", (), {"blank_text": "1) What is 2+2?", "answer_key_text": "1) 4"}
                )(),
            ),
            patch(
                "app.routes.assignment_library.try_chunk_assignment_qa_pairs",
                return_value=[{"question_id": "1", "question": "What is 2+2?", "answer": "4"}],
            ),
        ):
            fin = self.client.post(f"/api/assignment-library/{assignment_id}/finalize")
        self.assertEqual(fin.status_code, 200, fin.text)
        self.assertEqual(fin.json()["chunking_status"], "ok")

        detail = self.client.get(f"/api/assignment-library/{assignment_id}")
        self.assertEqual(detail.status_code, 200)
        chunks = detail.json()["chunks"]
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["question_text"], "What is 2+2?")
        self.assertFalse(chunks[0]["is_edited"])

        history = self.client.get("/api/assignment-library")
        self.assertEqual(history.status_code, 200)
        self.assertIn(assignment_id, [a["id"] for a in history.json()])

        edited = self.client.put(
            f"/api/assignment-library/{assignment_id}/chunks",
            json={
                "chunks": [
                    {"id": chunks[0]["id"], "question_id": "1", "question_text": "What is 2+2?", "answer_text": "four"},
                    {"question_id": "2", "question_text": "What is 3+3?", "answer_text": "6"},
                ]
            },
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        saved = edited.json()["chunks"]
        self.assertEqual(len(saved), 2)
        self.assertTrue(all(c["is_edited"] for c in saved))
        self.assertEqual({c["answer_text"] for c in saved}, {"four", "6"})


if __name__ == "__main__":
    unittest.main()
