from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(alias="DATABASE_URL")
    credential_encryption_key: str = Field(default="", alias="CREDENTIAL_ENCRYPTION_KEY")
    credential_encryption_key_version: str = Field(default="v1", alias="CREDENTIAL_ENCRYPTION_KEY_VERSION")
    jwt_secret_key: str = Field(default="", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_token_expires_seconds: int = Field(default=3600, alias="JWT_ACCESS_TOKEN_EXPIRES_SECONDS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
