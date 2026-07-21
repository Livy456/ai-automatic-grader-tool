from fastapi import APIRouter

router = APIRouter()


@router.get("/api/health")
def health():
    return {"status": "ok"}


@router.get("/api/healthz")
def healthz():
    """ALB-friendly alias; same payload as /api/health."""
    return {"status": "ok"}
