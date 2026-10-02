"""Route guards. Mutating methods get Origin + CSRF checks automatically from require_role."""
import logging

from fastapi import Request, WebSocket
from starlette.requests import HTTPConnection

from app.auth.service import ROLE_RANK, AuthError, AuthService, Principal, forbidden

logger = logging.getLogger("app.auth")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
WS_POLICY_VIOLATION = 1008


def get_auth(connection: HTTPConnection) -> AuthService:
    return connection.app.state.auth


def require_role(minimum: str):
    """Dependency: the session's role must be at least `minimum` (viewer < dispatcher < admin).

    Order of checks: Origin (403) -> session (401) -> CSRF header (403) -> role (403).
    For future admin settings: `Depends(require_role("admin"))`."""
    if minimum not in ROLE_RANK:
        raise ValueError(f"Unknown role: {minimum}")

    async def dependency(request: Request) -> Principal:
        auth = get_auth(request)
        unsafe = request.method not in SAFE_METHODS
        if unsafe:
            auth.check_origin(request.headers)
        principal = await auth.authenticate(request.cookies.get(auth.cookie_name))
        if unsafe:
            auth.check_csrf(principal, request.headers)
        if ROLE_RANK[principal.role] < ROLE_RANK[minimum]:
            logger.info("access_denied user=%s role=%s needs=%s path=%s",
                        principal.username, principal.role, minimum, request.url.path)
            raise forbidden()
        request.state.principal = principal
        return principal

    dependency.__name__ = f"require_role_{minimum}"
    return dependency


async def origin_guard(request: Request) -> None:
    """For endpoints without a session yet (login/logout): Origin before any body parsing."""
    get_auth(request).check_origin(request.headers)


async def authorize_websocket(ws: WebSocket, minimum: str = "viewer") -> Principal | None:
    """Check Origin (mandatory for WS) and session *before* accept. On failure closes and returns None."""
    auth = get_auth(ws)
    try:
        auth.check_origin(ws.headers, require=True)
        principal = await auth.authenticate(ws.cookies.get(auth.cookie_name))
        if ROLE_RANK[principal.role] < ROLE_RANK[minimum]:
            raise forbidden()
        return principal
    except AuthError as exc:
        logger.info("ws_rejected code=%s", exc.code)
        await ws.close(code=WS_POLICY_VIOLATION)
    except Exception as exc:  # session store unavailable: refuse, do not accept unchecked
        logger.error("ws_auth_unavailable error_type=%s", type(exc).__name__)
        await ws.close(code=1013)
    return None
