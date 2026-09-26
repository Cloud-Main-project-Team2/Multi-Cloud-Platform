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

    # app/providers/session.py — AWS 역할 위임(AssumeRole)을 호출할 "출발 신원".
    # 이 값들은 우리 서비스 자신의 AWS 신원이지, 사용자에게 받는 값이 아니다. 비어 있으면
    # 위임 방식 credential만 쓸 수 없고(레거시 access key 방식은 그대로 동작) 나머지 기능엔
    # 영향이 없다. 서버를 AWS 위(EC2 instance role/ECS task role)에 올리면 키 두 개를 비우고
    # boto3의 기본 자격증명 체인에 맡기면 된다.
    platform_aws_account_id: str = Field(default="", alias="PLATFORM_AWS_ACCOUNT_ID")
    platform_aws_access_key_id: str = Field(default="", alias="PLATFORM_AWS_ACCESS_KEY_ID")
    platform_aws_secret_access_key: str = Field(default="", alias="PLATFORM_AWS_SECRET_ACCESS_KEY")
    # 우리 플랫폼 키가 빌릴 수 있는 역할의 ARN 패턴(IAM 정책의 Resource와 같은 값을 둔다).
    # 이름을 하나로 못 박으면 사내 명명 규칙이 있는 계정이 연결 자체를 못 하므로 접두사로 둔다.
    platform_aws_assumable_role_pattern: str = Field(
        default="arn:aws:iam::*:role/MultiCloudOps*", alias="PLATFORM_AWS_ASSUMABLE_ROLE_PATTERN"
    )
    # AssumeRole 세션 수명(초). 역할의 MaxSessionDuration을 넘으면 STS가 거부한다.
    # terraform apply 타임아웃(900초)보다 충분히 길어야 한다.
    platform_aws_session_duration_seconds: int = Field(
        default=3600, alias="PLATFORM_AWS_SESSION_DURATION_SECONDS"
    )

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

    # app/cost/ — 비용 조회 API의 지연 판정 임계(시간). 정본은 이 값 하나뿐이다 — 화면·API·
    # 보고서가 각자 숫자를 갖지 않는다(하루 1회 수집 24h + 실행 시각이 밀릴 여유 12h = 36).
    cost_stale_after_hours: int = Field(default=36, alias="COST_STALE_AFTER_HOURS")
    # 비용 자동 수집이 매일 도는 시각(UTC, 0-23). PR 4의 스케줄러가 읽는다.
    cost_ingest_hour_utc: int = Field(default=6, alias="COST_INGEST_HOUR_UTC")

    # app/cost/gating.py — "어댑터가 있다"와 "이 CSP를 실제로 수집한다"를 분리한다. 어댑터 등록
    # (app/cost/__init__.py의 COST_ADAPTERS)은 **구현 지원** 표시일 뿐이고, 실제 CSP 호출 여부는
    # 아래 두 값이 정한다. 새 CSP를 추가해도 이 값을 바꾸지 않으면 호출이 0건이다.
    #   COST_INGEST_PROVIDERS       수동 수집(POST /cost-ingestion-runs)을 허용할 provider 목록
    #   COST_AUTO_INGEST_PROVIDERS  자동 수집(하루 1회 스케줄러)을 허용할 provider 목록
    # 둘은 독립이다 — 테스트 계정 하나를 수동으로 허용해도 자동 수집은 켜지지 않는다.
    cost_ingest_providers: str = Field(default="aws", alias="COST_INGEST_PROVIDERS")
    cost_auto_ingest_providers: str = Field(default="aws", alias="COST_AUTO_INGEST_PROVIDERS")
    # 계정 단위 허용 목록 — `provider:cloud_account_id` 쌍을 쉼표로 나열한다(예: "azure:42,gcp:7").
    # gating.ACCOUNT_SCOPED_PROVIDERS에 든 provider는 **여기에 적힌 계정만** 수집한다(비어 있으면
    # 그 provider는 한 계정도 수집하지 않는다 — 빈 목록을 "전체 허용"으로 읽지 않는다).
    cost_ingest_account_ids: str = Field(default="", alias="COST_INGEST_ACCOUNT_IDS")

    # app/cost/azure_cost.py — 실수집 상한. 승인된 범위를 코드로 강제하기 위한 값이며, 초과가
    # 예상되면 **성공으로 끝내지 않는다**(부분 데이터도 저장하지 않는다).
    #   COST_AZURE_MAX_PAGES     따라갈 nextLink 페이지 수 상한
    #   COST_AZURE_MAX_REQUESTS  비용 조회 HTTP 요청 총량(첫 페이지 + 다음 페이지 + 429 재시도)
    # 제한 실수집 때는 예: COST_AZURE_MAX_PAGES=2, COST_AZURE_MAX_REQUESTS=3.
    cost_azure_max_pages: int = Field(default=50, alias="COST_AZURE_MAX_PAGES")
    cost_azure_max_requests: int = Field(default=60, alias="COST_AZURE_MAX_REQUESTS")
    # 429에서 서버가 요구한 대기시간이 이 값을 넘으면 기다리지 않고 종료한다(초).
    cost_azure_max_retry_wait_seconds: int = Field(default=30, alias="COST_AZURE_MAX_RETRY_WAIT_SECONDS")

    # app/cost/gcp_cost.py — GCP는 비용 "API"가 아니라 **BigQuery 청구 Export 테이블**을 읽는다.
    # 그래서 위험이 요청 수가 아니라 **스캔한 바이트(=과금)**다. 실행 전 dry-run으로 스캔량을
    # 먼저 재고, 상한을 넘으면 실제 쿼리를 보내지 않는다.
    #   COST_GCP_EXPORT_TABLES  "<project_id>:<프로젝트.데이터셋.테이블>" 목록(쉼표 구분).
    #                           비어 있으면 그 계정은 수집 대상이 아니다(임의 추측 금지).
    #   COST_GCP_MAX_SCANNED_BYTES  dry-run 예상치·실제 쿼리 모두에 거는 상한(기본 2GiB)
    cost_gcp_export_tables: str = Field(default="", alias="COST_GCP_EXPORT_TABLES")
    cost_gcp_max_scanned_bytes: int = Field(default=2 * 1024 ** 3, alias="COST_GCP_MAX_SCANNED_BYTES")
    cost_gcp_query_timeout_seconds: int = Field(default=120, alias="COST_GCP_QUERY_TIMEOUT_SECONDS")

    # app/report_scheduler.py — 보고서 정기 메일 발송 체크가 매일 도는 시각(UTC, 0-23).
    # "일간" 주기까지만 지원하므로 하루 1회 체크로 충분하다(cost 스케줄러와 동일 패턴).
    report_send_hour_utc: int = Field(default=7, alias="REPORT_SEND_HOUR_UTC")


@lru_cache
def get_settings() -> Settings:
    return Settings()
