"""Stage 6 access control: fixed accounts viewer/dispatcher/admin, PostgreSQL sessions.

Wiring is deliberately small: main.py calls install_auth(app, settings) and attaches a
SessionStore in its lifespan; routes use Depends(require_role(...)); /ws uses
authorize_websocket() and AuthService.wait_session_end(). See docs/auth.md.
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.auth.dependencies import authorize_websocket, get_auth, require_role
from app.auth.service import CSRF_HEADER, AuthError, AuthService, Principal
from app.auth.store import PostgresSessionStore
from app.domain.models import ApiError

__all__ = ["CSRF_HEADER", "AuthError", "AuthService", "PostgresSessionStore", "Principal",
           "authorize_websocket", "get_auth", "install_auth", "require_role"]


def install_auth(app: FastAPI, settings, **service_kwargs) -> AuthService:
    from app.auth.routes import router

    auth = AuthService.from_settings(settings, **service_kwargs)
    app.state.auth = auth

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, exc: AuthError):
        response = JSONResponse(status_code=exc.status, content=ApiError(
            code=exc.code, message=exc.message, details=[]).model_dump(mode="json"),
            headers={**exc.headers, "Cache-Control": "no-store"})
        if exc.clear_cookie:
            request.app.state.auth.clear_cookie(response)
        return response

    app.include_router(router)
    return auth
