"""Tests for the model registries in summarizer classes.

These tests don't make network calls — they just check that the model IDs
configured in MODELS dicts haven't drifted from what the providers actually
accept. If a provider deprecates a model, you'd update both the constant
here and the source.
"""
from omascribe.ai_summarizer import (
    AnthropicSummarizer,
    OpenAISummarizer,
    OpenRouterSummarizer,
)


def test_anthropic_haiku_is_current_3_5():
    """Haiku tier uses the real claude-3-5-haiku model ID."""
    haiku = AnthropicSummarizer.MODELS["haiku"]
    assert haiku["id"] == "claude-3-5-haiku-20241022"
    assert "3.5" in haiku["name"]


def test_anthropic_sonnet_is_current_3_5():
    """Sonnet tier uses the real claude-3-5-sonnet model ID."""
    sonnet = AnthropicSummarizer.MODELS["sonnet"]
    assert sonnet["id"] == "claude-3-5-sonnet-20241022"
    assert "3.5" in sonnet["name"]


def test_anthropic_no_fictional_ids_remain():
    """Guard against accidentally reintroducing made-up model IDs."""
    for tier, info in AnthropicSummarizer.MODELS.items():
        # Real Anthropic IDs use the claude-3-5-* or claude-3-7-* pattern
        assert "claude-" in info["id"], f"{tier} doesn't look like a real Anthropic ID"
        assert "haiku-4-" not in info["id"] and "sonnet-4-" not in info["id"], \
            f"{tier} contains fictional version number"


def test_anthropic_models_have_required_fields():
    for tier, info in AnthropicSummarizer.MODELS.items():
        for field in ("id", "name", "cost_per_1k_input", "cost_per_1k_output"):
            assert field in info, f"Anthropic {tier} missing {field}"
        assert isinstance(info["cost_per_1k_input"], (int, float))


def test_openai_models_have_required_fields():
    for tier, info in OpenAISummarizer.MODELS.items():
        for field in ("id", "name", "cost_per_1k_input", "cost_per_1k_output"):
            assert field in info, f"OpenAI {tier} missing {field}"


def test_openrouter_models_have_required_fields():
    for tier, info in OpenRouterSummarizer.MODELS.items():
        for field in ("id", "name"):
            assert field in info, f"OpenRouter {tier} missing {field}"


def test_anthropic_summarizer_requires_api_key(monkeypatch):
    """Without an API key (and no env var), construction should fail clearly."""
    import pytest
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key"):
        AnthropicSummarizer(api_key=None, model="haiku")


def test_anthropic_summarizer_rejects_unknown_model():
    """Unknown model tier should not be silently accepted."""
    import pytest
    with pytest.raises((KeyError, ValueError)):
        AnthropicSummarizer(api_key="sk-ant-test", model="not-a-tier")
