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
import json
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


class SubmissionsRouteTests(ApiRoutesTestCase):
    """``GET /api/teacher/submissions`` — the "View Submissions" history page's data source."""

    def test_list_recent_submissions_empty(self):
        res = self.client.get("/api/teacher/submissions")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), [])

    def test_list_recent_submissions_includes_assignment_and_course(self):
        from app.database.init_db import SessionLocal
        from app.database.models import Assignment, Course, Submission

        db = SessionLocal()
        try:
            course = Course(code="6.100", title="Intro to CS")
            db.add(course)
            db.flush()
            assignment = Assignment(
                course_id=course.id, title="PSet 1", modality="written", rubric=[], created_at=None
            )
            db.add(assignment)
            db.flush()
            sub = Submission(assignment_id=assignment.id, student_id=None, status="graded", final_score=9.5)
            db.add(sub)
            db.commit()
            sub_id = sub.id
        finally:
            db.close()

        res = self.client.get("/api/teacher/submissions")
        self.assertEqual(res.status_code, 200, res.text)
        rows = res.json()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], sub_id)
        self.assertEqual(row["assignment"], "PSet 1")
        self.assertEqual(row["course"], "6.100")
        self.assertEqual(row["status"], "graded")
        self.assertEqual(row["final_score"], 9.5)


class GradeSubmissionContextTests(ApiRoutesTestCase):
    """
    ``app.tasks.grade_submission`` should source the blank-assignment template from the
    Assignment's saved ``AssignmentAttachment`` automatically (Assignment Creation flow), instead
    of requiring the student to re-upload it via a separate "Add Context" submission step.
    """

    def test_blank_assignment_attachment_becomes_modality_hint(self):
        from app import tasks
        from app.database.init_db import SessionLocal
        from app.database.models import Assignment, AssignmentAttachment, Submission, SubmissionArtifact

        db = SessionLocal()
        try:
            assignment = Assignment(
                course_id=None,
                title="Library assignment",
                modality="written",
                rubric=[],
                created_at=None,
                grader_answer_key_text="1) 4",
            )
            db.add(assignment)
            db.flush()
            db.add(
                AssignmentAttachment(
                    assignment_id=assignment.id,
                    kind="blank_assignment",
                    object_key="blank.ipynb",
                    filename="blank.ipynb",
                )
            )
            sub = Submission(assignment_id=assignment.id, student_id=None, status="queued")
            db.add(sub)
            db.flush()
            db.add(SubmissionArtifact(submission_id=sub.id, kind="ipynb", object_key="work.ipynb"))
            db.commit()
            sub_id = sub.id
        finally:
            db.close()

        def _fake_get_object_bytes(cfg, key):
            return b'{"cells": []}' if key == "blank.ipynb" else b"print(1)"

        captured: dict = {}

        def _fake_pipeline(cfg, assign_for_prompt, artifacts, **kwargs):
            captured.update(kwargs)
            return {"overall": {}, "criteria": [], "question_grades": []}

        with (
            patch("app.tasks.get_object_bytes", side_effect=_fake_get_object_bytes),
            patch("app.tasks.run_db_submission_multimodal_pipeline", side_effect=_fake_pipeline),
            patch("app.tasks.minio_client"),
        ):
            tasks.grade_submission.run(sub_id)

        hints = captured.get("modality_hints_extra")
        self.assertIsNotNone(hints)
        self.assertEqual(hints["blank_assignment_template_bytes"], b'{"cells": []}')
        self.assertEqual(hints["blank_assignment_template_suffix"], ".ipynb")
        self.assertEqual(hints["blank_assignment_ipynb_bytes"], b'{"cells": []}')
        # Library assignments already carry the answer key text on the Assignment row itself.
        self.assertIn("1) 4", captured.get("answer_key_text") or "")

    def test_question_chunks_become_prechunked_qa_pairs_hint(self):
        """
        Assignment Creation's teacher-reviewed ``AssignmentQuestionChunk`` rows should be threaded
        through as ``modality_hints_extra["prechunked_qa_pairs"]`` so the multimodal pipeline can
        pair a student's response against them instead of re-decomposing the assignment from
        scratch (see ``app.grading.chunking.prechunked_response_pairing_agent``).
        """
        from app import tasks
        from app.database.init_db import SessionLocal
        from app.database.models import (
            Assignment,
            AssignmentQuestionChunk,
            Submission,
            SubmissionArtifact,
        )

        db = SessionLocal()
        try:
            assignment = Assignment(
                course_id=None,
                title="Library assignment",
                modality="written",
                rubric=[],
                created_at=None,
            )
            db.add(assignment)
            db.flush()
            db.add(
                AssignmentQuestionChunk(
                    assignment_id=assignment.id,
                    question_id="q1",
                    order_index=0,
                    question_text="What is 2+2?",
                    answer_text="4",
                )
            )
            db.add(
                AssignmentQuestionChunk(
                    assignment_id=assignment.id,
                    question_id="q2",
                    order_index=1,
                    question_text="What is 3+3?",
                    answer_text="6",
                )
            )
            sub = Submission(assignment_id=assignment.id, student_id=None, status="queued")
            db.add(sub)
            db.flush()
            db.add(SubmissionArtifact(submission_id=sub.id, kind="txt", object_key="work.txt"))
            db.commit()
            sub_id = sub.id
        finally:
            db.close()

        captured: dict = {}

        def _fake_pipeline(cfg, assign_for_prompt, artifacts, **kwargs):
            captured.update(kwargs)
            return {"overall": {}, "criteria": [], "question_grades": []}

        with (
            patch("app.tasks.get_object_bytes", return_value=b"four\nsix\n"),
            patch("app.tasks.run_db_submission_multimodal_pipeline", side_effect=_fake_pipeline),
            patch("app.tasks.minio_client"),
        ):
            tasks.grade_submission.run(sub_id)

        hints = captured.get("modality_hints_extra")
        self.assertIsNotNone(hints)
        pairs = hints["prechunked_qa_pairs"]
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0], {"question_id": "q1", "question_text": "What is 2+2?", "answer_text": "4"})
        self.assertEqual(pairs[1], {"question_id": "q2", "question_text": "What is 3+3?", "answer_text": "6"})

    def test_grading_report_question_text_prefers_stored_assignment_chunk(self):
        """
        The MinIO grading report's ``question_grades[].question_payload.question`` should show
        the exact ``AssignmentQuestionChunk.question_text`` saved during Assignment Creation, even
        when this submission's own chunking pass produced different (e.g. re-labeled) text for the
        same ``question_id`` — see ``app.grading.multimodal.grading_report.report_question_grades_rows``.
        """
        from app import tasks
        from app.database.init_db import SessionLocal
        from app.database.models import (
            Assignment,
            AssignmentQuestionChunk,
            Submission,
            SubmissionArtifact,
        )

        db = SessionLocal()
        try:
            assignment = Assignment(
                course_id=None, title="Library assignment", modality="written", rubric=[], created_at=None
            )
            db.add(assignment)
            db.flush()
            db.add(
                AssignmentQuestionChunk(
                    assignment_id=assignment.id,
                    question_id="q1",
                    order_index=0,
                    question_text="What is 2+2?",
                    answer_text="4",
                )
            )
            sub = Submission(assignment_id=assignment.id, student_id=None, status="queued")
            db.add(sub)
            db.flush()
            db.add(SubmissionArtifact(submission_id=sub.id, kind="txt", object_key="work.txt"))
            db.commit()
            sub_id = sub.id
        finally:
            db.close()

        fake_chunk_id = "s1:a1:prechunked_qa_pairing:0:q1"
        fake_result = {
            "overall": {"score": 1.0, "max_points": 10, "rubric_points_earned": 10},
            "criteria": [],
            "question_grades": [
                {
                    "chunk_id": "pair_1",
                    "_source_chunk_id": fake_chunk_id,
                    "overall": {"score": 1.0, "max_points": 10, "rubric_points_earned": 10, "confidence": 0.9},
                    "criteria": [],
                }
            ],
            "_multimodal_pipeline_audit": {
                "pipeline_audit": {
                    "chunking": [
                        {
                            "chunks": [
                                {
                                    "chunk_id": fake_chunk_id,
                                    "question_id": "q1",
                                    "extracted_text": "four",
                                    "evidence": {
                                        "trio": {
                                            # Deliberately different from the stored assignment
                                            # question text, to prove the override wins.
                                            "question": "Re-derived (possibly drifted) text",
                                            "student_response": "four",
                                        }
                                    },
                                }
                            ]
                        }
                    ]
                }
            },
        }

        captured_report: dict = {}

        class _FakeMinioClient:
            def put_object(self, **kwargs):
                captured_report.update(json.loads(kwargs["Body"].decode("utf-8")))

        with (
            patch("app.tasks.get_object_bytes", return_value=b"four\n"),
            patch(
                "app.tasks.run_db_submission_multimodal_pipeline",
                return_value=dict(fake_result),
            ),
            patch("app.tasks.minio_client", return_value=_FakeMinioClient()),
        ):
            tasks.grade_submission.run(sub_id)

        self.assertEqual(len(captured_report["question_grades"]), 1)
        self.assertEqual(
            captured_report["question_grades"][0]["question_payload"]["question"],
            "What is 2+2?",
        )


class CourseSubmissionResultsRouteTests(ApiRoutesTestCase):
    """
    ``GET /api/submissions/{id}`` + ``GET /api/submissions/{id}/report`` — course/library
    submission-review parity with the standalone autograder's results endpoints (see
    ``app.routes.standalone.standalone_get`` / ``standalone_get_report``).
    """

    def _make_graded_submission(self, *, with_report: bool):
        from app.database.init_db import SessionLocal
        from app.database.models import AIScore, Assignment, Submission

        db = SessionLocal()
        try:
            assignment = Assignment(
                course_id=None, title="PSet 1", modality="written", rubric=[], created_at=None
            )
            db.add(assignment)
            db.flush()
            sub = Submission(
                assignment_id=assignment.id,
                student_id=None,
                status="graded",
                final_score=0.5,
                final_feedback="Nice work overall.",
                grading_report_object_key=(
                    "grading-reports/course/1/1_report.json" if with_report else None
                ),
            )
            db.add(sub)
            db.flush()
            db.add(
                AIScore(
                    submission_id=sub.id,
                    criterion="Correctness",
                    score=5,
                    confidence=0.9,
                    rationale="Looks right.",
                    evidence={"trio": {"question": "What is 2+2?", "student_response": "4"}},
                    model="anthropic:claude-opus-4-7",
                )
            )
            db.commit()
            sub_id = sub.id
        finally:
            db.close()
        return sub_id

    def test_get_submission_falls_back_to_flat_ai_scores_without_report(self):
        sub_id = self._make_graded_submission(with_report=False)
        res = self.client.get(f"/api/submissions/{sub_id}")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["assignment_title"], "PSet 1")
        self.assertEqual(body["question_grades"], [])
        self.assertEqual(len(body["ai_scores"]), 1)
        score_row = body["ai_scores"][0]
        self.assertEqual(score_row["question"], "What is 2+2?")
        self.assertEqual(score_row["student_evidence"], "4")
        self.assertIsNone(body["grading_report_object_key"])
        # final_score 0.5 (a 0..1 fraction) is normalized to a percentage like standalone does.
        self.assertEqual(body["final_score"], 50.0)

    def test_get_submission_enriches_with_question_grades_from_report(self):
        sub_id = self._make_graded_submission(with_report=True)
        report = {
            "question_grades": [
                {
                    "chunk_id": "chunk-1",
                    "overall": {"score": 1.0, "max_points": 10, "rubric_points_earned": 8, "confidence": 0.9},
                    "question_payload": {"question": "What is 2+2?", "student_response": "4"},
                    "criteria": [{"criterion": "Correctness", "score": 8, "max_points": 10}],
                }
            ]
        }
        with patch(
            "app.grading.multimodal.grading_report_view.get_object_bytes",
            return_value=json.dumps(report).encode("utf-8"),
        ):
            res = self.client.get(f"/api/submissions/{sub_id}")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(len(body["question_grades"]), 1)
        self.assertEqual(body["max_points"], 10.0)
        self.assertEqual(body["rubric_points_earned"], 8.0)
        self.assertEqual(body["final_score"], 80.0)

    def test_get_submission_report_returns_presigned_url(self):
        sub_id = self._make_graded_submission(with_report=True)
        with patch(
            "app.routes.submissions.get_presigned_url",
            return_value="https://minio.example/report.json",
        ) as mock_url:
            res = self.client.get(f"/api/submissions/{sub_id}/report")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["download_url"], "https://minio.example/report.json")
        mock_url.assert_called_once()

    def test_get_submission_report_404_without_report(self):
        sub_id = self._make_graded_submission(with_report=False)
        res = self.client.get(f"/api/submissions/{sub_id}/report")
        self.assertEqual(res.status_code, 404)


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
        detail_body = detail.json()
        chunks = detail_body["chunks"]
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["question_text"], "What is 2+2?")
        self.assertFalse(chunks[0]["is_edited"])
        self.assertEqual(detail_body["blank_assignment_text"], "1) What is 2+2?")
        self.assertEqual(detail_body["answer_key_text"], "1) 4")

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

    def test_material_view_notebook_spreadsheet_and_missing_kind(self):
        with patch(
            "app.routes.assignment_library.presigned_put_url",
            return_value="https://minio.example/fake-presigned-url",
        ):
            start = self.client.post(
                "/api/assignment-library/start",
                json={
                    "title": "Notebook assignment",
                    "files": [
                        {"filename": "blank.ipynb", "content_type": "application/x-ipynb+json", "artifact_kind": "blank_assignment"},
                        {"filename": "key.pdf", "content_type": "application/pdf", "artifact_kind": "answer_key"},
                        {"filename": "rubric.json", "content_type": "application/json", "artifact_kind": "rubric"},
                    ],
                },
            )
        assignment_id = start.json()["assignment_id"]

        notebook_bytes = json.dumps(
            {"cells": [{"cell_type": "markdown", "source": ["# Q1"]}, {"cell_type": "code", "source": "print(1)"}]}
        ).encode("utf-8")
        with (
            patch("app.routes.assignment_library.get_presigned_url", return_value="https://minio.example/download"),
            patch("app.routes.assignment_library.get_object_bytes", return_value=notebook_bytes),
        ):
            res = self.client.get(f"/api/assignment-library/{assignment_id}/materials/blank_assignment/view")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["download_url"], "https://minio.example/download")
        self.assertEqual(body["view"]["type"], "notebook")
        self.assertEqual(len(body["view"]["cells"]), 2)

        res_bad_kind = self.client.get(f"/api/assignment-library/{assignment_id}/materials/rubric/view")
        self.assertEqual(res_bad_kind.status_code, 400)

        res_missing = self.client.get("/api/assignment-library/999999/materials/blank_assignment/view")
        self.assertEqual(res_missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
