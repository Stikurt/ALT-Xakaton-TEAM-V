from fastapi import APIRouter, Depends, Request

from app.auth import require_role

from app.domain.models import ApiError, Contract, StateResponse

router = APIRouter()


class HealthResponse(Contract):
    status: str
    database: str
    stage: str


@router.get("/health", response_model=HealthResponse, responses={503: {"model": ApiError}})
def health(request: Request):
    request.app.state.repository.check_ready()
    owner = getattr(request.app.state,"runtime",None)
    if owner is None and request.app.state.runtime_required:
        from app.runtime.coordinator import RuntimeUnavailable
        raise RuntimeUnavailable(getattr(request.app.state,"runtime_error",None)
                                 or "Движок не инициализирован: выполните bootstrap и перезапустите сервер.")
    if owner and owner.fault:
        from app.runtime.coordinator import RuntimeUnavailable
        raise RuntimeUnavailable(owner.fault)
    return HealthResponse(status="ok", database="ready", stage="2-live" if owner else "1-foundation")


@router.get("/api/state", response_model=StateResponse, dependencies=[Depends(require_role("viewer"))],
            responses={401: {"model": ApiError}, 503: {"model": ApiError}})
def state(request: Request):
    """Full live snapshot; its dynamic state has already been committed to PostgreSQL."""
    owner = getattr(request.app.state,"runtime",None)
    if owner: return owner.get_state()
    if request.app.state.runtime_required:
        from app.runtime.coordinator import RuntimeUnavailable
        raise RuntimeUnavailable(getattr(request.app.state,"runtime_error",None)
                                 or "Движок не инициализирован: выполните bootstrap и перезапустите сервер.")
    return request.app.state.repository.current_state()
