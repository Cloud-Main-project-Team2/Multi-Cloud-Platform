"""Terraform 실행을 위한 얇은 subprocess 래퍼.

- 어떤 provider 모듈에도 재사용 가능하도록 provider 고유 로직을 담지 않는다.
- 자격 증명은 이 모듈을 호출하는 쪽이 `env`로만 주입한다(파일에 쓰지 않음).
- stdout/stderr에 자격 증명 값이 우연히 섞여 나오는 경우를 대비해 저장 전 redact한다.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class TerraformError(Exception):
    """Terraform 실행 실패. 메시지는 이미 redact된 상태여야 한다."""


@dataclass
class TerraformResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int


MIN_REDACT_LENGTH = 8


def redact(text: str, secrets: list[str]) -> str:
    """text에서 secrets에 담긴 값들을 제거한다.

    `MIN_REDACT_LENGTH`보다 짧은 값은 무시한다 — 실제 Azure 등의 client secret/GUID는
    충분히 길어서 문제가 없지만, 짧은 값(테스트 placeholder 등)은 에러 메시지 안의 흔한
    단어와 우연히 겹쳐 메시지 전체를 알아볼 수 없게 뭉개버릴 수 있다(실제로 겪은 문제 —
    tenant_id="t" 같은 한 글자 값이 "Trace"의 "T"까지 지워버렸다). 이 길이 미만 값은 애초에
    redact로 보호할 실익도 적다.
    """
    redacted = text
    for secret in secrets:
        if secret and len(secret) >= MIN_REDACT_LENGTH:
            redacted = redacted.replace(secret, "***REDACTED***")
    return redacted


def prepare_workspace(module_dir: Path, workspace_dir: Path) -> None:
    """module_dir의 .tf 파일들을 workspace_dir로 복사해 job 전용 실행 디렉터리를 만든다.

    여러 job이 같은 모듈을 동시에 실행해도 state/plan 파일이 서로 섞이지 않도록 한다.
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)
    for tf_file in module_dir.glob("*.tf"):
        shutil.copy2(tf_file, workspace_dir / tf_file.name)


def _run(args: list[str], cwd: Path, env: dict[str, str], timeout: int, secrets: list[str]) -> TerraformResult:
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TerraformError(f"terraform {' '.join(args)} timed out after {timeout}s") from exc

    stdout = redact(proc.stdout, secrets)
    stderr = redact(proc.stderr, secrets)
    return TerraformResult(ok=proc.returncode == 0, stdout=stdout, stderr=stderr, returncode=proc.returncode)


def apply(
    workspace_dir: Path,
    env: dict[str, str],
    secrets: list[str],
    timeout: int,
) -> TerraformResult:
    """`terraform init` 후 `terraform apply -auto-approve`를 실행한다.

    init이 실패하면 apply는 시도하지 않는다. 반환값의 stdout/stderr는 이미 redact됐다.
    """
    init_result = _run(["terraform", "init", "-input=false"], workspace_dir, env, timeout, secrets)
    if not init_result.ok:
        return init_result

    return _run(
        ["terraform", "apply", "-auto-approve", "-input=false", "-var-file=terraform.tfvars.json"],
        workspace_dir,
        env,
        timeout,
        secrets,
    )


def output_json(workspace_dir: Path, env: dict[str, str], secrets: list[str], timeout: int) -> str:
    """`terraform output -json`의 (redact된) 원문을 반환한다. 파싱은 호출자가 한다."""
    result = _run(["terraform", "output", "-json"], workspace_dir, env, timeout, secrets)
    if not result.ok:
        raise TerraformError(result.stderr or "terraform output failed")
    return result.stdout
