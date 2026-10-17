"""FastAPI composition root."""

from collections.abc import Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from aurinara_platform import __version__
from aurinara_platform.api import router
from aurinara_platform.auth import JwksTokenVerifier, TokenVerifier
from aurinara_platform.config import Settings, get_settings
from aurinara_platform.database import create_session_factory
from aurinara_platform.problems import install_problem_handlers
from aurinara_platform.services import (
    AuthorizationService,
    MembershipService,
    ObjectService,
    StudyService,
    UserService,
)
from aurinara_platform.storage import (
    DevelopmentScanner,
    HttpMalwareScanner,
    MalwareScanner,
    ObjectStorage,
    S3ObjectStorage,
)


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: object | None = None,
    token_verifier: TokenVerifier | None = None,
    object_storage: ObjectStorage | None = None,
    malware_scanner: MalwareScanner | None = None,
) -> FastAPI:
    resolved = settings or get_settings()
    authorization = AuthorizationService(resolved)
    storage = object_storage or S3ObjectStorage(resolved)
    scanner = malware_scanner or (
        HttpMalwareScanner(resolved) if resolved.malware_scan_required else DevelopmentScanner()
    )
    app = FastAPI(
        title="Aurinara Platform Control Plane",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/v1/openapi.json",
    )
    app.state.settings = resolved
    app.state.session_factory = session_factory or create_session_factory(resolved)
    app.state.token_verifier = token_verifier or JwksTokenVerifier(resolved)
    app.state.authorization_service = authorization
    app.state.study_service = StudyService(authorization)
    app.state.user_service = UserService(authorization)
    app.state.membership_service = MembershipService(authorization)
    app.state.object_service = ObjectService(resolved, authorization, storage, scanner)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], object]) -> Response:
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.correlation_id = request.headers.get("X-Correlation-ID") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response

    install_problem_handlers(app)
    app.include_router(router, prefix=resolved.api_prefix)

    @app.get("/health/live", include_in_schema=False)
    def live() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
