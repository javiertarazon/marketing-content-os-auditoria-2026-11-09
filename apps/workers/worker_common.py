"""Utilidades compartidas por los workers locales de Windows.

Reune lo que los dos workers (investigacion y render) necesitan igual:

- Carga del ``.env`` de la raiz del repositorio (``../../.env``) sin pisar las
  variables ya presentes en el entorno del proceso (el entorno gana siempre).
- Resolucion de la configuracion con mensajes de error claros cuando falta un valor.
- Cliente HTTP de la API con el contrato exacto que expone ``apps/api/main.py``:
  ``POST /v1/jobs/next``, ``POST /v1/jobs/{id}/heartbeat``, ``POST /v1/jobs/{id}/progress``,
  ``POST /v1/jobs/{id}/complete``, ``POST /v1/jobs/{id}/fail`` y ``POST /v1/workers/heartbeat``.
- Renovacion periodica del lease del trabajo (cada 60 s) en un hilo aparte.
- Bucle de trabajo: pide un job, lo procesa y vuelve a pedir otro.

La URL de la API se resuelve en este orden:

1. El argumento ``--base-url`` de la linea de comandos.
2. La variable de entorno ``API_BASE_URL`` del proceso.
3. La variable ``API_BASE_URL`` del ``.env`` **solo si apunta a un host local**; el
   ``.env`` del repo trae la direccion del despliegue externo ( tunel HTTPS) y el
   worker local debe hablar con ``http://127.0.0.1:8000``.
4. El valor por defecto ``http://127.0.0.1:8000``.

Aqui no se exige HTTPS: en local la API se sirve en HTTP plano.
"""

from __future__ import annotations

import inspect
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import boto3
import requests

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"
DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
POLL_SECONDS = 5.0
LEASE_RENEW_SECONDS = 60.0
FAIL_MESSAGE_LIMIT = 1900
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def load_env_file(env_file: Path | None = None) -> None:
    """Carga el ``.env`` de la raiz del repo. El entorno del proceso tiene prioridad.

    No se imprime ningun valor: el fichero contiene secretos.
    """
    path = env_file or DEFAULT_ENV_FILE
    if not path.is_file():
        logger.debug("No hay fichero .env en %s; se usa solo el entorno del proceso.", path)
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class WorkerConfig:
    """Configuracion efectiva de un worker local."""

    api_base_url: str
    worker_api_token: str
    worker: str
    s3_endpoint_url: str
    s3_bucket: str
    s3_access_key_id: str
    s3_secret_access_key: str
    hf_token: str
    script_space_url: str
    voice_studio_url: str
    youtube_api_key: str
    youtube_region_code: str


def _resolve_base_url(cli_value: str | None, process_value: str, env_value: str) -> str:
    """Aplica el orden de precedencia descrito en la cabecera del modulo."""
    for candidate in (cli_value, process_value):
        if candidate and candidate.strip():
            return candidate.strip().rstrip("/")
    if env_value.strip():
        host = (urlparse(env_value).hostname or "").lower()
        if host in LOCAL_HOSTS:
            return env_value.strip().rstrip("/")
        logger.info(
            "Se ignora API_BASE_URL del .env (%s): el worker local usa %s.",
            host,
            DEFAULT_API_BASE_URL,
        )
    return DEFAULT_API_BASE_URL


def resolve_config(worker: str, base_url: str | None = None) -> WorkerConfig:
    """Lee la configuracion del ``.env`` y del entorno del proceso.

    Lanza ``RuntimeError`` indicando que clave falta si no se puede seguir.
    """
    process_base_url = (os.environ.get("API_BASE_URL") or "").strip()
    load_env_file()
    env_base_url = (os.environ.get("API_BASE_URL") or "").strip()

    def value(name: str) -> str:
        return (os.environ.get(name) or "").strip()

    missing = [name for name in ("WORKER_API_TOKEN",) if not value(name)]
    if missing:
        raise RuntimeError(
            "Faltan estas variables de configuracion (definelas en el .env de la raiz "
            f"del repo o en el entorno): {', '.join(missing)}"
        )

    config = WorkerConfig(
        api_base_url=_resolve_base_url(base_url, process_base_url, env_base_url),
        worker_api_token=value("WORKER_API_TOKEN"),
        worker=worker,
        s3_endpoint_url=value("S3_ENDPOINT_URL"),
        s3_bucket=value("S3_BUCKET"),
        s3_access_key_id=value("S3_ACCESS_KEY_ID"),
        s3_secret_access_key=value("S3_SECRET_ACCESS_KEY"),
        hf_token=value("HF_TOKEN"),
        script_space_url=value("SCRIPT_SPACE_URL").rstrip("/"),
        voice_studio_url=value("VOICE_STUDIO_URL").rstrip("/"),
        youtube_api_key=value("YOUTUBE_API_KEY"),
        youtube_region_code=(value("YOUTUBE_REGION_CODE") or "VE").upper(),
    )
    if not config.s3_bucket or not config.s3_endpoint_url:
        raise RuntimeError(
            "Faltan S3_BUCKET o S3_ENDPOINT_URL: el worker necesita R2 para guardar "
            "el audio y los videos."
        )
    return config


def build_storage_client(config: WorkerConfig) -> Any:
    """Crea el cliente S3/R2 con las credenciales del ``.env``."""
    return boto3.client(
        "s3",
        endpoint_url=config.s3_endpoint_url,
        region_name="auto",
        aws_access_key_id=config.s3_access_key_id,
        aws_secret_access_key=config.s3_secret_access_key,
    )


def build_hf_client(space_url: str, token: str | None) -> Any:
    """Crea un cliente de ``gradio_client`` para un Space privado de Hugging Face."""
    from gradio_client import Client

    parameter = "token" if "token" in inspect.signature(Client).parameters else "hf_token"
    kwargs: dict[str, Any] = {}
    if token:
        kwargs[parameter] = token
    return Client(space_url, verbose=False, **kwargs)


class ApiClient:
    """Cliente de la API del Marketing Content OS con el contrato de ``apps/api/main.py``."""

    def __init__(self, config: WorkerConfig, worker_id: str | None = None) -> None:
        self.config = config
        self.base_url = config.api_base_url.rstrip("/")
        self.worker = config.worker
        self.worker_id = worker_id or uuid.uuid4().hex
        self.session = requests.Session()
        self.headers = {"Authorization": f"Bearer {config.worker_api_token}"}

    def lease_headers(self, lease_token: str) -> dict[str, str]:
        """Cabeceras con el token de reserva del trabajo."""
        return {**self.headers, "X-Worker-Lease": lease_token}

    def next_job(self) -> dict[str, Any] | None:
        """Reserva el siguiente job del tipo del worker. ``None`` si no hay cola."""
        response = self.session.post(
            f"{self.base_url}/v1/jobs/next",
            params={"worker": self.worker},
            headers=self.headers,
            timeout=30,
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        if not response.content.strip():
            return None
        payload = response.json()
        if not payload:
            return None
        return payload

    def job_heartbeat(self, job_id: str, lease_token: str) -> None:
        """Renueva el lease del trabajo."""
        response = self.session.post(
            f"{self.base_url}/v1/jobs/{job_id}/heartbeat",
            headers=self.lease_headers(lease_token),
            timeout=30,
        )
        response.raise_for_status()

    def report_progress(
        self, job_id: str, lease_token: str, stage: str, message: str, percent: int
    ) -> None:
        """Informa el avance del trabajo (percent entre 0 y 100)."""
        response = self.session.post(
            f"{self.base_url}/v1/jobs/{job_id}/progress",
            headers=self.lease_headers(lease_token),
            json={"stage": stage, "message": message, "percent": max(0, min(100, percent))},
            timeout=30,
        )
        response.raise_for_status()

    def complete_job(self, job_id: str, lease_token: str, output: dict[str, Any]) -> None:
        """Marca el trabajo como completado con su salida."""
        response = self.session.post(
            f"{self.base_url}/v1/jobs/{job_id}/complete",
            headers=self.lease_headers(lease_token),
            json={"output": output},
            timeout=60,
        )
        response.raise_for_status()

    def fail_job(self, job_id: str, lease_token: str, error: str) -> None:
        """Marca el trabajo como fallido (mensaje recortado al limite de la API)."""
        response = self.session.post(
            f"{self.base_url}/v1/jobs/{job_id}/fail",
            headers=self.lease_headers(lease_token),
            json={"error": str(error)[:FAIL_MESSAGE_LIMIT]},
            timeout=60,
        )
        response.raise_for_status()

    def worker_heartbeat(
        self, job_id: str | None = None, stage: str = "idle", stopped: bool = False
    ) -> None:
        """Informa la presencia del worker en el tablero de la API."""
        response = self.session.post(
            f"{self.base_url}/v1/workers/heartbeat",
            headers=self.headers,
            json={
                "worker_id": self.worker_id,
                "worker": self.worker,
                "current_job_id": str(job_id) if job_id else None,
                "stage": stage,
                "stopped": stopped,
            },
            timeout=20,
        )
        response.raise_for_status()


class LeaseRenewer:
    """Renueva el lease del trabajo en segundo plano y avisa si se pierde."""

    def __init__(self, client: ApiClient, job_id: str, lease_token: str) -> None:
        self.client = client
        self.job_id = job_id
        self.lease_token = lease_token
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        """Arranca el hilo de renovacion."""
        self._thread.start()

    def stop(self) -> None:
        """Detiene el hilo de renovacion."""
        self._stop.set()
        self._thread.join(timeout=5)

    @property
    def lost(self) -> bool:
        """``True`` si la renovacion del lease fallo alguna vez."""
        return self._lost.is_set()

    def ensure_active(self) -> None:
        """Lanza si el lease se perdio: no se envia un resultado tardio."""
        if self._lost.is_set():
            raise RuntimeError("El lease del trabajo se perdio; no se envia el resultado tardio.")

    def _run(self) -> None:
        while not self._stop.wait(LEASE_RENEW_SECONDS):
            try:
                self.client.job_heartbeat(self.job_id, self.lease_token)
            except requests.RequestException as error:
                self._lost.set()
                logger.warning(
                    "No se pudo renovar el lease del trabajo %s: %s", self.job_id, error
                )
                return
            try:
                self.client.worker_heartbeat(self.job_id, "working")
            except requests.RequestException as error:
                logger.warning("No se pudo actualizar la presencia del worker: %s", error)


def run_worker_loop(
    client: ApiClient,
    process_job: Callable[[dict[str, Any]], bool],
    *,
    once: bool = False,
    max_jobs: int | None = None,
    wait_seconds: float | None = None,
) -> int:
    """Bucle principal: pide trabajos y los procesa hasta agotar los limites.

    ``once`` procesa un unico trabajo y sale, ``max_jobs`` acota el total de trabajos
    y ``wait_seconds`` acota la espera cuando no hay cola (modo ``--once``).
    """
    processed = 0
    waited = 0.0
    try:
        while True:
            if max_jobs is not None and processed >= max_jobs:
                break
            try:
                client.worker_heartbeat(None, "idle")
            except requests.RequestException as error:
                logger.warning("No se pudo actualizar la presencia del worker: %s", error)
            try:
                job = client.next_job()
            except requests.RequestException as error:
                logger.warning("No se pudo pedir trabajo a la API: %s", error)
                job = None
            if job is None:
                if once and wait_seconds is not None and waited >= wait_seconds:
                    logger.info("No llego ningun trabajo en %.0f s; se sale.", waited)
                    break
                time.sleep(POLL_SECONDS)
                waited += POLL_SECONDS
                continue
            waited = 0.0
            try:
                process_job(job)
                processed += 1
            except Exception as error:  # noqa: BLE001 - el bucle no debe morir
                logger.error("Error procesando el trabajo %s: %s", job.get("job_id"), error)
            if once:
                break
    except KeyboardInterrupt:
        logger.info("Worker %s detenido por el usuario.", client.worker)
    finally:
        try:
            client.worker_heartbeat(None, "stopped", stopped=True)
        except requests.RequestException:
            pass
    return processed


def configure_logging(verbose: bool) -> None:
    """Configura el logging del worker (INFO y, con ``--verbose``, DEBUG)."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not verbose:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
