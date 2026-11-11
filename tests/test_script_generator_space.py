import ast
from pathlib import Path


APP = Path(__file__).parents[1] / "apps" / "script-generator-space" / "app.py"


def test_script_generator_space_defaults_to_cpu_with_opt_in_zerogpu() -> None:
    source = APP.read_text(encoding="utf-8")
    ast.parse(source, filename=str(APP))

    assert 'MODEL_ID = "Qwen/Qwen3-4B" if USE_ZEROGPU else "Qwen/Qwen3-1.7B"' in source
    assert 'os.getenv("USE_ZEROGPU", "0")' in source
    assert 'spaces.GPU(duration=120)(function)' in source
    assert 'device_map=DEVICE' in source
    assert 'api_name="generate"' in source
