"""Worker local de render de video (equivalente al notebook 02).

Sustituye a ``notebooks/02_render_remotion.ipynb`` en Windows: habla con la API por HTTP
plano en ``http://127.0.0.1:8000``, descarga el audio (y el asset visual si existe) de
R2, genera el proyecto Remotion, renderiza y valida el MP4 antes de subirlo.

Diferencias respecto al notebook, todas por el entorno local:

- El proyecto Remotion se escribe en un directorio escribible (por defecto
  ``%TEMP%\\marketing-content-os-renderer``), no en ``/content`` de Linux. El directorio
  es persistente para no reinstalar ``node_modules`` en cada trabajo.
- ``ffmpeg`` y ``ffprobe`` se localizan de forma portable: primero en el ``PATH``,
  luego en la carpeta de WinGet y por ultimo en ``C:\\ProgramData\\chocolatey\\bin``.
- No se pasa ``--browser-executable=/usr/bin/chromium``: si hay un Chrome o Edge local
  se usa su ruta, y si no, Remotion usa su propio Chrome Headless Shell.
- ``npm`` y ``npx`` se invocan como ``.cmd`` en Windows.

Entrada: el trabajo de render, cuyo ``request`` trae ``script`` y ``audio_object_key``
(y opcionalmente el asset visual). Salida::

    {"output": {"video_object_key": "renders/<job_id>.mp4"}}
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

# Permite ejecutar el worker directamente con su ruta
# (python apps/workers/local_render_worker.py): sys.path[0] seria el propio
# directorio del modulo y "apps" no seria importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from apps.workers.remotion_project import write_remotion_project  # noqa: E402
from apps.workers.worker_common import (  # noqa: E402
    EXIT_API_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    ApiClient,
    LeaseRenewer,
    WorkerAuthError,
    WorkerUnavailableError,
    build_storage_client,
    configure_logging,
    report_progress_safe,
    resolve_config,
    run_worker_loop,
)

logger = logging.getLogger(__name__)

PROJECT_DIR_NAME = "marketing-content-os-renderer"
# La coma final es lo que convierte esto en una TUPLA de patrones. Sin ella
# queda una sola cadena y el bucle de find_media_tool la recorre caracter a
# caracter, con lo que glob("/") lanza NotImplementedError.
WINGET_FFMPEG_GLOBS = (
    "Users/*/AppData/Local/Microsoft/WinGet/Packages/"
    "Gyan.FFmpeg_Microsoft.Winget.Source_*/ffmpeg-*/bin",
)
CHOCO_BIN = Path(r"C:\ProgramData\chocolatey\bin")
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
ASSET_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
}
# Hosts locales: se permiten en http plano porque la API y el storage local
# se sirven sin TLS.
LOCAL_AUDIO_HOSTS = {"localhost", "127.0.0.1", "::1"}


def find_media_tool(name: str) -> str | None:
    """Localiza ``ffmpeg``/``ffprobe``: PATH, WinGet o Chocolatey."""
    found = shutil.which(name)
    if found:
        return found
    candidates: list[Path] = []
    system_drive = Path(os.environ.get("SystemDrive", "C:") + "/")
    for pattern in WINGET_FFMPEG_GLOBS:
        candidates.extend(system_drive.glob(pattern))
    candidates.append(CHOCO_BIN)
    for directory in candidates:
        executable = directory / f"{name}.exe"
        if executable.is_file():
            return str(executable)
    return None


def find_local_browser() -> str | None:
    """Devuelve la ruta de un Chrome o Edge local, si hay alguno."""
    local_appdata = Path.home() / "AppData" / "Local"
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    edge_local = local_appdata / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    if edge_local.is_file():
        return str(edge_local)
    return None


def resolve_node_tool(name: str) -> str:
    """Devuelve el ejecutable de Node (``npm``/``npx``) disponible en Windows."""
    found = shutil.which(name)
    if found:
        return found
    for suffix in (".cmd", ".exe", ".bat"):
        found = shutil.which(f"{name}{suffix}")
        if found:
            return found
    raise RuntimeError(
        f"No se encontro {name}. Instala Node.js (https://nodejs.org) y abre una terminal "
        "nueva para que node quede en el PATH."
    )


def _process_group_options() -> dict[str, Any]:
    """Opciones de ``subprocess`` para que el comando tenga su propio grupo.

    En POSIX el hijo pasa a ser lider de sesion (grupo de procesos propio) y en
    Windows se crea un grupo de procesos nuevo. Asi, al matar el grupo, mueren
    tambien los nietos (node, Chrome) que el comando dejo abiertos.
    """
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _kill_process_group(process: "subprocess.Popen[Any]") -> None:
    """Mata el grupo de procesos completo, nietos incluidos."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            check=False,
            capture_output=True,
        )
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        process.kill()


def run_command(command: list[str], cwd: Path, timeout: int = 3600) -> str:
    """Ejecuta un comando externo y devuelve su salida (ultimas lineas).

    El comando se lanza en su propio grupo de procesos. Si se agota el tiempo se
    mata el grupo entero (no solo al proceso directo), que es lo que dejaba
    bloqueados los temporales en Windows, y se levanta un ``RuntimeError`` como
    en un fallo del comando: asi el fallback de navegador tambien se activa
    cuando el comando se queda colgado.
    """
    logger.info("Ejecutando: %s", " ".join(command))
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **_process_group_options(),
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        _kill_process_group(process)
        try:
            output, _ = process.communicate(timeout=30)
        except Exception:  # noqa: BLE001 - el proceso ya esta muerto
            output = ""
        raise RuntimeError(
            f"El comando '{command[0]}' supero el limite de {timeout} s y se detuvo."
        ) from error
    output = output or ""
    if process.returncode != 0:
        raise RuntimeError(
            f"El comando '{command[0]} {command[1] if len(command) > 1 else ''}' termino con "
            f"codigo {process.returncode}: {output.strip()[-1200:]}"
        )
    return output.strip()[-2000:]


def probe_media(ffprobe: str, path: Path) -> dict[str, Any]:
    """Devuelve la informacion de ``ffprobe`` en JSON."""
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def audio_duration(ffprobe: str, path: Path) -> float:
    """Duracion en segundos del WAV de la narracion."""
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    duration = float(completed.stdout.strip())
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("No se pudo determinar la duracion de la narracion")
    return duration


def resolve_visual_asset(request: dict[str, Any]) -> dict[str, Any] | None:
    """Localiza el asset visual en ``visual_asset`` o en ``media_assets``."""
    asset = request.get("visual_asset")
    if isinstance(asset, dict) and asset.get("object_key"):
        return asset
    media_assets = request.get("media_assets")
    if isinstance(media_assets, list):
        for item in media_assets:
            if isinstance(item, dict) and item.get("object_key"):
                return item
    return None


def audio_allowed_hosts() -> set[str]:
    """Hosts desde los que el worker puede descargar audio.

    Se configuran con ``STORAGE_PUBLIC_BASE_URL`` (host publico del bucket de
    medios) o con ``R2_PUBLIC_HOSTS`` (lista de hosts separados por comas).
    """
    hosts: set[str] = set()
    base_url = (os.environ.get("STORAGE_PUBLIC_BASE_URL") or "").strip()
    if base_url:
        host = (urlparse(base_url).hostname or "").lower()
        if host:
            hosts.add(host)
    for item in (os.environ.get("R2_PUBLIC_HOSTS") or "").split(","):
        host = item.strip().lower()
        if host:
            hosts.add(host)
    return hosts


def validate_audio_url(url: str, allowed_hosts: Iterable[str] | None = None) -> str:
    """Valida la URL de audio antes de descargarla (defensa anti-SSRF).

    Solo se admiten ``http`` y ``https``; fuera de los hosts locales el esquema
    tiene que ser ``https`` y el host debe estar en la allowlist. Si la URL no
    cumple las reglas se lanza ``ValueError`` con un mensaje claro.
    """
    candidate = str(url or "").strip()
    parsed = urlparse(candidate)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(
            "URL de audio no permitida: solo se admiten http y https "
            f"(esquema recibido: {scheme or 'ninguno'})."
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL de audio no permitida: no tiene host.")
    if host in LOCAL_AUDIO_HOSTS:
        return candidate
    if scheme != "https":
        raise ValueError(f"URL de audio no permitida: {host} tiene que usar https.")
    allowed = {
        str(item).strip().lower()
        for item in (audio_allowed_hosts() if allowed_hosts is None else allowed_hosts)
        if str(item).strip()
    }
    if host not in allowed:
        raise ValueError(
            f"URL de audio no permitida: el host {host} no esta en la allowlist "
            "(STORAGE_PUBLIC_BASE_URL o R2_PUBLIC_HOSTS)."
        )
    return candidate


def download_audio(
    request: dict[str, Any], destination: Path, storage: Any, bucket: str
) -> None:
    """Descarga el WAV de la narracion desde R2 (o desde ``audio_url``)."""
    object_key = request.get("audio_object_key")
    if object_key:
        storage.download_file(bucket, object_key, str(destination))
        return
    audio_url = request.get("audio_url")
    if audio_url:
        validate_audio_url(audio_url)
        response = requests.get(audio_url, timeout=120)
        response.raise_for_status()
        destination.write_bytes(response.content)
        return
    raise ValueError("El trabajo requiere guion y audio")


def prepare_project(
    public_dir: Path, request: dict[str, Any], storage: Any, bucket: str
) -> dict[str, Any] | None:
    """Descarga el asset visual (si lo hay) y devuelve sus props de Remotion."""
    visual_props = None
    asset = resolve_visual_asset(request)
    if asset:
        suffix = ASSET_SUFFIXES.get(asset.get("content_type", ""))
        if suffix is None:
            raise ValueError(
                "Formato de imagen/video no compatible para renderizar: "
                f"{asset.get('content_type', 'desconocido')}"
            )
        filename = f"background{suffix}"
        storage.download_file(bucket, asset["object_key"], str(public_dir / filename))
        visual_props = {"asset_type": asset.get("asset_type", "image"), "filename": filename}
    return visual_props


def validate_video(plan_path: Path, ffprobe: str, expected_duration: float) -> None:
    """Valida el MP4: H.264, 1080x1920, AAC y duracion coherente con el WAV."""
    info = probe_media(ffprobe, plan_path)
    video_stream = next(
        (stream for stream in info.get("streams", []) if stream.get("codec_type") == "video"),
        None,
    )
    audio_stream = next(
        (stream for stream in info.get("streams", []) if stream.get("codec_type") == "audio"),
        None,
    )
    rendered_duration = float(info.get("format", {}).get("duration", 0))
    if not video_stream or video_stream.get("codec_name") != "h264":
        raise RuntimeError("El MP4 no contiene una pista de video H.264")
    if (video_stream.get("width"), video_stream.get("height")) != (1080, 1920):
        raise RuntimeError("El MP4 no tiene la resolucion vertical 1080x1920")
    if not audio_stream or audio_stream.get("codec_name") != "aac":
        raise RuntimeError("El MP4 no contiene audio AAC")
    if rendered_duration <= 0 or abs(rendered_duration - expected_duration) > 1:
        raise RuntimeError(
            f"La duracion del MP4 ({rendered_duration:.2f} s) no coincide con la narracion "
            f"({expected_duration:.2f} s)"
        )


def process_render_job(
    client: ApiClient,
    job: dict[str, Any],
    work_dir: Path,
) -> bool:
    """Procesa un trabajo de render de principio a fin."""
    job_id = str(job["job_id"])
    lease_token = job["lease_token"]
    renewer = LeaseRenewer(client, job_id, lease_token)
    renewer.start()
    try:
        client.worker_heartbeat(job_id, "starting")
        client.job_heartbeat(job_id, lease_token)

        request = job.get("request") or {}
        if not request.get("script") or not (
            request.get("audio_object_key") or request.get("audio_url")
        ):
            raise ValueError("El trabajo requiere guion y audio")

        ffprobe = find_media_tool("ffprobe")
        ffmpeg = find_media_tool("ffmpeg")
        if not ffprobe or not ffmpeg:
            raise RuntimeError(
                "No se encontraron ffmpeg ni ffprobe. Instala FFmpeg (winget install "
                "Gyan.FFmpeg) y vuelve a ejecutar el worker; se buscan en el PATH, en la "
                "carpeta de WinGet y en C:\\ProgramData\\chocolatey\\bin."
            )

        config = client.config
        storage = build_storage_client(config)
        report_progress_safe(
            client,
            job_id,
            lease_token,
            "media_download",
            "Descargando audio y medios privados de R2",
            15,
        )

        with tempfile.TemporaryDirectory(prefix="marketing-render-") as job_tmp:
            job_tmp_dir = Path(job_tmp)
            audio_path = job_tmp_dir / "voice.wav"
            download_audio(request, audio_path, storage, config.s3_bucket)
            narration_duration = audio_duration(ffprobe, audio_path)

            report_progress_safe(
                client,
                job_id,
                lease_token,
                "media_ready",
                "Medios listos; preparando edicion",
                35,
            )
            public_dir = work_dir / "public"
            public_dir.mkdir(parents=True, exist_ok=True)
            visual_props = prepare_project(public_dir, request, storage, config.s3_bucket)
            props = {
                "topic": request.get("topic", ""),
                "script": request["script"],
                "duration_seconds": request.get("duration_seconds", 35),
                "audio_duration_seconds": narration_duration,
                "visual_asset": visual_props,
            }
            plan = write_remotion_project(work_dir, props)
            shutil.copyfile(audio_path, plan.audio_path)

            npm = resolve_node_tool("npm")
            npx = resolve_node_tool("npx")
            report_progress_safe(
                client,
                job_id,
                lease_token,
                "dependencies",
                "Instalando dependencias de Remotion",
                45,
            )
            if not (work_dir / "node_modules").is_dir():
                run_command([npm, "install", "--no-audit", "--no-fund"], cwd=work_dir, timeout=1800)
            try:
                run_command([npx, "remotion", "browser", "ensure"], cwd=work_dir, timeout=1800)
            except RuntimeError as error:
                browser = find_local_browser()
                if not browser:
                    raise RuntimeError(
                        "Remotion no pudo preparar su navegador y no hay un Chrome o Edge "
                        f"local: {error}"
                    ) from error
                logger.warning("No se pudo preparar el navegador de Remotion: %s", error)

            renewer.ensure_active()
            report_progress_safe(
                client,
                job_id,
                lease_token,
                "rendering",
                "Editando y renderizando el video",
                50,
            )
            video_path = job_tmp_dir / f"{job_id}.mp4"
            command = plan.render_command(video_path, npx=npx)
            browser = find_local_browser()
            if browser:
                command.append(f"--browser-executable={browser}")
            run_command(command, cwd=work_dir, timeout=3600)

            if not video_path.is_file() or video_path.stat().st_size == 0:
                raise RuntimeError("Remotion no produjo un MP4 valido")

            report_progress_safe(
                client,
                job_id,
                lease_token,
                "video_validation",
                "Validando el MP4 final",
                80,
            )
            validate_video(video_path, ffprobe, narration_duration)

            report_progress_safe(
                client,
                job_id,
                lease_token,
                "video_storage",
                "Subiendo video terminado a R2 privado",
                85,
            )
            renewer.ensure_active()
            video_key = f"renders/{job_id}.mp4"
            storage.upload_file(
                str(video_path),
                config.s3_bucket,
                video_key,
                ExtraArgs={"ContentType": "video/mp4"},
            )
            renewer.ensure_active()
            client.complete_job(job_id, lease_token, {"video_object_key": video_key})
            logger.info("Video listo: %s", job_id)
        return True
    except Exception as error:  # noqa: BLE001
        logger.error("El trabajo %s no se pudo completar: %s", job_id, error)
        try:
            client.fail_job(job_id, lease_token, str(error))
        except requests.RequestException:
            logger.error(
                "No se pudo informar el fallo; la API recuperara el trabajo al vencer el lease."
            )
        return False
    finally:
        renewer.stop()


def build_parser() -> argparse.ArgumentParser:
    """Parser de la linea de comandos del worker de render."""
    parser = argparse.ArgumentParser(
        description="Worker local de render de video del Marketing Content OS."
    )
    parser.add_argument("--once", action="store_true", help="Procesa un trabajo y sale.")
    parser.add_argument("--max-jobs", type=int, default=None, help="Maximo de trabajos a procesar.")
    parser.add_argument("--topic", default=None, help="Tema del trabajo que se quiere procesar.")
    parser.add_argument(
        "--base-url", default=None, help="URL de la API (por defecto http://127.0.0.1:8000)."
    )
    parser.add_argument("--verbose", action="store_true", help="Log en modo DEBUG.")
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=60.0,
        help="Segundos que se espera un trabajo en modo --once (por defecto 60).",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help=(
            "Directorio del proyecto Remotion "
            "(por defecto, marketing-content-os-renderer en el temporal del sistema)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del worker de render."""
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)
    config = resolve_config("render", args.base_url)
    logger.info("Worker de render contra %s", config.api_base_url)
    client = ApiClient(config)
    work_dir = (
        Path(args.work_dir) if args.work_dir else Path(tempfile.gettempdir()) / PROJECT_DIR_NAME
    )
    work_dir.mkdir(parents=True, exist_ok=True)

    def process(job: dict[str, Any]) -> bool:
        topic = (job.get("request") or {}).get("topic", "")
        if args.topic and topic and args.topic.lower() not in topic.lower():
            logger.warning(
                "El trabajo %s tiene el tema %r y se pidio %r; se procesa igualmente "
                "porque la cola es FIFO y el lease ya esta tomado.",
                job.get("job_id"),
                topic,
                args.topic,
            )
        return process_render_job(client, job, work_dir)

    try:
        processed = run_worker_loop(
            client,
            process,
            once=args.once,
            max_jobs=args.max_jobs,
            wait_seconds=args.wait_seconds,
        )
    except WorkerAuthError as error:
        logger.error("El worker de render se detiene: %s", error)
        return EXIT_CONFIG_ERROR
    except WorkerUnavailableError as error:
        logger.error("El worker de render se detiene: %s", error)
        return EXIT_API_ERROR
    logger.info("Trabajos procesados: %s", processed)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
