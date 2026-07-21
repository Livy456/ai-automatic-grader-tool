from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import log_event
from app.deps import get_db, require_role
from app.database.models import Assignment, AuditLog, Course, Enrollment, IssuedJwt, User

router = APIRouter()


@router.get("/api/admin/users")
def users(db: Session = Depends(get_db), user: dict = Depends(require_role("admin"))):
    items = db.query(User).order_by(User.id.desc()).all()
    return [{"id": u.id, "email": u.email, "name": u.name, "role": u.role} for u in items]


@router.post("/api/admin/users/{user_id}/role")
def set_role(
    user_id: int,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    role = payload.get("role")
    if role not in ["student", "teacher", "admin"]:
        raise HTTPException(400, "invalid role")

    u = db.query(User).get(user_id)
    if not u:
        raise HTTPException(404, "not found")
    u.role = role
    for tok in db.query(IssuedJwt).filter_by(user_id=user_id).all():
        tok.revoked_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.get("/api/admin/courses")
def list_courses(db: Session = Depends(get_db), user: dict = Depends(require_role("admin"))):
    courses = db.query(Course).order_by(Course.id.desc()).all()
    count_rows = (
        db.query(Enrollment.course_id, func.count(Enrollment.id))
        .group_by(Enrollment.course_id)
        .all()
    )
    count_map = {cid: int(n) for cid, n in count_rows}
    return [
        {
            "id": c.id,
            "code": c.code,
            "title": c.title,
            "description": c.description,
            "enrollment_count": count_map.get(c.id, 0),
        }
        for c in courses
    ]


@router.post("/api/admin/courses", status_code=201)
def create_course(
    payload: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    code = (payload.get("code") or "").strip()
    title = (payload.get("title") or "").strip()
    if not code or not title:
        raise HTTPException(400, "code and title required")
    desc = payload.get("description")
    description = desc.strip() if isinstance(desc, str) else None
    c = Course(code=code, title=title, description=description)
    db.add(c)
    db.commit()
    db.refresh(c)
    log_event(
        user["id"],
        "CREATE_COURSE",
        "Course",
        c.id,
        {"code": c.code, "title": c.title},
    )
    return {"id": c.id}


@router.get("/api/admin/courses/{course_id}/enrollments")
def list_course_enrollments(
    course_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    c = db.query(Course).get(course_id)
    if not c:
        raise HTTPException(404, "not found")
    rows = (
        db.query(Enrollment, User)
        .join(User, Enrollment.user_id == User.id)
        .filter(Enrollment.course_id == course_id)
        .all()
    )
    return [
        {
            "enrollment_id": e.id,
            "user_id": u.id,
            "email": u.email,
            "name": u.name or "",
            "role": e.role,
        }
        for e, u in rows
    ]


@router.post("/api/admin/enrollments", status_code=201)
def create_enrollment(
    payload: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    role = payload.get("role")
    if role not in ("student", "teacher"):
        raise HTTPException(400, "role must be student or teacher")
    try:
        course_id = int(payload["course_id"])
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "course_id and user_id required")

    if not db.query(Course).get(course_id):
        raise HTTPException(404, "course not found")
    if not db.query(User).get(user_id):
        raise HTTPException(404, "user not found")

    e = Enrollment(course_id=course_id, user_id=user_id, role=role)
    try:
        db.add(e)
        db.commit()
        db.refresh(e)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "user already enrolled in this course")

    log_event(
        user["id"],
        "ENROLL_USER",
        "Enrollment",
        e.id,
        {"course_id": e.course_id, "user_id": e.user_id, "role": e.role},
    )
    return {"id": e.id}


@router.delete("/api/admin/enrollments/{enrollment_id}")
def delete_enrollment(
    enrollment_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    e = db.query(Enrollment).get(enrollment_id)
    if not e:
        raise HTTPException(404, "not found")
    meta = {"course_id": e.course_id, "user_id": e.user_id}
    db.delete(e)
    db.commit()
    log_event(user["id"], "REMOVE_ENROLLMENT", "Enrollment", enrollment_id, meta)
    return {"ok": True}


@router.get("/api/admin/assignments")
def list_assignments(db: Session = Depends(get_db), user: dict = Depends(require_role("admin"))):
    items = (
        db.query(Assignment)
        .filter(Assignment.course_id.isnot(None))
        .order_by(Assignment.id.desc())
        .limit(200)
        .all()
    )
    return [
        {"id": a.id, "course_id": a.course_id, "title": a.title, "modality": a.modality}
        for a in items
    ]


@router.get("/api/admin/audit")
def audit(db: Session = Depends(get_db), user: dict = Depends(require_role("admin"))):
    logs = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(300).all()
    return [
        {
            "time": l.created_at.isoformat(),
            "actor_user_id": l.actor_user_id,
            "action": l.action,
            "target_type": l.target_type,
            "target_id": l.target_id,
            "metadata": l.event_metadata,
        }
        for l in logs
    ]
