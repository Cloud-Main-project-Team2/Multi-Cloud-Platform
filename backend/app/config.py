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

    # 프로비저닝(Terraform 실행) 설정. MVP: 로컬 backend + FastAPI BackgroundTasks.
    # terraform/README.md "알려진 한계" 참고.
    terraform_modules_dir: str = Field(default="terraform", alias="TERRAFORM_MODULES_DIR")
    terraform_runs_dir: str = Field(default="terraform/.runs", alias="TERRAFORM_RUNS_DIR")
    terraform_timeout_seconds: int = Field(default=900, alias="TERRAFORM_TIMEOUT_SECONDS")
    # job마다 격리된 workspace에서 매번 `terraform init`을 새로 하므로, provider plugin
    # (azurerm 등)을 공유 캐시 없이 매 job마다 registry에서 재다운로드하게 된다. 이 디렉터리를
    # TF_PLUGIN_CACHE_DIR로 지정해 job 간에 재사용한다.
    terraform_plugin_cache_dir: str = Field(default="terraform/.plugin-cache", alias="TERRAFORM_PLUGIN_CACHE_DIR")


@lru_cache
def get_settings() -> Settings:
    return Settings()
