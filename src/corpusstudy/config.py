"""Runtime configuration, loaded from environment variables and the git-ignored `.env` file."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Language(StrEnum):
    EN = "en"
    FR = "fr"


class Translation(StrEnum):
    """Bible translations the bot may quote. Public domain only."""

    KJV = "KJV"
    LSG1910 = "LSG1910"

    @property
    def language(self) -> Language:
        return TRANSLATION_LANGUAGE[self]

    @property
    def full_name(self) -> str:
        return TRANSLATION_NAMES[self]


TRANSLATION_LANGUAGE: dict[Translation, Language] = {
    Translation.KJV: Language.EN,
    Translation.LSG1910: Language.FR,
}

TRANSLATION_NAMES: dict[Translation, str] = {
    Translation.KJV: "King James Version (1769)",
    Translation.LSG1910: "Louis Segond (1910)",
}

PUBLIC_DOMAIN_TRANSLATIONS: frozenset[Translation] = frozenset(Translation)


class Stance(StrEnum):
    """Interpretive framing for doctrinal answers."""

    REFORMED = "reformed"


# Public-domain commentaries and reference works allowed in the corpus.
ALLOWED_COMMENTARIES: dict[str, str] = {
    "calvin": "John Calvin, Commentaries (Calvin Translation Society, 1840s-1850s; "
    "selections tr. J. Haroutunian, Library of Christian Classics XXIII, 1958)",
    "henry": "Matthew Henry, Commentary on the Whole Bible (1706-1721) and its Concise abridgement",
    "tsk": "The Treasury of Scripture Knowledge (c. 1830s)",
}

DEFAULT_MODEL = "claude-opus-5-5"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="CORPUS_",
        extra="ignore",
    )

    # LLM provider. These two use the conventional unprefixed variable names.
    ai_provider: str = Field(default="anthropic", validation_alias="AI_PROVIDER")
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    llm_model: str = DEFAULT_MODEL
    llm_effort: str = "medium"  # low | medium | high | xhigh | max
    llm_max_tokens: int = 16000
    llm_router: bool = False  # ask the model to refine open questions' routes (one more call)

    default_language: Language = Language.EN
    translations: dict[Language, Translation] = Field(
        default_factory=lambda: {Language.EN: Translation.KJV, Language.FR: Translation.LSG1910}
    )

    stance: Stance = Stance.REFORMED
    show_other_views: bool = True

    # Knowledge bundle (OKF). Answers use human-reviewed concepts only unless use_drafts.
    reviewer: str = "human:dhnguyen"
    drafter: str = "corpusstudy/drafter-1"
    use_drafts: bool = False

    # Retrieval: "none" = keyword (FTS5) only for now; dense vectors can be added later.
    embeddings: str = "none"
    allowed_commentaries: list[str] = Field(default_factory=lambda: list(ALLOWED_COMMENTARIES))

    data_dir: Path = REPO_ROOT / "data"
    knowledge_dir: Path = REPO_ROOT / "knowledge"
    index_dir: Path = REPO_ROOT / "indexes"
    imports_dir: Path = REPO_ROOT / "imports"  # hand-downloaded commentary files (prepare)

    api_host: str = "127.0.0.1"
    api_port: int = 8000

    @field_validator("ai_provider")
    @classmethod
    def _known_provider(cls, v: str) -> str:
        v = v.strip().lower()
        if v != "anthropic":
            raise ValueError(f"unsupported AI_PROVIDER {v!r}; only 'anthropic' is configured")
        return v

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def _join_key(cls, v: object) -> object:
        # A key pasted across lines picks up whitespace; keys never contain any.
        if isinstance(v, str):
            v = "".join(v.split())
            return v or None
        return v

    @field_validator("translations")
    @classmethod
    def _translations_match_language(
        cls, v: dict[Language, Translation]
    ) -> dict[Language, Translation]:
        for lang, tr in v.items():
            if tr not in PUBLIC_DOMAIN_TRANSLATIONS:
                raise ValueError(f"{tr} is not a public-domain translation")
            if tr.language != lang:
                raise ValueError(f"{tr} is a {tr.language} translation, not {lang}")
        return v

    @field_validator("allowed_commentaries")
    @classmethod
    def _commentaries_public_domain(cls, v: list[str]) -> list[str]:
        unknown = set(v) - set(ALLOWED_COMMENTARIES)
        if unknown:
            raise ValueError(f"not an approved public-domain commentary: {sorted(unknown)}")
        return v

    @field_validator("reviewer")
    @classmethod
    def _human_reviewer(cls, v: str) -> str:
        if not v.startswith("human:") or len(v) <= len("human:"):
            raise ValueError("reviewer must be an OKF human actor, e.g. human:dhnguyen")
        return v

    @field_validator("llm_effort")
    @classmethod
    def _effort(cls, v: str) -> str:
        if v not in ("low", "medium", "high", "xhigh", "max"):
            raise ValueError("llm_effort must be low, medium, high, xhigh or max")
        return v

    @field_validator("embeddings")
    @classmethod
    def _embeddings(cls, v: str) -> str:
        if v not in ("none", "voyage", "local"):
            raise ValueError("embeddings must be none, voyage or local")
        return v

    @field_validator("data_dir", "knowledge_dir", "index_dir", "imports_dir")
    @classmethod
    def _resolve_path(cls, v: Path) -> Path:
        return v if v.is_absolute() else REPO_ROOT / v

    def translation_for(self, language: Language) -> Translation:
        return self.translations[language]

    @property
    def has_api_key(self) -> bool:
        return self.anthropic_api_key is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()
