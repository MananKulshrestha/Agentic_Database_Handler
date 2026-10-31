from backend.app.agent import _model_parameters
from backend.app.config import settings


def test_luna_chat_completions_disables_reasoning_for_tools(monkeypatch):
    monkeypatch.setattr(settings, "openai_model", "gpt-6-luna")
    assert _model_parameters() == {"reasoning_effort": "none"}


def test_other_models_keep_their_existing_request_shape(monkeypatch):
    monkeypatch.setattr(settings, "openai_model", "gpt-4.1-mini")
    assert _model_parameters() == {}
