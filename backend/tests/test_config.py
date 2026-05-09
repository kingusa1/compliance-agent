import pytest
from pydantic import ValidationError

from app.config import Settings


def test_use_agent_analyzer_defaults_false():
    s = Settings()
    assert s.use_agent_analyzer is False


def test_agent_model_settings_have_defaults():
    s = Settings()
    assert s.gemini_flash_model == "google/gemini-2.5-flash"
    assert s.agent_escalation_model == "anthropic/claude-opus-4.7"


def test_agent_escalation_threshold_is_low_confidence():
    s = Settings()
    assert s.agent_escalation_threshold == "low"


def test_agent_escalation_threshold_rejects_invalid():
    with pytest.raises(ValidationError):
        Settings(agent_escalation_threshold="yolo")
