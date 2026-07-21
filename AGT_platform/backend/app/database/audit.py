from .init_db import SessionLocal
from .models import AuditLog

def log_event(actor_user_id, action, target_type, target_id, event_metadata=None):
    """     
    This function logs an event to the audit log.
    
    Parameters:
        actor_user_id: The ID of the user who performed the action.
        action: The action that was performed.
        target_type: The type of the target of the action.
        target_id: The ID of the target of the action.
        event_metadata: The metadata of the event.
    """
    db = SessionLocal()
    try:
        db.add(AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            event_metadata=event_metadata or {}
        ))
        db.commit()
    finally:
        db.close()
