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
    # refresh token: 서버에 해시로 저장하고 회전(rotation)하는 opaque 토큰. 만료 14일 기본.
    refresh_token_expires_seconds: int = Field(default=1209600, alias="REFRESH_TOKEN_EXPIRES_SECONDS")

    # 메일 발송(app/mailer.py). 로컬/데모는 docker-compose의 MailHog(host=mailhog, port=1025)로
    # 나가는 메일을 가로채 :8025 웹 UI에서 확인한다. 운영은 이 env만 실제 SMTP로 교체하면 된다.
    mail_host: str = Field(default="localhost", alias="MAIL_HOST")
    mail_port: int = Field(default=1025, alias="MAIL_PORT")
    mail_username: str = Field(default="", alias="MAIL_USERNAME")
    mail_password: str = Field(default="", alias="MAIL_PASSWORD")
    mail_from: str = Field(default="no-reply@multicloud.example", alias="MAIL_FROM")
    mail_use_tls: bool = Field(default=False, alias="MAIL_USE_TLS")
    # 이메일 검증 링크·비밀번호 재설정 링크가 가리킬 프론트 오리진.
    frontend_base_url: str = Field(default="http://localhost:8080", alias="FRONTEND_BASE_URL")
    # OTP 이메일 검증 파라미터.
    email_verification_code_ttl_seconds: int = Field(default=600, alias="EMAIL_VERIFICATION_CODE_TTL_SECONDS")
    email_verification_max_attempts: int = Field(default=5, alias="EMAIL_VERIFICATION_MAX_ATTEMPTS")
    email_verification_resend_interval_seconds: int = Field(
        default=60, alias="EMAIL_VERIFICATION_RESEND_INTERVAL_SECONDS"
    )
    # 검증 완료 후 이 시간 안에 가입해야 유효(가입 게이트 window).
    email_verification_valid_window_seconds: int = Field(
        default=1800, alias="EMAIL_VERIFICATION_VALID_WINDOW_SECONDS"
    )
    # 비밀번호 재설정 링크 토큰 만료.
    password_reset_token_ttl_seconds: int = Field(default=1800, alias="PASSWORD_RESET_TOKEN_TTL_SECONDS")

    # app/terraform_runner.py — 프로비저닝 job마다 독립된 워크스페이스 디렉터리를 이 경로 아래에 둔다.
    terraform_binary_path: str = Field(default="terraform", alias="TERRAFORM_BINARY_PATH")
    terraform_workspaces_dir: str = Field(default="/tmp/terraform-workspaces", alias="TERRAFORM_WORKSPACES_DIR")
    terraform_plugin_cache_dir: str = Field(default="/tmp/terraform-plugin-cache", alias="TERRAFORM_PLUGIN_CACHE_DIR")
    terraform_apply_timeout_seconds: int = Field(default=900, alias="TERRAFORM_APPLY_TIMEOUT_SECONDS")

    # app/agent.py — AI 비용 어시스턴트(OpenAI Chat Completions API). 키가 비어 있으면
    # /agent/chat이 503(AGENT_NOT_CONFIGURED)을 반환한다(개발 중 키 없이도 나머지 기능은 그대로
    # 쓸 수 있게).
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")


@lru_cache
def get_settings() -> Settings:
    return Settings()
