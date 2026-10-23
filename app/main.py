"""Tasks API: API simples usada como carga de trabalho para o projeto DevOps."""

import hmac
import os
from itertools import count

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

APP_VERSION = os.getenv("APP_VERSION", "dev")
ENVIRONMENT = os.getenv("ENVIRONMENT", "local")

app = FastAPI(title="Tasks API", version=APP_VERSION)


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    done: bool = False


class Task(TaskIn):
    id: int


# Armazenamento em memoria: simples de proposito. Com mais de uma task rodando,
# cada instancia tem sua propria lista. Isso sera resolvido com DynamoDB/RDS
# em uma etapa futura do portfolio.
_tasks: dict[int, Task] = {}
_ids = count(1)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Protege rotas de escrita. A chave vem do Secrets Manager via variavel API_KEY.

    Se API_KEY nao estiver definida (ex.: desenvolvimento local), a checagem e ignorada.
    """
    expected = os.getenv("API_KEY")
    if expected and not hmac.compare_digest(x_api_key or "", expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key invalida")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "tasks-api", "version": APP_VERSION, "environment": ENVIRONMENT}


@app.get("/tasks", response_model=list[Task])
def list_tasks() -> list[Task]:
    return list(_tasks.values())


@app.post(
    "/tasks",
    response_model=Task,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
def create_task(payload: TaskIn) -> Task:
    task = Task(id=next(_ids), **payload.model_dump())
    _tasks[task.id] = task
    return task


@app.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: int) -> Task:
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task nao encontrada")
    return task


@app.put(
    "/tasks/{task_id}",
    response_model=Task,
    dependencies=[Depends(require_api_key)],
)
def update_task(task_id: int, payload: TaskIn) -> Task:
    if task_id not in _tasks:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task nao encontrada")
    task = Task(id=task_id, **payload.model_dump())
    _tasks[task_id] = task
    return task


@app.delete(
    "/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_api_key)],
)
def delete_task(task_id: int) -> None:
    if _tasks.pop(task_id, None) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task nao encontrada")
