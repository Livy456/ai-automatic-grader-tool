from .extensions import SessionLocal
from .models import AuditLog

def log_event(actor_user_id, action, target_type, target_id, metadata=None):
    db = SessionLocal()
    try:
        db.add(AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata or {}
        ))
        db.commit()
    finally:
        db.close()
