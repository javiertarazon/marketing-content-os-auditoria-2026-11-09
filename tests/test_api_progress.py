import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

API_DIRECTORY = Path(__file__).parents[1] / "apps" / "api"
sys.path.insert(0, str(API_DIRECTORY))
sys.path.insert(0, str(Path(__file__).parents[1] / "packages" / "python"))
os.environ["WORKER_API_TOKEN"] = "pytest-worker-token"

import main as api


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params=()):
        self.calls.append((query, params))
        return FakeCursor(self.row)


def test_worker_progress_requires_valid_authentication_and_lease(monkeypatch) -> None:
    job_id = UUID("f2810a6b-75e3-49fd-b7d9-9d6cbef8502e")
    monkeypatch.setattr(api, "init_database", lambda: None)
    now = datetime.now(timezone.utc)
    row = {
        "job_id": job_id,
        "worker": "render",
        "status": "running",
        "request": {"topic": "Video de prueba"},
        "progress": {"stage": "rendering", "message": "Renderizando", "percent": 60},
        "output": None,
        "error": None,
        "attempts": 1,
        "created_at": now,
        "updated_at": now,
    }
    monkeypatch.setattr(api, "get_connection", lambda: FakeConnection(row))

    with TestClient(api.app) as client:
        unauthorized = client.post(
            f"/v1/jobs/{job_id}/progress",
            json={"stage": "rendering", "message": "Renderizando", "percent": 60},
            headers={"X-Worker-Lease": "lease-token"},
        )
        assert unauthorized.status_code == 401

        response = client.post(
            f"/v1/jobs/{job_id}/progress",
            json={"stage": "rendering", "message": "Renderizando", "percent": 60},
            headers={
                "Authorization": "Bearer pytest-worker-token",
                "X-Worker-Lease": "lease-token",
            },
        )

    assert response.status_code == 200
    assert response.json()["progress"]["percent"] == 60


def test_research_completion_links_render_job_to_parent(monkeypatch) -> None:
    job_id = UUID("f2810a6b-75e3-49fd-b7d9-9d6cbef8502e")
    now = datetime.now(timezone.utc)
    connection = FakeConnection(
        {
            "job_id": job_id,
            "worker": "research",
            "status": "running",
            "request": {"topic": "Video de prueba", "duration_seconds": 35},
            "progress": {"stage": "voice", "message": "Voz", "percent": 80},
            "output": None,
            "error": None,
            "attempts": 1,
            "created_at": now,
            "updated_at": now,
        }
    )
    monkeypatch.setattr(api, "init_database", lambda: None)
    monkeypatch.setattr(api, "get_connection", lambda: connection)

    with TestClient(api.app) as client:
        response = client.post(
            f"/v1/jobs/{job_id}/complete",
            headers={
                "Authorization": "Bearer pytest-worker-token",
                "X-Worker-Lease": "lease-token",
            },
            json={
                "output": {
                    "research": {"sources": []},
                    "script": "Guion de prueba",
                    "audio_object_key": "audio/test.wav",
                }
            },
        )

    assert response.status_code == 200
    insert_query, insert_params = connection.calls[1]
    assert "parent_job_id" in insert_query
    assert insert_params[1] == job_id
    update_query, _ = connection.calls[2]
    assert "'message', %s::text" in update_query


def test_job_failure_builds_typed_progress_values(monkeypatch) -> None:
    job_id = UUID("f2810a6b-75e3-49fd-b7d9-9d6cbef8502e")
    now = datetime.now(timezone.utc)
    connection = FakeConnection(
        {
            "job_id": job_id,
            "worker": "render",
            "status": "running",
            "request": {"topic": "Video de prueba"},
            "progress": {"stage": "rendering", "message": "Renderizando", "percent": 60},
            "output": None,
            "error": None,
            "attempts": 1,
            "created_at": now,
            "updated_at": now,
        }
    )
    monkeypatch.setattr(api, "init_database", lambda: None)
    monkeypatch.setattr(api, "get_connection", lambda: connection)

    with TestClient(api.app) as client:
        response = client.post(
            f"/v1/jobs/{job_id}/fail",
            headers={
                "Authorization": "Bearer pytest-worker-token",
                "X-Worker-Lease": "lease-token",
            },
            json={"error": "Error simulado"},
        )

    assert response.status_code == 200
    query, params = connection.calls[0]
    assert "'stage', %s::text" in query
    assert "'message', %s::text" in query
    assert "'percent', %s::integer" in query
    assert params[3:6] == ("failed", "Fallido", 0)
