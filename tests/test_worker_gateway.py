from pathlib import Path


GATEWAY_CONFIG = Path(__file__).parents[1] / "infra" / "worker-gateway.conf"


def test_worker_gateway_exposes_only_authenticated_worker_routes() -> None:
    config = GATEWAY_CONFIG.read_text(encoding="utf-8")

    assert "location = /v1/jobs/next" in config
    assert "location = /v1/workers/heartbeat" in config
    assert "(complete|fail|heartbeat|progress)" in config
    assert config.count("limit_except POST") == 3
    assert "location / {" in config
    assert "return 404;" in config
    assert "location /v1/jobs {" not in config
    assert "location /v1/assets" not in config
