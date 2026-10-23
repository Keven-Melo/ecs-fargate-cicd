from itertools import count

import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    main._tasks.clear()
    main._ids = count(1)
    monkeypatch.delenv("API_KEY", raising=False)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_returns_service_info():
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "tasks-api"
    assert "version" in body and "environment" in body


def test_create_and_list_tasks():
    created = client.post("/tasks", json={"title": "estudar ECS"})
    assert created.status_code == 201
    assert created.json() == {"id": 1, "title": "estudar ECS", "done": False}

    listed = client.get("/tasks")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_get_update_delete_task():
    client.post("/tasks", json={"title": "pipeline"})

    assert client.get("/tasks/1").json()["title"] == "pipeline"

    updated = client.put("/tasks/1", json={"title": "pipeline", "done": True})
    assert updated.status_code == 200
    assert updated.json()["done"] is True

    assert client.delete("/tasks/1").status_code == 204
    assert client.get("/tasks/1").status_code == 404


def test_missing_task_returns_404():
    assert client.get("/tasks/999").status_code == 404
    assert client.put("/tasks/999", json={"title": "x"}).status_code == 404
    assert client.delete("/tasks/999").status_code == 404


def test_invalid_payload_returns_422():
    assert client.post("/tasks", json={"title": ""}).status_code == 422
    assert client.post("/tasks", json={}).status_code == 422


def test_write_routes_require_api_key_when_configured(monkeypatch):
    monkeypatch.setenv("API_KEY", "segredo")

    assert client.post("/tasks", json={"title": "x"}).status_code == 401
    assert (
        client.post("/tasks", json={"title": "x"}, headers={"X-API-Key": "errada"}).status_code
        == 401
    )
    ok = client.post("/tasks", json={"title": "x"}, headers={"X-API-Key": "segredo"})
    assert ok.status_code == 201

    # leitura continua publica
    assert client.get("/tasks").status_code == 200
