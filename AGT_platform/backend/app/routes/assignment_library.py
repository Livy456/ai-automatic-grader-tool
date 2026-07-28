"""
Assignment creation ("Assignment Library"): a teacher/admin uploads a blank assignment
template, an answer key, and a rubric to create a reusable, course-independent
``Assignment`` row (``course_id`` is ``NULL``), plus one ``AssignmentAttachment`` row per
uploaded file.

Mirrors the standalone autograder's upload-context flow (see ``app/routes/standalone.py``):
presigned MinIO PUT URLs are handed back from ``start``, the browser PUTs file bytes directly
to object storage, then ``finalize`` verifies every object landed. Unlike the standalone
autograder, nothing is queued for grading here. Instead, ``finalize`` runs two small agents so
teachers land on an editable question bank instead of a blank page:

1. :mod:`app.grading.parsing.assignment_context_parser` ("parsing agent") — deterministically
   extracts plain text from the uploaded blank template + answer key bytes.
2. :mod:`app.grading.chunking.assignment_qa_chunker` ("chunking agent") — one LLM call that
   pairs each question in the blank template with its answer-key reference, isolated from
   every other question.

The resulting pairs are persisted as editable ``AssignmentQuestionChunk`` rows; teachers can
edit/add/remove them on the review page and re-save via ``PUT .../chunks``, and revisit any
past assignment from the history list (``GET /api/assignment-library``).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload
from werkzeug.utils import secure_filename

from app.config import Config
from app.database.audit import log_event
from app.database.models import Assignment, AssignmentAttachment, AssignmentQuestionChunk
from app.deps import get_db, require_role
from app.database.storage import get_object_bytes, get_presigned_url, object_exists, presigned_put_url
from app.grading.chunking.assignment_qa_chunker import try_chunk_assignment_qa_pairs
from app.grading.parsing.assignment_context_parser import parse_assignment_context
from app.grading.parsing.original_view import build_original_view

router = APIRouter()

_MODALITIES = frozenset({"code", "written", "notebook", "video", "image"})
_ATTACHMENT_KINDS = ("blank_assignment", "answer_key", "rubric")
_VIEWABLE_KINDS = ("blank_assignment", "answer_key")
_SAFE_KIND_DIR = {
    "blank_assignment": "blank-templates",
    "answer_key": "answer-keys",
    "rubric": "rubrics",
}
_MAX_FILES = 10
_MAX_TITLE_LEN = 255


def _normalize_rubric(raw: Any) -> list[dict[str, Any]] | None:
    """Same shape as ``routes/courses.py``: ``[{"criterion": str, "max_score": float}, ...]``."""
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        crit = item.get("criterion")
        mx = item.get("max_score")
        if not isinstance(crit, str) or not crit.strip():
            return None
        try:
            mx_num = float(mx)
        except (TypeError, ValueError):
            return None
        out.append({"criterion": crit.strip(), "max_score": mx_num})
    return out


def _serialize_assignment(a: Assignment) -> dict[str, Any]:
    return {
        "id": a.id,
        "title": a.title,
        "description": a.description or "",
        "modality": a.modality,
        "rubric": a.rubric if a.rubric is not None else [],
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "blank_assignment_text": a.blank_assignment_text or "",
        "answer_key_text": a.grader_answer_key_text or "",
    }


def _serialize_chunk(c: AssignmentQuestionChunk) -> dict[str, Any]:
    return {
        "id": c.id,
        "question_id": c.question_id,
        "order_index": c.order_index,
        "question_text": c.question_text,
        "answer_text": c.answer_text,
        "is_edited": c.is_edited,
    }


def _get_library_assignment(db: Session, assignment_id: int) -> Assignment:
    a = db.query(Assignment).filter_by(id=assignment_id, course_id=None).one_or_none()
    if not a:
        raise HTTPException(404, "not found")
    return a


@router.post("/api/assignment-library/start")
def start_assignment_library_entry(
    body: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("teacher", "admin")),
):
    """Create the ``Assignment`` (+ ``AssignmentAttachment`` rows) and return presigned PUT URLs."""
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    if len(title) > _MAX_TITLE_LEN:
        raise HTTPException(400, "title too long")

    description = body.get("description")
    description = description.strip() if isinstance(description, str) else ""

    modality = (body.get("modality") or "written").strip().lower()
    if modality not in _MODALITIES:
        raise HTTPException(400, "invalid modality")

    rubric_text = (body.get("rubric_text") or "").strip() or None
    answer_key_text = (body.get("answer_key_text") or "").strip() or None
    grading_instructions = (body.get("grading_instructions") or "").strip() or None

    files = body.get("files")
    if not files or not isinstance(files, list):
        raise HTTPException(400, "files[] required")
    if len(files) > _MAX_FILES:
        raise HTTPException(400, f"at most {_MAX_FILES} files")

    cfg = Config()
    a = Assignment(
        course_id=None,
        title=title,
        description=description,
        modality=modality,
        rubric=[],
        created_at=datetime.utcnow(),
        grader_rubric_text=rubric_text,
        grader_answer_key_text=answer_key_text,
        grader_instructions=grading_instructions,
    )
    db.add(a)
    db.flush()

    uploads_out = []
    for spec in files:
        raw_kind = (spec.get("artifact_kind") or spec.get("kind") or "").strip().lower()
        if raw_kind not in _ATTACHMENT_KINDS:
            db.rollback()
            raise HTTPException(
                400,
                "each file must set artifact_kind to one of: " + ", ".join(_ATTACHMENT_KINDS),
            )
        raw_name = (spec.get("filename") or "").strip()
        filename = secure_filename(raw_name)
        if not filename:
            continue
        content_type = (spec.get("content_type") or "application/octet-stream").strip()
        key = (
            f"assignments/by-id/{a.id}/materials/{_SAFE_KIND_DIR[raw_kind]}/"
            f"{uuid.uuid4().hex}_{filename}"
        )
        row = AssignmentAttachment(
            assignment_id=a.id,
            kind=raw_kind,
            object_key=key,
            filename=filename,
            uploaded_by_id=user.get("id"),
        )
        db.add(row)
        db.flush()
        url = presigned_put_url(cfg, key, content_type)
        uploads_out.append(
            {
                "artifact_id": row.id,
                "object_key": key,
                "upload_url": url,
                "content_type": content_type,
                "kind": raw_kind,
            }
        )

    if not uploads_out:
        db.rollback()
        raise HTTPException(400, "no valid files")

    db.commit()
    db.refresh(a)
    log_event(
        user["id"],
        "CREATE_ASSIGNMENT_LIBRARY_ENTRY",
        "Assignment",
        a.id,
        {"n_files": len(uploads_out), "title": a.title},
    )
    return {
        "assignment_id": a.id,
        "status": "uploading",
        "uploads": uploads_out,
    }


@router.post("/api/assignment-library/{assignment_id}/finalize")
def finalize_assignment_library_entry(
    assignment_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("teacher", "admin")),
):
    """
    Verify every uploaded object landed in MinIO, best-effort parse a JSON rubric file into
    ``Assignment.rubric``, then run the parsing + chunking agents to seed an editable question
    bank (``AssignmentQuestionChunk`` rows) from the blank template + answer key.
    """
    cfg = Config()
    a = (
        db.query(Assignment)
        .filter_by(id=assignment_id, course_id=None)
        .with_for_update()
        .one_or_none()
    )
    if not a:
        raise HTTPException(404, "not found")

    attachments = db.query(AssignmentAttachment).filter_by(assignment_id=assignment_id).all()
    kinds = {att.kind for att in attachments}
    missing = [k for k in _ATTACHMENT_KINDS if k not in kinds]
    if missing:
        raise HTTPException(
            400,
            {
                "error": "missing required context",
                "detail": (
                    "Assignment creation requires a blank assignment template, an answer "
                    "key, and a rubric before it can be finalized."
                ),
                "missing": missing,
            },
        )

    for att in attachments:
        if not object_exists(cfg, att.object_key):
            raise HTTPException(400, f"missing object: {att.object_key}")

    by_kind = {att.kind: att for att in attachments}
    rubric_att = by_kind.get("rubric")
    if rubric_att and rubric_att.filename.lower().endswith(".json"):
        try:
            raw = get_object_bytes(cfg, rubric_att.object_key)
            normalized = _normalize_rubric(json.loads(raw.decode("utf-8")))
            if normalized:
                a.rubric = normalized
        except Exception:
            pass  # Keep a.rubric == [] and rely on the stored file if it doesn't parse.

    chunking_status = "skipped"
    blank_att = by_kind.get("blank_assignment")
    answer_key_att = by_kind.get("answer_key")
    if blank_att is not None:
        try:
            blank_bytes = get_object_bytes(cfg, blank_att.object_key)
        except Exception:
            blank_bytes = None
        answer_key_bytes = None
        if answer_key_att is not None:
            try:
                answer_key_bytes = get_object_bytes(cfg, answer_key_att.object_key)
            except Exception:
                answer_key_bytes = None

        parsed_context = parse_assignment_context(
            blank_bytes=blank_bytes,
            blank_filename=blank_att.filename,
            answer_key_bytes=answer_key_bytes,
            answer_key_filename=answer_key_att.filename if answer_key_att else "",
        )
        answer_key_text = parsed_context.answer_key_text or (a.grader_answer_key_text or "")
        # Persist the extracted plaintext so the review page can show the full original
        # documents, and so later grading (see app.tasks) has the answer key text even when it
        # was only supplied as a file (not pasted at creation time).
        if parsed_context.blank_text:
            a.blank_assignment_text = parsed_context.blank_text
        if parsed_context.answer_key_text:
            a.grader_answer_key_text = parsed_context.answer_key_text
        pairs = try_chunk_assignment_qa_pairs(
            blank_text=parsed_context.blank_text,
            answer_key_text=answer_key_text,
            cfg=cfg,
        )
        if pairs:
            for i, pair in enumerate(pairs):
                db.add(
                    AssignmentQuestionChunk(
                        assignment_id=a.id,
                        question_id=pair["question_id"],
                        order_index=i,
                        question_text=pair["question"],
                        answer_text=pair["answer"],
                        is_edited=False,
                    )
                )
            chunking_status = "ok"
        else:
            chunking_status = "no_pairs"

    db.commit()
    log_event(user["id"], "FINALIZE_ASSIGNMENT_LIBRARY_ENTRY", "Assignment", a.id, {})
    return _serialize_assignment(a) | {"status": "created", "chunking_status": chunking_status}


@router.get("/api/assignment-library")
def list_assignment_library_entries(
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("teacher", "admin")),
):
    """History: most recent course-independent assignments created via this flow."""
    items = (
        db.query(Assignment)
        .filter(Assignment.course_id.is_(None))
        .order_by(Assignment.created_at.desc())
        .limit(50)
        .all()
    )
    return [_serialize_assignment(a) for a in items]


@router.get("/api/assignment-library/{assignment_id}")
def get_assignment_library_entry(
    assignment_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("teacher", "admin")),
):
    """Assignment detail + its (possibly teacher-edited) question/answer chunks, in order."""
    a = (
        db.query(Assignment)
        .options(selectinload(Assignment.question_chunks))
        .filter_by(id=assignment_id, course_id=None)
        .one_or_none()
    )
    if not a:
        raise HTTPException(404, "not found")
    chunks = sorted(a.question_chunks, key=lambda c: c.order_index)
    return _serialize_assignment(a) | {"chunks": [_serialize_chunk(c) for c in chunks]}


@router.get("/api/assignment-library/{assignment_id}/materials/{kind}/view")
def get_assignment_material_view(
    assignment_id: int,
    kind: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("teacher", "admin")),
):
    """
    Original-form view of the uploaded blank template / answer key, for the review page's
    "Blank Assignment" / "Answer Key" tabs — see :func:`app.grading.parsing.original_view.build_original_view`
    for the notebook / spreadsheet / PDF / plaintext shapes this can return.
    """
    if kind not in _VIEWABLE_KINDS:
        raise HTTPException(400, "kind must be one of: " + ", ".join(_VIEWABLE_KINDS))
    a = _get_library_assignment(db, assignment_id)

    att = (
        db.query(AssignmentAttachment)
        .filter_by(assignment_id=a.id, kind=kind)
        .order_by(AssignmentAttachment.created_at.desc())
        .first()
    )
    if not att:
        raise HTTPException(404, f"no {kind} uploaded for this assignment")

    cfg = Config()
    download_url = get_presigned_url(cfg, att.object_key, method="GET", expires=3600)
    try:
        data = get_object_bytes(cfg, att.object_key)
    except Exception:
        data = b""
    view = build_original_view(data, att.filename) if data else {"type": "unsupported"}
    return {"filename": att.filename, "download_url": download_url, "view": view}


@router.put("/api/assignment-library/{assignment_id}/chunks")
def save_assignment_library_chunks(
    assignment_id: int,
    body: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("teacher", "admin")),
):
    """
    Replace this assignment's question/answer chunks with the given (teacher-edited) list —
    the review page always sends its full current set, so this is a straightforward
    update-existing / insert-new / delete-missing sync rather than a partial patch.
    """
    a = _get_library_assignment(db, assignment_id)

    raw_chunks = body.get("chunks")
    if not isinstance(raw_chunks, list):
        raise HTTPException(400, "chunks[] required")

    existing_by_id = {c.id: c for c in db.query(AssignmentQuestionChunk).filter_by(assignment_id=a.id).all()}
    keep_ids: set[int] = set()

    for i, item in enumerate(raw_chunks):
        if not isinstance(item, dict):
            raise HTTPException(400, f"invalid chunk at index {i}")
        question_text = str(item.get("question_text") or "").strip()
        answer_text = str(item.get("answer_text") or "").strip()
        question_id = str(item.get("question_id") or "").strip() or f"q{i + 1}"
        chunk_id = item.get("id")

        if isinstance(chunk_id, int) and chunk_id in existing_by_id:
            row = existing_by_id[chunk_id]
            changed = (
                row.question_id != question_id
                or row.question_text != question_text
                or row.answer_text != answer_text
            )
            row.question_id = question_id[:120]
            row.order_index = i
            row.question_text = question_text
            row.answer_text = answer_text
            if changed:
                row.is_edited = True
            keep_ids.add(row.id)
        else:
            row = AssignmentQuestionChunk(
                assignment_id=a.id,
                question_id=question_id[:120],
                order_index=i,
                question_text=question_text,
                answer_text=answer_text,
                is_edited=True,
            )
            db.add(row)
            db.flush()
            keep_ids.add(row.id)

    for cid, row in existing_by_id.items():
        if cid not in keep_ids:
            db.delete(row)

    db.commit()
    log_event(
        user["id"],
        "SAVE_ASSIGNMENT_LIBRARY_CHUNKS",
        "Assignment",
        a.id,
        {"n_chunks": len(raw_chunks)},
    )
    chunks = (
        db.query(AssignmentQuestionChunk)
        .filter_by(assignment_id=a.id)
        .order_by(AssignmentQuestionChunk.order_index)
        .all()
    )
    return {"assignment_id": a.id, "chunks": [_serialize_chunk(c) for c in chunks]}
