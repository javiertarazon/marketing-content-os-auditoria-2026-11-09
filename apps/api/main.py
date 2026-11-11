import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated
from uuid import UUID, uuid4

import boto3
import httpx
import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from psycopg.rows import dict_row
# Settings import
from settings import get_settings

settings = get_settings()
# Los valores validados por Settings son la unica fuente de verdad.
DATABASE_URL = settings.DATABASE_URL
WORKER_API_TOKEN = settings.WORKER_API_TOKEN
JOB_LEASE_SECONDS = settings.JOB_LEASE_SECONDS
MAX_JOB_ATTEMPTS = settings.MAX_JOB_ATTEMPTS
MAX_ASSET_BYTES = settings.MAX_ASSET_BYTES
S3_ENDPOINT_URL = settings.S3_ENDPOINT_URL
S3_BUCKET = settings.S3_BUCKET
S3_ACCESS_KEY_ID = settings.S3_ACCESS_KEY_ID
S3_SECRET_ACCESS_KEY = settings.S3_SECRET_ACCESS_KEY
HF_TOKEN = settings.HF_TOKEN
SCRIPT_SPACE_URL = settings.SCRIPT_SPACE_URL
VOICE_STUDIO_URL = settings.VOICE_STUDIO_URL
YOUTUBE_API_KEY = settings.YOUTUBE_API_KEY

from core.schemas import (
    JobApproval,
    JobCompletion,
    JobCreate,
    JobFailure,
    JobProgress,
    JobRecord,
    JobRejection,
    JobStatus,
    MediaAsset,
    WorkerHeartbeat,
    WorkerInstanceStatus,
    WorkerType,
)

logger = logging.getLogger(__name__)


class AssetType(str, Enum):
    voice = "voice"
    video = "video"
    image = "image"


ASSET_FORMATS = {
    AssetType.voice: {".wav": "audio/wav"},
    AssetType.video: {".mp4": "video/mp4"},
    AssetType.image: {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    },
}


def get_connection() -> psycopg.Connection:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no esta configurada")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def get_storage_client():
    required = {
        "S3_ENDPOINT_URL": settings.S3_ENDPOINT_URL,
        "S3_BUCKET": settings.S3_BUCKET,
        "S3_ACCESS_KEY_ID": settings.S3_ACCESS_KEY_ID,
        "S3_SECRET_ACCESS_KEY": settings.S3_SECRET_ACCESS_KEY,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Almacenamiento R2/S3 sin configurar: {', '.join(missing)}",
        )
    if not settings.S3_ENDPOINT_URL.startswith("https://"):
        # HTTP solo se permite contra un servidor local (MinIO en desarrollo).
        # Fuera de localhost las credenciales traveling en claro son inaceptables.
        from urllib.parse import urlparse

        endpoint_host = (urlparse(settings.S3_ENDPOINT_URL).hostname or "").lower()
        local_hosts = {"localhost", "127.0.0.1", "::1", "minio", "0.0.0.0"}
        if endpoint_host not in local_hosts:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="S3_ENDPOINT_URL debe usar HTTPS para proteger las credenciales",
            )
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def has_valid_media_header(asset_type: AssetType, extension: str, header: bytes) -> bool:
    if asset_type is AssetType.voice:
        return header.startswith(b"RIFF") and header[8:12] == b"WAVE"
    if asset_type is AssetType.video:
        return len(header) >= 8 and header[4:8] == b"ftyp"
    if extension == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if extension == ".webp":
        return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    return False


def init_database() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id UUID PRIMARY KEY,
                worker TEXT NOT NULL CHECK (worker IN ('research', 'render')),
                parent_job_id UUID REFERENCES jobs(job_id),
                status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
                request JSONB NOT NULL,
                progress JSONB NOT NULL DEFAULT '{"stage":"queued","message":"En cola","percent":0}'::jsonb,
                output JSONB,
                error TEXT,
                lease_token TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS media_assets (
                asset_id UUID PRIMARY KEY,
                asset_type TEXT NOT NULL CHECK (asset_type IN ('voice', 'video', 'image')),
                original_name TEXT NOT NULL,
                object_key TEXT NOT NULL UNIQUE,
                content_type TEXT NOT NULL,
                size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        connection.execute(
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ"
        )
        # La puerta de aprobacion humana introduce tres estados nuevos. La
        # restricion CHECK original limitaba status a cuatro valores, asi que hay
        # que recrearla: un DROP IF EXISTS evita fallar si no existe todavia.
        connection.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_status_check")
        connection.execute(
            """
            ALTER TABLE jobs ADD CONSTRAINT jobs_status_check
            CHECK (status IN (
                'queued', 'running', 'succeeded', 'failed',
                'awaiting_approval', 'approved', 'rejected'
            ))
            """
        )
        connection.execute(
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS progress JSONB"
        )
        connection.execute(
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS parent_job_id UUID REFERENCES jobs(job_id)"
        )
        connection.execute(
            """
            UPDATE jobs
            SET progress = jsonb_build_object(
                'stage', status,
                'message', CASE status
                    WHEN 'queued' THEN 'En cola'
                    WHEN 'running' THEN 'En proceso'
                    WHEN 'succeeded' THEN 'Completado'
                    ELSE 'Fallido'
                END,
                'percent', CASE WHEN status = 'succeeded' THEN 100 ELSE 0 END
            )
            WHERE progress IS NULL
            """
        )
        connection.execute(
            """
            UPDATE jobs
            SET lease_expires_at = now() + (%s * interval '1 second')
            WHERE status = 'running' AND lease_expires_at IS NULL
            """,
            (JOB_LEASE_SECONDS,),
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS jobs_queue_idx ON jobs (worker, created_at) WHERE status = 'queued'"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS jobs_parent_job_idx ON jobs (parent_job_id) WHERE parent_job_id IS NOT NULL"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS worker_instances (
                worker_id TEXT PRIMARY KEY,
                worker TEXT NOT NULL CHECK (worker IN ('research', 'render')),
                state TEXT NOT NULL CHECK (state IN ('idle', 'working', 'stopped')),
                current_job_id UUID REFERENCES jobs(job_id) ON DELETE SET NULL,
                stage TEXT NOT NULL DEFAULT 'idle',
                last_seen TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS worker_instances_last_seen_idx ON worker_instances (last_seen DESC)"
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    yield


app = FastAPI(title="Marketing Content OS API", version="1.0.0", lifespan=lifespan)

cors_origins = [
    origin.strip()
    for origin in settings.CORS_ORIGINS.split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Worker-Lease"],
)


def require_worker_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    if not WORKER_API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WORKER_API_TOKEN no esta configurado",
        )
    expected = f"Bearer {WORKER_API_TOKEN}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de worker invalido",
            headers={"WWW-Authenticate": "Bearer"},
        )


def serialize_job(row: dict) -> JobRecord:
    return JobRecord(
        job_id=row["job_id"],
        parent_job_id=row.get("parent_job_id"),
        worker=row["worker"],
        status=row["status"],
        request=row["request"],
        progress=row.get("progress") or {
            "stage": row["status"],
            "message": "En cola" if row["status"] == "queued" else "En proceso",
            "percent": 100
            if row["status"] in ("succeeded", "approved", "rejected", "awaiting_approval")
            else 0,
        },
        output=row["output"],
        error=row["error"],
        attempts=row["attempts"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    try:
        with get_connection() as connection:
            connection.execute("SELECT 1")
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se puede conectar a PostgreSQL",
        ) from exc
    return {"status": "ok"}


@app.post(
    "/v1/workers/heartbeat",
    response_model=WorkerInstanceStatus,
    dependencies=[Depends(require_worker_token)],
)
def heartbeat_worker(heartbeat: WorkerHeartbeat) -> WorkerInstanceStatus:
    state = "stopped" if heartbeat.stopped else ("working" if heartbeat.current_job_id else "idle")
    with get_connection() as connection:
        row = connection.execute(
            """
            INSERT INTO worker_instances
                (worker_id, worker, state, current_job_id, stage, last_seen)
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (worker_id) DO UPDATE SET
                worker = EXCLUDED.worker,
                state = EXCLUDED.state,
                current_job_id = EXCLUDED.current_job_id,
                stage = EXCLUDED.stage,
                last_seen = now()
            RETURNING worker_id, worker, state, current_job_id, stage, last_seen,
                (last_seen >= now() - interval '90 seconds') AS online
            """,
            (
                heartbeat.worker_id,
                heartbeat.worker.value,
                state,
                heartbeat.current_job_id,
                heartbeat.stage,
            ),
        ).fetchone()
    return WorkerInstanceStatus(**row)


@app.get("/v1/workers", response_model=list[WorkerInstanceStatus])
def list_workers() -> list[WorkerInstanceStatus]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT worker_id, worker, state, current_job_id, stage, last_seen,
                (last_seen >= now() - interval '90 seconds' AND state <> 'stopped') AS online
            FROM worker_instances
            WHERE last_seen >= now() - interval '24 hours'
            ORDER BY worker, last_seen DESC
            """
        ).fetchall()
    return [WorkerInstanceStatus(**row) for row in rows]


@app.post("/v1/jobs", response_model=JobRecord, status_code=status.HTTP_201_CREATED)
def create_job(job: JobCreate) -> JobRecord:
    job_id = UUID(bytes=secrets.token_bytes(16), version=4)
    request = job.request.model_dump(mode="json")
    with get_connection() as connection:
        voice_asset_id = request.pop("voice_reference_asset_id")
        voice_transcript = request.pop("voice_reference_transcript")
        request.pop("voice_clone_authorized")
        visual_asset_id = request.pop("visual_asset_id")
        if voice_asset_id is not None:
            voice_asset = connection.execute(
                """
                SELECT asset_id, object_key, content_type
                FROM media_assets WHERE asset_id = %s AND asset_type = 'voice'
                """,
                (UUID(voice_asset_id),),
            ).fetchone()
            if voice_asset is None:
                raise HTTPException(status_code=404, detail="Muestra de voz no encontrada")
            request["voice_reference"] = {
                "asset_id": str(voice_asset["asset_id"]),
                "object_key": voice_asset["object_key"],
                "content_type": voice_asset["content_type"],
                "transcript": voice_transcript,
            }
        if visual_asset_id is not None:
            visual_asset = connection.execute(
                """
                SELECT asset_id, asset_type, object_key, content_type
                FROM media_assets
                WHERE asset_id = %s AND asset_type IN ('video', 'image')
                """,
                (UUID(visual_asset_id),),
            ).fetchone()
            if visual_asset is None:
                raise HTTPException(status_code=404, detail="Video o imagen no encontrada")
            request["visual_asset"] = {
                "asset_id": str(visual_asset["asset_id"]),
                "asset_type": visual_asset["asset_type"],
                "object_key": visual_asset["object_key"],
                "content_type": visual_asset["content_type"],
            }
        row = connection.execute(
            """
            INSERT INTO jobs (job_id, worker, status, request, progress)
            VALUES (%s, %s, 'queued', %s::jsonb,
                    '{"stage":"queued","message":"Esperando worker","percent":0}'::jsonb)
            RETURNING *
            """,
            (job_id, job.worker.value, json.dumps(request)),
        ).fetchone()
    return serialize_job(row)


@app.get("/v1/jobs", response_model=list[JobRecord])
def list_jobs(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[JobRecord]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT %s", (limit,)
        ).fetchall()
    return [serialize_job(row) for row in rows]


@app.get("/v1/assets", response_model=list[MediaAsset])
def list_assets() -> list[MediaAsset]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT asset_id, asset_type, original_name, content_type, size_bytes, created_at
            FROM media_assets ORDER BY created_at DESC LIMIT 200
            """
        ).fetchall()
    return [MediaAsset(**row) for row in rows]


@app.post("/v1/assets", response_model=MediaAsset, status_code=status.HTTP_201_CREATED)
def upload_asset(
    asset_type: Annotated[AssetType, Form()],
    file: Annotated[UploadFile, File()],
) -> MediaAsset:
    original_name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    extension = os.path.splitext(original_name)[1].lower()
    content_type = ASSET_FORMATS[asset_type].get(extension)
    if content_type is None:
        allowed = ", ".join(ASSET_FORMATS[asset_type])
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Formato no permitido para {asset_type.value}; usa: {allowed}",
        )
    file.file.seek(0, os.SEEK_END)
    size_bytes = file.file.tell()
    file.file.seek(0)
    if size_bytes == 0:
        raise HTTPException(status_code=400, detail="El archivo está vacío")
    if size_bytes > MAX_ASSET_BYTES:
        raise HTTPException(status_code=413, detail="El archivo supera el máximo de 250 MB")
    header = file.file.read(16)
    file.file.seek(0)
    if not has_valid_media_header(asset_type, extension, header):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="El contenido del archivo no coincide con su formato declarado",
        )

    asset_id = uuid4()
    object_key = f"assets/{asset_type.value}/{asset_id}{extension}"
    storage = get_storage_client()
    try:
        storage.upload_fileobj(
            file.file,
            settings.S3_BUCKET,
            object_key,
            ExtraArgs={"ContentType": content_type},
        )
    except (BotoCoreError, ClientError) as exc:
        logger.exception("No se pudo subir el medio a S3/R2")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo guardar el medio en R2/S3; revisa endpoint y permisos",
        ) from exc

    try:
        with get_connection() as connection:
            row = connection.execute(
                """
                INSERT INTO media_assets
                    (asset_id, asset_type, original_name, object_key, content_type, size_bytes)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING asset_id, asset_type, original_name, content_type, size_bytes, created_at
                """,
                (
                    asset_id,
                    asset_type.value,
                    original_name,
                    object_key,
                    content_type,
                    size_bytes,
                ),
            ).fetchone()
    except psycopg.Error as exc:
        try:
            storage.delete_object(Bucket=settings.S3_BUCKET, Key=object_key)
        except (BotoCoreError, ClientError):
            logger.exception("No se pudo limpiar un objeto huérfano de R2/S3")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="El medio se subió, pero no se pudo registrar en la biblioteca",
        ) from exc
    return MediaAsset(**row)


@app.get("/v1/jobs/{job_id}", response_model=JobRecord)
def get_job(job_id: UUID) -> JobRecord:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM jobs WHERE job_id = %s", (job_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    return serialize_job(row)


@app.get("/v1/jobs/{job_id}/media/{media_type}")
def get_job_media(job_id: UUID, media_type: str) -> RedirectResponse:
    if media_type not in {"audio", "video"}:
        raise HTTPException(status_code=404, detail="Tipo de medio no soportado")
    with get_connection() as connection:
        row = connection.execute(
            "SELECT worker, request, output FROM jobs WHERE job_id = %s",
            (job_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    if media_type == "audio":
        object_key = (
            (row["output"] or {}).get("audio_object_key")
            or (row["request"].get("voice_reference") or {}).get("object_key")
        )
    else:
        object_key = (row["output"] or {}).get("video_object_key")
    if not object_key:
        raise HTTPException(status_code=404, detail="El trabajo aún no tiene ese medio")
    try:
        url = get_storage_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.S3_BUCKET, "Key": object_key},
            ExpiresIn=300,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.exception("No se pudo generar el enlace temporal de R2/S3")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo obtener el medio desde R2/S3",
        ) from exc
    return RedirectResponse(url=url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@app.post("/v1/jobs/next", dependencies=[Depends(require_worker_token)])
def reserve_job(
    worker: WorkerType,
) -> dict | None:
    lease_token = secrets.token_urlsafe(32)
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET status = 'queued', lease_token = NULL, lease_expires_at = NULL,
                error = 'Worker lease expired; returned to queue', updated_at = now(),
                progress = '{"stage":"queued","message":"Reencolado tras perder conexión con el worker","percent":0}'::jsonb
            WHERE status = 'running' AND lease_expires_at <= now()
              AND attempts < %s
            """,
            (MAX_JOB_ATTEMPTS,),
        )
        connection.execute(
            """
            UPDATE jobs
            SET status = 'failed', lease_token = NULL, lease_expires_at = NULL,
                error = 'Worker lease expired after maximum attempts', updated_at = now(),
                progress = '{"stage":"failed","message":"Se agotaron los intentos del worker","percent":0}'::jsonb
            WHERE status = 'running' AND lease_expires_at <= now()
              AND attempts >= %s
            """,
            (MAX_JOB_ATTEMPTS,),
        )
        row = connection.execute(
            """
            WITH next_job AS (
                SELECT job_id FROM jobs
                WHERE worker = %s AND status = 'queued'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE jobs
            SET status = 'running', lease_token = %s,
                lease_expires_at = now() + (%s * interval '1 second'),
                attempts = attempts + 1, updated_at = now(),
                progress = jsonb_build_object(
                    'stage', 'starting',
                    'message', CASE worker
                        WHEN 'research' THEN 'Iniciando investigación'
                        ELSE 'Iniciando renderizado'
                    END,
                    'percent', 1
                )
            WHERE job_id = (SELECT job_id FROM next_job)
            RETURNING *
            """,
            (worker.value, lease_token, JOB_LEASE_SECONDS),
        ).fetchone()
    if row is None:
        return None
    record = serialize_job(row).model_dump(mode="json")
    record["lease_token"] = lease_token
    return record


@app.post("/v1/jobs/{job_id}/heartbeat", dependencies=[Depends(require_worker_token)])
def heartbeat_job(
    job_id: UUID,
    lease_token: Annotated[str | None, Header(alias="X-Worker-Lease")] = None,
) -> dict[str, str]:
    if not lease_token:
        raise HTTPException(status_code=401, detail="Falta el token de reserva del trabajo")
    with get_connection() as connection:
        row = connection.execute(
            """
            UPDATE jobs
            SET lease_expires_at = now() + (%s * interval '1 second'),
                updated_at = now()
            WHERE job_id = %s AND status = 'running' AND lease_token = %s
            RETURNING job_id
            """,
            (JOB_LEASE_SECONDS, job_id, lease_token),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=409, detail="Reserva invalida o trabajo no activo")
    return {"status": "ok"}


@app.post(
    "/v1/jobs/{job_id}/progress",
    response_model=JobRecord,
    dependencies=[Depends(require_worker_token)],
)
def report_progress(
    job_id: UUID,
    progress: JobProgress,
    lease_token: Annotated[str | None, Header(alias="X-Worker-Lease")] = None,
) -> JobRecord:
    if not lease_token:
        raise HTTPException(status_code=401, detail="Falta el token de reserva del trabajo")
    with get_connection() as connection:
        row = connection.execute(
            """
            UPDATE jobs
            SET progress = %s::jsonb, updated_at = now()
            WHERE job_id = %s AND status = 'running' AND lease_token = %s
            RETURNING *
            """,
            (json.dumps(progress.model_dump()), job_id, lease_token),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=409, detail="Reserva invalida o trabajo no activo")
    return serialize_job(row)


def update_leased_job(
    job_id: UUID,
    lease_token: str | None,
    *,
    new_status: JobStatus,
    output: dict | None = None,
    error: str | None = None,
) -> JobRecord:
    if not lease_token:
        raise HTTPException(status_code=401, detail="Falta el token de reserva del trabajo")
    with get_connection() as connection:
        row = connection.execute(
            """
            UPDATE jobs
            SET status = %s, output = %s::jsonb, error = %s,
                lease_token = NULL, lease_expires_at = NULL, updated_at = now(),
                progress = jsonb_build_object(
                    'stage', %s::text,
                    'message', %s::text,
                    'percent', %s::integer
                )
            WHERE job_id = %s AND status = 'running' AND lease_token = %s
            RETURNING *
            """,
            (
                new_status.value,
                json.dumps(output) if output is not None else None,
                error,
                "completed" if new_status == JobStatus.succeeded else "failed",
                "Completado" if new_status == JobStatus.succeeded else "Fallido",
                100 if new_status == JobStatus.succeeded else 0,
                job_id,
                lease_token,
            ),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=409, detail="Reserva invalida o trabajo no activo")
    return serialize_job(row)


@app.post("/v1/jobs/{job_id}/complete", response_model=JobRecord)
def complete_job(
    job_id: UUID,
    completion: JobCompletion,
    lease_token: Annotated[str | None, Header(alias="X-Worker-Lease")] = None,
    _: None = Depends(require_worker_token),
) -> JobRecord:
    if not lease_token:
        raise HTTPException(status_code=401, detail="Falta el token de reserva del trabajo")

    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM jobs WHERE job_id = %s AND status = 'running' AND lease_token = %s FOR UPDATE",
            (job_id, lease_token),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="Reserva invalida o trabajo no activo")

        if row["worker"] == WorkerType.research.value:
            required = {"research", "script"}
            missing = required.difference(completion.output)
            if not completion.output.get("audio_object_key") and not completion.output.get("audio_url"):
                missing.add("audio_object_key")
            if missing:
                raise HTTPException(
                    status_code=422,
                    detail=f"El resultado de investigacion no incluye: {', '.join(sorted(missing))}",
                )
            render_request = {**row["request"], **completion.output}
            connection.execute(
                """
                INSERT INTO jobs (job_id, worker, parent_job_id, status, request, progress)
                VALUES (%s, 'render', %s, 'queued', %s::jsonb,
                        '{"stage":"queued","message":"Esperando worker de render","percent":0}'::jsonb)
                """,
                (
                    UUID(bytes=secrets.token_bytes(16), version=4),
                    job_id,
                    json.dumps(render_request),
                ),
            )

        completed_message = (
            "Investigación completada"
            if row["worker"] == WorkerType.research.value
            else "Video listo, pendiente de aprobación"
        )
        # Un render NO termina como 'succeeded': el video queda a la espera de
        # que una persona lo revise y lo apruebe. Solo entonces se publica.
        completed_status = (
            JobStatus.succeeded.value
            if row["worker"] == WorkerType.research.value
            else JobStatus.awaiting_approval.value
        )
        completed_stage = (
            "completed"
            if row["worker"] == WorkerType.research.value
            else "awaiting_approval"
        )
        updated = connection.execute(
            """
            UPDATE jobs
            SET status = %s, output = %s::jsonb, error = NULL,
                lease_token = NULL, lease_expires_at = NULL, updated_at = now(),
                progress = jsonb_build_object('stage', %s::text, 'message', %s::text, 'percent', 100)
            WHERE job_id = %s
            RETURNING *
            """,
            (
                completed_status,
                json.dumps(completion.output),
                completed_stage,
                completed_message,
                job_id,
            ),
        ).fetchone()
    return serialize_job(updated)


@app.post("/v1/jobs/{job_id}/fail", response_model=JobRecord)
def fail_job(
    job_id: UUID,
    failure: JobFailure,
    lease_token: Annotated[str | None, Header(alias="X-Worker-Lease")] = None,
    _: None = Depends(require_worker_token),
) -> JobRecord:
    return update_leased_job(
        job_id, lease_token, new_status=JobStatus.failed, error=failure.error
    )


# ---------------------------------------------------------------------------
# Puerta de aprobacion humana
#
# El render deja el video en 'awaiting_approval'. Desde aqui una persona
# revisa el MP4 y decide: aprobar (y opcionalmente publicar) o rechazar.
# La publicacion NUNCA ocurre sin pasar por esta puerta.
# ---------------------------------------------------------------------------


def publish_video(job: JobRecord, platforms: list[str]) -> dict:
    """Encola el video en el scheduler a traves del publisher-service.

    Devuelve un diccionario con el resultado. Nunca lanza: si el publicador
    falla, la aprobacion sigue siendo valida y el error queda registrado para
    poder reintentar.
    """
    publisher_base = (settings.PUBLISHER_BASE_URL or "").rstrip("/")
    if not publisher_base:
        return {
            "published": False,
            "error": "PUBLISHER_BASE_URL no esta configurado en la API",
        }

    output = job.output or {}
    video_key = output.get("video_object_key")
    if not video_key:
        return {
            "published": False,
            "error": "El trabajo no tiene video_object_key en su salida",
        }

    storage_base = (settings.STORAGE_PUBLIC_BASE_URL or "").rstrip("/")
    if not storage_base:
        return {
            "published": False,
            "error": "STORAGE_PUBLIC_BASE_URL no esta configurado en la API",
        }

    request = job.request or {}
    title = str(request.get("topic") or "Contenido de Marketing Content OS")[:500]
    payload = {
        "video_url": f"{storage_base}/{video_key}",
        "title": title,
        "platforms": platforms,
    }

    try:
        response = httpx.post(
            f"{publisher_base}/v1/publish",
            json=payload,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        return {"published": False, "error": f"No se pudo contactar con el publicador: {exc}"}

    if response.status_code >= 400:
        detail = response.text[:500]
        return {
            "published": False,
            "error": f"El publicador respondio {response.status_code}: {detail}",
        }

    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:500]}
    return {"published": True, "response": body, "request": payload}


@app.post("/v1/jobs/{job_id}/approve", response_model=JobRecord)
def approve_job(job_id: UUID, approval: JobApproval | None = None) -> JobRecord:
    """Aprueba un video renderizado y, salvo que se indique lo contrario, lo publica.

    Es idempotente: volver a aprobar un trabajo ya aprobado reintenta la
    publicacion, que es la forma de recuperarla si el scheduler estaba caido.
    """
    decision = approval or JobApproval()

    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (job_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Trabajo no encontrado")
        if row["status"] not in (
            JobStatus.awaiting_approval.value,
            JobStatus.approved.value,
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"El trabajo esta en '{row['status']}' y no se puede aprobar. "
                    "Solo los videos en 'awaiting_approval' o ya 'approved'."
                ),
            )

        job = serialize_job(row)

        request = job.request or {}
        platforms = [str(p) for p in (decision.platforms or request.get("platforms") or [])]
        if not platforms:
            raise HTTPException(
                status_code=422,
                detail=(
                    "El trabajo no indica plataformas de destino. Indicalas al "
                    "aprobar (por ejemplo ['tiktok', 'instagram', 'youtube'])."
                ),
            )

        publish_result: dict | None = None
        if decision.publish:
            publish_result = publish_video(job, platforms)

        output = dict(job.output or {})
        output["approval"] = {
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "publish_requested": decision.publish,
            "platforms": platforms,
            "note": decision.note,
        }
        if publish_result is not None:
            output["publication"] = publish_result

        message = "Video aprobado"
        if publish_result is not None:
            message = (
                "Video aprobado y publicado"
                if publish_result.get("published")
                else "Video aprobado, pero la publicacion fallo"
            )

        updated = connection.execute(
            """
            UPDATE jobs
            SET status = 'approved', output = %s::jsonb, updated_at = now(),
                progress = jsonb_build_object(
                    'stage', 'approved', 'message', %s::text, 'percent', 100
                )
            WHERE job_id = %s
            RETURNING *
            """,
            (json.dumps(output), message, job_id),
        ).fetchone()
    return serialize_job(updated)


@app.post("/v1/jobs/{job_id}/reject", response_model=JobRecord)
def reject_job(job_id: UUID, rejection: JobRejection | None = None) -> JobRecord:
    """Rechaza un video renderizado: no se publica y se queda registrado."""
    reason = (rejection or JobRejection()).reason

    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (job_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Trabajo no encontrado")
        if row["status"] not in (
            JobStatus.awaiting_approval.value,
            JobStatus.approved.value,
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"El trabajo esta en '{row['status']}' y no se puede rechazar."
                ),
            )

        job = serialize_job(row)
        output = dict(job.output or {})
        output["rejection"] = {
            "rejected_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        }

        updated = connection.execute(
            """
            UPDATE jobs
            SET status = 'rejected', output = %s::jsonb, updated_at = now(),
                progress = jsonb_build_object(
                    'stage', 'rejected', 'message', 'Video rechazado', 'percent', 100
                )
            WHERE job_id = %s
            RETURNING *
            """,
            (json.dumps(output), job_id),
        ).fetchone()
    return serialize_job(updated)
