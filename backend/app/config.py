from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BINS = [
    "000740001307", "020240002363", "020440003656", "030440003698",
    "050740004819", "051040005150", "100140011059", "120940001946", "140340016539",
    "150540000186", "171041003124", "210240019348", "210240033968", "210941010761",
    "230740013340", "231040023028", "780140000023", "900640000128", "940740000911",
    "940940000384", "960440000220", "970940001378", "971040001050", "980440001034",
    "981140001551", "990340005977", "990740002243",
]
YEARS = [2024, 2025, 2026]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    ows_token: str = ""
    ows_base_url: str = "https://ows.goszakup.gov.kz"
    ows_concurrency: int = 6
    ows_page_size: int = 500

    database_url: str = "postgresql://goszakup:goszakup@localhost:5433/goszakup"
    agent_ro_password: str = "agent_ro_change_me"

    @property
    def database_url_ro(self) -> str:
        """DSN read-only роли agent_ro (тот же хост/БД, другая учётная запись)."""
        from urllib.parse import quote, urlsplit, urlunsplit
        u = urlsplit(self.database_url)
        host = u.hostname + (f":{u.port}" if u.port else "")
        return urlunsplit((u.scheme, f"agent_ro:{quote(self.agent_ro_password)}@{host}", u.path, u.query, u.fragment))

    llm_provider: str = "anthropic"  # anthropic | openai_compat
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    openai_compat_base_url: str = "http://localhost:11434/v1"
    openai_compat_api_key: str = "ollama"
    openai_compat_model: str = "qwen2.5:7b-instruct"
    openai_compat_max_retries: int = 6
    openai_compat_temperature: float | None = 0.0   # пусто → не передавать (reasoning-модели его не принимают)

    sync_interval_seconds: int = 3600
    cors_origins: str = "http://localhost:5173,http://localhost:8080"   # через запятую

    @field_validator("openai_compat_temperature", mode="before")
    @classmethod
    def _empty_is_none(cls, v):
        return None if v == "" else v


settings = Settings()
