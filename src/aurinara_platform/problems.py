"""Sanitized HTTP problem responses."""

from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


@dataclass(slots=True)
class Problem(Exception):
    status_code: int
    code: str
    detail: str
    context: dict[str, object] = field(default_factory=dict)


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(Problem)
    async def handle_problem(request: Request, error: Problem) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        correlation_id = getattr(request.state, "correlation_id", None)
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": error.code,
                    "message": error.detail,
                    "details": error.context,
                    "request_id": request_id,
                    "correlation_id": correlation_id,
                }
            },
        )
