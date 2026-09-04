import pytest

from app.schemas import RecoveryDecisionSchema
from app.services.llm import get_llm_provider
from app.services.llm.mock_provider import MockLLMProvider
from tests.test_safety_validator import get_dummy_context


@pytest.mark.asyncio
async def test_mock_llm_provider():
    provider = MockLLMProvider()
    context = get_dummy_context()

    decision, _raw = await provider.analyze_case(context)

    assert isinstance(decision, RecoveryDecisionSchema)
    assert decision.case_id == "case-123"
    assert decision.action == "SEND_PAYMENT_LINK"
    assert decision.delay_hours == 0
    assert decision.llm_confidence == 0.9


@pytest.mark.asyncio
async def test_get_llm_provider(monkeypatch):
    from app.config import settings

    # Force mock for test without overriding env entirely if it was gemini
    monkeypatch.setattr(
        settings, "LLM_PROVIDER", "openai"
    )  # openai not implemented, defaults to mock
    provider = get_llm_provider()
    assert isinstance(provider, MockLLMProvider)
