import os
import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

API_DIRECTORY = Path(__file__).parents[1] / "apps" / "api"
sys.path.insert(0, str(API_DIRECTORY))
sys.path.insert(0, str(Path(__file__).parents[1] / "packages" / "python"))
os.environ.setdefault("WORKER_API_TOKEN", "pytest-worker-token")

import main as api


class FakeStorage:
    def __init__(self):
        self.uploaded = None

    def upload_fileobj(self, file_obj, bucket, object_key, ExtraArgs):
        self.uploaded = (file_obj.read(), bucket, object_key, ExtraArgs)

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        assert operation == "get_object"
        assert ExpiresIn == 300
        return f"https://private.example/{Params['Key']}?signed=temporary"


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, *_):
        return FakeCursor(self.row)


class SequentialConnection(FakeConnection):
    def __init__(self, rows):
        self.rows = iter(rows)
        self.calls = []

    def execute(self, query, params=()):
        self.calls.append((query, params))
        return FakeCursor(next(self.rows))


def test_upload_asset_stores_metadata_without_exposing_object_key(monkeypatch) -> None:
    asset_id = uuid4()
    now = datetime.now(timezone.utc)
    storage = FakeStorage()
    monkeypatch.setattr(api, "init_database", lambda: None)
    monkeypatch.setattr(api, "get_storage_client", lambda: storage)
    monkeypatch.setattr(
        api,
        "get_connection",
        lambda: FakeConnection(
            {
                "asset_id": asset_id,
                "asset_type": "voice",
                "original_name": "voice.wav",
                "content_type": "audio/wav",
                "size_bytes": 12,
                "created_at": now,
            }
        ),
    )
    monkeypatch.setenv("S3_BUCKET", "private-assets")
    # El bucket se resuelve desde Settings, que es la fuente de verdad unica.
    monkeypatch.setattr(api.settings, "S3_BUCKET", "private-assets")

    with TestClient(api.app) as client:
        response = client.post(
            "/v1/assets",
            data={"asset_type": "voice"},
            files={"file": ("voice.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
        )

    assert response.status_code == 201
    assert response.json()["asset_id"] == str(asset_id)
    assert "object_key" not in response.json()
    assert storage.uploaded is not None
    assert storage.uploaded[0] == b"RIFF\x00\x00\x00\x00WAVE"
    assert storage.uploaded[1] == "private-assets"
    assert storage.uploaded[2].startswith("assets/voice/")


def test_upload_asset_rejects_unsupported_extension_before_storage(monkeypatch) -> None:
    monkeypatch.setattr(api, "init_database", lambda: None)
    monkeypatch.setattr(
        api,
        "get_storage_client",
        lambda: (_ for _ in ()).throw(AssertionError("storage must not be used")),
    )

    with TestClient(api.app) as client:
        response = client.post(
            "/v1/assets",
            data={"asset_type": "voice"},
            files={"file": ("voice.mp3", b"audio", "audio/mpeg")},
        )

    assert response.status_code == 415


def test_upload_asset_rejects_mismatched_media_header(monkeypatch) -> None:
    monkeypatch.setattr(api, "init_database", lambda: None)
    monkeypatch.setattr(
        api,
        "get_storage_client",
        lambda: (_ for _ in ()).throw(AssertionError("storage must not be used")),
    )

    with TestClient(api.app) as client:
        response = client.post(
            "/v1/assets",
            data={"asset_type": "voice"},
            files={"file": ("voice.wav", b"not a wave file", "audio/wav")},
        )

    assert response.status_code == 415


def test_job_media_uses_temporary_signed_url(monkeypatch) -> None:
    job_id = uuid4()
    monkeypatch.setattr(api, "init_database", lambda: None)
    monkeypatch.setattr(api, "get_storage_client", lambda: FakeStorage())
    monkeypatch.setattr(
        api,
        "get_connection",
        lambda: FakeConnection(
            {
                "worker": "research",
                "request": {},
                "output": {"audio_object_key": "audio/example.wav"},
            }
        ),
    )
    monkeypatch.setenv("S3_BUCKET", "private-assets")
    # El bucket se resuelve desde Settings, que es la fuente de verdad unica.
    monkeypatch.setattr(api.settings, "S3_BUCKET", "private-assets")

    with TestClient(api.app) as client:
        response = client.get(f"/v1/jobs/{job_id}/media/audio", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://private.example/audio/example.wav")


def test_create_job_resolves_selected_private_assets(monkeypatch) -> None:
    voice_id = uuid4()
    visual_id = uuid4()
    job_id = uuid4()
    now = datetime.now(timezone.utc)
    connection = SequentialConnection(
        [
            {
                "asset_id": voice_id,
                "object_key": f"assets/voice/{voice_id}.wav",
                "content_type": "audio/wav",
            },
            {
                "asset_id": visual_id,
                "asset_type": "image",
                "object_key": f"assets/image/{visual_id}.png",
                "content_type": "image/png",
            },
            {
                "job_id": job_id,
                "worker": "research",
                "status": "queued",
                "request": {},
                "progress": {"stage": "queued", "message": "Esperando worker", "percent": 0},
                "output": None,
                "error": None,
                "attempts": 0,
                "created_at": now,
                "updated_at": now,
            },
        ]
    )
    monkeypatch.setattr(api, "init_database", lambda: None)
    monkeypatch.setattr(api, "get_connection", lambda: connection)

    with TestClient(api.app) as client:
        response = client.post(
            "/v1/jobs",
            json={
                "worker": "research",
                "request": {
                    "topic": "Prueba de medios propios",
                    "source_url": "https://example.com/article",
                    "voice_reference_asset_id": str(voice_id),
                    "voice_reference_transcript": "Texto exacto de la muestra",
                    "voice_clone_authorized": True,
                    "visual_asset_id": str(visual_id),
                },
            },
        )

    assert response.status_code == 201
    persisted_request = json.loads(connection.calls[2][1][2])
    assert persisted_request["voice_reference"]["object_key"] == f"assets/voice/{voice_id}.wav"
    assert persisted_request["voice_reference"]["transcript"] == "Texto exacto de la muestra"
    assert persisted_request["visual_asset"]["object_key"] == f"assets/image/{visual_id}.png"
    assert "voice_clone_authorized" not in persisted_request
