"""Terraform subprocess 오케스트레이션 — provider 무관 (§10 프로비저닝).

이 서버엔 별도 워커/큐 프로세스가 없다(`app/resource_sync.py`/`app/routers/sync_jobs.py`와 같은
전제) — 프로비저닝 job도 FastAPI `BackgroundTasks` 안에서 같은 프로세스가 terraform CLI를
subprocess로 직접 구동한다. job마다 **독립된 워크스페이스 디렉터리**를 쓴다: 공유 디렉터리 +
`terraform workspace new`는 서로 다른 사용자의 job이 같은 프로세스에서 동시에 백그라운드로 돌 때
`.terraform/environment` 선택 포인터가 레이스될 수 있어 배제했다. 대신 `TF_PLUGIN_CACHE_DIR`
(공유 볼륨)로 provider 플러그인 재다운로드 비용만 줄인다 — 단, 이 캐시 디렉터리 자체는 동시
`terraform init` 접근에 안전하지 않아(Terraform 공식 문서) `_INIT_LOCK`으로 init만 직렬화한다.

state는 워크스페이스 디렉터리 안 로컬 backend에만 남는다 — 원격 backend(S3/GCS 등)는 구성하지
않는다(단일 호스트/단일 프로세스 전제, CLAUDE.md에 한계로 기록). `provisioning_jobs.terraform_state_ref`
에는 이 워크스페이스 경로만 저장하고 state 본문은 절대 DB에 넣지 않는다.

## 자격 증명 전달 방식 — provider 3사 통합

세 provider가 credential을 terraform에 넘기는 방식이 달라 하나의 `run_apply()`가 두 경로를
모두 지원한다(§18 "복호화 범위 최소화" — secret은 `.tfvars.json`/Terraform 변수에 절대 안 넣는다).

- **AWS**: `credential_env`에 `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`을
  담아 넘기면 provider가 환경변수로 자동으로 읽는다. 파일 불필요.
- **Azure**: `credential_env`에 `ARM_*` 네 개 + (VM OS 비밀번호인) `TF_VAR_admin_password`를 담아
  넘긴다. 역시 파일 불필요.
- **GCP**: ADC가 환경변수 dict를 못 읽으므로 서비스 계정 키 JSON을 워크스페이스 안에 0600
  임시 파일로 써서 `GOOGLE_APPLICATION_CREDENTIALS`로 그 subprocess 호출에만 노출하고
  `finally`에서 즉시 지운다 — 호출부가 `credentials_file=secret_payload`로 넘기면 된다.

stdout/stderr에 secret 값이 우연히 섞여 나오는 경우를 대비해, 호출부가 `secrets=[...]`를 주면
저장 전 redact한다(Azure는 client_secret 등을 넘긴다). Terraform 자체도 sensitive 변수를
마스킹하지만 방어적으로 한 번 더 지운다.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.config import get_settings

_STDERR_TAIL_CHARS = 2000

# terraform init의 provider 설치 단계는 공유 TF_PLUGIN_CACHE_DIR에 처음 받는 provider 바이너리를
# 그 경로에 직접 써 넣는다 — 이 디렉터리는 Terraform 공식 문서상 동시 접근에 안전하지 않다.
# 여러 job이 FastAPI BackgroundTasks로 같은 프로세스 안에서 동시에 돌면(§ 위 docstring) 서로 다른
# job의 init이 같은 provider 바이너리 경로에 동시에 쓰기를 시도해 "text file busy"로 실패할 수
# 있다(RDS apply가 몇 분씩 걸리는 동안 다른 job의 init이 겹쳐 실제로 재현됨). init만 이 락으로
# 직렬화한다 — plan/apply/output은 워크스페이스별로 독립이라 캐시를 다시 건드리지 않는다.
_INIT_LOCK = threading.Lock()

# secret 값이 이 길이보다 짧으면 redact 대상에서 제외한다 — 짧은 값(테스트 placeholder,
# tenant_id="t" 같은 한 글자 등)은 에러 메시지 안 흔한 단어와 우연히 겹쳐 메시지 전체를
# 알아볼 수 없게 뭉개버릴 수 있고, 애초에 보호할 실익도 적다.
MIN_REDACT_LENGTH = 8

_AUTH_FAILURE_PATTERNS = (
    "invalidclienttokenid",
    "authfailure",
    "signaturedoesnotmatch",
    "unrecognizedclientexception",
    "could not find aws credentials",
    "no valid credential sources",
    "invalid_grant",
    "invalid_client",
    "could not find default credentials",
    "failed to find default credentials",
    "oauth2: cannot fetch token",
    "invalid authentication credentials",
)
_PERMISSION_DENIED_PATTERNS = (
    "unauthorizedoperation",
    "accessdenied",
    "is not authorized to perform",
    "permission_denied",
    "permission denied",
    "insufficient authentication scopes",
    "does not have permission",
    "caller does not have permission",
    "error 401",
    "error 403",
    "status code: 403",
)


@dataclass
class TerraformResult:
    success: bool
    outputs: dict | None = None
    error_code: str | None = None
    error_message: str | None = None
    cancelled: bool = False


def redact(text: str, secrets: list[str]) -> str:
    """text에서 secrets에 담긴 (충분히 긴) 값들을 `***REDACTED***`로 치환한다."""
    redacted = text
    for secret in secrets:
        if secret and len(secret) >= MIN_REDACT_LENGTH:
            redacted = redacted.replace(secret, "***REDACTED***")
    return redacted


def _classify_error(stderr: str) -> str:
    lowered = stderr.lower()
    if any(p in lowered for p in _AUTH_FAILURE_PATTERNS):
        return "PROVIDER_AUTHENTICATION_FAILED"
    if any(p in lowered for p in _PERMISSION_DENIED_PATTERNS):
        return "CLOUD_PERMISSION_DENIED"
    return "TERRAFORM_ERROR"


def _safe_error_message(stderr: str, secrets: list[str] | None) -> str:
    """DB에 넣기 전에 secret을 redact하고 길이를 제한한다."""
    message = redact(stderr, secrets) if secrets else stderr
    return message.strip()[-_STDERR_TAIL_CHARS:]


def prepare_workspace(workspace_dir: Path, module_source_dir: Path) -> None:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    for item in module_source_dir.iterdir():
        if item.suffix == ".tf":
            shutil.copy2(item, workspace_dir / item.name)


def _write_credentials_file(workspace_dir: Path, credentials_json: dict) -> Path:
    fd, raw_path = tempfile.mkstemp(dir=workspace_dir, prefix=".gcp-credentials-", suffix=".json")
    path = Path(raw_path)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w") as f:
            json.dump(credentials_json, f)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _run(cmd: list[str], *, cwd: Path, env: dict, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)


def _base_env(credential_env: dict[str, str], plugin_cache_dir: Path) -> dict[str, str]:
    return {
        **os.environ,
        **credential_env,
        "TF_PLUGIN_CACHE_DIR": str(plugin_cache_dir),
        "TF_IN_AUTOMATION": "1",
        "TF_INPUT": "0",
    }


def run_apply(
    workspace_dir: Path,
    module_source_dir: Path,
    tfvars: dict,
    credential_env: dict[str, str],
    *,
    credentials_file: dict | None = None,
    secrets: list[str] | None = None,
    cancel_check: Callable[[], bool] = lambda: False,
    timeout_seconds: int | None = None,
) -> TerraformResult:
    """init → plan → apply → output 순으로 실행한다. 예외를 던지지 않고 `TerraformResult`로만
    실패를 표현한다(백그라운드 태스크 밖으로 예외가 새 나가지 않게).

    - `credential_env`: subprocess 환경변수에 병합할 자격 증명/민감 변수(AWS 키, Azure ARM_*,
      TF_VAR_admin_password 등).
    - `credentials_file`: GCP처럼 파일 경유 자격 증명이 필요할 때 dict를 넘기면 워크스페이스 안에
      0600 임시 파일로 써서 GOOGLE_APPLICATION_CREDENTIALS로 노출하고 종료 시 지운다.
    - `secrets`: 에러 메시지 저장 전에 redact할 값 목록.
    """
    settings = get_settings()
    timeout = timeout_seconds or settings.terraform_apply_timeout_seconds
    terraform_bin = settings.terraform_binary_path

    try:
        prepare_workspace(workspace_dir, module_source_dir)
    except OSError as exc:
        return TerraformResult(success=False, error_code="TERRAFORM_ERROR", error_message=str(exc)[:_STDERR_TAIL_CHARS])

    (workspace_dir / "terraform.tfvars.json").write_text(json.dumps(tfvars))

    plugin_cache_dir = Path(settings.terraform_plugin_cache_dir)
    plugin_cache_dir.mkdir(parents=True, exist_ok=True)
    env = _base_env(credential_env, plugin_cache_dir)

    credentials_path: Path | None = None
    if credentials_file is not None:
        credentials_path = _write_credentials_file(workspace_dir, credentials_file)
        env["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)

    try:
        with _INIT_LOCK:
            init = _run([terraform_bin, "init", "-no-color"], cwd=workspace_dir, env=env, timeout=timeout)
        if init.returncode != 0:
            return TerraformResult(
                success=False, error_code=_classify_error(init.stderr),
                error_message=_safe_error_message(init.stderr, secrets),
            )

        if cancel_check():
            return TerraformResult(success=False, cancelled=True)

        plan = _run(
            [terraform_bin, "plan", "-no-color", "-input=false", "-out=tfplan"],
            cwd=workspace_dir, env=env, timeout=timeout,
        )
        if plan.returncode != 0:
            return TerraformResult(
                success=False, error_code=_classify_error(plan.stderr),
                error_message=_safe_error_message(plan.stderr, secrets),
            )

        if cancel_check():
            return TerraformResult(success=False, cancelled=True)

        apply = _run(
            [terraform_bin, "apply", "-no-color", "-input=false", "-auto-approve", "tfplan"],
            cwd=workspace_dir, env=env, timeout=timeout,
        )
        if apply.returncode != 0:
            return TerraformResult(
                success=False, error_code=_classify_error(apply.stderr),
                error_message=_safe_error_message(apply.stderr, secrets),
            )

        output = _run([terraform_bin, "output", "-no-color", "-json"], cwd=workspace_dir, env=env, timeout=timeout)
        if output.returncode != 0:
            # apply는 성공했지만 output 조회만 실패 — 리소스는 이미 생성됐으므로 실패로 되돌리지
            # 않고 outputs 없이 성공 처리한다(중복 생성 방지가 더 중요하다).
            return TerraformResult(success=True, outputs={})

        try:
            raw_outputs = json.loads(output.stdout)
            outputs = {k: v.get("value") for k, v in raw_outputs.items()}
        except (ValueError, AttributeError):
            outputs = {}

        return TerraformResult(success=True, outputs=outputs)
    except subprocess.TimeoutExpired:
        return TerraformResult(
            success=False,
            error_code="TERRAFORM_ERROR",
            error_message=(
                "terraform 실행이 제한 시간을 초과했습니다. 실제 생성 여부가 불명확하므로 자동 재시도하지 "
                "않습니다 — CSP 콘솔에서 확인한 뒤 재시도하세요."
            ),
        )
    finally:
        if credentials_path is not None:
            credentials_path.unlink(missing_ok=True)


def run_destroy(
    workspace_dir: Path,
    credential_env: dict[str, str],
    *,
    credentials_file: dict | None = None,
    secrets: list[str] | None = None,
    timeout_seconds: int | None = None,
) -> TerraformResult:
    """`run_apply()`가 이미 만든 워크스페이스(§ .tf 파일 + state)에 대고 destroy만 실행한다.

    API로는 노출하지 않는다(§8.5 `/resources/action`이 이미 만들어진 리소스의 제어를 SDK로
    맡는다는 원칙과 겹치지 않으려면 resource-sync 연동이 먼저 필요) — `app/dev_destroy_job.py`
    CLI 전용 정리 도구에서만 호출한다.
    """
    settings = get_settings()
    timeout = timeout_seconds or settings.terraform_apply_timeout_seconds
    terraform_bin = settings.terraform_binary_path

    plugin_cache_dir = Path(settings.terraform_plugin_cache_dir)
    plugin_cache_dir.mkdir(parents=True, exist_ok=True)
    env = _base_env(credential_env, plugin_cache_dir)

    credentials_path: Path | None = None
    if credentials_file is not None:
        credentials_path = _write_credentials_file(workspace_dir, credentials_file)
        env["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)

    try:
        destroy = _run(
            [terraform_bin, "destroy", "-no-color", "-input=false", "-auto-approve"],
            cwd=workspace_dir, env=env, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return TerraformResult(
            success=False,
            error_code="TERRAFORM_ERROR",
            error_message="terraform destroy가 제한 시간을 초과했습니다. CSP 콘솔에서 상태를 확인하세요.",
        )
    finally:
        if credentials_path is not None:
            credentials_path.unlink(missing_ok=True)

    if destroy.returncode != 0:
        return TerraformResult(
            success=False, error_code=_classify_error(destroy.stderr),
            error_message=_safe_error_message(destroy.stderr, secrets),
        )
    return TerraformResult(success=True, outputs={})
