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

## 백엔드 버킷: 전용 버킷 자동 생성 또는 기존 버킷 연결 중 선택(2026-09-14, 체크박스로 확장)

처음엔 사용자가 기존 Cloud Storage 버킷 이름을 입력받아 그대로 연결했는데, 그러려면 그 버킷에
공개 읽기 권한을 부여해야 해서(안 그러면 CDN이 원본을 못 읽어 `AccessDenied` — 실사용 테스트로
확인함) **"범용 버킷"과 "CDN 공개용 버킷"이 뒤섞이는 문제**가 있었다: 나중에 그 버킷에 실수로
민감한 파일을 같이 넣으면 그것도 같이 공개된다. 그래서 한 번은 "CDN 전용 버킷을 항상 자동 생성"
으로 좁혔는데, 이후 "실제로는 이미 있는 버킷을 CDN에 연결하고 싶은 경우도 많다"는 점이 확인돼
**둘 다 지원하도록 다시 넓혔다**.

`provider_spec.create_bucket`(bool, 생략 시 `True`로 간주 — 기존 동작과 호환)으로 선택한다:

- **`True`(기본, "CDN 전용 버킷 자동 생성")**: `app/gcp_cloudsql_provisioning.py`(#47, 이승현님)가
  "공유 자원 재사용" 대신 "이 리소스 전용 자원을 새로 만듦"(DB마다 전용 VPC)으로 같은 종류의
  문제를 해결한 것과 같은 방향 — 이 CDN 전용 버킷을 직접 만든다
  (`terraform/gcp/cloud_cdn/main.tf`의 `google_storage_bucket.cdn_bucket`).
  `app/gcp_storage_provisioning.py`(범용 Storage 기능)는 전혀 건드리지 않는다 — 완전히 별개의,
  서로 공유하지 않는 버킷 생성 경로다. **이름 충돌 방지**: 버킷 이름은 `mcp-cdn-{job_id}`로
  짓는다. `job_id`는 DB auto-increment라 **한 번 쓰인 값은 절대 재사용되지 않는다** — 같은 CDN을
  지우고 새로 만들어도 새 job은 항상 새 번호를 받으므로, GCS 전역 유일성 제약과 무관하게 항상
  겹치지 않는 이름이 보장된다(사용자가 이름을 직접 고를 필요도, 겹칠 걱정도 없음). `job_id`는
  요청 처리 시점(`validate_spec()`)엔 아직 없고 job이 DB에 만들어진 뒤 백그라운드 실행(`run()`)
  시점에만 있으므로, 버킷 이름 조합은 `run()` 안에서만 한다. 우리가 만든 버킷이므로 이 경로에서만
  Terraform이 공개 읽기 IAM도 함께 부여한다(아래 참고).
- **`False`("기존 버킷 연결")**: `provider_spec.backend_bucket_name`(필수, 이미 있는 GCS 버킷
  이름)을 `google_compute_backend_bucket`의 백엔드로 그대로 연결한다. **이 경로에서는 그 버킷의
  IAM(공개 읽기 권한)을 절대 건드리지 않는다** — 대신 `provider_spec.existing_bucket_public_ack`
  로 "그 버킷을 GCP 콘솔에서 이미 공개 읽기로 설정해 두었다"는 사실 확인을 받는다.

### 왜 기존 버킷의 IAM은 자동으로 바꾸지 않는가(2026-09-15 결정)

처음엔 두 경로 모두 우리 Terraform이 `google_storage_bucket_iam_member`(`allUsers`에
`roles/storage.objectViewer`)로 공개 읽기를 직접 부여했다. 그런데 "기존 버킷 연결" 경로는 **우리가
만들지 않은, 사용자가 이미 다른 용도로 쓰고 있었을 수도 있는 버킷의 보안 설정을 자동으로 바꾸는
것**이라, 버킷 이름을 잘못 입력하는 사소한 실수 하나로 **엉뚱한(어쩌면 민감한) 버킷이 인터넷에
공개**될 수 있다는 문제가 있었다(팀 논의로 확인). AWS가 S3 "퍼블릭 액세스 차단"을 기본값으로 걸고
여러 단계 확인 없이는 못 풀게 만든 것과 같은 이유로, **자동화가 되돌리기 어려운 보안 설정 변경까지
대신하지 않도록** 방향을 바꿨다: 같은 실수를 해도 "정보 유출"이 아니라 "AccessDenied로 생성 실패"
(안전한 방향의 실패)가 되도록.

그래서 지금은:
- **자동 생성 버킷**(`create_bucket=true`): 우리가 만든 리소스라 IAM도 우리가 안전하게 부여한다.
  다른 용도와 섞이지 않는 전용 버킷이라 공개해도 위험이 없다.
- **기존 버킷**(`create_bucket=false`): `existing_bucket_public_ack`는 "동의"가 아니라 **"확인"**
  이다 — 사용자가 GCP 콘솔에서 그 버킷을 이미 공개 읽기로 설정해 뒀는지 스스로 확인하는 절차이며,
  우리 시스템은 그 설정을 절대 대신 바꾸지 않는다. 안 해놨다면 apply가 `AccessDenied`로 실패할
  뿐, 어떤 보안 사고도 나지 않는다.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.errors import ApiError, validation_error
from app.terraform_runner import TerraformResult, run_apply

MODULE_DIR = Path(__file__).resolve().parent.parent / "terraform" / "gcp" / "cloud_cdn"

# 만들어지는 리소스 자체의 비밀값이 없다(Compute Engine/Cloud Storage와 동일) — 비어 있음.
SENSITIVE_PROVIDER_SPEC_FIELDS: frozenset[str] = frozenset()

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,38}[a-z0-9]$")

# GCS 버킷 이름 규칙(간소화): 소문자/숫자로 시작·끝, 중간엔 소문자/숫자/`-`/`_`/`.` 허용, 3~63자.
_BUCKET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")


@dataclass(frozen=True)
class _DerivedSpec:
    instance_name: str
    create_bucket: bool
    backend_bucket_name: str | None  # create_bucket=False일 때만 값 있음(기존 버킷 이름)


def _derive(common_spec: dict, provider_spec: dict) -> _DerivedSpec:
    """입력을 검증하고 파생값을 반환한다. 실패 시 422 `ApiError`를 raise한다.

    자동 생성 버킷의 실제 이름(`mcp-cdn-{job_id}`)은 여기서 정하지 않는다 — `job_id`가 필요한데
    이 함수는 job 생성 전(요청 처리 중)에도 호출되기 때문이다(`validate_spec()`). 그 조합은
    `run()`에서 `job_id`가 확정된 뒤에 한다.
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
            "전체 로드밸런서 스택(백엔드/URL맵/프록시/포워딩룰)을 새로 만들며, 그만큼 별도 비용이 "
            "발생한다는 데 동의해야 합니다.",
            details=[{"field": "provider_spec.lb_stack_ack", "reason": "required"}],
        )

    create_bucket = provider_spec.get("create_bucket", True)
    if not isinstance(create_bucket, bool):
        raise validation_error(
            "provider_spec.create_bucket은 true/false여야 합니다.",
            details=[{"field": "provider_spec.create_bucket", "reason": "invalid"}],
        )

    if create_bucket:
        return _DerivedSpec(instance_name=f"mcp-{name}", create_bucket=True, backend_bucket_name=None)

    backend_bucket_name = provider_spec.get("backend_bucket_name")
    if not isinstance(backend_bucket_name, str) or not _BUCKET_NAME_RE.fullmatch(backend_bucket_name):
        raise validation_error(
            "기존 버킷을 사용하려면 provider_spec.backend_bucket_name에 유효한 GCS 버킷 이름을 "
            "입력해야 합니다.",
            details=[{"field": "provider_spec.backend_bucket_name", "reason": "required"}],
        )

    if provider_spec.get("existing_bucket_public_ack") is not True:
        raise validation_error(
            "provider_spec.existing_bucket_public_ack가 true여야 합니다 — 이 플랫폼은 기존 버킷의 "
            "권한을 자동으로 바꾸지 않습니다. CDN이 정상 동작하려면 선택한 버킷 전체(개별 파일이 "
            "아님)가 이미 GCP 콘솔에서 공개 읽기(allUsers · Storage Object Viewer)로 설정돼 있어야 "
            "하며, 그 사실을 확인해야 합니다.",
            details=[{"field": "provider_spec.existing_bucket_public_ack", "reason": "required"}],
        )

    return _DerivedSpec(instance_name=f"mcp-{name}", create_bucket=False, backend_bucket_name=backend_bucket_name)


def validate_spec(common_spec: dict, provider_spec: dict) -> None:
    """라우터가 요청 처리 중(202 반환 전) 동기적으로 호출한다 — 실패 시 422 `ApiError`를 raise한다."""
    _derive(common_spec, provider_spec)


def build_tfvars(job_id: int, project_id: str, instance_name: str, create_bucket: bool, backend_bucket_name: str | None) -> dict:
    # job_id는 DB auto-increment라 한 번 쓰인 값이 절대 재사용되지 않는다 — 같은 CDN을 지우고
    # 새로 만들어도 새 job은 항상 새 번호를 받으므로 GCS 전역 유일성 제약과 무관하게 항상 겹치지
    # 않는 이름이 보장된다. create_bucket=False면 사용자가 고른 기존 버킷 이름을 그대로 쓴다.
    bucket_name = f"mcp-cdn-{job_id}" if create_bucket else backend_bucket_name
    return {
        "project_id": project_id,
        "instance_name": instance_name,
        "bucket_name": bucket_name,
        "create_bucket": create_bucket,
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
        derived = _derive(common_spec, provider_spec)
    except ApiError as exc:
        return TerraformResult(success=False, error_code=exc.code, error_message=exc.message)

    tfvars = build_tfvars(
        job_id, project_id, derived.instance_name, derived.create_bucket, derived.backend_bucket_name
    )
    # GCP는 secret_payload(서비스 계정 키 JSON)를 환경변수가 아니라 파일로 넘긴다(다른 GCP
    # 러너와 동일) — terraform_runner가 0600 임시 파일로 써서 GOOGLE_APPLICATION_CREDENTIALS로만
    # 노출한다.
    return run_apply(
        workspace_dir, MODULE_DIR, tfvars, {}, credentials_file=secret_payload, cancel_check=cancel_check
    )
