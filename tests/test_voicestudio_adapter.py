from pathlib import Path

import pytest

from packages.python.adapters.voicestudio_adapter import synthesize_voice


def test_synthesize_voice_calls_private_gradio_space(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VOICE_STUDIO_URL", "https://voice.example")
    monkeypatch.setenv("HF_TOKEN", "read-token")
    captured: dict = {}
    audio_file = tmp_path / "output.wav"
    audio_file.write_bytes(b"RIFFtest")

    class FakeClient:
        def __init__(self, url: str, *, token: str, verbose: bool) -> None:
            captured["url"] = url
            captured["token"] = token
            captured["verbose"] = verbose

        def predict(
            self,
            text: str,
            gender: str,
            age: str,
            pitch: str,
            *,
            api_name: str,
        ) -> str:
            captured["text"] = text
            captured["voice_style"] = (gender, age, pitch)
            captured["api_name"] = api_name
            return str(audio_file)

    monkeypatch.setattr(
        "packages.python.adapters.voicestudio_adapter.Client", FakeClient
    )

    result = synthesize_voice("Hola", "female, middle-aged, moderate pitch")

    assert result == b"RIFFtest"
    assert captured == {
        "url": "https://voice.example",
        "token": "read-token",
        "verbose": False,
        "text": "Hola",
        "voice_style": ("female", "middle-aged", "moderate pitch"),
        "api_name": "/predict",
    }


def test_synthesize_voice_rejects_non_wav_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("VOICE_STUDIO_URL", "https://voice.example")
    monkeypatch.setenv("HF_TOKEN", "read-token")
    audio_file = tmp_path / "output.wav"
    audio_file.write_bytes(b"{}")

    monkeypatch.setattr(
        "packages.python.adapters.voicestudio_adapter.Client",
        lambda *args, **kwargs: type(
            "FakeClient",
            (),
            {"predict": lambda self, *args, **kwargs: str(audio_file)},
        )(),
    )

    with pytest.raises(RuntimeError, match="archivo WAV"):
        synthesize_voice("Hola")


def test_synthesize_voice_requires_hf_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_STUDIO_URL", "https://voice.example")
    monkeypatch.delenv("HF_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="HF_TOKEN"):
        synthesize_voice("Hola")


def test_synthesize_voice_rejects_unsupported_style() -> None:
    with pytest.raises(ValueError, match="no admitido"):
        from packages.python.adapters.voicestudio_adapter import _voice_style_fields

        _voice_style_fields("voz cálida")
