"""Tests de la puerta de aprobacion humana.

El render deja el video en 'awaiting_approval'. Estos tests comprueban que:
- aprobar marca el trabajo como 'approved' y llama al publisher,
- rechazar marca el trabajo como 'rejected' y NO publica,
- un trabajo en cualquier otro estado no se puede aprobar,
- un render completado NO queda en 'succeeded' sino en 'awaiting_approval',
- los fallos del publisher no invalidan la aprobacion (quedan registrados).
"""

import json
import os
import re
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

JOB_ID = UUID("11111111-2222-4333-8444-555555555555")


def make_row(status: str = "awaiting_approval", **overrides):
    now = datetime.now(timezone.utc)
    row = {
        "job_id": JOB_ID,
        "parent_job_id": None,
        "worker": "render",
        "status": status,
        "request": {"topic": "Finanzas personales", "platforms": ["tiktok"]},
        "progress": {
            "stage": "awaiting_approval",
            "message": "Video listo, pendiente de aprobación",
            "percent": 100,
        },
        "output": {"video_object_key": "renders/demo.mp4"},
        "error": None,
        "attempts": 1,
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    """Simula lo justo de psycopg: SELECT devuelve la fila, UPDATE la modifica.

    La API escribe el estado de dos formas: como parametro (``SET status = %s``)
    o como literal (``SET status = 'approved'``). Esta clase cubre ambas y
    reconstruye el progreso para que las aserciones sobre el mensaje valgan.
    """

    def __init__(self, row):
        self.row = dict(row)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params=()):
        self.calls.append((query, params))
        if "UPDATE jobs" not in query:
            # Un SELECT por otro trabajo no devuelve fila.
            if params and params[0] != self.row["job_id"]:
                return FakeCursor(None)
            return FakeCursor(dict(self.row))

        status = None
        if "SET status = %s" in query:
            status = params[0]
            rest = params[1:]
        else:
            literal = re.search(r"SET status = '([a-z_]+)'", query)
            if literal:
                status = literal.group(1)
                rest = params

        output = None
        for value in rest:
            if isinstance(value, str) and value.startswith("{"):
                output = json.loads(value)
                break

        # El mensaje es el parametro de texto que no es el estado ni la etapa.
        candidates = [
            value
            for value in rest
            if isinstance(value, str) and not value.startswith("{") and value != status
        ]
        message = candidates[-1] if candidates else status

        self.row["status"] = status or self.row["status"]
        if output is not None:
            self.row["output"] = output
        self.row["progress"] = {
            "stage": self.row["status"],
            "message": message,
            "percent": 100,
        }
        return FakeCursor(dict(self.row))


def install(monkeypatch, row, publisher=None):
    """Engancha la conexion falsa y, opcionalmente, un publisher simulado."""
    connection = FakeConnection(row)
    monkeypatch.setattr(api, "init_database", lambda: None)
    monkeypatch.setattr(api, "get_connection", lambda: connection)
    calls = []

    if publisher is not None:

        def fake_post(url, json=None, timeout=None):
            calls.append({"url": url, "json": json})
            return publisher

        monkeypatch.setattr(api.httpx, "post", fake_post)
    return connection, calls


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_approve_marks_the_job_approved_and_publishes(monkeypatch) -> None:
    monkeypatch.setattr(api.settings, "PUBLISHER_BASE_URL", "http://publisher:8001")
    monkeypatch.setattr(api.settings, "STORAGE_PUBLIC_BASE_URL", "https://pub.example.r2.dev")
    publisher = FakeResponse(200, {"unit_id": "unit-1", "enqueued": [{"target": "tiktok"}]})
    connection, calls = install(monkeypatch, make_row(), publisher)

    with TestClient(api.app) as client:
        response = client.post(f"/v1/jobs/{JOB_ID}/approve", json={"publish": True})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["output"]["publication"]["published"] is True
    assert body["output"]["publication"]["response"]["unit_id"] == "unit-1"
    assert body["output"]["approval"]["platforms"] == ["tiktok"]
    # La URL que se entrega al publisher es publica y apunta al MP4.
    assert calls[0]["url"] == "http://publisher:8001/v1/publish"
    assert calls[0]["json"]["video_url"] == "https://pub.example.r2.dev/renders/demo.mp4"
    assert calls[0]["json"]["platforms"] == ["tiktok"]
    assert connection.row["status"] == "approved"


def test_approve_without_publish_does_not_call_the_publisher(monkeypatch) -> None:
    monkeypatch.setattr(api.settings, "PUBLISHER_BASE_URL", "http://publisher:8001")
    monkeypatch.setattr(api.settings, "STORAGE_PUBLIC_BASE_URL", "https://pub.example.r2.dev")
    publisher = FakeResponse(200, {"unit_id": "unit-1"})
    _, calls = install(monkeypatch, make_row(), publisher)

    with TestClient(api.app) as client:
        response = client.post(f"/v1/jobs/{JOB_ID}/approve", json={"publish": False})

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert calls == []
    assert response.json()["output"]["approval"]["publish_requested"] is False


def test_approve_records_the_publisher_error_without_failing(monkeypatch) -> None:
    monkeypatch.setattr(api.settings, "PUBLISHER_BASE_URL", "http://publisher:8001")
    monkeypatch.setattr(api.settings, "STORAGE_PUBLIC_BASE_URL", "https://pub.example.r2.dev")
    publisher = FakeResponse(502, {"detail": "el scheduler no responde"})
    install(monkeypatch, make_row(), publisher)

    with TestClient(api.app) as client:
        response = client.post(f"/v1/jobs/{JOB_ID}/approve", json={"publish": True})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["output"]["publication"]["published"] is False
    assert "502" in body["output"]["publication"]["error"]


def test_approve_without_publisher_configured_is_recorded(monkeypatch) -> None:
    monkeypatch.setattr(api.settings, "PUBLISHER_BASE_URL", None)
    monkeypatch.setattr(api.settings, "STORAGE_PUBLIC_BASE_URL", "https://pub.example.r2.dev")
    install(monkeypatch, make_row(), None)

    with TestClient(api.app) as client:
        response = client.post(f"/v1/jobs/{JOB_ID}/approve", json={"publish": True})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["output"]["publication"]["published"] is False
    assert "PUBLISHER_BASE_URL" in body["output"]["publication"]["error"]


def test_approve_a_job_in_another_status_conflicts(monkeypatch) -> None:
    install(monkeypatch, make_row(status="queued"), None)

    with TestClient(api.app) as client:
        response = client.post(f"/v1/jobs/{JOB_ID}/approve", json={})

    assert response.status_code == 409
    assert "queued" in response.json()["detail"]


def test_approve_a_missing_job_returns_404(monkeypatch) -> None:
    install(monkeypatch, make_row(), None)
    missing = UUID("99999999-8888-4777-8666-555555555555")

    with TestClient(api.app) as client:
        response = client.post(f"/v1/jobs/{missing}/approve", json={})

    assert response.status_code == 404


def test_approve_without_platforms_returns_422(monkeypatch) -> None:
    install(monkeypatch, make_row(request={"topic": "Sin plataformas"}), None)

    with TestClient(api.app) as client:
        response = client.post(f"/v1/jobs/{JOB_ID}/approve", json={})

    assert response.status_code == 422
    assert "plataformas" in response.json()["detail"].lower()


def test_reject_marks_the_job_rejected_and_does_not_publish(monkeypatch) -> None:
    monkeypatch.setattr(api.settings, "PUBLISHER_BASE_URL", "http://publisher:8001")
    monkeypatch.setattr(api.settings, "STORAGE_PUBLIC_BASE_URL", "https://pub.example.r2.dev")
    publisher = FakeResponse(200, {"unit_id": "no-deberia-existir"})
    connection, calls = install(monkeypatch, make_row(), publisher)

    with TestClient(api.app) as client:
        response = client.post(
            f"/v1/jobs/{JOB_ID}/reject", json={"reason": "El gancho no convence"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["output"]["rejection"]["reason"] == "El gancho no convence"
    assert calls == []
    assert connection.row["status"] == "rejected"


def test_reject_a_missing_job_returns_404(monkeypatch) -> None:
    install(monkeypatch, make_row(), None)
    missing = UUID("99999999-8888-4777-8666-555555555555")

    with TestClient(api.app) as client:
        response = client.post(f"/v1/jobs/{missing}/reject", json={})

    assert response.status_code == 404


def test_a_finished_render_waits_for_approval_instead_of_succeeding(monkeypatch) -> None:
    """El render NUNCA termina en 'succeeded': queda a la espera de revision."""
    connection = FakeConnection(
        make_row(
            status="running",
            output=None,
            progress={"stage": "rendering", "message": "Renderizando", "percent": 90},
        )
    )
    monkeypatch.setattr(api, "init_database", lambda: None)
    monkeypatch.setattr(api, "get_connection", lambda: connection)
    monkeypatch.setattr(api.settings, "PUBLISHER_BASE_URL", None)

    with TestClient(api.app) as client:
        response = client.post(
            f"/v1/jobs/{JOB_ID}/complete",
            headers={"X-Worker-Lease": "lease-1", "Authorization": "Bearer pytest-worker-token"},
            json={"output": {"video_object_key": "renders/demo.mp4"}},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "awaiting_approval"
    assert "aprobaci" in response.json()["progress"]["message"].lower()


def test_job_status_enum_contains_the_approval_states() -> None:
    from core.schemas import JobStatus

    values = {status.value for status in JobStatus}
    assert {"awaiting_approval", "approved", "rejected"} <= values
