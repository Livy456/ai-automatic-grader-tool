from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import log_event
from app.deps import get_current_user, get_db, require_role
from app.database.models import Assignment, Course, Enrollment, User

router = APIRouter()

MODALITIES = frozenset({"code", "written", "notebook", "video", "image"})


def _parse_due_date(raw):
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        try:
            # ISO 8601 from datetime-local or full ISO
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _user_can_view_course(db: Session, user_id: int, role: str, course_id: int) -> bool:
    if role == "admin":
        return True
    return (
        db.query(Enrollment)
        .filter_by(user_id=user_id, course_id=course_id)
        .first()
        is not None
    )


def _user_is_course_teacher(db: Session, user_id: int, course_id: int) -> bool:
    row = (
        db.query(Enrollment)
        .filter_by(user_id=user_id, course_id=course_id, role="teacher")
        .first()
    )
    return row is not None


def _normalize_rubric(raw):
    if raw is None:
        return []
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


@router.get("/api/courses")
def list_courses(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    role = user["role"]
    user_id = user["id"]
    if role == "admin":
        courses = db.query(Course).order_by(Course.id).all()
        return [
            {"id": c.id, "code": c.code, "title": c.title, "enrollment_role": None}
            for c in courses
        ]
    enrollments = db.query(Enrollment).filter_by(user_id=user_id).all()
    by_course = {e.course_id: e.role for e in enrollments}
    course_ids = list(by_course.keys())
    if not course_ids:
        return []
    courses = db.query(Course).filter(Course.id.in_(course_ids)).order_by(Course.id).all()
    return [
        {
            "id": c.id,
            "code": c.code,
            "title": c.title,
            "enrollment_role": by_course.get(c.id),
        }
        for c in courses
    ]


@router.get("/api/courses/{course_id}")
def get_course(
    course_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    user_id = user["id"]
    role = user["role"]
    c = db.query(Course).get(course_id)
    if not c:
        raise HTTPException(404, "not found")
    if not _user_can_view_course(db, user_id, role, course_id):
        raise HTTPException(403, "forbidden")
    rows = (
        db.query(Enrollment, User)
        .join(User, Enrollment.user_id == User.id)
        .filter(Enrollment.course_id == course_id)
        .all()
    )
    enrollments = [
        {"user_id": u.id, "email": u.email, "name": u.name or "", "role": e.role}
        for e, u in rows
    ]
    return {"id": c.id, "code": c.code, "title": c.title, "enrollments": enrollments}


def _serialize_assignment(a: Assignment):
    created = a.created_at.isoformat() if a.created_at else None
    due = a.due_date.isoformat() if getattr(a, "due_date", None) else None
    return {
        "id": a.id,
        "course_id": a.course_id,
        "title": a.title,
        "description": a.description or "",
        "modality": a.modality,
        "rubric": a.rubric if a.rubric is not None else [],
        "due_date": due,
        "created_at": created,
    }


@router.get("/api/courses/{course_id}/assignments")
def list_assignments(
    course_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    user_id = user["id"]
    role = user["role"]
    c = db.query(Course).get(course_id)
    if not c:
        raise HTTPException(404, "not found")
    if not _user_can_view_course(db, user_id, role, course_id):
        raise HTTPException(403, "forbidden")
    items = (
        db.query(Assignment).filter_by(course_id=course_id).order_by(Assignment.id).all()
    )
    return [_serialize_assignment(a) for a in items]


@router.post("/api/courses/{course_id}/assignments", status_code=201)
def create_assignment(
    course_id: int,
    payload: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("teacher", "admin")),
):
    user_id = user["id"]
    role = user["role"]
    c = db.query(Course).get(course_id)
    if not c:
        raise HTTPException(404, "course not found")
    if role != "admin" and not _user_is_course_teacher(db, user_id, course_id):
        raise HTTPException(403, "forbidden")

    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise HTTPException(400, "title is required")
    title = title.strip()
    if len(title) > 255:
        raise HTTPException(400, "title too long")

    modality = payload.get("modality")
    if modality not in MODALITIES:
        raise HTTPException(400, "invalid modality")

    description = payload.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise HTTPException(400, "invalid description")

    rubric = _normalize_rubric(payload.get("rubric", []))
    if rubric is None:
        raise HTTPException(400, "invalid rubric")

    due_date = _parse_due_date(payload.get("due_date"))

    a = Assignment(
        course_id=course_id,
        title=title,
        description=description,
        modality=modality,
        rubric=rubric,
        created_at=datetime.utcnow(),
        due_date=due_date,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    log_event(
        user["id"],
        "CREATE_ASSIGNMENT",
        "Assignment",
        a.id,
        {"course_id": course_id, "title": a.title},
    )
    return {"id": a.id, "title": a.title, "course_id": course_id}


@router.put("/api/courses/{course_id}/assignments/{assignment_id}")
@router.patch("/api/courses/{course_id}/assignments/{assignment_id}")
def update_assignment(
    course_id: int,
    assignment_id: int,
    payload: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("teacher", "admin")),
):
    user_id = user["id"]
    role = user["role"]
    c = db.query(Course).get(course_id)
    if not c:
        raise HTTPException(404, "course not found")
    if role != "admin" and not _user_is_course_teacher(db, user_id, course_id):
        raise HTTPException(403, "forbidden")

    a = db.query(Assignment).get(assignment_id)
    if not a or a.course_id != course_id:
        raise HTTPException(404, "not found")

    if "title" in payload:
        title = payload["title"]
        if not isinstance(title, str) or not title.strip():
            raise HTTPException(400, "invalid title")
        if len(title.strip()) > 255:
            raise HTTPException(400, "title too long")
        a.title = title.strip()

    if "description" in payload:
        d = payload["description"]
        if d is not None and not isinstance(d, str):
            raise HTTPException(400, "invalid description")
        a.description = d if isinstance(d, str) else ""

    if "modality" in payload:
        if payload["modality"] not in MODALITIES:
            raise HTTPException(400, "invalid modality")
        a.modality = payload["modality"]

    if "rubric" in payload:
        rubric = _normalize_rubric(payload["rubric"])
        if rubric is None:
            raise HTTPException(400, "invalid rubric")
        a.rubric = rubric

    if "due_date" in payload:
        a.due_date = _parse_due_date(payload["due_date"])

    db.commit()
    db.refresh(a)
    log_event(
        user["id"],
        "UPDATE_ASSIGNMENT",
        "Assignment",
        a.id,
        {"course_id": course_id},
    )
    return {"id": a.id, "title": a.title}
