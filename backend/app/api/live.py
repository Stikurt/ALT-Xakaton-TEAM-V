import asyncio
import anyio

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from app.domain.models import ControlCommand, ApiError
from app.runtime.coordinator import RuntimeUnavailable

router = APIRouter()


def runtime(request):
    value = getattr(request.app.state,"runtime",None)
    if value is None:
        raise RuntimeUnavailable("Выполните миграции и bootstrap, затем перезапустите backend.")
    return value


@router.post("/api/simulation/control", responses={409:{"model":ApiError},503:{"model":ApiError}})
async def control(command: ControlCommand, request: Request):
    origin = request.headers.get("origin")
    if origin and origin not in request.app.state.allowed_origins:
        from fastapi import HTTPException
        raise HTTPException(403,"Origin не разрешён")
    return await runtime(request).submit(command.model_dump(exclude_none=True))


@router.websocket("/ws")
async def websocket_stream(ws: WebSocket):
    origin = ws.headers.get("origin")
    if origin and origin not in ws.app.state.allowed_origins:
        await ws.close(code=1008)
        return
    try:
        owner = runtime(ws)
    except RuntimeUnavailable:
        await ws.close(code=1013)
        return
    subscriber = owner.broadcast.subscribe()
    first = owner.message("snapshot",owner.get_state().model_dump(mode="json"))
    tasks = []
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

        tasks = [asyncio.create_task(send()),asyncio.create_task(receive())]
        await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect,asyncio.TimeoutError,asyncio.CancelledError):
        pass
    finally:
        owner.broadcast.unsubscribe(subscriber)
        with anyio.CancelScope(shield=True):
            for task in tasks: task.cancel()
            if tasks: await asyncio.gather(*tasks,return_exceptions=True)
            try:
                await ws.close()
            except (RuntimeError,WebSocketDisconnect):
                pass
