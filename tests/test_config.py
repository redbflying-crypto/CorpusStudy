import pytest
from pydantic import ValidationError

from corpusstudy.config import (
    REPO_ROOT,
    Language,
    Settings,
    Stance,
    Translation,
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    for var in ("AI_PROVIDER", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def make(**kwargs) -> Settings:
    # _env_file=None: tests never read the developer's real .env
    return Settings(_env_file=None, **kwargs)


def test_defaults():
    s = make()
    assert s.ai_provider == "anthropic"
    assert s.default_language == Language.EN
    assert s.translation_for(Language.EN) == Translation.KJV
    assert s.translation_for(Language.FR) == Translation.LSG1910
    assert s.stance == Stance.REFORMED
    assert s.show_other_views is True
    assert set(s.allowed_commentaries) == {"calvin", "henry", "tsk"}
    assert s.knowledge_dir == REPO_ROOT / "knowledge"
    assert not s.has_api_key


def test_key_from_env_is_secret(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-123")
    s = make()
    assert s.has_api_key
    assert s.anthropic_api_key.get_secret_value() == "sk-ant-test-123"
    assert "sk-ant-test-123" not in repr(s)
    assert "sk-ant-test-123" not in str(s.model_dump())


def test_key_split_across_lines_is_joined(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc\n  def ")
    assert make().anthropic_api_key.get_secret_value() == "sk-ant-abcdef"


def test_blank_key_is_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    assert not make().has_api_key


def test_provider_must_be_anthropic(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    with pytest.raises(ValidationError):
        make()


def test_prefixed_env_vars(monkeypatch):
    monkeypatch.setenv("CORPUS_DEFAULT_LANGUAGE", "fr")
    monkeypatch.setenv("CORPUS_SHOW_OTHER_VIEWS", "false")
    s = make()
    assert s.default_language == Language.FR
    assert s.show_other_views is False


def test_translation_must_match_language():
    with pytest.raises(ValidationError):
        make(translations={Language.EN: Translation.LSG1910})


def test_only_public_domain_commentaries():
    with pytest.raises(ValidationError):
        make(allowed_commentaries=["calvin", "some-modern-commentary"])


def test_relative_paths_resolve_to_repo_root():
    assert make(data_dir="mydata").data_dir == REPO_ROOT / "mydata"


def test_translation_metadata():
    assert Translation.KJV.language == Language.EN
    assert Translation.LSG1910.language == Language.FR
    assert "Segond" in Translation.LSG1910.full_name
