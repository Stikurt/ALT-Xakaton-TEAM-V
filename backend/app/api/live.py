import asyncio
import anyio

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect

from app.auth import authorize_websocket, require_role
from app.domain.models import ControlCommand, IncidentCommand, IncidentBatchCommand, CommandResult, ApiError
from app.runtime.coordinator import RuntimeUnavailable

router = APIRouter()


def runtime(request):
    value = getattr(request.app.state,"runtime",None)
    if value is None:
        raise RuntimeUnavailable("Выполните миграции и bootstrap, затем перезапустите backend.")
    return value


# Stage 6: every command needs dispatcher/admin. require_role checks Origin (403),
# session (401), the X-CSRF-Token header (403) and role (403) before the body is used.
DISPATCHER = [Depends(require_role("dispatcher"))]
GUARDED = {401:{"model":ApiError},403:{"model":ApiError}}


@router.post("/api/simulation/control", response_model=CommandResult, dependencies=DISPATCHER,
             responses={**GUARDED,409:{"model":ApiError},503:{"model":ApiError}})
async def control(command: ControlCommand, request: Request):
    return await runtime(request).submit(command.model_dump(exclude_none=True))


@router.post("/api/incidents", response_model=CommandResult, dependencies=DISPATCHER,
             responses={**GUARDED,409:{"model":ApiError},503:{"model":ApiError}})
async def incident(command: IncidentCommand, request: Request):
    return await runtime(request).submit({
        "command_id":command.command_id,"run_id":command.run_id,"action":"incident",
        "incident":command.model_dump(exclude={"command_id","run_id"},exclude_none=True),
    })


@router.post("/api/incidents/batch", response_model=CommandResult, dependencies=DISPATCHER,
             responses={**GUARDED,409:{"model":ApiError},503:{"model":ApiError}})
async def incidents(command: IncidentBatchCommand, request: Request):
    return await runtime(request).submit({
        "command_id":command.command_id,"run_id":command.run_id,"action":"incidents",
        "incidents":[item.model_dump(exclude_none=True) for item in command.incidents],
    })


@router.websocket("/ws")
async def websocket_stream(ws: WebSocket):
    # Origin and session are verified before accept; failure closes with 1008.
    principal = await authorize_websocket(ws)
    if principal is None:
        return
    try:
        owner = runtime(ws)
    except RuntimeUnavailable:
        await ws.close(code=1013)
        return
    subscriber = owner.broadcast.subscribe()
    first = owner.message("snapshot",owner.get_state().model_dump(mode="json"))
    tasks = []
    close_code, close_reason = 1000, None
    try:
        await ws.accept()
        await asyncio.wait_for(ws.send_json(dict(first,ws_seq=1)),timeout=2)

        async def send():
            seq = 1
            while True:
                if subscriber.overflow.is_set():
                    await ws.close(code=1013,reason="Reconnect for a fresh snapshot")
                    return
                try:
                    message = await asyncio.wait_for(subscriber.queue.get(),timeout=1)
                except asyncio.TimeoutError:
                    continue
                seq += 1
                await asyncio.wait_for(ws.send_json(dict(message,ws_seq=seq)),timeout=2)

        async def receive():
            while True:
                message = await ws.receive()
                if message['type'] == 'websocket.disconnect':
                    return

        # The third task ends when the session expires or is revoked (logout wakes it at once).
        watch = asyncio.create_task(ws.app.state.auth.wait_session_end(principal))
        tasks = [asyncio.create_task(send()),asyncio.create_task(receive()),watch]
        done,_ = await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        if watch in done:
            close_code, close_reason = watch.result()
    except (WebSocketDisconnect,asyncio.TimeoutError,asyncio.CancelledError):
        pass
    finally:
        owner.broadcast.unsubscribe(subscriber)
        with anyio.CancelScope(shield=True):
            for task in tasks: task.cancel()
            if tasks: await asyncio.gather(*tasks,return_exceptions=True)
            try:
                await ws.close(code=close_code,reason=close_reason)
            except (RuntimeError,WebSocketDisconnect):
                pass
