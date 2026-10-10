"""HTTP entrypoint: /health, POST /tasks, GET /tasks/{id}."""

import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from aprendiz.agent.runner import AgentRunner
from aprendiz.config import Settings
from aprendiz.models import TaskCreated, TaskRequest, TaskStatus
from aprendiz.runner import Runner
from aprendiz.tasks import TaskManager

_bearer = HTTPBearer(auto_error=False)


def _manager(request: Request) -> TaskManager:
    return request.app.state.manager


def _require_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    expected = request.app.state.settings.TEAM_TOKEN.encode()
    given = credentials.credentials.encode() if credentials else b""
    if not credentials or not hmac.compare_digest(given, expected):
        raise HTTPException(401, "invalid or missing token", headers={"WWW-Authenticate": "Bearer"})


def create_app(settings: Settings | None = None, runner: Runner | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings or Settings()
        app.state.manager = TaskManager(
            app.state.settings, runner or AgentRunner(app.state.settings)
        )
        yield
        await app.state.manager.shutdown()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/tasks", dependencies=[Depends(_require_token)])
    async def create_task(
        body: TaskRequest, manager: Annotated[TaskManager, Depends(_manager)]
    ) -> TaskCreated:
        return TaskCreated(task_id=await manager.submit(body.tarea, body.escritorio))

    @app.get("/tasks/{task_id}", dependencies=[Depends(_require_token)])
    async def get_task(
        task_id: str, manager: Annotated[TaskManager, Depends(_manager)]
    ) -> TaskStatus:
        status = await manager.status(task_id)
        if status is None:
            raise HTTPException(404, "task not found")
        return status

    return app


app = create_app()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    uvicorn.run("aprendiz.api:app", host=settings.HOST, port=settings.PORT)


if __name__ == "__main__":
    main()
