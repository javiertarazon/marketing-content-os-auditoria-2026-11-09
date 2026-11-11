"""Worker local de investigacion, guion y voz (equivalente al notebook 01).

Sustituye a ``notebooks/01_research_scrapegraph.ipynb`` en Windows, sin Colab y sin
tunel HTTPS: habla con la API por HTTP plano en ``http://127.0.0.1:8000``.

Pipeline de cada trabajo:

1. **Investigacion** (``viral_research``): busca videos virales de YouTube. Si existe
   ``YOUTUBE_API_KEY`` usa la Data API oficial con ``order=viewCount``; si no, usa el
   extractor sin clave que raspa la pagina de resultados. Los fallos de red o de formato
   no abortan el trabajo: se registran en ``platform_status`` y se continua.
2. **Guion**: llama al Space ``SCRIPT_SPACE_URL`` con el prompt exacto del notebook y
   parsea las secciones ``INVESTIGACION:`` y ``GUION:``. Si el Space no responde se usa
   un guion de plantilla en espanol y se anota ``GUION DE EMERGENCIA`` en las notas.
3. **Voz**: llama al Space ``VOICE_STUDIO_URL`` (``/predict``) con los seis argumentos
   posicionales (texto, genero, edad, tono, muestra de voz opcional y su transcripcion).
4. **Subida**: guarda el WAV en R2 con la clave ``audio/<job_id>.wav``.

Salida del trabajo (la que exige ``apps/api/main.py``)::

    {"output": {
       "research": {"source": "<url>",
                    "notes": "<texto de la seccion INVESTIGACION>",
                    "sources": [{"platform", "source_type", "title", "url",
                                 "view_count", "published_at", "channel"}],
                    "platform_status": [{"platform", "status", "result_count", "message"}]},
       "script": "<texto de la seccion GUION>",
       "audio_object_key": "audio/<job_id>.wav"}}

Como pedir un trabajo concreto (la API expone ``POST /v1/jobs`` sin autenticacion)::

    curl -X POST http://127.0.0.1:8000/v1/jobs \
      -H "Content-Type: application/json" \
      -d '{"worker": "research",
           "request": {"topic": "finanzas personales",
                       "style": "financial-news",
                       "duration_seconds": 35,
                       "voice_gender": "female",
                       "voice_age": "young adult",
                       "voice_pitch": "moderate pitch",
                       "platforms": ["youtube"]}}'

Ese ``POST`` lo hace el agente principal para probar; este worker **no** crea trabajos
por su cuenta (salvo que se pase ``--create-job``), solo consume la cola.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

# Permite ejecutar el worker directamente con su ruta
# (python apps/workers/local_research_worker.py): sys.path[0] seria el propio
# directorio del modulo y "apps" no seria importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from apps.workers import viral_research  # noqa: E402
from apps.workers.worker_common import (  # noqa: E402
    ApiClient,
    LeaseRenewer,
    WorkerConfig,
    build_hf_client,
    build_storage_client,
    configure_logging,
    resolve_config,
    run_worker_loop,
)

logger = logging.getLogger(__name__)

SCRIPT_SECTIONS_PATTERN = re.compile(
    r"\s*INVESTIGACION\s*:\s*(.*?)\s*GUION\s*:\s*(.*?)\s*", flags=re.IGNORECASE | re.DOTALL
)
EMERGENCY_MARKER = "GUION DE EMERGENCIA"
VIDEO_SOURCE_KEYS = (
    "platform",
    "source_type",
    "title",
    "url",
    "view_count",
    "published_at",
    "channel",
)


def build_prompt(request: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    """Prompt de generacion de guion: el mismo criterio que usa el notebook 01."""
    context = "\n\n".join(
        "Plataforma: {platform}\nTipo de fuente: {source_type}\nTitulo: {title}\n"
        "URL: {url}\nCanal: {channel}\nVistas: {views}\nPublicado: {published}".format(
            platform=item.get("platform", ""),
            source_type=item.get("source_type", ""),
            title=item.get("title", ""),
            url=item.get("url", ""),
            channel=item.get("channel", "") or "desconocido",
            views=item.get("view_count") if item.get("view_count") is not None else "no disponible",
            published=item.get("published_at") or "no disponible",
        )
        for item in sources
    )
    duration_seconds = request.get("duration_seconds", 35)
    topic = request.get("topic", "")
    style = request.get("style", "financial-news")
    return (
        "Eres un estratega de contenido y creador de videos virales. Analiza las fuentes "
        "publicas recopiladas sobre el tema.\n"
        "Identifica patrones de alto impacto: gancho inicial (hook), formato, ritmo y "
        "palabras clave observables en las fuentes.\n"
        "Si hay metricas de YouTube, compara vistas por hora y fecha; no llames viral a un "
        "video solo por tener muchas vistas.\n"
        "Separa hechos citados, señales cuantitativas y recomendaciones; no inventes "
        "tendencias, datos ni citas.\n"
        "Crea una idea y un guion originales inspirados en patrones, sin copiar frases ni "
        "guiones de las fuentes.\n"
        "Devuelve exactamente dos secciones en espanol con estos encabezados, sin formato "
        "Markdown ni texto adicional:\n"
        "INVESTIGACION:\n(Gancho detectado, palabras clave, idea central e investigacion)\n"
        "GUION:\n(Solo la narracion hablada, empezando con un hook demoledor en los primeros "
        f"3 segundos, directa y dinamica; no excedas aproximadamente {duration_seconds} "
        "segundos)\n\n"
        f"Tema: {topic}\nEstilo: {style}\n"
        f"Fuentes públicas recopiladas:\n{context}"
    )


def emergency_script(request: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    """Guion de plantilla en espanol para que el pipeline no se detenga."""
    topic = request.get("topic", "el tema")
    title = sources[0]["title"] if sources else topic
    channel = sources[0].get("channel") if sources else None
    reference = f" Como muestra {channel}, “{title}”." if channel else ""
    return (
        f"{topic}: lo que casi nadie te cuenta y si te conviene.{reference} "
        "Primero, entiende el problema real y no el ruido que te venden. "
        "Segundo, compara opciones con numeros y no con promesas. "
        f"Tercero, decide hoy una accion pequena y medible sobre {topic}. "
        "Guarda este video y revisa los detalles antes de dar el paso."
    )


def emergency_notes(request: dict[str, Any], sources: list[dict[str, Any]], reason: str) -> str:
    """Notas de investigacion cuando no hay un Space de guiones disponible."""
    titles = "; ".join(item.get("title", "") for item in sources[:3])
    return (
        f"{EMERGENCY_MARKER}: {reason} "
        f"Investigacion basica del tema “{request.get('topic', '')}”. "
        f"Fuentes encontradas: {titles or 'ninguna'}. "
        "El guion es una plantilla construida con el tema y las fuentes disponibles."
    )


def research_sources(
    request: dict[str, Any], config: WorkerConfig, limit: int = 6
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Busca fuentes publicas y devuelve ``(sources, platform_status)``.

    Nunca lanza: si YouTube falla o esta bloqueado se registra ``status="error"`` y el
    worker sigue con el resto del pipeline.
    """
    topic = request.get("topic", "")
    sources: list[dict[str, Any]] = []
    platform_status: list[dict[str, Any]] = []
    platforms = request.get("platforms") or ["youtube"]
    for platform in platforms:
        if platform == "youtube":
            found, status = viral_research.search_viral_videos(
                topic,
                limit=limit,
                region_code=config.youtube_region_code,
                api_key=config.youtube_api_key or None,
            )
            sources.extend(found)
            platform_status.append(status)
            continue
        found, status = viral_research.search_platform_snippets(topic, platform)
        sources.extend(found)
        platform_status.append(status)
    return sources, platform_status


def normalize_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deja las fuentes con las claves exactas del contrato de salida."""
    normalized: list[dict[str, Any]] = []
    for item in sources:
        entry = {key: item.get(key) for key in VIDEO_SOURCE_KEYS}
        entry["source_type"] = item.get("source_type") or "page"
        entry["platform"] = item.get("platform") or "web"
        normalized.append(entry)
    return normalized


def generate_script(
    request: dict[str, Any], sources: list[dict[str, Any]], config: WorkerConfig
) -> tuple[str, str, bool]:
    """Pide el guion al Space. Devuelve ``(research_text, script, es_emergencia)``."""
    prompt = build_prompt(request, sources)
    try:
        client = build_hf_client(config.script_space_url, config.hf_token)
        generated = client.submit(prompt, api_name="/generate").result(timeout=900)
    except Exception as error:  # noqa: BLE001 - el Space es externo y puede fallar
        logger.warning("El Space de guiones no respondio: %s", error)
        reason = f"el Space de guiones fallo ({type(error).__name__})."
        return (
            emergency_notes(request, sources, reason),
            emergency_script(request, sources),
            True,
        )

    if not isinstance(generated, str):
        generated = str(generated)

    sections = SCRIPT_SECTIONS_PATTERN.fullmatch(generated)
    if not sections:
        logger.warning("El Space no devolvio las secciones INVESTIGACION y GUION esperadas.")
        try:
            client = build_hf_client(config.script_space_url, config.hf_token)
            retry = client.submit(
                prompt + "\n\nEmpieza tu respuesta por INVESTIGACION: y termina con GUION:.",
                api_name="/generate",
            ).result(timeout=900)
            if isinstance(retry, str):
                sections = SCRIPT_SECTIONS_PATTERN.fullmatch(retry)
                if sections:
                    generated = retry
        except Exception as error:  # noqa: BLE001
            logger.warning("El reintento del Space de guiones fallo: %s", error)

    if sections:
        research_text, script = (part.strip() for part in sections.groups())
        if research_text and script:
            return research_text, script, False
        logger.warning("El Space devolvio investigacion o guion vacios.")

    # Fallo de parseo: se usa el texto crudo como guion para no abortar el trabajo.
    script = generated.strip() or emergency_script(request, sources)
    research_text = emergency_notes(
        request, sources, "el Space de guiones no devolvio las secciones esperadas."
    )
    return research_text, script, True


def synthesize_voice(
    request: dict[str, Any],
    script: str,
    config: WorkerConfig,
    storage: Any,
    job_id: str,
) -> bytes | None:
    """Sintetiza la voz con el Space VoiceStudio. Devuelve los bytes WAV o ``None``."""
    reference_path: Path | None = None
    reference_text = ""
    voice_reference = request.get("voice_reference")
    if voice_reference:
        reference_path = Path(tempfile.gettempdir()) / f"{job_id}-voice-reference.wav"
        storage.download_file(
            config.s3_bucket, voice_reference["object_key"], str(reference_path)
        )
        reference_text = voice_reference.get("transcript", "")

    arguments = [
        script,
        request.get("voice_gender", "female"),
        request.get("voice_age", "young adult"),
        request.get("voice_pitch", "moderate pitch"),
        str(reference_path) if reference_path else None,
        reference_text,
    ]
    try:
        client = build_hf_client(config.voice_studio_url, config.hf_token)
        try:
            voice_path = client.submit(*arguments, api_name="/predict").result(timeout=1800)
        except Exception as error:  # noqa: BLE001 - un reintento antes de rendirse
            logger.warning("La sintesis de voz fallo, se reintenta una vez: %s", error)
            time.sleep(5)
            client = build_hf_client(config.voice_studio_url, config.hf_token)
            voice_path = client.submit(*arguments, api_name="/predict").result(timeout=1800)
        if not isinstance(voice_path, str) or not Path(voice_path).is_file():
            raise RuntimeError("El Space privado no devolvio el archivo de audio")
        voice_bytes = Path(voice_path).read_bytes()
        if not voice_bytes.startswith(b"RIFF"):
            raise RuntimeError("VoiceStudio devolvio un archivo que no es WAV")
        return voice_bytes
    except Exception as error:  # noqa: BLE001
        logger.error("La sintesis de voz fallo: %s", error)
        return None
    finally:
        if reference_path and reference_path.exists():
            reference_path.unlink(missing_ok=True)


def process_research_job(client: ApiClient, job: dict[str, Any]) -> bool:
    """Procesa un trabajo de investigacion de principio a fin."""
    job_id = str(job["job_id"])
    lease_token = job["lease_token"]
    renewer = LeaseRenewer(client, job_id, lease_token)
    renewer.start()
    try:
        client.worker_heartbeat(job_id, "starting")
        client.job_heartbeat(job_id, lease_token)

        request = job.get("request") or {}
        config = client.config
        storage = build_storage_client(config)

        client.report_progress(
            job_id, lease_token, "research_search", "Buscando contenido viral de YouTube", 10
        )
        sources, platform_status = research_sources(request, config)
        renewer.ensure_active()
        for item in platform_status:
            logger.info("Plataforma %s: %s", item.get("platform"), item.get("status"))

        client.report_progress(
            job_id, lease_token, "research_script", "Analizando fuentes y generando guion", 32
        )
        research_text, script, is_emergency = generate_script(request, sources, config)
        renewer.ensure_active()
        if is_emergency:
            logger.warning("Se usa un guion de emergencia para el trabajo %s", job_id)

        audio_key = ""
        voice_error = ""
        try:
            client.report_progress(
                job_id, lease_token, "voice_synthesis", "Preparando narracion de voz", 55
            )
            voice_bytes = synthesize_voice(request, script, config, storage, job_id)
            if voice_bytes is None:
                voice_error = (
                    "el Space de voz no devolvio un WAV valido; el trabajo se completo sin audio"
                )
            else:
                client.report_progress(
                    job_id, lease_token, "audio_storage", "Guardando narracion en R2 privado", 85
                )
                audio_key = f"audio/{job_id}.wav"
                storage.put_object(
                    Bucket=config.s3_bucket,
                    Key=audio_key,
                    Body=voice_bytes,
                    ContentType="audio/wav",
                )
        except Exception as error:  # noqa: BLE001 - el audio nunca debe abortar el trabajo
            voice_error = f"la sintesis de voz fallo ({type(error).__name__}: {error})"
            logger.error("No se pudo sintetizar la voz: %s", error)

        renewer.ensure_active()
        output: dict[str, Any] = {
            "research": {
                "source": sources[0]["url"] if sources else "",
                "notes": research_text,
                "sources": normalize_sources(sources),
                "platform_status": platform_status,
            },
            "script": script,
        }
        if audio_key:
            output["audio_object_key"] = audio_key

        try:
            client.complete_job(job_id, lease_token, output)
        except requests.HTTPError as error:
            status_code = error.response.status_code if error.response is not None else 0
            if status_code == 422 and not audio_key:
                message = (
                    "La sintesis de voz fallo y la API exige audio_object_key para completar "
                    f"un trabajo de investigacion: {voice_error}"
                )
                client.fail_job(job_id, lease_token, message)
                raise RuntimeError(message) from error
            raise
        logger.info("Investigacion, guion y voz listos para el trabajo %s", job_id)
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


def create_research_job(client: ApiClient, topic: str) -> str | None:
    """Crea un trabajo de investigacion con ``POST /v1/jobs`` (no requiere token)."""
    payload = {
        "worker": "research",
        "request": {
            "topic": topic,
            "style": "financial-news",
            "duration_seconds": 35,
            "voice_gender": "female",
            "voice_age": "young adult",
            "voice_pitch": "moderate pitch",
            "platforms": ["youtube"],
        },
    }
    try:
        response = client.session.post(
            f"{client.base_url}/v1/jobs", json=payload, timeout=30
        )
        response.raise_for_status()
        job_id = response.json().get("job_id")
        logger.info("Trabajo de investigacion creado: %s", job_id)
        return str(job_id) if job_id else None
    except requests.RequestException as error:
        logger.error("No se pudo crear el trabajo para el tema %r: %s", topic, error)
        return None


def build_parser() -> argparse.ArgumentParser:
    """Parser de la linea de comandos del worker de investigacion."""
    parser = argparse.ArgumentParser(
        description="Worker local de investigacion, guion y voz del Marketing Content OS.",
        epilog=(
            "Para pedir un trabajo concreto crealo con POST /v1/jobs y despues ejecuta "
            "este worker con --topic. El payload esta documentado en la cabecera del modulo."
        ),
    )
    parser.add_argument("--once", action="store_true", help="Procesa un trabajo y sale.")
    parser.add_argument("--max-jobs", type=int, default=None, help="Maximo de trabajos a procesar.")
    parser.add_argument("--topic", default=None, help="Tema del trabajo que se quiere procesar.")
    parser.add_argument(
        "--create-job",
        action="store_true",
        help="Crea el trabajo con POST /v1/jobs si no hay cola (por defecto no se crea).",
    )
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
        "--limit",
        type=int,
        default=6,
        help="Numero de videos virales a recuperar (por defecto 6).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del worker de investigacion."""
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)
    config = resolve_config("research", args.base_url)
    logger.info("Worker de investigacion contra %s", config.api_base_url)
    client = ApiClient(config)

    if args.topic and args.create_job:
        create_research_job(client, args.topic)

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
        return process_research_job(client, job)

    processed = run_worker_loop(
        client,
        process,
        once=args.once,
        max_jobs=args.max_jobs,
        wait_seconds=args.wait_seconds,
    )
    logger.info("Trabajos procesados: %s", processed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
