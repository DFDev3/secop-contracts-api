"""Application settings, read from environment variables (or a local .env file).

Keeping configuration out of the code means the same code runs locally, in CI and on
Cloud Run; only the environment changes.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://secop:secop@localhost:5432/secop"
    socrata_app_token: str | None = None


settings = Settings()
