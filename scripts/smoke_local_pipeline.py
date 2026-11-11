"""Run a disposable API/PostgreSQL pipeline smoke test against the local stack."""

from __future__ import annotations

import json
import os
import base64
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

import psycopg


ROOT = Path(__file__).resolve().parents[1]
API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def load_local_env() -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = ROOT / ".env"
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def api_request(path: str, *, method: str = "GET", payload: dict | None = None,
                headers: dict[str, str] | None = None) -> tuple[int, object]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = Request(
        f"{API_BASE_URL}{path}", data=body, headers=request_headers, method=method
    )
    try:
        with urlopen(request, timeout=20) as response:
            content = response.read()
            return response.status, json.loads(content) if content else None
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API {method} {path} devolvió HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"No se pudo conectar con {API_BASE_URL}: {error.reason}") from error


def resolve(env: dict[str, str], name: str, default: str = "") -> str:
    """Las variables de entorno del proceso tienen prioridad sobre el .env."""
    return os.environ.get(name) or env.get(name, default)


def clean_test_jobs(parent_id: UUID, env: dict[str, str]) -> None:
    with psycopg.connect(
        host=resolve(env, "POSTGRES_HOST", "127.0.0.1"),
        port=int(resolve(env, "POSTGRES_PORT", "5432")),
        user=resolve(env, "POSTGRES_USER", "content"),
        password=resolve(env, "POSTGRES_PASSWORD"),
        dbname=resolve(env, "POSTGRES_DB", "content"),
        connect_timeout=10,
    ) as connection:
        connection.execute("DELETE FROM jobs WHERE parent_job_id = %s", (parent_id,))
        connection.execute("DELETE FROM jobs WHERE job_id = %s", (parent_id,))


def smoke_private_storage(env: dict[str, str]) -> bool:
    required = (
        "S3_ENDPOINT_URL", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"
    )
    values = {name: resolve(env, name) for name in required}
    if any(not values[name] for name in required):
        print("SKIP: R2 no tiene sus cuatro variables S3 configuradas en .env.")
        return False

    import boto3

    boundary = f"----CodexSmoke{uuid4().hex}"
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
        "l2sAAAAASUVORK5CYII="
    )
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"asset_type\"\r\n\r\nimage\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"codex-smoke.png\"\r\n"
        "Content-Type: image/png\r\n\r\n"
    ).encode("utf-8") + png_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = Request(
        f"{API_BASE_URL}/v1/assets",
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )

    asset_id: UUID | None = None
    object_key: str | None = None
    storage = boto3.client(
        "s3",
        endpoint_url=values["S3_ENDPOINT_URL"],
        aws_access_key_id=values["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=values["S3_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
            assert response.status == 201
        asset_id = UUID(result["asset_id"])
        object_key = f"assets/image/{asset_id}.png"
        assert "object_key" not in result
        storage.head_object(Bucket=values["S3_BUCKET"], Key=object_key)
        signed_url = storage.generate_presigned_url(
            "get_object",
            Params={"Bucket": values["S3_BUCKET"], "Key": object_key},
            ExpiresIn=60,
        )
        with urlopen(signed_url, timeout=30) as response:
            assert response.status == 200 and response.read() == png_bytes
        print("PASS: upload a R2 privado, HEAD y descarga firmada del asset de prueba.")
        return True
    finally:
        if object_key:
            storage.delete_object(Bucket=values["S3_BUCKET"], Key=object_key)
        if asset_id:
            with psycopg.connect(
                host=resolve(env, "POSTGRES_HOST", "127.0.0.1"),
                port=int(resolve(env, "POSTGRES_PORT", "5432")),
                user=resolve(env, "POSTGRES_USER", "content"),
                password=resolve(env, "POSTGRES_PASSWORD"),
                dbname=resolve(env, "POSTGRES_DB", "content"),
                connect_timeout=10,
            ) as connection:
                connection.execute("DELETE FROM media_assets WHERE asset_id = %s", (asset_id,))
            print("Limpieza: objeto y metadatos del asset temporal eliminados.")


def run() -> None:
    env = load_local_env()
    token = env.get("WORKER_API_TOKEN") or os.environ.get("WORKER_API_TOKEN")
    if not token:
        raise RuntimeError("WORKER_API_TOKEN no está configurado en .env")

    _, jobs = api_request("/v1/jobs?limit=100")
    queued = [job for job in jobs if job["status"] == "queued"]
    if queued:
        counts: dict[str, int] = {}
        for job in queued:
            counts[job["worker"]] = counts.get(job["worker"], 0) + 1
        summary = ", ".join(f"{worker}: {count}" for worker, count in sorted(counts.items()))
        raise RuntimeError(
            f"No se prueba la reserva para no tomar trabajos existentes ({summary}). "
            "Espera a que terminen y vuelve a ejecutar."
        )

    parent_id: UUID | None = None
    auth_headers = {"Authorization": f"Bearer {token}"}
    try:
        topic = f"Prueba técnica local {uuid4()}"
        status_code, parent = api_request(
            "/v1/jobs",
            method="POST",
            payload={
                "worker": "research",
                "request": {
                    "topic": topic,
                    "style": "smoke-test",
                    "duration_seconds": 15,
                    "platforms": [],
                },
            },
        )
        assert status_code == 201 and parent["status"] == "queued"
        parent_id = UUID(parent["job_id"])

        _, claimed = api_request(
            "/v1/jobs/next?worker=research", method="POST", headers=auth_headers
        )
        assert claimed and UUID(claimed["job_id"]) == parent_id
        lease_headers = {
            **auth_headers,
            "X-Worker-Lease": claimed["lease_token"],
        }

        _, heartbeat = api_request(
            f"/v1/jobs/{parent_id}/heartbeat", method="POST", headers=lease_headers
        )
        assert heartbeat["status"] == "ok"
        api_request(
            f"/v1/jobs/{parent_id}/progress",
            method="POST",
            payload={"stage": "smoke_test", "message": "Validando API y PostgreSQL", "percent": 50},
            headers=lease_headers,
        )

        _, completed = api_request(
            f"/v1/jobs/{parent_id}/complete",
            method="POST",
            payload={
                "output": {
                    "research": {"source": "smoke://local", "sources": []},
                    "script": "Narración sintética de prueba; no se genera audio real.",
                    "audio_object_key": f"smoke-test/{parent_id}.wav",
                }
            },
            headers=lease_headers,
        )
        assert completed["status"] == "succeeded"

        _, jobs = api_request("/v1/jobs?limit=100")
        render = next(
            job for job in jobs
            if job["worker"] == "render" and job.get("parent_job_id") == str(parent_id)
        )
        render_id = UUID(render["job_id"])
        assert render["status"] == "queued"

        _, claimed_render = api_request(
            "/v1/jobs/next?worker=render", method="POST", headers=auth_headers
        )
        assert claimed_render and UUID(claimed_render["job_id"]) == render_id
        render_headers = {
            **auth_headers,
            "X-Worker-Lease": claimed_render["lease_token"],
        }
        _, failed_render = api_request(
            f"/v1/jobs/{render_id}/fail",
            method="POST",
            payload={"error": "Smoke test: no se adjuntaron WAV ni MP4 reales."},
            headers=render_headers,
        )
        assert failed_render["status"] == "failed"
        print("PASS: crear job, reservar, heartbeat, progreso, completar investigación,")
        print("      enlazar render por parent_job_id, reservar render y registrar fallo.")
        print("      La prueba no genera ni afirma archivos de audio/video.")
    finally:
        if parent_id is not None:
            clean_test_jobs(parent_id, env)
            print("Limpieza: jobs temporales eliminados de PostgreSQL.")

    smoke_private_storage(env)


if __name__ == "__main__":
    run()
