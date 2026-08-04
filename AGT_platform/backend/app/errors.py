from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def _error_body(detail: object) -> dict:
    if isinstance(detail, dict):
        body = dict(detail)
        body.setdefault("error", "request failed")
        return body
    return {"error": str(detail) if detail else "request failed"}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_error_body(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Malformed / missing request body fields -> 400 (matches the hand-rolled
        # ``{"error": "..."}"", 400`` validation used throughout the old Flask routes).
        return JSONResponse(
            status_code=400,
            content={"error": "invalid request", "detail": exc.errors()},
        )
