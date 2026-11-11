import ast
import json
from pathlib import Path


NOTEBOOKS = Path(__file__).parents[1] / "notebooks"


def test_notebooks_are_valid_json_with_python_cells() -> None:
    notebooks = sorted(NOTEBOOKS.glob("*.ipynb"))
    assert len(notebooks) == 2

    for path in notebooks:
        notebook = json.loads(path.read_text(encoding="utf-8"))
        assert notebook["nbformat"] == 4

        if path.name == "01_research_scrapegraph.ipynb":
            source = "\n".join(
                "".join(cell["source"])
                for cell in notebook["cells"]
                if cell["cell_type"] in ("code", "markdown")
            )
            assert "OPENAI_API_KEY" not in source
            assert "SCRIPT_SPACE_URL" in source
            assert 'api_name="/generate"' in source
            assert "search_on_web" in source
            assert 'search_engine="duckduckgo"' in source
            assert "DDGS().text" in source
            assert '"search_snippet"' in source
            assert "platform_status" in source
            assert "Scrapegraph-ai.git@c75c8084" in source

        code_source = "\n".join(
            "".join(cell["source"])
            for cell in notebook["cells"]
            if cell["cell_type"] == "code"
        )
        assert "run_worker_loop" in code_source
        assert "start_lease_heartbeat" in code_source
        assert "/progress" in code_source
        assert "audio_object_key" in code_source
        assert "STORAGE_PUBLIC_BASE_URL" not in code_source

        if path.name == "01_research_scrapegraph.ipynb":
            assert "process_one_research_job" in code_source
            assert "Detener para desconectarlo" in code_source
            assert "voice_reference" in code_source
            assert "voice_reference[\"transcript\"]" in code_source
            assert 'submit(prompt, api_name="/generate").result(timeout=900)' in code_source
            assert 'api_name="/predict",' in code_source
            assert ").result(timeout=1800)" in code_source
            assert "re.fullmatch(" in code_source
            assert "sections.groups()" in code_source
            assert "hf_space_client(os.environ[\"SCRIPT_SPACE_URL\"])" in code_source
            assert "inspect.signature(Client).parameters" in source
            assert 'token=os.environ["HF_TOKEN"]' not in source
        if path.name == "02_render_remotion.ipynb":
            assert "process_one_render_job" in code_source
            assert "Detener para desconectarlo" in code_source
            assert "video_object_key" in code_source
            assert "visual_asset" in code_source
            assert "OffthreadVideo" in code_source
            assert '"--audio-codec=aac"' in code_source
            assert '"--crf=18"' in code_source
            assert '"--concurrency=2"' in code_source
            assert "ffprobe" in code_source
            assert "1080x1920" in code_source

        for cell in notebook["cells"]:
            if cell["cell_type"] != "code":
                continue
            source = "".join(cell["source"])
            python_lines = [
                line
                for line in source.splitlines()
                if not line.lstrip().startswith(("!", "%"))
            ]
            if python_lines:
                ast.parse("\n".join(python_lines), filename=str(path))


def test_huggingface_spaces_default_to_cpu_without_zero_gpu_decorator() -> None:
    root = Path(__file__).parents[1]
    voice = (root / "apps/voice-studio-space/app.py").read_text(encoding="utf-8")
    script = (root / "apps/script-generator-space/app.py").read_text(encoding="utf-8")

    for source in (voice, script):
        ast.parse(source)
        assert 'os.getenv("USE_ZEROGPU", "0")' in source
        assert "spaces.GPU(duration=120)(function)" in source
        assert "@spaces.GPU(duration=1)" in source

    assert 'else "cpu"' in voice
    assert 'else "cpu"' in script
    assert 'else "Qwen/Qwen3-1.7B"' in script
