from myelin.config import Settings


def test_blank_optional_gateway_uses_official_api(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    assert Settings.load().base_url == "https://api.openai.com/v1"


def test_explicit_gateway_is_retained(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example/v1")
    assert Settings.load().base_url == "https://gateway.example/v1"
