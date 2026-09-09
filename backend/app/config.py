from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(alias="DATABASE_URL")
    credential_encryption_key: str = Field(default="", alias="CREDENTIAL_ENCRYPTION_KEY")
    credential_encryption_key_version: str = Field(default="v1", alias="CREDENTIAL_ENCRYPTION_KEY_VERSION")


@lru_cache
def get_settings() -> Settings:
    return Settings()
