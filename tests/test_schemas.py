import pytest
from pydantic import ValidationError

from packages.python.core.schemas import (
    ContentRequest,
    JobCreate,
    JobRecord,
    JobProgress,
    JobStatus,
    WorkerType,
)


def test_content_request_uses_independent_platform_lists() -> None:
    first = ContentRequest(topic="Tema A")
    second = ContentRequest(topic="Tema B")

    first.platforms.append("youtube")

    assert second.platforms == []


def test_content_request_rejects_invalid_duration() -> None:
    with pytest.raises(ValidationError):
        ContentRequest(topic="Tema", duration_seconds=0)


def test_content_request_accepts_voice_design_choices() -> None:
    request = ContentRequest(
        topic="Tema",
        voice_gender="male",
        voice_age="middle-aged",
        voice_pitch="low pitch",
    )

    assert (request.voice_gender, request.voice_age, request.voice_pitch) == (
        "male",
        "middle-aged",
        "low pitch",
    )


def test_content_request_accepts_supported_social_platforms() -> None:
    request = ContentRequest(topic="Tema", platforms=["youtube", "reddit"])

    assert request.model_dump(mode="json")["platforms"] == ["youtube", "reddit"]


def test_content_request_rejects_unsupported_or_repeated_platforms() -> None:
    with pytest.raises(ValidationError):
        ContentRequest(topic="Tema", platforms=["linkedin"])
    with pytest.raises(ValidationError):
        ContentRequest(topic="Tema", platforms=["youtube", "youtube"])


def test_job_create_requires_supported_worker() -> None:
    with pytest.raises(ValidationError):
        JobCreate.model_validate(
            {"worker": "unknown", "request": {"topic": "Tema"}}
        )

    job = JobCreate.model_validate(
        {"worker": WorkerType.research, "request": {"topic": "Tema"}}
    )
    assert job.worker is WorkerType.research


def test_job_record_preserves_render_fields_in_request() -> None:
    request = {
        "topic": "Tema",
        "duration_seconds": 35,
        "script": "Guion de prueba",
        "audio_url": "https://cdn.example.com/audio.wav",
        "research": {"facts": ["Dato"]},
    }
    record = JobRecord.model_validate(
        {
            "job_id": "f2810a6b-75e3-49fd-b7d9-9d6cbef8502e",
            "worker": WorkerType.render,
            "status": JobStatus.queued,
            "request": request,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
        }
    )

    assert record.model_dump(mode="json")["request"] == request
    assert record.progress["stage"] == "queued"
    assert record.parent_job_id is None


def test_job_record_preserves_pipeline_parent_id() -> None:
    parent_id = "8c5211cb-8d52-42e8-9980-3b465a3ab57b"
    record = JobRecord.model_validate(
        {
            "job_id": "f2810a6b-75e3-49fd-b7d9-9d6cbef8502e",
            "parent_job_id": parent_id,
            "worker": WorkerType.render,
            "status": JobStatus.queued,
            "request": {"topic": "Tema"},
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
        }
    )

    assert str(record.parent_job_id) == parent_id


def test_voice_reference_requires_transcript_and_authorization() -> None:
    voice_asset_id = "f2810a6b-75e3-49fd-b7d9-9d6cbef8502e"
    with pytest.raises(ValidationError):
        ContentRequest(topic="Tema", voice_reference_asset_id=voice_asset_id)

    request = ContentRequest(
        topic="Tema",
        voice_reference_asset_id=voice_asset_id,
        voice_reference_transcript="Texto exacto de muestra",
        voice_clone_authorized=True,
    )
    assert request.voice_reference_asset_id is not None


def test_job_progress_rejects_percent_out_of_range() -> None:
    with pytest.raises(ValidationError):
        JobProgress(stage="rendering", message="Renderizando", percent=101)
