from fastapi import APIRouter, Depends, Request, Response

from app.auth.dependencies import get_auth, origin_guard, require_role
from app.auth.models import LoginRequest, SessionInfo
from app.auth.service import AuthError, Principal
from app.domain.models import ApiError

router = APIRouter(prefix="/api", tags=["auth"])
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
ERRORS = {code: {"model": ApiError} for code in (401, 403)}


def info(principal: Principal) -> SessionInfo:
    return SessionInfo(username=principal.username, role=principal.role,
                       permissions=principal.permissions, expires_at=principal.expires_at,
                       csrf_token=principal.csrf_token)


@router.post("/login", response_model=SessionInfo, dependencies=[Depends(origin_guard)],
             responses={**ERRORS, 429: {"model": ApiError}, 503: {"model": ApiError}})
async def login(body: LoginRequest, request: Request, response: Response):
    """Sets an HttpOnly session cookie and returns the CSRF token for later commands."""
    auth = get_auth(request)
    client = request.client.host if request.client else "unknown"
    token, principal = await auth.login(body.username, body.password.get_secret_value(), client)
    previous = request.cookies.get(auth.cookie_name)
    if previous:   # a fresh token every login; the old cookie's session is revoked
        await auth.logout(previous)
    auth.set_cookie(response, token)
    response.headers.update(NO_STORE)
    return info(principal)


@router.post("/logout", status_code=204, dependencies=[Depends(origin_guard)],
             responses={403: {"model": ApiError}})
async def logout(request: Request):
    """Revokes the session in PostgreSQL and clears the cookie. Idempotent without a session."""
    auth = get_auth(request)
    token = request.cookies.get(auth.cookie_name)
    if token:
        try:
            principal = await auth.authenticate(token)
        except AuthError:
            principal = None
        if principal is not None:
            auth.check_csrf(principal, request.headers)
            await auth.logout(token)
    response = Response(status_code=204, headers=NO_STORE)
    auth.clear_cookie(response)
    return response


@router.get("/me", response_model=SessionInfo, responses={401: {"model": ApiError}})
async def me(response: Response, principal: Principal = Depends(require_role("viewer"))):
    response.headers.update(NO_STORE)
    return info(principal)
