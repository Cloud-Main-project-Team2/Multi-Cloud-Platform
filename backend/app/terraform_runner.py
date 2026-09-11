"""Terraform subprocess 오케스트레이션 (§10 프로비저닝).

이 서버엔 별도 워커/큐 프로세스가 없다(`app/resource_sync.py`/`app/routers/sync_jobs.py`와 같은
전제) — 프로비저닝 job도 FastAPI `BackgroundTasks` 안에서 같은 프로세스가 terraform CLI를
subprocess로 직접 구동한다. job마다 **독립된 워크스페이스 디렉터리**를 쓴다: 공유 디렉터리 +
`terraform workspace new`는 서로 다른 사용자의 job이 같은 프로세스에서 동시에 백그라운드로 돌 때
`.terraform/environment` 선택 포인터가 레이스될 수 있어 배제했다. 대신 `TF_PLUGIN_CACHE_DIR`
(공유 볼륨)로 provider 플러그인 재다운로드 비용만 줄인다.

state는 워크스페이스 디렉터리 안 로컬 backend에만 남는다 — 원격 backend(S3 등)는 구성하지 않는다
(단일 호스트/단일 프로세스 전제, CLAUDE.md에 한계로 기록). `provisioning_jobs.terraform_state_ref`에는
이 워크스페이스 경로만 저장하고 state 본문은 절대 DB에 넣지 않는다.

크리덴셜은 이 모듈 밖에서 이미 복호화된 뒤 **환경변수로만** 넘어온다(`credential_env`) —
`.tfvars.json`/Terraform 변수에는 secret을 절대 넣지 않는다(§18 "복호화 범위 최소화"). AWS는
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN` 환경변수를 provider가 자동으로
읽으므로, GCP처럼 워크스페이스 안에 파일을 써서 경로를 가리킬 필요가 없다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.config import get_settings

_STDERR_TAIL_CHARS = 2000

_AUTH_FAILURE_PATTERNS = (
    "invalidclienttokenid",
    "authfailure",
    "signaturedoesnotmatch",
    "unrecognizedclientexception",
    "could not find aws credentials",
    "no valid credential sources",
)
_PERMISSION_DENIED_PATTERNS = (
    "unauthorizedoperation",
    "accessdenied",
    "is not authorized to perform",
    "403",
)


@dataclass
class TerraformResult:
    success: bool
    outputs: dict | None = None
    error_code: str | None = None
    error_message: str | None = None
    cancelled: bool = False


def _classify_error(stderr: str) -> str:
    lowered = stderr.lower()
    if any(p in lowered for p in _AUTH_FAILURE_PATTERNS):
        return "PROVIDER_AUTHENTICATION_FAILED"
    if any(p in lowered for p in _PERMISSION_DENIED_PATTERNS):
        return "CLOUD_PERMISSION_DENIED"
    return "TERRAFORM_ERROR"


def _safe_error_message(stderr: str) -> str:
    """Terraform 자체가 sensitive 변수를 마스킹하지만, DB에 넣기 전에 한 번 더 길이를 제한한다."""
    return stderr.strip()[-_STDERR_TAIL_CHARS:]


def prepare_workspace(workspace_dir: Path, module_source_dir: Path) -> None:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    for item in module_source_dir.iterdir():
        if item.suffix == ".tf":
            shutil.copy2(item, workspace_dir / item.name)


def _run(cmd: list[str], *, cwd: Path, env: dict, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)


def run_apply(
    workspace_dir: Path,
    module_source_dir: Path,
    tfvars: dict,
    credential_env: dict[str, str],
    *,
    cancel_check: Callable[[], bool] = lambda: False,
    timeout_seconds: int | None = None,
) -> TerraformResult:
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

    env = {
        **os.environ,
        **credential_env,
        "TF_PLUGIN_CACHE_DIR": str(plugin_cache_dir),
        "TF_IN_AUTOMATION": "1",
        "TF_INPUT": "0",
    }

    try:
        init = _run([terraform_bin, "init", "-no-color"], cwd=workspace_dir, env=env, timeout=timeout)
        if init.returncode != 0:
            return TerraformResult(
                success=False, error_code=_classify_error(init.stderr), error_message=_safe_error_message(init.stderr)
            )

        if cancel_check():
            return TerraformResult(success=False, cancelled=True)

        plan = _run(
            [terraform_bin, "plan", "-no-color", "-input=false", "-out=tfplan"],
            cwd=workspace_dir,
            env=env,
            timeout=timeout,
        )
        if plan.returncode != 0:
            return TerraformResult(
                success=False, error_code=_classify_error(plan.stderr), error_message=_safe_error_message(plan.stderr)
            )

        if cancel_check():
            return TerraformResult(success=False, cancelled=True)

        apply = _run(
            [terraform_bin, "apply", "-no-color", "-input=false", "-auto-approve", "tfplan"],
            cwd=workspace_dir,
            env=env,
            timeout=timeout,
        )
        if apply.returncode != 0:
            return TerraformResult(
                success=False,
                error_code=_classify_error(apply.stderr),
                error_message=_safe_error_message(apply.stderr),
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
                "않습니다 — AWS 콘솔에서 확인한 뒤 재시도하세요."
            ),
        )


def run_destroy(
    workspace_dir: Path,
    credential_env: dict[str, str],
    *,
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
    env = {
        **os.environ,
        **credential_env,
        "TF_PLUGIN_CACHE_DIR": str(plugin_cache_dir),
        "TF_IN_AUTOMATION": "1",
        "TF_INPUT": "0",
    }

    try:
        destroy = _run(
            [terraform_bin, "destroy", "-no-color", "-input=false", "-auto-approve"],
            cwd=workspace_dir,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return TerraformResult(
            success=False,
            error_code="TERRAFORM_ERROR",
            error_message="terraform destroy가 제한 시간을 초과했습니다. AWS 콘솔에서 상태를 확인하세요.",
        )

    if destroy.returncode != 0:
        return TerraformResult(
            success=False, error_code=_classify_error(destroy.stderr), error_message=_safe_error_message(destroy.stderr)
        )
    return TerraformResult(success=True, outputs={})
