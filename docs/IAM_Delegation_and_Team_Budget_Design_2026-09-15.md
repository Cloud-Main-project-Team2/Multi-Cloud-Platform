# IAM 위임 인증 전환 · 팀별 예산 관리 — 코드 검증 반영 설계 (2026-09-15)

> **이 문서의 위치**
> - 입력: 2026-09-14 강사 피드백 2건에서 출발한 제안 문서 "IAM 위임 인증 전환 · 팀별 계정·예산
>   관리 설계 방향(2026-09-15)". 그 문서는 **코드를 보지 않고 쓴 목표 설계**다.
> - 선행 문서: `docs/change_iam.md`(2026-09-14) — 그보다 **앞선** 설계안("멀티 클라우드 연결
>   인증 전환 설계안")을 코드로 검증한 결과. 이 문서는 그 검증을 **부정하지 않고 이어받는다**
>   (특히 §4의 "백엔드 프로덕션 신원 미정"은 여기서도 그대로 유효하다 → §2-6).
> - 이 문서: 09-15 제안 문서를 `main`(커밋 `b581f7a` 시점) 기준으로 재검증하고, `change_iam.md`가
>   다루지 않은 **팀·예산 기능**까지 합쳐 실행 순서를 확정한 판본. 앞으로 이 문서를 기준으로 한다.

## 0. 요약

1. **위임 전환은 제안 문서가 추정한 것보다 싸다.** 특히 AWS는 하류(소비처) 코드 수정이 사실상
   0이고 DB 마이그레이션도 필요 없다(§2-1, §2-2).
2. **팀·예산은 제안 문서가 추정한 것보다 비싸다**(단 §3은 **이번 범위 밖 — 타 담당**). 제안 문서는 "대시보드가 이미 3사 비용을
   actual/estimated로 계산해두고 있다"를 전제했으나, 실제로 있는 것은 **정가 기반 추정
   (`list_price_estimate`)뿐**이고 실측 비용 API 연동은 여전히 0줄이다(§3-1).
3. **이 문서 담당자의 실행 순서: AWS 위임(§2-3) → GCP impersonation(§2-4) → Azure는 설계만(§2-5).**
   팀·예산(§3)은 담당이 분리돼 이 순서에 포함되지 않는다(§4).
4. 3사 균형에 대한 입장: 편중이 아니라 **CSP별 표준 성숙도 차이**다. AWS/GCP는 대칭으로
   구현하고 Azure는 "왜 다른지"를 근거와 함께 남긴다(§2-5).

## 1. 코드 검증 결과 — 제안 문서의 전제 vs 실제

| 제안 문서의 전제 | 실제 코드(`main`, `b581f7a`) | 결론 |
|---|---|---|
| `cloud_credentials.credential_json`에 **평문 저장** | `credentials.encrypted_payload` + `encryption_nonce`. **AES-256-GCM 암호화**(`app/security/credential_crypto.py`) | 전제 오류(`change_iam.md` §1③과 동일 결론). 단 **우리가 복호화 가능한 장기 키를 보관**한다는 본질은 그대로라 전환 근거는 유효 |
| `aws_client.py` / `azure_client.py` / `gcp_client.py` | `app/providers/{aws,azure,gcp}.py` | 파일명만 다름 |
| `terraform_service._credential_env` 한 곳 | 러너가 **11개**로 늘었다 — AWS 4(ec2/rds/s3/cloudfront)·Azure 3(vm/storage/database)·GCP 4(compute/cloudsql/storage/cdn) | 잠재 수정 지점이 1곳이 아니라 11곳. 단 §2-1 방식이면 **AWS 4곳은 손대지 않아도 된다** |
| `Base.metadata.create_all`만 있어 기존 테이블에 컬럼 추가 불가 | **Alembic 있음**(리비전 다수, `tests/test_migration_backfill.py` 존재) | 제안 문서 2-4절의 최대 리스크는 **소멸** |
| Account/Credential 분리가 아직일 수 있음 | **이미 분리 완료**(`cloud_accounts` ↔ `credentials`) | Team은 2단계 작업이 아니라 바로 얹으면 됨 |
| `routers/dashboard.py`가 3사 비용을 actual/estimated로 통일해 계산 중 | **dashboard 라우터 없음.** 2026-09-14 PR #55로 **정가 기반 추정**(`app/pricing.py`)이 들어왔고, 프로비저닝 성공 시 `resources.estimated_monthly_cost`에 `cost_source="list_price_estimate"`로 기록된다. **실측(actual) 비용 API 연동은 0줄** | 절반만 맞다 → §3-1 |

## 2. Access Key 저장 → IAM 위임 기반 임시 자격증명

### 2-1. 핵심 설계: 복호화 지점 5곳이 단일 choke point다

`decrypt_credential_json()` 호출부는 정확히 5곳이고, 전부 `복호화 → secret_payload(dict) →
provider 어댑터/러너로 전달` 구조다.

```
app/routers/credentials.py:399   재검증
app/routers/provisioning.py:464  프로비저닝 실행
app/routers/resources.py:337     start/stop/delete
app/routers/sync_jobs.py:232     리소스 동기화
app/dev_destroy_job.py:41        CLI 정리 도구
```

여기에 **한 단계만 끼운다**:

```python
# app/providers/session.py (신규)
def resolve_secret_payload(provider: str, external_account_id: str, stored_payload: dict) -> dict:
    """저장된 payload가 '위임 참조'면 STS AssumeRole / SA impersonation으로 단기 자격증명을
    발급해, 기존 코드가 기대하는 것과 **같은 모양의 dict**로 돌려준다.
    장기 키(레거시)면 그대로 통과시킨다."""
```

`change_iam.md` §3이 "복호화 호출부 4곳을 임시 자격 획득으로 대체"라고 적은 것과 같은 지점이며,
이 문서는 거기에 **"하류 시그니처를 그대로 두는 어댑터 형태"**라는 제약을 더한다. 이 제약이
AWS에서 작업량을 0에 가깝게 만든다 — 코드가 이미 세션 토큰을 받을 준비가 돼 있기 때문이다:

- `app/providers/aws.py:_client()` → `aws_session_token=secret_payload.get("session_token")`
- AWS 러너 **4개 전부**의 `_credential_env()` → `session_token`이 있으면 `AWS_SESSION_TOKEN`
  주입 (`aws_provisioning`/`aws_rds_`/`aws_s3_`/`aws_cloudfront_`에서 확인)

즉 `resolve_secret_payload()`가 `{access_key_id, secret_access_key, session_token}`을 돌려주면
**검증·동기화·리소스 액션·Terraform 4종이 그대로 동작**한다.

### 2-2. 스키마 변경 0 — payload 안의 `auth_type` 판별자

`change_iam.md` §3은 "`models.py` 수정 + 신규 Alembic 마이그레이션(role_arn/external_id 컬럼
추가, 장기 비밀 필드 제거)"을 예상했지만, `encrypted_payload`가 **자유형 JSON**이라 컬럼을 늘리지
않고도 두 방식을 공존시킬 수 있다. 1단계에서는 이쪽을 택한다.

```jsonc
// 레거시 (그대로 동작해야 함)
{"access_key_id": "AKIA...", "secret_access_key": "..."}

// 신규
{"auth_type": "assume_role",
 "role_arn": "arn:aws:iam::123456789012:role/MultiCloudOpsAccess",
 "external_id": "8f3c...-uuid"}
```

- `app/providers/__init__.py`의 `REQUIRED_SECRET_FIELDS`를 `(provider, auth_type)` 기준으로
  분기. `auth_type`이 없으면 레거시(`access_key`)로 간주한다.
- **병존이 필수인 이유**: 현재 DB에는 팀이 실제로 등록해 둔 자격 증명이 살아 있고
  `seed_mock_data.py`의 목업도 레거시 형태다. 전환이 기존 credential을 깨면 데모가 통째로 죽는다.
  신규 등록만 위임 방식을 기본으로 하고, 레거시 행은 마이페이지에서 "레거시 키" 배지로 표시해
  **사용자가 스스로 교체**하게 한다(자동 마이그레이션은 불가능 — 장기 키로부터 role을 만들어
  줄 수는 없다).
- 조회/필터 요구가 생기면 그때 `credentials.auth_type` 컬럼을 별도 리비전으로 승격한다.

### 2-3. AWS — Cross-account IAM Role + `sts:AssumeRole`

**온보딩 흐름**

1. 마이페이지에서 "AWS 계정 연결(위임 방식)" 선택 → 서버가
   `{platform_account_id, external_id(신규 UUID), trust_policy_json, cloudformation_launch_url}`
   반환 (신규 엔드포인트 `GET /api/v1/credentials/aws/delegation-setup`).
2. 사용자가 자기 계정에 IAM Role 생성 — Trust Policy에 우리 고정 AWS 계정 ID + 발급된
   External ID. CloudFormation Launch URL을 제공하면 클릭 한 번으로 축약된다.
3. 사용자가 `role_arn`을 제출 → 서버가 저장 전 **AssumeRole을 실제로 시도**해 검증.

**검증 로직은 오히려 강해진다.** 지금은 `sts:GetCallerIdentity`만 보지만, 위임 방식에서는
(a) AssumeRole 성공 자체가 검증이고, (b) 반환된 ARN의 계정 ID가 `external_account_id`와
일치하는지까지 확인할 수 있다(confused deputy 방지). `permission_scope` 프로빙(AWS 4항목,
`iam:SimulatePrincipalPolicy` 포함)은 임시 자격증명으로 동일하게 동작하므로 **수정 불필요**.

**토큰 만료 vs Terraform**: `terraform_apply_timeout_seconds` 기본값이 **900초(15분)**이고
AssumeRole 기본 세션은 3600초라 안전 마진이 4배다. 제안 문서가 우려한 "RDS apply가 1시간 안에
끝나는가"는 **현재 설정에서는 문제되지 않는다**(타임아웃이 먼저 걸린다).

**개발·데모용 AWS 준비물(2026-09-15 확정)** — 플랫폼 측과 고객 측을 **같은 계정 하나**로 쓴다.
한 계정 안의 user → role AssumeRole은 정상 동작하며 코드 경로는 실제 타사 계정과 동일하다.
단 **신뢰 정책(역할 쪽)과 `sts:AssumeRole` 권한(user 쪽)이 둘 다** 있어야 한다 — 하나만 있으면
같은 계정이어도 `AccessDenied`다.

| | 리소스 | 내용 |
|---|---|---|
| ① 플랫폼(호출자) | IAM 사용자 `mcp-platform-caller` | 권한은 `sts:AssumeRole` 하나뿐, `Resource`를 `arn:aws:iam::*:role/MultiCloudOpsAccess`로 제한 → 키가 유출돼도 이 역할 외엔 아무것도 못 빌린다. 액세스 키는 `.env`로 |
| ② 고객(피호출) | IAM 역할 `MultiCloudOpsAccess` | 신뢰 정책 Principal = ①의 user ARN, `Condition`에 `sts:ExternalId`. 권한은 데모 범위로 `AmazonEC2FullAccess`/`AmazonRDSFullAccess`/`AmazonS3FullAccess`/`CloudFrontFullAccess` + 인라인 `ce:GetCostAndUsage`·`iam:SimulatePrincipalPolicy`. 최대 세션 1시간(기본값) |

역할 이름을 `MultiCloudOpsAccess`로 고정하는 것이 ①의 `Resource` 제한이 성립하는 전제다.
권한을 최소권한으로 조이는 것은 후속 과제 — 지금 조이면 Terraform이 VPC/보안그룹 생성 단계에서
막혀 디버깅에 시간을 쓰게 된다.

`.env` 신규 키: `PLATFORM_AWS_ACCOUNT_ID` / `PLATFORM_AWS_ACCESS_KEY_ID` /
`PLATFORM_AWS_SECRET_ACCESS_KEY`.

**ExternalId 발급 순서**: 원래 흐름은 "서버 발급 → 사용자가 신뢰 정책에 붙여넣기"지만, 역할을
먼저 만들어 두는 개발 초기를 위해 **등록 요청이 `external_id`를 받을 수 있게** 한다(없으면 서버가
발급). 이렇게 해야 나중에 CloudFormation 흐름을 그대로 얹을 수 있다.

### 2-4. GCP — Service Account Impersonation

고객 온보딩 난이도는 AWS와 비슷하다(자기 SA에 우리 플랫폼 아이덴티티로
`roles/iam.serviceAccountTokenCreator` 부여). 다만 **우리 구현량은 AWS의 3~4배**다:

- `app/providers/gcp.py` **3곳 전부** — `verify()`, `perform_resource_action()`,
  `discover_resources()`가 모두 `service_account.Credentials.from_service_account_info()`를
  직접 호출한다. `google.auth.impersonated_credentials`로 교체해야 한다.
- GCP 러너 **4개**(`gcp_provisioning`/`gcp_cloudsql_`/`gcp_storage_`/`gcp_cdn_`)가
  `run_apply(..., credentials_file=secret_payload)`로 **파일 경유**한다. 임시 토큰 방식에서는
  `GOOGLE_OAUTH_ACCESS_TOKEN` 환경변수 경로가 AWS와 대칭이라 더 낫다.
  `terraform_runner.py`의 `credentials_file` 경로는 레거시 GCP 키를 위해 남겨 둔다.
  (`change_iam.md` §2 GCP는 "파일 내용만 WIF 설정 파일로 교체"하는 선택지를 제시했다 — 그
  경로도 가능하지만, 토큰 주입이 AWS와 코드 모양을 맞출 수 있어 이 문서는 토큰 쪽을 권장한다.)

따라서 제안 문서의 "GCP는 AWS와 대등"은 **고객 온보딩 관점에서만 맞고, 우리 구현 관점에서는
아니다.**

### 2-5. Azure — 코드가 아니라 테스트 환경이 막는다

제안 문서는 Azure Lighthouse를 "우리 구현 난이도 높음"으로 평가했지만 검증 결과는 반대에 가깝다:
Lighthouse로 가면 `secret_payload`에서 `client_secret`이 사라지고 `ARM_*`가 플랫폼 설정값에서
오므로 **Azure 러너 3개의 코드는 오히려 단순해진다**(저장 값은 `subscription_id`뿐).

진짜 제약은 **고객 온보딩(ARM 템플릿 배포)과 테스트 환경**이다 — 위임을 검증하려면 테넌트/구독이
2개(제공자 측 + 고객 측) 필요하다. 팀에 두 번째 테넌트가 없으면 구현해도 데모가 불가능하므로,
**이번 범위에서는 설계만 남기고 코드는 현행 Service Principal 유지**를 권장한다.

제안 문서의 절충안("client secret 대신 인증서 기반 SP")은 **비권장**이다: 공수는 드는데
"우리가 장기 크리덴셜을 보관한다"는 본질이 그대로라 보안 개선 폭이 작고, 발표에서 설명하기도
애매하다. 차라리 현행 유지 + 로드맵 명시가 낫다.

### 2-6. 남는 한계 — `change_iam.md` §4①과 같은 결론

위임으로 바꿔도 **우리 플랫폼 자신의 장기 자격증명 1개는 남는다.** 로컬/단일 컨테이너로 도는 한
`.env`에 우리 AWS 키가 있어야 STS AssumeRole을 호출할 수 있다. `change_iam.md`가 "백엔드가
프로덕션에서 어디서 어떤 신원으로 도는가가 저장소에 정의돼 있지 않다"고 지적한 바로 그 지점이며,
2026-09-15 기준으로도 **여전히 미정**이다(`app/config.py`에 플랫폼 아이덴티티 설정이 하나도 없다).

그러므로 이 전환의 정직한 성과는 "비밀키 0개"가 아니라:

> **고객 장기 키 N개 → 플랫폼 장기 키 1개**로 blast radius 축소.
> 그 1개마저 EC2 인스턴스 역할 / ECS Task Role / Workload Identity로 옮기면 0이 된다(배포 단계 과제).

이 프레이밍을 발표에서 먼저 말하는 편이, "IAM으로 바꿨습니다"라고 한 뒤 "그럼 그 역할은 누가
빌리나요?"를 되묻는 것보다 낫다.

부수 작업으로 `app/config.py`에 `PLATFORM_AWS_ACCOUNT_ID`, `PLATFORM_AWS_*`(또는 인스턴스 역할
사용 플래그), `PLATFORM_GCP_SERVICE_ACCOUNT` 등을 신설해야 한다.

### 2-7. 영향 범위 (AWS 1단계 기준)

| 파일 | 변경 |
|---|---|
| `app/providers/session.py` | **신규** — `resolve_secret_payload()` |
| `app/providers/__init__.py` | `REQUIRED_SECRET_FIELDS`를 `auth_type`별로 분기, `validate_secret_payload()` 수정 |
| `app/providers/aws.py` | `verify()`를 AssumeRole 기반으로 확장(계정 ID 일치 확인 추가) |
| `app/routers/credentials.py` | 복호화 직후 `resolve_*` 호출 + 신규 `delegation-setup` 엔드포인트 |
| `app/routers/{provisioning,resources,sync_jobs}.py`, `app/dev_destroy_job.py` | 복호화 직후 `resolve_*` 호출 **1줄씩** |
| `app/config.py` | 플랫폼 아이덴티티 설정 추가 |
| `frontend/mypage.html`, `frontend/assets/js/mypage.js` | AWS 입력 폼을 Role ARN + External ID + 안내로 교체, 레거시 배지 |
| **AWS 러너 4개 / `terraform_runner.py`** | **변경 없음** (`AWS_SESSION_TOKEN` 이미 지원) |
| Alembic | **마이그레이션 없음**(§2-2) |
| 테스트 | `tests/test_credentials_api.py`, `tests/test_gcp_credential_payload.py`가 `REQUIRED_SECRET_FIELDS` 형태를 고정하고 있어 수정 필요 |

## 3. 팀 단위 계정 관리 + 팀별 예산 — **이번 범위 밖(타 담당)**

> **담당 분리(2026-09-15)**: 조은솔의 이번 작업 범위는 **§2 인증 방식 전환뿐**이다.
> 비용 수집·팀 예산 API는 다른 담당자의 업무이므로, 이 절은 **착수 전 조사 결과와
> 설계 제안**으로만 남긴다. 담당자가 정해지면 §3-1의 현황 표부터 다시 확인할 것
> (그 사이에 비용 관련 PR이 더 들어왔을 수 있다).

> 제품 기능으로서의 정의(2026-09-15 확인): **우리 서비스 사용자가 자기 클라우드 계정들을 팀
> 단위로 묶어 비용·리소스를 조회하고 팀별 예산 한도를 관리**하는 기능. 고객의 AWS
> Organization/Control Tower/SCP를 우리가 배포해주는 것이 아니다(사진 속 랜딩존은 개념적 레퍼런스).

### 3-1. 비용 데이터의 현재 상태 — 절반만 있다

| 구분 | 상태 |
|---|---|
| **정가 기반 추정**(`cost_kind=list_price_estimate`) | **있음**(PR #55, `app/pricing.py`). 단 **프로비저닝으로 우리가 만든 리소스에만** 붙는다 — `app/routers/provisioning.py:_create_resource_from_job`에서 기록 |
| 동기화로 발견한 기존 리소스의 추정 비용 | **없음**(`app/resource_sync.py`/`routers/sync_jobs.py`는 `pricing`을 호출하지 않는다) |
| Storage/CDN 류 사용량 기반 서비스 | **추정 안 함**(`estimate_monthly_cost_usd()`가 `None` 반환 — 사용량을 모르는 채로 숫자를 지어내지 않겠다는 의도적 결정) |
| **실측 비용**(`cost_kind=actual`, CSP 비용 API) | **0줄**. 3사 모두 미연동 |

따라서 팀 예산 기능의 선행 과제는 둘로 쪼개진다:

- **(A) 예산 뼈대 + 정가 추정 기반 집계** — 지금 당장 가능. 3사 대칭으로 화면이 채워진다.
- **(B) 실측 비용 연동** — AWS Cost Explorer부터. 데모 리스크가 있어 (A) 위에 얹는다.

(A)를 먼저 세우면 (B)가 늦어져도 화면이 비지 않는다. 다만 **동기화로 발견된 리소스에는 추정치가
없어 팀 합계가 과소 집계된다** — (A) 단계에서 `resource_sync` 쪽에도 `pricing` 호출을 붙이는
작업(적은 공수)을 함께 넣는 것을 권장한다.

### 3-2. 데이터 모델

- 신규 `teams`(id, user_id, name, monthly_budget_limit, currency, created_at, updated_at)
- `cloud_accounts.team_id` **nullable FK** 추가 — Alembic 리비전 1개. 기존 unique 제약
  (`uq_cloud_accounts_user_provider_external`)과 충돌 없음.
- `cloud_accounts.purpose`(production/development 등 optional 라벨)도 같은 리비전에서 추가 가능 —
  사진의 Prod/Dev 분리를 라벨로 흉내 낸다.
- **1 Account : 1 Team**으로 단순하게 시작(N:N은 필요해지면 확장).

### 3-3. 실측 비용은 계정 단위로 수집한다

팀 예산은 "계정들의 묶음"에 대한 합계라 **리소스 단위 granularity가 필요 없다.** 이 단순화가
(B)의 작업량을 크게 줄인다.

- AWS Cost Explorer는 리소스 단위 granularity를 켜면 비싸고 기간 제한이 있지만 **계정/서비스
  단위 월별 조회는 싸고 간단**하다. `permission_scope.cost_read`를 AWS는 이미 Cost Explorer로
  프로빙하고 있어 권한 판단 로직이 존재한다(`app/providers/aws.py`).
- **문제**: `cloud_resource_costs.resource_id`가 NOT NULL FK라 계정 단위 비용을 넣을 자리가
  없다 → 신규 테이블 `cloud_account_costs`(cloud_account_id, cost_kind, amount, currency,
  period_start, period_end, as_of, source, source_record_key) 추가. 기존 테이블은 건드리지 않는다.
- **provider 순서**: AWS → Azure(Cost Management API) → GCP. GCP는 Billing Export→BigQuery가
  표준이라 고객 설정이 무겁고, `01_API_Specification_v1.1.md` §19에도 미확정으로 남아 있다.
- **데모 리스크**: 신규 AWS 계정은 Cost Explorer 활성화 후 24시간이 지나야 데이터가 나오고
  과금 이력 자체가 거의 없어 0원으로만 보일 수 있다. (A)를 먼저 깔아야 하는 이유다.

### 3-4. 예산 초과 정책 — 서버 차단 채택

제안 문서 2-2절의 (a)/(b) 중 **(b) 서버 차단**을 권장한다. 삽입 지점이 깔끔하다 —
`app/routers/provisioning.py`의 `create_provisioning_job`이 이미 credential → cloud_account를
조회하므로 거기서 팀 예산을 재검증하고 `409 BUDGET_EXCEEDED`로 거부한다. "프론트 차단만으로는
우회 가능"이라는 제안 문서의 지적이 맞다.

80% 임계치 경고는 **기존 `notifications` 테이블**을 그대로 쓴다(`type=budget_threshold`,
`message_key=notif.budget.threshold`). 새 알림 인프라는 필요 없다.

### 3-5. 인가 모델 제약 — 팀은 "한 사용자 안의 그룹"

현재 모든 소유권 검사가 `cloud_accounts.user_id` 한 줄로 이루어진다(`credentials.py`,
`resources.py`, `sync_jobs.py`, `provisioning.py` 전부). 팀원 초대·공유 개념이 들어오면 **인가
모델 전체를 다시 설계해야 한다**(역할, 초대, 권한 매트릭스). 이번 범위는 **"운영팀/개발팀
라벨링 + 예산"까지**로 못 박는다.

## 4. 실행 순서

| 순서 | 작업 | 브랜치(안) | 공수 | 위험 |
|---|---|---|---|---|
| 1 | AWS AssumeRole 위임 + `session.py` choke point + 온보딩 UI | `solcho/be-assume-role` | 1일 | **조은솔** |
| 2 | `teams` + `cloud_accounts.team_id` + 팀 CRUD/배정 API + 마이페이지 팀 UI | `*/be-teams` | 낮음 | 타 담당 |
| 3 | 예산 (A): 정가 추정 기반 팀 집계·예산 카드·초과 차단 + 동기화 리소스에도 추정치 부여 | `*/be-team-budget` | 낮음~중간 | 타 담당 |
| 4 | 예산 (B): AWS 실측 비용 수집(`cloud_account_costs`) | `*/be-cost-actual` | **중간** | 타 담당 |
| 5 | GCP impersonation 대칭 전환 | `solcho/be-gcp-impersonation` | 1일 | **조은솔**(A 완료 후 판단) |
| 6 | Azure Lighthouse — **설계 문서만**, 코드는 SP 유지 | — | — | **조은솔**(문서만) |

1·5·6이 이 문서 담당자의 작업이고, 2~4는 담당이 분리됐다(§3 머리말). 1을 먼저 두는 이유는
**보안 이슈가 더 무겁고 구현이 더 싸기** 때문이다.

## 5. 확인이 필요한 항목

- **강사 피드백 1번의 의도**: "IAM 기반으로 로그인해서 권한을 위임받는 방식"이
  (a) CSP 접근을 AssumeRole 위임으로 바꾸라는 뜻인지, (b) 우리 서비스의 사용자 로그인을
  IdP(IAM Identity Center/SAML·OIDC) 연동으로 바꾸라는 뜻인지. 첨부 이미지에 IAM Identity
  Center가 있어 (b) 가능성도 배제할 수 없고, (b)라면 작업 성격이 완전히 다르다(현재 우리는
  자체 JWT + refresh token). 이 문서는 (a)를 전제로 쓰였다.
- **백엔드 프로덕션 호스팅/신원** — `change_iam.md` §4①에서 이미 제기됐고 아직 미결. §2-6의
  "장기 키 1개"가 0이 되는지 여부가 여기에 달렸다.
- **예산 통화 단위**: USD 고정인지, 표시만 KRW 환산인지. `pricing.py`는 USD 기준이고 3사 비용
  API의 통화는 서로 다르다.
- **팀 삭제 시 소속 계정 처리**: `team_id`를 NULL로 되돌리는지, 삭제를 막는지.
- **Azure 두 번째 테넌트 확보 가능 여부** — 가능하면 §4의 6번이 선택지가 된다.

## 6. 참고

- 선행 검증: `docs/change_iam.md`
- 현재 스키마: `docs/DB_ERD_v1.1.md`
- API 계약: `docs/01_API_Specification_v1.1.md` (§6 자격 증명, §10 프로비저닝, §19 미확정 항목)
- 프로비저닝 설정값 범위: `docs/Multicloud_Provider_Feature_Mapping_2026-09-10.md`
- 결정 이력: 루트 `CLAUDE.md`의 "Key architectural decisions"
