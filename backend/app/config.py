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

    # app/terraform_runner.py — 프로비저닝 job마다 독립된 워크스페이스 디렉터리를 이 경로 아래에 둔다.
    terraform_binary_path: str = Field(default="terraform", alias="TERRAFORM_BINARY_PATH")
    terraform_workspaces_dir: str = Field(default="/tmp/terraform-workspaces", alias="TERRAFORM_WORKSPACES_DIR")
    terraform_plugin_cache_dir: str = Field(default="/tmp/terraform-plugin-cache", alias="TERRAFORM_PLUGIN_CACHE_DIR")
    terraform_apply_timeout_seconds: int = Field(default=900, alias="TERRAFORM_APPLY_TIMEOUT_SECONDS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
