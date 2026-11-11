from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class WorkerType(str, Enum):
    research = "research"
    render = "render"


class WorkerHeartbeat(BaseModel):
    worker_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    worker: WorkerType
    current_job_id: UUID | None = None
    stage: str = Field(default="idle", min_length=1, max_length=100)
    stopped: bool = False


class WorkerInstanceStatus(BaseModel):
    worker_id: str
    worker: WorkerType
    state: str
    current_job_id: UUID | None = None
    stage: str
    last_seen: datetime
    online: bool


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    # El render deja el video listo y espera a que una persona lo apruebe.
    # Sin esta puerta, el video se publicaba sin revision.
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    rejected = "rejected"


class ResearchPlatform(str, Enum):
    youtube = "youtube"
    reddit = "reddit"
    x = "x"
    instagram = "instagram"
    tiktok = "tiktok"
    facebook = "facebook"


class ContentRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    style: str = Field(default="financial-news", min_length=1, max_length=100)
    duration_seconds: int = Field(default=35, ge=1, le=3600)
    voice_gender: Literal["female", "male"] = "female"
    voice_age: Literal["child", "teenager", "young adult", "middle-aged", "elderly"] = "young adult"
    voice_pitch: Literal[
        "very low pitch",
        "low pitch",
        "moderate pitch",
        "high pitch",
        "very high pitch",
    ] = "moderate pitch"
    platforms: list[ResearchPlatform] = Field(default_factory=list, max_length=6)
    source_url: HttpUrl | None = None
    voice_reference_asset_id: UUID | None = None
    voice_reference_transcript: str | None = Field(default=None, max_length=5000)
    voice_clone_authorized: bool = False
    visual_asset_id: UUID | None = None

    @field_validator("platforms")
    @classmethod
    def validate_platforms(
        cls, platforms: list[ResearchPlatform]
    ) -> list[ResearchPlatform]:
        if len(platforms) != len(set(platforms)):
            raise ValueError("No repitas plataformas de investigación")
        return platforms

    @model_validator(mode="after")
    def validate_voice_reference(self) -> "ContentRequest":
        if self.voice_reference_asset_id is None:
            if self.voice_reference_transcript or self.voice_clone_authorized:
                raise ValueError("La transcripcion y autorizacion requieren una muestra de voz")
            return self
        if not self.voice_reference_transcript or not self.voice_reference_transcript.strip():
            raise ValueError("La muestra de voz requiere su transcripcion exacta")
        if not self.voice_clone_authorized:
            raise ValueError("Confirma que tienes autorizacion para clonar esta voz")
        return self


class JobCreate(BaseModel):
    worker: WorkerType
    request: ContentRequest


class JobRecord(BaseModel):
    job_id: UUID
    parent_job_id: UUID | None = None
    worker: WorkerType
    status: JobStatus
    request: dict[str, Any]
    progress: dict[str, Any] = Field(
        default_factory=lambda: {
            "stage": "queued",
            "message": "En cola",
            "percent": 0,
        }
    )
    output: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 0
    created_at: datetime
    updated_at: datetime


class JobProgress(BaseModel):
    stage: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    percent: int = Field(ge=0, le=100)


class MediaAsset(BaseModel):
    asset_id: UUID
    asset_type: str
    original_name: str
    content_type: str
    size_bytes: int
    created_at: datetime


class JobCompletion(BaseModel):
    output: dict[str, Any]


class JobApproval(BaseModel):
    """Decision humana sobre un video ya renderizado.

    ``publish`` permite aprobar sin publicar (por ejemplo para revisarlo en
    otros canales antes). Si no se indica, se publica.
    """

    publish: bool = True
    platforms: list[ResearchPlatform] = Field(
        default_factory=list,
        max_length=6,
        description=(
            "Plataformas destino. Si se deja vacio se usan las del trabajo "
            "original."
        ),
    )
    note: Optional[str] = Field(
        default=None, max_length=1000, description="Motivo de la decision"
    )


class JobRejection(BaseModel):
    reason: Optional[str] = Field(
        default=None, max_length=1000, description="Motivo del rechazo"
    )


class JobFailure(BaseModel):
    error: str = Field(min_length=1, max_length=2000)
