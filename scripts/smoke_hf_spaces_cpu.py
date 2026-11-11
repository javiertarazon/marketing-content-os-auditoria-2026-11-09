#!/usr/bin/env python3
"""Exercise the private HF script and voice Spaces without requesting GPU."""

from __future__ import annotations

import subprocess
import time
import wave
import re
import inspect
from pathlib import Path

from gradio_client import Client

SCRIPT_SPACE = "javiertarazon/marketing-script-generator"
VOICE_SPACE = "javiertarazon/voice-studio-agent"


def get_token() -> str:
    result = subprocess.run(
        ["hf", "auth", "token"], check=True, capture_output=True, text=True
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("No hay un token HF activo en la caché del CLI.")
    return token


def make_client(space: str, token: str) -> Client:
    parameter = "token" if "token" in inspect.signature(Client).parameters else "hf_token"
    return Client(space, **{parameter: token}, verbose=False)


def main() -> None:
    token = get_token()
    prompt = (
        "Devuelve exactamente dos secciones en español, sin Markdown ni texto adicional.\n"
        "INVESTIGACION:\nResume solo el hecho que aparece en la fuente.\n"
        "GUION:\nEscribe una narración oral de una sola frase, con un gancho inicial.\n\n"
        "Tema: hábitos saludables. Estilo: directo.\n"
        "Fuentes públicas recopiladas: caminar a diario mejora la actividad física."
    )

    started = time.monotonic()
    script_client = make_client(SCRIPT_SPACE, token)
    generated = script_client.submit(prompt, api_name="/generate").result(timeout=900)
    if not isinstance(generated, str):
        raise RuntimeError("El Space de guiones no devolvió texto.")
    sections = re.fullmatch(
        r"\s*INVESTIGACION\s*:\s*(.*?)\s*GUION\s*:\s*(.*?)\s*",
        generated,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not sections:
        print("SCRIPT_CPU_FORMAT_FAIL", repr(generated[:400]))
        raise RuntimeError("La respuesta CPU no respeta el contrato de secciones del worker.")
    script_text = sections.group(2).strip()
    if not script_text:
        raise RuntimeError("La sección GUION está vacía.")
    print(
        "SCRIPT_CPU_PASS",
        "elapsed_s=",
        round(time.monotonic() - started),
        "script_chars=",
        len(script_text),
    )

    started = time.monotonic()
    voice_client = make_client(VOICE_SPACE, token)
    audio_result = voice_client.submit(
        "Hola. Esta es una prueba corta de narración en español.",
        "female",
        "young adult",
        "moderate pitch",
        None,
        "",
        api_name="/predict",
    ).result(timeout=1800)

    audio_path = audio_result[0] if isinstance(audio_result, tuple) else audio_result
    if not isinstance(audio_path, (str, Path)) or not Path(audio_path).is_file():
        raise RuntimeError("El Space de voz no devolvió un WAV descargable.")
    audio_path = Path(audio_path)
    try:
        with wave.open(str(audio_path), "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            duration = wav.getnframes() / sample_rate
        if sample_rate <= 0 or duration <= 0:
            raise RuntimeError("El WAV no tiene frecuencia o duración válidas.")
        print(
            "VOICE_CPU_PASS",
            "elapsed_s=",
            round(time.monotonic() - started),
            "sample_rate=",
            sample_rate,
            "channels=",
            channels,
            "duration_s=",
            round(duration, 2),
            "bytes=",
            audio_path.stat().st_size,
        )
    finally:
        audio_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
