import os
from pathlib import Path

from gradio_client import Client

VOICE_STYLE_GROUPS = {
    "gender": {"female", "male"},
    "age": {"child", "teenager", "young adult", "middle-aged", "elderly"},
    "pitch": {
        "very low pitch",
        "low pitch",
        "moderate pitch",
        "high pitch",
        "very high pitch",
    },
}
DEFAULT_VOICE_STYLE = {
    "gender": "female",
    "age": "young adult",
    "pitch": "moderate pitch",
}


def _voice_style_fields(voice_style: str | None) -> tuple[str, str, str]:
    selected = DEFAULT_VOICE_STYLE.copy()
    if voice_style:
        selected_categories: set[str] = set()
        for item in voice_style.split(","):
            style = item.strip().lower()
            category = next(
                (
                    name
                    for name, choices in VOICE_STYLE_GROUPS.items()
                    if style in choices
                ),
                None,
            )
            if category is None:
                raise ValueError(f"Estilo de voz OmniVoice no admitido: {style}")
            if category in selected_categories:
                raise ValueError(f"Hay más de un valor para el estilo: {category}")
            selected[category] = style
            selected_categories.add(category)
    return selected["gender"], selected["age"], selected["pitch"]


def synthesize_voice(text: str, voice_style: str | None = None) -> bytes:
    space_url = os.environ.get("VOICE_STUDIO_URL")
    hf_token = os.environ.get("HF_TOKEN")
    if not space_url or not space_url.startswith("https://"):
        raise RuntimeError("VOICE_STUDIO_URL HTTPS no esta configurada")
    if not hf_token:
        raise RuntimeError("HF_TOKEN de solo lectura no esta configurado")

    client = Client(space_url.rstrip("/"), token=hf_token, verbose=False)
    gender, age, pitch = _voice_style_fields(voice_style)
    result = client.predict(
        text,
        gender,
        age,
        pitch,
        api_name="/predict",
    )
    if not isinstance(result, str) or not Path(result).is_file():
        raise RuntimeError("El Space no devolvio la ruta de un archivo WAV")
    audio = Path(result).read_bytes()
    if not audio.startswith(b"RIFF"):
        raise RuntimeError("La respuesta de VoiceStudio no es un archivo WAV")
    return audio
