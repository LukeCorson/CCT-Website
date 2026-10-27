"""FastAPI entry point for the compact CCT browser workstation."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .controller import WebController

STATIC = Path(__file__).with_name("static")
controller = WebController()


class ConnectRequest(BaseModel):
    mode: str = "simulation"
    profile_path: str | None = None


class StartRequest(BaseModel):
    config: dict = Field(default_factory=dict)


class ManualRequest(BaseModel):
    action: str
    arguments: dict = Field(default_factory=dict)


@asynccontextmanager
async def lifespan(app):
    controller.start_loop()
    yield
    controller.close()


app = FastAPI(title="CCT Control", version="1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def perform(operation, *args):
    try:
        return operation(*args)
    except (ValueError, RuntimeError, OSError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state():
    return controller.snapshot()


@app.post("/api/connect")
def connect(request: ConnectRequest):
    return perform(controller.connect, request.mode, request.profile_path)


@app.post("/api/disconnect")
def disconnect():
    return perform(controller.disconnect) or {"ok": True}


@app.post("/api/tests/start")
def start_test(request: StartRequest):
    return {"run_id": perform(controller.start_test, request.config)}


@app.post("/api/tests/stop")
def stop_test():
    return perform(controller.stop_test) or {"ok": True}


@app.post("/api/manual")
def manual(request: ManualRequest):
    return {"command_id": perform(controller.manual, request.action, request.arguments)}


@app.get("/api/runs")
def runs():
    return controller.runs()


@app.websocket("/ws/state")
async def state_stream(socket: WebSocket):
    await socket.accept()
    try:
        while True:
            await socket.send_json(controller.snapshot())
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return


def main():
    import uvicorn
    uvicorn.run("cct.web.app:app", host="127.0.0.1", port=8765, reload=False)

