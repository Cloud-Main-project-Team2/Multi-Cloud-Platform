"""GCP Cloud CDN 프로비저닝 — `app/terraform_runner.py` 호출부 (§10.3).

`app/gcp_provisioning.py`(Compute Engine)·`app/gcp_cloudsql_provisioning.py`(Cloud SQL)·
`app/gcp_storage_provisioning.py`(Cloud Storage)와 같은 flat 계약을 따른다:
`validate_spec()`(동기, 요청 처리 중 raise) + `run(...)→TerraformResult`(비동기 백그라운드,
raise하지 않음) 두 함수와 `SENSITIVE_PROVIDER_SPEC_FIELDS` 상수만 제공한다. `app/provisioning.py`
레지스트리가 이 모듈을 `(gcp, cloud_cdn)`에 연결한다.

## GCP엔 "CDN" 리소스가 없다 — 로드밸런서 스택으로 구현

AWS(CloudFront)·Azure(Front Door)는 CDN이 독립 리소스 하나지만, GCP는 그런 리소스가 없다.
`enable_cdn`은 백엔드(버킷 또는 서비스)에 붙는 설정일 뿐이고, 실제로 트래픽을 받으려면 로드밸런서
스택(백엔드 → URL맵 → 프록시 → 포워딩룰) 전체가 필요하다(`terraform/gcp/cloud_cdn/main.tf` 참고).
그래서 `provider_spec.lb_stack_ack`(로드밸런서 스택 생성 동의)를 필수로 받는다 — 맵핑 문서의
"LB stack 확인 체크박스(필수)" 반영. AWS/Azure보다 리소스가 많아 비용(로드밸런서 자체 요금 +
데이터 처리 요금이 CDN 캐시 요금 위에 추가로 붙음)과 실패 지점이 더 많다는 걸 사용자가 인지하고
동의해야 한다는 의미다.

## 백엔드 버킷은 CDN 전용으로 자동 생성한다(2026-09-14 결정 — 기존 버킷 재사용 안 함)

처음엔 사용자가 기존 Cloud Storage 버킷 이름을 입력받아 그대로 연결했는데, 그러려면 그 버킷에
공개 읽기 권한을 부여해야 해서(안 그러면 CDN이 원본을 못 읽어 `AccessDenied` — 실사용 테스트로
확인함) **"범용 버킷"과 "CDN 공개용 버킷"이 뒤섞이는 문제**가 있었다: 나중에 그 버킷에 실수로
민감한 파일을 같이 넣으면 그것도 같이 공개된다.

`app/gcp_cloudsql_provisioning.py`(#47, 이승현님)가 "공유 자원 재사용" 대신 "이 리소스 전용
자원을 새로 만듦"(DB마다 전용 VPC)으로 같은 종류의 문제를 해결한 것과 같은 방향으로, **CDN도
기존 버킷을 받지 않고 이 CDN 전용 버킷을 직접 만든다**(`terraform/gcp/cloud_cdn/main.tf`의
`google_storage_bucket.cdn_bucket`). `app/gcp_storage_provisioning.py`(범용 Storage 기능)는
전혀 건드리지 않는다 — 완전히 별개의, 서로 공유하지 않는 버킷 생성 경로다.

**이름 충돌 방지**: 버킷 이름은 `mcp-cdn-{job_id}`로 짓는다. `job_id`는 DB auto-increment라
**한 번 쓰인 값은 절대 재사용되지 않는다** — 같은 CDN을 지우고 새로 만들어도 새 job은 항상 새
번호를 받으므로, GCS 전역 유일성 제약과 무관하게 항상 겹치지 않는 이름이 보장된다(사용자가 이름을
직접 고를 필요도, 겹칠 걱정도 없음). `job_id`는 요청 처리 시점(`validate_spec()`)엔 아직 없고
job이 DB에 만들어진 뒤 백그라운드 실행(`run()`) 시점에만 있으므로, 버킷 이름 조합은 `run()`
안에서만 한다(`validate_spec()`은 `lb_stack_ack`/이름 형식만 미리 검증).

이 전용 버킷엔 `google_storage_bucket_iam_member`(`allUsers`에 `roles/storage.objectViewer`)로
공개 읽기 권한을 부여한다 — 이 버킷은 처음부터 "CDN이 공개로 서빙할 콘텐츠 전용"으로만 만들어져서,
범용 버킷을 나중에 공개로 전환하는 것과 달리 다른 용도와 섞일 위험이 없다.

## 1차 범위: 백엔드 버킷(GCS)만 지원

백엔드 서비스(인스턴스/NEG 기반)는 대상이 이미 떠 있어야 하고 헬스체크 등 구성이 복잡해 범위
밖으로 뺐다(맵핑 문서 "Health Probe는 백엔드 서비스일 때만" 노출 — 이번엔 버킷만 지원하므로
자연히 제외).

## HTTPS/커스텀 도메인은 범위 밖

Google 관리형 SSL 인증서는 DNS 검증 때문에 ACTIVE 상태가 되기까지 수십 분~몇 시간 걸릴 수 있어,
이 앱의 `TERRAFORM_APPLY_TIMEOUT_SECONDS`(기본 15분)를 넘기기 쉽다 — AWS CloudFront가 커스텀
도메인/ACM 인증서를 범위 밖으로 빼고 기본 인증서만 쓴 것과 같은 이유다. 그래서 HTTP(포트 80)로만
서빙한다. Cache Mode(`CACHE_ALL_STATIC`)·Compression(`AUTOMATIC`)은 맵핑 문서 기본값으로 고정하고
입력받지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from app.errors import ApiError, validation_error
from app.terraform_runner import TerraformResult, run_apply

MODULE_DIR = Path(__file__).resolve().parent.parent / "terraform" / "gcp" / "cloud_cdn"

# 만들어지는 리소스 자체의 비밀값이 없다(Compute Engine/Cloud Storage와 동일) — 비어 있음.
SENSITIVE_PROVIDER_SPEC_FIELDS: frozenset[str] = frozenset()

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,38}[a-z0-9]$")


def _derive(common_spec: dict, provider_spec: dict) -> str:
    """`instance_name`을 반환한다. 실패 시 422 `ApiError`를 raise한다.

    버킷 이름은 여기서 정하지 않는다 — `job_id`가 필요한데 이 함수는 job 생성 전(요청 처리 중)에도
    호출되기 때문이다(`validate_spec()`). 버킷 이름 조합은 `run()`에서 `job_id`가 확정된 뒤에 한다.
    """
    name = common_spec.get("name")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise validation_error(
            "common_spec.name은 소문자로 시작하는 영소문자/숫자/하이픈 2~40자여야 합니다.",
            details=[{"field": "common_spec.name", "reason": "invalid"}],
        )

    if provider_spec.get("lb_stack_ack") is not True:
        raise validation_error(
            "provider_spec.lb_stack_ack가 true여야 합니다 — 이 리소스는 CDN 하나만이 아니라 "
            "전체 로드밸런서 스택(백엔드/URL맵/프록시/포워딩룰) + 이 CDN 전용 공개 버킷을 새로 "
            "만들며, 그만큼 별도 비용이 발생한다는 데 동의해야 합니다.",
            details=[{"field": "provider_spec.lb_stack_ack", "reason": "required"}],
        )

    return f"mcp-{name}"


def validate_spec(common_spec: dict, provider_spec: dict) -> None:
    """라우터가 요청 처리 중(202 반환 전) 동기적으로 호출한다 — 실패 시 422 `ApiError`를 raise한다."""
    _derive(common_spec, provider_spec)


def build_tfvars(job_id: int, project_id: str, instance_name: str) -> dict:
    return {
        "project_id": project_id,
        "instance_name": instance_name,
        # job_id는 DB auto-increment라 한 번 쓰인 값이 절대 재사용되지 않는다 — 같은 CDN을
        # 지우고 새로 만들어도 새 job은 항상 새 번호를 받으므로 GCS 전역 유일성 제약과 무관하게
        # 항상 겹치지 않는 이름이 보장된다.
        "bucket_name": f"mcp-cdn-{job_id}",
    }


def run(
    *,
    job_id: int,
    workspace_dir: Path,
    project_id: str,
    common_spec: dict,
    provider_spec: dict,
    secret_payload: dict,
    cancel_check: Callable[[], bool] = lambda: False,
) -> TerraformResult:
    """백그라운드 job에서 호출된다 — 이미 `validate_spec()`을 통과한 입력이지만, raise 대신
    `TerraformResult`로 실패를 표현해 백그라운드 태스크 밖으로 예외가 새 나가지 않게 한다."""
    try:
        instance_name = _derive(common_spec, provider_spec)
    except ApiError as exc:
        return TerraformResult(success=False, error_code=exc.code, error_message=exc.message)

    tfvars = build_tfvars(job_id, project_id, instance_name)
    # GCP는 secret_payload(서비스 계정 키 JSON)를 환경변수가 아니라 파일로 넘긴다(다른 GCP
    # 러너와 동일) — terraform_runner가 0600 임시 파일로 써서 GOOGLE_APPLICATION_CREDENTIALS로만
    # 노출한다.
    return run_apply(
        workspace_dir, MODULE_DIR, tfvars, {}, credentials_file=secret_payload, cancel_check=cancel_check
    )
