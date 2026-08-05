import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
load_dotenv(BASE_DIR / ".env.local")
load_dotenv(BASE_DIR / ".env", override=True)


def _env_str(key: str) -> str:
    """Always use empty-string default with os.getenv (never None)."""

    return os.getenv(key, "")


def _env_int(key: str, *, default: int) -> int:
    """Always use empty-string default with os.getenv (never None)."""
    v = os.getenv(key, "").strip()
    return default if not v else int(v, 10)

def _env_float(key: str, *, default: float) -> float:
    """Always use empty-string default with os.getenv (never None)."""
    v = os.getenv(key, "").strip()
    return default if not v else float(v)

def _env_bool(key: str) -> bool:
    """Always use empty-string default with os.getenv (never None)."""
    return os.getenv(key, "").strip().lower() == "true"

def _refresh_cookie_secure() -> bool:
    """True when refresh cookies must use Secure. Mirrors session cookies unless overridden."""
    raw = os.getenv("REFRESH_COOKIE_SECURE", "").strip().lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    return _env_bool("SESSION_COOKIE_SECURE")

class Config:
    """This class is used to store the configuration for the application."""

    SECRET_KEY = _env_str("SECRET_KEY")
    # Host port for `python -m app.main` only. Default 5000; raise if Docker/backend already binds 5000.
    FLASK_PORT = _env_int("FLASK_PORT", default=5000) # update this later to FastAPI port
    
    # update this later for handling jwt tokens for authentication and authorization
    # Short-lived API bearer (JWT). If JWT_ACCESS_EXPIRATION_SECONDS is unset, JWT_EXPIRATION_SECONDS is used (legacy).
    JWT_ACCESS_EXPIRATION_SECONDS = (
        _env_int("JWT_ACCESS_EXPIRATION_SECONDS", default=0)
        or _env_int("JWT_EXPIRATION_SECONDS", default=15 * 60)
    )
    # Same value as JWT_ACCESS_EXPIRATION_SECONDS (legacy env name used in ops docs).
    JWT_EXPIRATION_SECONDS = JWT_ACCESS_EXPIRATION_SECONDS
    # Long-lived refresh (HttpOnly cookie); used to mint new access tokens without re-login.
    JWT_REFRESH_EXPIRATION_SECONDS = _env_int(
        "JWT_REFRESH_EXPIRATION_SECONDS", default=7 * 24 * 3600
    )

    # update this later so that there is no refresh tokens, might pivot from using them
    REFRESH_TOKEN_COOKIE_NAME = _env_str("REFRESH_TOKEN_COOKIE_NAME").strip() or "refresh_token"
    REFRESH_COOKIE_SECURE = _refresh_cookie_secure()
    _rss = _env_str("REFRESH_COOKIE_SAMESITE").strip().lower()
    REFRESH_COOKIE_SAMESITE = _rss if _rss in ("lax", "strict", "none") else "lax"
    



    DATABASE_URL = _env_str("DATABASE_URL")


    REDIS_URL = _env_str("REDIS_URL")
    FRONTEND_BASE_URL = _env_str("FRONTEND_BASE_URL")

    # Browser-reachable API origin for OAuth redirect_uri (no path, no trailing slash).
    PUBLIC_API_URL = _env_str("PUBLIC_API_URL").strip().rstrip("/")

    # OAuth (Authlib) would store CSRF state in session cookies (no OAuth router is currently
    # Set True in production when users only hit the API over HTTPS (e.g. .env.production).
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE")

    # no longer am separating between web and gpu deployments
    # DEPLOYMENT_TIER = _env_str("DEPLOYMENT_TIER").strip().lower() or "web"

    # MinIO object storage.
    MINIO_ENDPOINT = _env_str("MINIO_ENDPOINT").strip().rstrip("/")
        
    MINIO_ACCESS_KEY = (
        _env_str("MINIO_ACCESS_KEY").strip() or "minio"
    )
    MINIO_SECRET_KEY = _env_str("MINIO_SECRET_KEY").strip()
    MINIO_BUCKET = _env_str("MINIO_BUCKET").strip() 
    MINIO_REGION = _env_str("MINIO_REGION").strip()
    MINIO_SECURE = _env_bool("MINIO_SECURE")
    MINIO_ADDRESSING_STYLE = _env_str("MINIO_ADDRESSING_STYLE").strip()
    OBJECT_STORAGE_REGION = _env_str("OBJECT_STORAGE_REGION").strip() or MINIO_REGION

    # Host/port the browser uses for presigned PUT/GET URLs.
    _presign_ep = _env_str("MINIO_PRESIGN_ENDPOINT").strip().rstrip("/")
    if not _presign_ep and MINIO_ENDPOINT == "http://minio:9000":
        _presign_ep = "http://127.0.0.1:9000"
    MINIO_PRESIGN_ENDPOINT = _presign_ep

    # Post-grading JSON reports (optional separate bucket; defaults to uploads bucket).
    MINIO_GRADING_REPORTS_BUCKET = (
        _env_str("MINIO_GRADING_REPORTS_BUCKET").strip()
        or MINIO_BUCKET
    )

    # Prefix for student submission objects (unique keys still include ids/uuids).
    UPLOADS_OBJECT_PREFIX = (
        _env_str("UPLOADS_OBJECT_PREFIX").strip()
        or "assignments/by-id"
    )

    MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", default=1024)
    MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

    # Cap request body size on the API when not using multipart uploads (JSON-only ingress).
    WEB_MAX_BODY_MB = _env_int("WEB_MAX_BODY_MB", default=4)
    WEB_MAX_BODY_BYTES = WEB_MAX_BODY_MB * 1024 * 1024

    MINIO_INLINE_UPLOAD_MAX_BYTES = _env_int(
        "MINIO_INLINE_UPLOAD_MAX_BYTES",
        default=32 * 1024 * 1024,
    )
    MINIO_UPLOAD_SPOOL_MAX_MEMORY_BYTES = _env_int(
        "MINIO_UPLOAD_SPOOL_MAX_MEMORY_BYTES",
        default=16 * 1024 * 1024,
    )

    # Presigned PUT lifetime (browser → MinIO direct upload).
    MINIO_PRESIGN_PUT_EXPIRES = _env_int(
        "MINIO_PRESIGN_PUT_EXPIRES",
        default=3600,
    )

    # Production: false — browser uses presigned object storage only. Dev docker: set true to use multipart to the API.
    ALLOW_FLASK_MULTIPART_UPLOAD = _env_bool("ALLOW_FLASK_MULTIPART_UPLOAD")

    OIDC_CLIENT_ID = _env_str("OIDC_CLIENT_ID")
    OIDC_CLIENT_SECRET = _env_str("OIDC_CLIENT_SECRET")
    OIDC_DISCOVERY_URL = _env_str("OIDC_DISCOVERY_URL")
    OIDC_REDIRECT_URI = _env_str("OIDC_REDIRECT_URI")

    MICROSOFT_CLIENT_ID = _env_str("MICROSOFT_CLIENT_ID")
    MICROSOFT_CLIENT_SECRET = _env_str("MICROSOFT_CLIENT_SECRET")
    # Empty → use "common" OpenID metadata (multi-tenant). Set to your Directory (tenant) ID
    # (GUID) for single-tenant: discovery issuer matches token iss and Authlib iss check passes
    # without workarounds. Also accepts "organizations" or "consumers" as the path segment.
    MICROSOFT_TENANT_ID = _env_str("MICROSOFT_TENANT_ID").strip()

    GOOGLE_CLIENT_ID = _env_str("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = _env_str("GOOGLE_CLIENT_SECRET")

    OPENAI_API_KEY = _env_str("OPENAI_API_KEY")
    # Anthropic (Claude) for optional assignment parsing / QA segmentation (see ``rag_embeddings``).
    ANTHROPIC_API_KEY = _env_str("ANTHROPIC_API_KEY")
    _anth_parse = _env_str("MULTIMODAL_ANTHROPIC_ASSIGNMENT_PARSING").strip().lower()
    if _anth_parse in ("0", "false", "no", "off"):
        MULTIMODAL_ANTHROPIC_ASSIGNMENT_PARSING = "off"
    elif _anth_parse in ("1", "true", "yes", "on"):
        MULTIMODAL_ANTHROPIC_ASSIGNMENT_PARSING = "on"
    else:
        MULTIMODAL_ANTHROPIC_ASSIGNMENT_PARSING = "auto"
    MULTIMODAL_ANTHROPIC_PARSING_MODEL = (
        _env_str("MULTIMODAL_ANTHROPIC_PARSING_MODEL").strip() or "claude-opus-4-7"
    )
    MULTIMODAL_ANTHROPIC_PARSING_MAX_TOKENS = max(
        1024,
        min(_env_int("MULTIMODAL_ANTHROPIC_PARSING_MAX_TOKENS", default=16384), 128000),
    )
    # Claude structured assignment chunking (see claude_structured_assignment_chunker).
    # off (default) | auto | on — ``auto`` / ``on`` run only when ANTHROPIC_API_KEY is set.
    _cc_chunk = _env_str("MULTIMODAL_CLAUDE_STRUCTURED_CHUNKING").strip().lower()
    if _cc_chunk in ("0", "false", "no", "off"):
        MULTIMODAL_CLAUDE_STRUCTURED_CHUNKING = "off"
    elif _cc_chunk in ("1", "true", "yes", "on"):
        MULTIMODAL_CLAUDE_STRUCTURED_CHUNKING = "on"
    elif _cc_chunk == "auto":
        MULTIMODAL_CLAUDE_STRUCTURED_CHUNKING = "auto"
    else:
        MULTIMODAL_CLAUDE_STRUCTURED_CHUNKING = "off"
    MULTIMODAL_CLAUDE_CHUNKING_MODEL = _env_str("MULTIMODAL_CLAUDE_CHUNKING_MODEL").strip()
    MULTIMODAL_CLAUDE_CHUNKING_MAX_TOKENS = max(
        0,
        min(_env_int("MULTIMODAL_CLAUDE_CHUNKING_MAX_TOKENS", default=0), 128000),
    )
    MULTIMODAL_CLAUDE_CHUNKING_MAX_STUDENT_CHARS = max(
        8000,
        min(
            _env_int("MULTIMODAL_CLAUDE_CHUNKING_MAX_STUDENT_CHARS", default=120_000),
            500_000,
        ),
    )
    MULTIMODAL_CLAUDE_CHUNKING_MAX_REF_CHARS = max(
        1000,
        min(
            _env_int("MULTIMODAL_CLAUDE_CHUNKING_MAX_REF_CHARS", default=48_000),
            200_000,
        ),
    )
    # Claude parsing agent: Pydantic-validated single-call decomposition into per-question
    # (question, student_response, answer) triples (see claude_parsing_agent.py). Tried first,
    # ahead of the OpenAI trio frontload and every heuristic chunker, in both the multimodal and
    # standalone grading pipelines (see MultimodalGradingPipeline.run).
    # off | auto (default) | on — auto/on run only when ANTHROPIC_API_KEY is set.
    _claude_agent = _env_str("MULTIMODAL_CLAUDE_PARSING_AGENT").strip().lower()
    if _claude_agent in ("0", "false", "no", "off"):
        MULTIMODAL_CLAUDE_PARSING_AGENT = "off"
    elif _claude_agent in ("1", "true", "yes", "on"):
        MULTIMODAL_CLAUDE_PARSING_AGENT = "on"
    else:
        MULTIMODAL_CLAUDE_PARSING_AGENT = "auto"
    MULTIMODAL_CLAUDE_PARSING_AGENT_MODEL = (
        _env_str("MULTIMODAL_CLAUDE_PARSING_AGENT_MODEL").strip() or "claude-opus-4-7"
    )
    MULTIMODAL_CLAUDE_PARSING_AGENT_MAX_TOKENS = max(
        1024,
        min(_env_int("MULTIMODAL_CLAUDE_PARSING_AGENT_MAX_TOKENS", default=16384), 128000),
    )
    MULTIMODAL_CLAUDE_PARSING_AGENT_MAX_CHARS_PER_SOURCE = max(
        8000,
        min(
            _env_int(
                "MULTIMODAL_CLAUDE_PARSING_AGENT_MAX_CHARS_PER_SOURCE", default=120_000
            ),
            500_000,
        ),
    )
    OPENAI_MODEL = _env_str("OPENAI_MODEL").strip() or "gpt-4o-mini"
    # If true, re-run or arbitrate grading with OpenAI when local model confidence is low.
    ESCALATE_TO_OPENAI = _env_bool("ESCALATE_TO_OPENAI")

    # Multi-LLM grading: two additional models grade alongside the primary.
    # Format: ``openai:<model>`` or a bare OpenAI model id. Empty = disabled (single-model flow).
    GRADING_MODEL_2 = _env_str("GRADING_MODEL_2").strip()
    GRADING_MODEL_3 = _env_str("GRADING_MODEL_3").strip()

    # five-stage pipeline: "legacy" (default) | "staged" | "chunk_entropy"
    GRADING_PIPELINE_MODE = (
        _env_str("GRADING_PIPELINE_MODE").strip().lower() or "legacy"
    )
    REVIEW_CONFIDENCE_THRESHOLD = _env_float(
        "REVIEW_CONFIDENCE_THRESHOLD", default=0.72
    )
    REVIEW_NEAR_BOUNDARY_POINTS = _env_float(
        "REVIEW_NEAR_BOUNDARY_POINTS", default=2.0
    )
    # Max characters of JSON payload sent per LLM call in staged mode (truncation safety).
    STAGED_PROMPT_MAX_CHARS = _env_int("STAGED_PROMPT_MAX_CHARS", default=28000)
    # When true, each rubric criterion is scored by every model in GRADING_MODEL_* + primary; scores are averaged.
    STAGED_MULTI_LLM = _env_bool("STAGED_MULTI_LLM")

    # Stochastic multi-sample grading + semantic entropy (legacy pipeline only; off by default).
    GRADING_ENTROPY_MODE = (
        _env_str("GRADING_ENTROPY_MODE").strip().lower() == "on"
    )
    # k samples per configured grading model; capped to limit cost. k=1 disables sampling path.
    GRADING_SAMPLES_PER_MODEL = max(
        1,
        min(_env_int("GRADING_SAMPLES_PER_MODEL", default=3), 16),
    )
    # Temperature for grade() when entropy sampling is active (k>1).
    GRADING_SAMPLE_TEMPERATURE = max(
        0.0,
        min(_env_float("GRADING_SAMPLE_TEMPERATURE", default=0.3), 2.0),
    )
    # Multimodal chunk grading only: k stochastic chat_json calls per model in
    # build_multimodal_grading_clients() (Hugging Face primary when GRADING_MODEL_2/3 unset).
    # Separate from GRADING_SAMPLES_PER_MODEL so legacy entropy pipelines are unchanged.
    MULTIMODAL_SAMPLES_PER_MODEL = max(
        1,
        min(_env_int("MULTIMODAL_SAMPLES_PER_MODEL", default=5), 16),
    )
    
    MULTIMODAL_LLM_CALL_CONCURRENCY = max(
        1,
        min(_env_int("MULTIMODAL_LLM_CALL_CONCURRENCY", default=5), 32),
    )
    # Optional absolute path for assignment-wide multimodal ``custom_rubric/*.json`` caches.
    # Empty → ``MULTIMODAL_CUSTOM_RUBRIC_OUTPUT_DIR`` env → repo ``custom_rubric/``.
    MULTIMODAL_CUSTOM_RUBRIC_OUTPUT_DIR = _env_str(
        "MULTIMODAL_CUSTOM_RUBRIC_OUTPUT_DIR"
    ).strip()
    # Whisper (OpenAI) for submission audio: off | on | auto (default). ``auto`` transcribes when
    # ``OPENAI_API_KEY`` is set. Runs once when building ``extracted_plaintext`` for chunking.
    _wt_raw = _env_str("MULTIMODAL_WHISPER_TRANSCRIBE").strip().lower()
    if _wt_raw in ("0", "false", "no", "off"):
        MULTIMODAL_WHISPER_TRANSCRIBE = "off"
    elif _wt_raw in ("1", "true", "yes", "on"):
        MULTIMODAL_WHISPER_TRANSCRIBE = "on"
    else:
        MULTIMODAL_WHISPER_TRANSCRIBE = "auto"
    OPENAI_WHISPER_MODEL = _env_str("OPENAI_WHISPER_MODEL").strip() or "whisper-1"
    # Max audio payload size (bytes) before skipping Whisper locally. OpenAI may impose a
    # separate upload limit; increase this if long interviews fail only with our guard.
    MULTIMODAL_WHISPER_MAX_FILE_BYTES = max(
        8 * 1024 * 1024,
        _env_int(
            "MULTIMODAL_WHISPER_MAX_FILE_BYTES",
            default=32 * 1024 * 1024,
        ),
    )
    # Split long single-file audio into duration halves, transcribe each, embed transcripts,
    # then OpenAI trio frontload runs twice (half A / half B) and blends RAG vectors.
    # off | on | auto (auto when bytes ≥ MULTIMODAL_AUDIO_HALF_SPLIT_AUTO_MIN_BYTES). Requires ffmpeg.
    _ahs = _env_str("MULTIMODAL_AUDIO_HALF_SPLIT").strip().lower()
    if _ahs in ("1", "true", "yes", "on"):
        MULTIMODAL_AUDIO_HALF_SPLIT = "on"
    elif _ahs in ("0", "false", "no", "off"):
        MULTIMODAL_AUDIO_HALF_SPLIT = "off"
    else:
        MULTIMODAL_AUDIO_HALF_SPLIT = "auto"
    MULTIMODAL_AUDIO_HALF_SPLIT_AUTO_MIN_BYTES = max(
        500_000,
        min(
            _env_int(
                "MULTIMODAL_AUDIO_HALF_SPLIT_AUTO_MIN_BYTES",
                default=3_000_000,
            ),
            80_000_000,
        ),
    )
    # When ``MULTIMODAL_AUDIO_HALF_SPLIT=auto`` and ``modality_hints["task_type"]`` is
    # ``oral_interview``, use this (typically lower) byte threshold so long interviews split
    # without requiring an 8+ MiB file.
    MULTIMODAL_AUDIO_HALF_SPLIT_AUTO_MIN_BYTES_ORAL = max(
        500_000,
        min(
            _env_int(
                "MULTIMODAL_AUDIO_HALF_SPLIT_AUTO_MIN_BYTES_ORAL",
                default=1_200_000,
            ),
            80_000_000,
        ),
    )
    # When true, each chunk is sent to the **structure** LLM once to fill ``evidence["trio"]``
    # (question / student_response / instructor_context) before answer-key alignment. Uses
    # Claude (Anthropic) when configured, else OpenAI.
    MULTIMODAL_LLM_TRIO_CHUNKING = _env_bool("MULTIMODAL_LLM_TRIO_CHUNKING")
    MULTIMODAL_TRIO_CHUNKING_MODEL = _env_str("MULTIMODAL_TRIO_CHUNKING_MODEL").strip()
    # huggingface | hf | openai — multimodal **per-chunk grading** uses OpenAI
    # (``OPENAI_MULTIMODAL_GRADING_MODEL``) when ``OPENAI_API_KEY`` is set. Structure
    # (parsing / trio chunking) uses Claude (Anthropic) then OpenAI.
    _mm_lb = _env_str("MULTIMODAL_LLM_BACKEND").strip().lower()
    if _mm_lb:
        raw = {"hf": "huggingface"}.get(_mm_lb, _mm_lb)
        MULTIMODAL_LLM_BACKEND = (
            raw if raw in ("huggingface", "openai") else "openai"
        )
    else:
        MULTIMODAL_LLM_BACKEND = "openai"
    # OpenAI chat model for multimodal per-chunk grading when ``MULTIMODAL_LLM_BACKEND=openai``.
    OPENAI_MULTIMODAL_GRADING_MODEL = (
        _env_str("OPENAI_MULTIMODAL_GRADING_MODEL").strip() or "gpt-5.4-nano"
    )
    # Hugging Face repo id (gated models need HUGGINGFACE_HUB_TOKEN or HF_TOKEN). Empty →
    # meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8 when backend is huggingface.
    HUGGINGFACE_GRADING_MODEL_ID = _env_str("HUGGINGFACE_GRADING_MODEL_ID").strip()
    HUGGINGFACE_HUB_TOKEN = _env_str("HUGGINGFACE_HUB_TOKEN").strip()
    HF_TOKEN = _env_str("HF_TOKEN").strip()
    HUGGINGFACE_MAX_NEW_TOKENS = _env_int("HUGGINGFACE_MAX_NEW_TOKENS", default=2048)
    HUGGINGFACE_TRUST_REMOTE_CODE = (
        _env_str("HUGGINGFACE_TRUST_REMOTE_CODE").strip().lower()
        in ("1", "true", "yes", "on")
    )
    # fingerprint (MVP) | openai (reserved; falls back to fingerprint) | off (same as fingerprint)
    GRADING_ENTROPY_EMBEDDINGS = (
        _env_str("GRADING_ENTROPY_EMBEDDINGS").strip().lower() or "fingerprint"
    )
    # If valid/attempted ratio falls below this, flag needs_review.
    GRADING_ENTROPY_MIN_SUCCESS_RATE = max(
        0.0,
        min(_env_float("GRADING_ENTROPY_MIN_SUCCESS_RATE", default=0.5), 1.0),
    )
    # Natural-log semantic entropy above this triggers review flag (tune per deployment).
    GRADING_ENTROPY_REVIEW_NATURAL_H = _env_float(
        "GRADING_ENTROPY_REVIEW_NATURAL_H", default=1.0
    )

    # RAG / local embedding export (submission text → vector).
    # RAG_EMBEDDING_BACKEND: sentence_transformers (default) | openai
    # — multimodal RAG uses :func:`app.grading.rag_embeddings.compute_submission_embedding`.
    # ``openai`` uses ``OPENAI_TRIO_RAG_EMBEDDING_MODEL`` (default text-embedding-3-small);
    # requires OPENAI_API_KEY (falls back to sentence_transformers then hash on failure).
    _rag_be_raw = _env_str("RAG_EMBEDDING_BACKEND").strip().lower()
    RAG_EMBEDDING_BACKEND = (
        _rag_be_raw
        if _rag_be_raw in ("sentence_transformers", "openai")
        else "sentence_transformers"
    )
    # Hugging Face id for ``sentence_transformers.SentenceTransformer`` when backend is ST.
    SENTENCE_TRANSFORMERS_MODEL = (
        _env_str("SENTENCE_TRANSFORMERS_MODEL").strip() or "all-MiniLM-L6-v2"
    )
    RAG_EMBED_MAX_CHARS = _env_int("RAG_EMBED_MAX_CHARS", default=24000)
    # auto | openai_first | openai_only — when ``auto``, try OpenAI first if OPENAI_API_KEY is set.
    _rag_order = _env_str("RAG_EMBED_ORDER").strip().lower() or "auto"
    RAG_EMBED_ORDER = (
        _rag_order if _rag_order in ("auto", "openai_first", "openai_only") else "auto"
    )
    # auto | on | off — auto enables OpenAI notebook digest when OPENAI_API_KEY is set.
    NOTEBOOK_OPENAI_DIGEST = _env_str("NOTEBOOK_OPENAI_DIGEST").strip().lower() or "auto"

    # Multimodal: one OpenAI chat (trio JSON) + OpenAI embeddings for all units, then
    # local Hugging Face or OpenAI for structure; per-chunk grading uses OpenAI. Requires OPENAI_API_KEY.
    # Values: off | false — disabled. on | true — forced on (still needs API key).
    # Empty or ``auto`` (default): on when OPENAI_API_KEY is set (chunk+trio+RAG via OpenAI).
    MULTIMODAL_OPENAI_TRIO_RAG_FRONTLOAD = (
        _env_str("MULTIMODAL_OPENAI_TRIO_RAG_FRONTLOAD").strip().lower()
    )
    OPENAI_TRIO_RAG_CHAT_MODEL = (
        _env_str("OPENAI_TRIO_RAG_CHAT_MODEL").strip() or "gpt-5.4-nano"
    )
    OPENAI_TRIO_RAG_EMBEDDING_MODEL = (
        _env_str("OPENAI_TRIO_RAG_EMBEDDING_MODEL").strip() or "text-embedding-3-small"
    )
    MULTIMODAL_OPENAI_TRIO_INPUT_MAX_CHARS = _env_int(
        "MULTIMODAL_OPENAI_TRIO_INPUT_MAX_CHARS", default=120_000
    )
    # When the submission plain text exceeds this many characters, trio extraction uses
    # overlapping windows (each call still receives up to ANSWER_KEY_MAX_CHARS of the key).
    MULTIMODAL_OPENAI_TRIO_WINDOW_CHARS = _env_int(
        "MULTIMODAL_OPENAI_TRIO_WINDOW_CHARS", default=48_000
    )
    MULTIMODAL_OPENAI_TRIO_WINDOW_OVERLAP_CHARS = _env_int(
        "MULTIMODAL_OPENAI_TRIO_WINDOW_OVERLAP_CHARS", default=4_096
    )
    # After trio JSON extraction, chunks whose ``student_response`` exceeds this length are
    # split into multiple chunks (paragraph-aware) so Whisper/long oral answers grade per slice.
    MULTIMODAL_OPENAI_TRIO_MAX_STUDENT_RESPONSE_CHARS = max(
        4_000,
        min(
            _env_int(
                "MULTIMODAL_OPENAI_TRIO_MAX_STUDENT_RESPONSE_CHARS",
                default=14_000,
            ),
            200_000,
        ),
    )
    MULTIMODAL_OPENAI_TRIO_ANSWER_KEY_MAX_CHARS = _env_int(
        "MULTIMODAL_OPENAI_TRIO_ANSWER_KEY_MAX_CHARS", default=32_000
    )
    # Blank instructor ``.ipynb`` (``blank_assignments/``) drives question boundaries when aligned
    # with the student notebook. Values: ``off`` | ``on`` | ``auto`` (default: use blank when bytes resolve).
    MULTIMODAL_BLANK_TEMPLATE_CHUNKING = (
        _env_str("MULTIMODAL_BLANK_TEMPLATE_CHUNKING").strip().lower() or "auto"
    )
    # When ``on``, notebook chunking prefers one LLM JSON call with blank ipynb + student ipynb +
    # answer key (see :mod:`llm_triplet_three_source`). Requires resolved blank bytes, non-empty
    # ``answer_key_plaintext`` in hints, and OPENAI_API_KEY (preferred) or a structure LLM client.
    MULTIMODAL_LLM_TRIPLET_THREE_SOURCE = (
        _env_str("MULTIMODAL_LLM_TRIPLET_THREE_SOURCE").strip().lower()
    )
    MULTIMODAL_LLM_TRIPLET_MAX_CHARS_PER_SOURCE = max(
        8_000,
        min(_env_int("MULTIMODAL_LLM_TRIPLET_MAX_CHARS_PER_SOURCE", default=1_000_000), 2_000_000),
    )
    # Defaults align with gpt-5.4-nano + text-embedding-3-small list pricing; override if your SKU differs.
    OPENAI_TRIO_RAG_CHAT_INPUT_USD_PER_MTOK = _env_float(
        "OPENAI_TRIO_RAG_CHAT_INPUT_USD_PER_MTOK", default=0.20
    )
    OPENAI_TRIO_RAG_CHAT_OUTPUT_USD_PER_MTOK = _env_float(
        "OPENAI_TRIO_RAG_CHAT_OUTPUT_USD_PER_MTOK", default=1.25
    )
    OPENAI_TRIO_RAG_EMBED_USD_PER_MTOK = _env_float(
        "OPENAI_TRIO_RAG_EMBED_USD_PER_MTOK", default=0.02
    )

    WHISPER_ENABLED = _env_bool("WHISPER_ENABLED")

    CELERY_WORKER_CONCURRENCY = _env_int("CELERY_WORKER_CONCURRENCY", default=5) # might limit the number of celery workers because this might increase api calls
    CELERY_WORKER_PREFETCH = _env_int("CELERY_WORKER_PREFETCH", default=1)

    # Comma-separated list of allowed origins; set CORS_ORIGINS in .env
    _cors = _env_str("CORS_ORIGINS").strip().lower()
    CORS_ORIGINS = [o.strip() for o in _cors.split(",") if o.strip()]