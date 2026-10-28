from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_database_url: str = "sqlite+aiosqlite:///./.local/handler.db"
    dbh_encryption_secret: str = "local-college-project-key"
    openai_api_key: str = ""
    openai_allow_schema_sharing: bool = False
    typesafe_api_key: str = ""
    typesafe_allow_schema_sharing: bool = False
    typesafe_model: str = "jev-latest"
    auth_required: bool = False
    auth_bootstrap_secret: str = ""
    openai_model: str = "gpt-6-luna"
    cors_origin: str = "http://localhost:5173"
    max_active_jobs: int = 2
    agent_tool_limit: int = 8
    agent_round_limit: int = 4


settings = Settings()


def ai_enabled() -> bool:
    return bool(settings.openai_api_key and settings.openai_allow_schema_sharing)


def jev_enabled() -> bool:
    return bool(settings.typesafe_api_key and settings.typesafe_allow_schema_sharing)
