# CLAUDE.md

Project context for AI (Claude Code) sessions working on this repository.
Human contributors: see `CONTRIBUTING.md` (same rules, human-readable form).

## Project overview

**Multi-Cloud Platform** — a unified operations dashboard that lets a team
provision, inventory, and monitor resources across multiple cloud providers
from a single UI.

## Tech stack

| Layer | Stack |
|---|---|
| Frontend | Build-less HTML/CSS (**Tailwind CSS**) + **Vanilla JS** |
| Backend | **FastAPI** (Python) |
| Database | **PostgreSQL** |
| Infra / IaC | **Terraform** |

## Repository layout

```
frontend/   # HTML/CSS(Tailwind)+Vanilla JS; screens added on feature/fe-* branches
backend/    # FastAPI + PostgreSQL + Terraform; filled on feature/be-* branches
docs/       # confirmed planning documents (spec, screen design, WBS)
.github/    # PR / issue templates
```

Sub-folders inside `frontend/` and `backend/` (e.g. `assets/js`, `app/routers`)
are **not** pre-created — add them on the branch that actually writes the code,
so skeleton commits don't clutter branch diffs.

## Team workflow

- **Branch naming**: `<name>/fe-<feature>` (frontend), `<name>/be-<feature>`
  (backend). The owner's romanized name is a namespace prefix; the `fe-`/`be-`
  prefix marks frontend/backend. One branch per WBS work item; branch off the
  latest `main`. Confirmed romanizations: 조은솔 → `solcho`, 김종국 → `jongkook`,
  이승현 → `seunghyun`, 안권형 → `kwonhyeong` (confirm spellings with each member).
  The name is just a "whose is this" label — merge/review unit is always the
  feature (e.g. `solcho/fe-pages`, `kwonhyeong/be-env-setup`).
- **Commit convention**: Conventional Commits with Korean descriptions —
  `<type>: <요약>`, where `type` ∈ `feat | fix | chore | docs | refactor | test`.
  Keep each commit small and reviewable.
- **PR / merge rule**: every change reaches `main` through a PR. **Merge owner:
  조은솔 — only 조은솔 merges into `main`.** Others open PRs and request review
  but do not press merge.
- **Merge method**: **Squash and merge** (uniform, keeps `main` history clean).
- Delete a branch (remote + local) once its PR is merged.

## Current phase & progress tracker

Phase 0 (repo skeleton + collaboration rules) complete. Feature work in progress.

| WBS 항목 | 브랜치 | 담당 | 상태 |
|---|---|---|---|
| 페이지 UI 구현 — 확정 화면 12개 정적 UI | `solcho/fe-pages` | 조은솔 | in progress |
| 개발 환경 구축 — DB 구축 | `kwonhyeong/be-env-setup` | 안권형/김종국/이승현 | not started |
| 프로비저닝 — Azure VM 생성 (`POST /provisioning/azure/vm`) | `seunghyunlee/azure` | 이승현 | in progress (PR 대기) |
| 목업 데이터 시딩 — 13테이블 최신 스키마 + 화면 예시 데이터 | `solcho/be-mock-data` | 조은솔 | in progress |
| 키 관리(마이페이지) API — cloud-accounts/credentials 10개 엔드포인트 + 최소 로그인(JWT)·회원가입 | `solcho/be-credentials-api` 외 | 조은솔 | merged |
| 리소스 조회 API — INV-01 인벤토리 4개 엔드포인트(조회·요약·상세·시작/중지/삭제) | `solcho/be-resources-api` | 조은솔 | merged |
| 리소스 동기화 API — 실제 CSP 리소스 탐색 + `/sync-jobs` 4개 엔드포인트 | `solcho/be-sync-api` | 조은솔 | merged |
| 인벤토리(INV-01/INV-02) 실API 연동 — 조회·동기화·시작/중지/삭제 | `solcho/fe-inventory-integration` | 조은솔 | in progress |

> Keep this table updated as branches open, progress, and merge.

## Key architectural decisions

- **API 형태(2026-09-10)**: 신규 통합 API 명세(`docs/01_API_명세서_v1.1.md`)가 아니라
  기존에 구현돼 있던 **provider별 개별 엔드포인트**(`/credentials/{provider}`,
  `/provisioning/{provider}/{service}`, `/resources/action` 형태)를 유지하기로 결정.
  이에 따라 그 통합 API 전용으로만 추가됐던 `provisioning_requests`·`resource_types`
  테이블과 참조 컬럼(`provisioning_jobs.provisioning_request_id`,
  `resources.resource_type_id`)을 제거(Alembic `0caab346f140`, 테이블 15→13).
  credentials 암호화·Account/Credential 분리·인벤토리 캐시·비용/감사 구조 등 나머지
  개선은 유지. 현재 스키마는 `docs/DB_ERD_v1.1.md` 참고.
- **목업/데모 데이터(2026-09-10)**: 화면 하드코딩 예시값(MY-01/INV-01/DASH-01)을
  `backend/app/seed_mock_data.py`로 시딩 — 데모 유저 1, 클라우드 계정 3, 자격증명 3
  (dev-gcp만 `verified=false` 검증실패 예시), 리소스 5, 프로비저닝 잡 2(성공/실패),
  동기화 상태 AWS·Azure·GCP 각각 상이(성공/실패/취소 — **항상 종결 상태로 시딩한다**: "running"으로
  두면 실제 `/sync-jobs` API의 "이미 진행 중인 job 있으면 거부" 로직과 충돌해 데모 계정에서
  새로고침이 영원히 막힌다, 2026-09-10 `solcho/fe-inventory-integration`에서 발견해 수정),
  비용 카드용 최소 행. **재실행 idempotent**.
  credentials는 `app/security/credential_crypto.py`(AES-256-GCM)로 실제 암호화 저장하며,
  이후 BE API(키 관리/리소스 조회/대시보드)는 이 데이터 위에서 개발·테스트한다.
- **Azure VM `admin_password` 정책(2026-09-10)**: `frontend/assets/js/provisioning.js`가
  실제로 수집하는 Azure Compute 인증 방식은 SSH 키가 아니라 사용자명/비밀번호다
  (`adminUsername`/`adminPassword`). `admin_password`는 `01_API_명세서_v1.1.md` 10.3절이
  금지하는 "secret"(CSP 계정 자격증명)과는 다른 값(생성될 리소스 자체의 OS 접속 정보)이라고
  판단해 **secret-필드 금지 검사에서 예외로 허용**하기로 결정. 대신 `provisioning_jobs.spec_json`
  (DB 저장, `GET /provisioning/jobs/{id}` 응답)에는 절대 남기지 않고, Terraform에는
  `TF_VAR_admin_password` 환경변수로만 전달한다(`app/services/provisioning/azure_vm.py`
  `SENSITIVE_PROVIDER_SPEC_FIELDS`). 같은 패턴으로 AWS/GCP compute나 DB 서비스의 master
  password도 처리할 수 있도록 라우터(`app/routers/provisioning.py`)는 실행기가 선언한
  `SENSITIVE_PROVIDER_SPEC_FIELDS`를 범용으로 참조한다.
- **Compute 공통 설정 필드 확정(2026-09-10)**: `docs/멀티클라우드 3사 기능 맵핑 — 설정값
  입력 범위 (2026-09-10).md` 1절 + `provisioning.js` 실제 구현 기준으로 azure/vm의
  `provider_spec`을 `region`/`instance_type`/`admin_username`/`admin_password`/`image`
  (curated label, publisher/offer/sku/version은 서버가 내부 매핑)로, `common_spec`을
  `name`/`tags`/`inbound_rules`(포트·CIDR 목록)로 확정. `instance_type`은 프론트가
  `Standard_` 접두사 없이 보내므로(`B1s` 등) 서버가 자동 보정한다.
- **최소 인증(JWT) 구현(2026-09-10, `solcho/be-credentials-api`)**: `01_API_명세서_v1.1.md`
  §5는 회원가입·로그인·비밀번호 재설정·`/me`까지 전체 인증 스펙을 정의하지만, 백엔드에는
  인증이 전혀 구현돼 있지 않았다(프론트 `auth-guard.js`는 `localStorage` 데모 세션일 뿐 실제
  토큰이 아님). credentials API 전체가 소유권 검사(`current_user`)를 전제로 하므로, 이 세션에서
  범위를 넓혀 `POST /api/v1/auth/login` + `Authorization: Bearer <JWT>` 검증만 최소 구현했다
  (`app/security/jwt_tokens.py`, `app/deps.py`, `app/routers/auth.py`). 회원가입·비밀번호
  재설정·`/me`·refresh token은 여전히 범위 밖 — 별도 인증 세션에서 이어서 구현한다. 목업
  데모 계정(`demo@multicloud.example` / `demo-pass-1234`, `seed_mock_data.py`)으로 로그인 가능.
- **회원가입(`POST /auth/sign-up`) 추가(2026-09-10, `solcho/be-credentials-api`)**: 로그인만
  있으면 목업 데모 계정 외에는 아무도 로그인할 수 없어(가입 경로 없음) 범위를 넓혀 회원가입만
  추가했다. 비밀번호 재설정·`/me`는 여전히 범위 밖(실제 메일 발송 없는 데모형 토큰 발급이
  필요해 별도 세션으로 미룸). 이때 §19 미확정 항목 두 개를 확정:
  - **비밀번호 정책**: `frontend/assets/js/validate.js`의 `MCVAL.isStrongPassword`와 동일하게
    "8자 이상 + 영문/숫자/기호 중 2종 이상"으로 통일(`app/security/passwords.py`의
    `is_strong_password`). 프론트가 이미 이 규칙으로 UI를 만들어 둬서 그대로 재사용했다.
  - **`affiliation_type=company`일 때 단체명 필수 여부**: 필수로 확정. `affiliation_name`이
    비어 있으면 `422 VALIDATION_ERROR`.
- **credential 검증 실패 시 저장 정책(2026-09-10, `solcho/be-credentials-api`)**: §19에서
  미확정으로 남아 있던 두 옵션 중 **"검증 실패해도 암호화 저장하고 `verified=false`로 반환"**
  (§6.2 옵션 1)을 채택했다. 근거: 사용자가 실패 원인을 보고 재시도/수정할 수 있어야 하고,
  rollback하면 마이페이지 "검증 실패" 행 UI(정적 화면에 이미 존재)가 표시할 대상 자체가
  사라진다. `POST /credentials/{provider}`·시크릿 교체를 포함한 `PATCH /credentials/{id}`
  모두 이 정책을 따른다.
- **cloud account 삭제 API 보류(2026-09-10, `solcho/be-credentials-api`)**: §6.5·§19에 명시된
  대로 snapshot 보존 정책이 확정되지 않아 `DELETE /cloud-accounts/{id}`는 실제로 삭제하지
  않는다. 라우트는 만들어 두되(소유권 검사까지는 정상 수행) 항상 `501
  CLOUD_ACCOUNT_DELETE_NOT_IMPLEMENTED`를 반환한다.
- **credential 실검증 permission_scope 프로빙 범위 축소(2026-09-10,
  `solcho/be-credentials-api`)**: `app/providers/{aws,azure,gcp}.py`는 신원 확인(AWS
  `sts:GetCallerIdentity`, Azure `SubscriptionClient.subscriptions.get`, GCP
  `projects.get`)은 3사 모두 실제로 호출한다. 그 위의 `permission_scope` 자동 판별은
  **AWS만 4개 항목 모두**(`inventory_read`=`ec2:DescribeInstances`, `cost_read`=Cost
  Explorer, `resource_control`/`provision`=`iam:SimulatePrincipalPolicy`) 구현했고,
  **Azure/GCP는 `inventory_read`만** 가벼운 목록 조회로 채우고 나머지는 `false` 고정이다.
  근거: 안전한 읽기 전용 프로빙 API가 provider/권한마다 표준화돼 있지 않고, GCP 비용 수집
  방식·권한은 이미 §19에 미확정 항목으로 남아 있어 이 세션에서 새로 정하지 않았다. 화면의
  자기신고 체크박스(정적 UI, 이번 세션에서 변경 없음)로 나머지를 보완하는 것을 전제로 한다.
- **GCP secret_payload는 서비스 계정 키 JSON을 통째로 저장한다(2026-09-11,
  `solcho/fix-gcp-credential-payload`)**: 처음엔 프론트가 붙여넣은 JSON에서 `type`/
  `client_email`/`private_key_id`/`private_key` 4개만 골라 보냈는데, google-auth의
  `service_account.Credentials.from_service_account_info()`가 **`token_uri`를 필수로**
  요구해서(`MalformedError: missing fields token_uri`) 멀쩡한 키인데도 검증·동기화·리소스
  액션이 전부 실패했다. **필드를 골라 담지 말고 키 파일 전체를 그대로 저장한다**(어차피
  AES-256-GCM으로 암호화 저장된다). `REQUIRED_SECRET_FIELDS["gcp"]`에도 `token_uri`를 넣어
  잘린 payload는 검증 전에 422로 걸러낸다. `tests/test_gcp_credential_payload.py`가
  "우리가 필수로 받는 필드만으로 google-auth가 credential을 만들 수 있는가"를 고정한다.
  ⚠️ 이 수정 이전에 등록된 GCP credential은 payload가 잘린 상태로 저장돼 있어 **다시 등록하거나
  마이페이지에서 "수정"으로 키를 교체해야** 한다(원본 키를 서버가 따로 보관하지 않으므로
  마이그레이션 불가).
- **마이페이지 자격 증명 수정/삭제 UI(2026-09-11, `solcho/fix-gcp-credential-payload`)**:
  검증에 실패할 때마다 새로 등록하느라 키를 반복해서 붙여넣어야 하는 불편이 있어, "연결된
  클라우드 계정" 표에 **수정 / 재검증 / 삭제** 버튼을 붙였다(백엔드 §6.7~6.9는 이미 있었고
  화면에서만 안 쓰고 있었다).
  - **수정 모드에서 바꿀 수 있는 것은 이름과 키 값뿐**이다. `external_account_id`(계정 식별자)와
    provider는 클라우드 계정에 속한 값이라 잠근다 — 다른 계정/프로젝트의 키라면 새로 등록해야
    한다. GCP도 마찬가지라 붙여넣은 JSON의 `project_id`가 달라도 계정은 바뀌지 않는다.
  - **키 값을 비워두고 저장하면 이름만 수정**된다(메타데이터 전용 PATCH — 서버가 확인 헤더를
    요구하지 않는 경로). 키를 채우면 교체 + 즉시 재검증이며 `X-Action-Confirmed: true`를 보낸다.
  - 표 행 드래그(순서 변경) 핸들러가 `pointerdown`에서 `preventDefault()`를 부르기 때문에,
    관리 버튼에서 시작한 pointerdown은 드래그 대상에서 제외해야 클릭이 먹지 않는다.
- **resources/action 일괄 요청 원자성(2026-09-10, `solcho/be-resources-api`)**: §19 미확정
  항목 중 "전체 실패 또는 항목별 부분 성공"을 **항목별 부분 성공**으로 확정(§8.5 예시 응답과
  같은 방향). 리소스 하나가 실패해도 나머지 리소스는 계속 처리하고, 각 항목은
  `success | rejected | failed` 중 하나로 결과에 남는다(`rejected`=사전 검사 단계에서 걸러짐,
  `failed`=CSP 호출까지 갔다가 실패). `app/routers/resources.py`의 `_process_action_item`.
- **resources/action SDK 어댑터 구현 범위(2026-09-10, `solcho/be-resources-api`)**:
  `aws_client.py`/`azure_client.py`/`gcp_client.py` 어댑터가 이 저장소에 전혀 없어서(구버전
  프로토타입 세션이 실행된 적 없음) `app/providers/{aws,azure,gcp}.py`의 `perform_resource_action`
  으로 새로 구현했다. §0 지시대로 "최소 EC2/RDS/S3"를 채우고, 목업 리소스(mcp-c3d4-vm)가
  Azure VM이라 Azure VM도, 같은 이유로 GCP Compute Engine도 추가했다.
  - **지원**: AWS EC2 인스턴스(start/stop/delete) · EBS Volume(delete만) · RDS 인스턴스
    (start/stop/delete) · S3 버킷(delete만, 비어있지 않으면 `BucketNotEmpty` →
    `force_empty:true` 재요청 지원) · Azure Virtual Machine(start/stop/delete) · GCP
    Compute Engine 인스턴스(start/stop/delete).
  - **미지원(`UNSUPPORTED_OPERATION` 고정)**: Azure SQL Database/Storage Account/CDN, GCP
    Cloud SQL/Cloud Storage/Cloud CDN, AWS CloudFront — 어댑터가 없어서가 아니라 이번 세션
    범위 밖으로 의도적으로 뺐다. `app/resource_actions.py`의 `supported_actions()` 참고.
  - **Azure 리소스 식별 규칑**: `resources.external_resource_id`는 Azure의 경우 ARM 리소스 ID
    전체(`/subscriptions/.../resourceGroups/.../providers/.../virtualMachines/...`)라고
    가정한다 — VM 이름만으로는 조작에 필요한 resource group을 알 수 없기 때문. 동기화(9장)
    구현 시 이 형식으로 저장해야 한다.
  - **GCP zone 단순화**: GCP Compute 인스턴스는 zone 단위로 존재하는데 스키마에 별도 zone
    컬럼이 없어 `resources.region` 값을 zone으로 그대로 사용한다(예: `asia-northeast3-a`).
  - **CSP 호출은 완료를 기다리지 않을 수 있다**: AWS(`start_instances`/`stop_instances`/
    `start_db_instance`/`stop_db_instance`)는 호출이 accept되면 성공으로 본다(실제 상태 전이
    완료까지 폴링하지 않음). Azure(`begin_*().result()`)와 GCP(`operation.result()`)는 SDK
    관용구상 완료까지 기다린다 — provider별 비대칭이 의도적이다.
  - **credential 권한 검사와 목업 데이터 호환**: 실행 전 `credentials.permission_scope
    .resource_control`이 있으면 확인하되, **빈 딕셔너리(`{}`)면 검사를 건너뛴다** — 실제
    `verify_credential()`을 거친 credential은 항상 4개 키를 다 채우므로, 빈 값은 "아직
    한 번도 검증된 적 없는 값"(예: `seed_mock_data.py`처럼 손으로 넣은 데이터)이라는 뜻이다.
    이렇게 해야 목업 credential(permission_scope 없음)로도 액션 파이프라인을 테스트할 수
    있다.
- **리소스 동기화 아키텍처(2026-09-10, `solcho/be-sync-api`)**: 이 서버엔 별도 워커/큐
  프로세스가 없어 `POST /sync-jobs`는 FastAPI `BackgroundTasks`로 응답을 먼저 돌려주고 같은
  프로세스 안에서 백그라운드로 실행한다(§9). **단일 프로세스 전제** — `docker-compose.yml`이
  이미 alembic 마이그레이션을 위해 `api` replica 1개를 전제하고 있어 이 제약과 같은 종류다.
  replica를 늘리면 별도 워커로 분리해야 한다.
  - **취소는 best-effort, 비영속**: `POST /sync-jobs/{id}/cancel`은 프로세스 메모리의 집합
    (`app/routers/sync_jobs.py`의 `_CANCEL_REQUESTED`)에 표시만 하고, 다음 계정 항목으로
    넘어가기 전에 확인해 멈춘다. 이미 시작된 항목 하나는 끝까지 진행된다. 이 집합은 DB
    컬럼이 아니라서 프로세스 재시작 시 사라진다 — 영속 취소 플래그는 이번 세션 범위 밖.
  - **동시 실행 정책**: 같은 사용자에게 `pending|running` job이 이미 있으면 `409
    JOB_ALREADY_RUNNING`으로 거부한다(§19 "구현 전 확정 필요" 중 기본 안전 동작 그대로 채택).
  - **동기화는 자동 삭제를 하지 않는다**: 이번 실행에서 안 보인 리소스는 `is_stale=true`로만
    표시하고 `deleted_at`은 건드리지 않는다(§9.3 "필요 시 deleted_at" 정책은 미확정으로 남겨
    둠) — 실제 삭제는 여전히 `POST /resources/action`(delete)을 통해서만 일어난다.
  - **CSP 호출 실패는 대부분 "0건 발견"으로 보인다, 실패가 아니라**: 리전/서비스별 호출은
    각각 개별 `try/except`로 감싸 하나가 막혀도(예: opt-in 리전 미활성화) 나머지는 계속
    수집한다. 그 결과 credential 자체가 완전히 잘못된 경우에도 item은 보통 `PROVIDER_API_ERROR`
    로 실패하지 않고 `resources_discovered=0`인 `success`로 끝난다(직접 확인함 — 목업 계정으로
    동기화하면 대상 리소스가 전부 `is_stale=true`가 된다). 사용자에게 "왜 0건이지?"를 구분해
    보여주려면 추후 세션에서 신원 확인(§6.2와 같은 `sts:GetCallerIdentity` 등)을 동기화 시작
    전 사전 게이트로 추가하는 것을 고려한다.
  - **탐색 범위는 `resource_actions.py`와 동일**: AWS EC2(리전 `ap-northeast-2`/`us-east-1`만,
    프로비저닝 폼 허용 리전과 통일)·EBS Volume·RDS 인스턴스·S3 버킷(리전은 조회 비용 때문에
    `None` 고정), Azure Virtual Machine(`list_all()`의 `provisioning_state`를 상태로 씀 —
    실제 전원 상태 아님, VM별 instance view 호출은 비용 문제로 생략), GCP Compute Engine
    인스턴스만 실제로 수집한다. 나머지 서비스는 `discover_resources()`가 빈 목록을 반환한다
    (에러 아님 — 단순히 아직 미지원).
  - **audit_events에 동기화를 기록하지 않는다**: §14 기본 기록 대상 표에 `resource.sync`류
    action이 없어 이번 세션에서 새로 만들지 않았다(표에 없는 action을 임의로 추가하지 않음).

## Assumptions — frontend static UI (`solcho/fe-pages`, 화면설계서 V1.1)

이번 정적 UI 작업의 확정/가정 항목. **화면설계서에 "[확인 필요]"로 남았지만 프로토타입 개발 프롬프트에서 이미 확정된 것**이라 다시 묻지 않음:
- **소셜 로그인(Google)**: 버튼만 배치, 클릭 시 "준비 중" 안내만(실제 OAuth 없음).
- **PROV-01 계정 선택**: 다중 선택 허용 — 같은 설정으로 계정 수만큼 생성된다는 안내 문구 표시.
- **마이페이지 "보고서 작성" 섹션**: 최신 화면설계서 기준으로 **넣지 않음**(옛 기능명세서엔 있었음).
- **인벤토리 "서비스 종류" 열**: 삭제하고 **"CSP 원본 리소스 유형"** 열로 대체(09/06 확정).

이 문서(프론트 프롬프트)에서 "가정"으로 처리한 것:
- **범위**: 화면 12개의 마크업+스타일만. 폼 검증/API/세션/데이터 저장 로직 없음. 순수 UI 상태 전환(사이드바 active, 테마 토글, 모달/드롭다운 open·close, 인트로 탭)만 포함.
- **다중 상태 화면**(로그인 실패 배너, PROV-02 성공/진행중/실패, 마이페이지 검증 실패 행 등): 조건 분기 없이 화면설계서대로 **정적 예시**로 동시 노출.
- **브랜드명 미확정**: `"MultiCloud Ops"` 자리표시자 — `frontend/assets/js/ui.js`의 `SERVICE_NAME` 한 곳에서 `data-service-name`로 주입.
- **인트로(INTRO-01~05) 소개 문구·이미지, 푸터 정책 문서 미확정** → 자리표시자 텍스트/링크.
- **디자인 스크린샷**(`docs/design/*.png`)은 색·타이포·컴포넌트 룩만 참고 — 화면 구성은 화면설계서 V1.1을 따름.
- **MAIN-01 헤더 테마 토글**: 화면설계서엔 명시 없으나 라이트/다크 검토가 가능하도록 헤더에 추가.

### Assumptions — 기본 기능 JS (`solcho/fe-basic-js`)

순수 클라이언트 검증/필터만 구현(서버 API 호출 없음 — Network 탭에 요청 0건). 데모성 동작:
- **로그인**: 형식 유효 시 실제 인증 없이 `dashboard.html`로 데모 리다이렉트. 실제 인증은 API 연동 단계에서 붙음.
- **회원가입 중복 확인**: 서버가 없어 이메일 형식이 유효하면 항상 "사용 가능한 이메일입니다"(데모). 이메일 변경 시 중복 확인 재요구.
- **비밀번호 찾기**: STEP1 발송 후 안내 노출·폼 비활성(실제 메일 발송 없음), STEP2 통과 시 `login.html` 데모 이동.
- **검증 실패 메시지**: 대부분 기존 요소 재사용. 없던 곳(중복확인 결과 `#dup-msg`, 비밀번호 불일치 `#pw-match-msg`)만 최소 추가.
- start/stop/delete·검색·정렬 등 테이블 액션은 하드코딩 예시 행 위에서만 동작(실데이터/서버 없음).

### Assumptions — 모달·팝업 공통 기능 (`solcho/fe-modals`)

- **모달 유틸을 별도 브랜치로 분리한 이유**: 정적 UI 단계(`fe-pages`)에서 "모달 열기/닫기"를 화면의 일부로 포함하라 했으나, 실제 결과물 일부(진행률·소셜 모달 등)가 열고 닫는 토글 없이 평면 정적 블록으로 얹혀 있었음. 이 브랜치에서 `assets/js/modal.js`(`MCPModal`) 공통 유틸(배경클릭/ESC/스크롤 잠금/전역 API)로 표준화하고 빠진 개폐 동작을 채움.
- **비밀번호 변경 성공 팝업 보완**: 기능명세서엔 있으나 정적 UI 문서에 누락됐던 팝업을 이 브랜치에서 가입완료 팝업과 동일한 결과 팝업 스타일로 `password-reset.html`에 추가(2절 기본값 "포함").
- **소셜 가입 추가정보 모달**: 마크업+id만 준비(`#signup-social-modal`), 실제 트리거 연결은 OAuth 연동 브랜치로 보류.
- **PROV-02 진행률**: 기본형↔축소형은 실제 진행 상태 반영이 아니라 정적 두 형태의 UI 토글일 뿐(실 폴링은 백엔드 연동 이후).
- 기존 `MCUI.open/close`(ui.js)로 열리던 모달들도 이 브랜치에서 `MCPModal`/data 속성으로 통일.

### Assumptions — 프로비저닝 설정 폼 (`solcho/fe-provisioning-form`)

PROV-01 마법사 ④공통·⑤추가 설정 스텝을 리소스 종류/플랫폼에 따라 동적 렌더링하고 입력값을
전역 상태(`window.provisioningSpec`)에 반영하는 작업. 서버 통신은 없음(Network 탭 요청 0건).
필드명은 이후 BE 연동을 위해 `01_API_명세서_v1.1.md` §10.3의 `common_spec`/`provider_spec`
구조에 맞춰 잡음(`assets/js/provisioning.js`).

- **사양 등급 → 실제 SKU 매핑**(추상 3등급, `provisioning.js`의 `SPEC_TIERS` 상수):

  | 등급 | AWS | Azure | GCP |
  |---|---|---|---|
  | 경량 (1 vCPU · 2GB) | `t3.micro` | `B1s` | `e2-micro` |
  | 표준 (2 vCPU · 4GB) | `t3.medium` | `B2s` | `e2-medium` |
  | 고성능 (4 vCPU · 8GB) | `t3.large` | `B4ms` | `e2-standard-4` |

  표준 등급은 가격 비교 모달 예시값과 일치. 사용자는 등급만 고르고 실제 SKU는 코드에서 변환한다.
- **리전 제한**(서비스 컨텍스트 9절): AWS `ap-northeast-2`/`us-east-1`, Azure
  `koreacentral`/`eastus`/`koreasouth`/`canadacentral`, GCP `asia-northeast3`/`us-central1`.
  선택한 플랫폼마다 별도 리전 select.
- **네트워크**: 오늘은 "새 VPC/Subnet 자동 생성" 고정, "기존 리소스 사용" 토글은 자리만 두고 비활성
  (실제 VPC/Subnet lookup은 BE 연동 이후).
- **맵핑 문서 "제외" 필드**(Compute 스토리지·권한, DB 사양·스토리지·네트워크·접근제어·가용성,
  Storage 접근제어·중복성·버전관리)는 폼에 렌더링하지 않고 `providerSpec`에 서버 기본값 상수로만
  채운다(사용자 입력 없음).
- **DB 공통 설정**: 엔진 옵션은 플랫폼별로 다름(AWS 7종, Azure/GCP 3종). Azure는 "MariaDB 25/9/19
  지원 종료" 경고 상시 노출. 인증은 마스터 사용자명+비밀번호, 단 **GCP는 루트 비밀번호만**. 백업은
  읽기 전용 안내(자동 백업 고정). `providerSpec[p]`에 `engine`/`region`/`masterUsername`/`masterPassword` 저장.
- **Storage 공통 설정**: 버킷/계정명(전역 고유, mcp- 프리픽스 없음), 리전(Azure는 'Central' 고정 안내).
  Azure 선택 시 태그 검색 미지원 경고. "이름(서비스)"는 읽기 전용 라벨.
- **리소스 종류 선택 가능화**: 정적 UI의 "권한 없음" 데모로 비활성이던 Database/Storage 라디오를 이
  브랜치에서 활성화(동적 폼이 실제로 동작해야 하므로). CDN만 비활성 유지.
- **⑤ 플랫폼별 추가 설정**: Compute 이미지는 **AWS/Azure만**(AWS는 "직접 AMI ID 입력" 선택 시
  AMI ID 입력칸 노출, GCP는 미노출), Storage 스토리지 등급은 **GCP만**(Standard/Nearline/Coldline/
  Archive), DB는 추가 입력 없음(서버 기본값). "제외" 필드는 `SERVER_DEFAULTS` 상수로 `providerSpec`에만
  채운다(사용자 입력 없음).
- **CDN**: 3사 공통 입력 스펙 미확정 → 선택은 가능하되 ④(공통)를 **건너뛰어 숨기고** ⑤에 "준비 중"
  안내만 표시, "생성하기"는 **비활성**. (CDN 라디오는 이 브랜치에서 선택 가능하게 활성화함.)
- **생성하기 활성화**: 선택된 각 플랫폼에 대해 리소스 종류의 공통 필수 + 해당 플랫폼 추가 필수
  필드가 전부 채워졌을 때만 버튼 활성화(`provisioning.js`의 `validate()`, 상태 기반). 미충족/CDN/
  플랫폼 미선택 시 비활성. 초기 로드 시에도 필수값 비어 있으면 비활성으로 시작한다.
- **진행 단위**: 이 브랜치는 (1)Compute 공통 폼 → (2)DB/Storage 공통 폼 → (3)⑤추가 설정+CDN
  → (4)생성하기 활성화 검증 순으로 커밋을 쪼갠다.

### Assumptions — 프로비저닝 페이지 완성 (`solcho/fe-provisioning-complete`)

`fe-provisioning-form` 후속. CDN 실입력 + 선택 시각 피드백 + 마법사 전체 흐름을 붙여 프로비저닝
페이지를 완성한다. 실제 API로 리소스가 생성되는 것 외의 모든 기능(버튼·모달·진행바 애니메이션
포함)을 구현하는 것이 목표. 서버 통신은 여전히 없음(Network 요청 0건).

- **리전 = 국가 단일 선택**: 공통 설정은 플랫폼 무관해야 하므로, 플랫폼별 리전 select를 없애고 국가
  하나(한국/미국)만 고르면 `COUNTRY_REGION` 상수로 각 플랫폼 리전(AWS `ap-northeast-2`/`us-east-1`,
  Azure `koreacentral`/`eastus`, GCP `asia-northeast3`/`us-central1`)이 `providerSpec[p].region`에
  자동 매핑된다. 일본 등 추가 국가는 허용 리전 확정 후.
- **④ 공통은 플랫폼 무관, 플랫폼별 항목은 ⑤로**: 플랫폼마다 내용/구역이 달라지는 항목을 ④에서 빼
  ⑤ 추가 설정으로 이동했다 — Compute의 **인바운드 규칙·인증**, DB의 **엔진·인증**. 결과적으로 ④
  공통은 이름·리전(국가)·(Compute)사양·네트워크·(DB)백업·태그처럼 모든 플랫폼에 동일한 필드만 남는다.

- **선택 시각 피드백**: ①플랫폼/③종류(카드)·②계정(chip)의 강조색을 마크업 고정이 아니라 **실제
  체크/선택 상태에서 JS(`syncSelectionUI`)가 계산**해 반영한다(카드=primary 테두리+muted 배경,
  chip=sky 배경+흰 텍스트). 선택 변경마다 갱신되고, ②계정 개수 문구도 동적으로 갱신된다.
- **점진적 노출(progressive reveal)**: ①~⑥을 다음/이전 버튼 없이, **한 단계를 만족하면 바로 아래에
  다음 단계가 자동으로 나타나는** 방식(`revealSteps`). 완료된 단계는 위에 그대로 쌓여 보이고, 스텝
  인디케이터가 완료(sky)/현재(primary)/대기(muted)를 표시한다. **CDN은 ④ 공통을 건너뛰어** ③ 다음에
  바로 ⑤가 나타난다(`visibleSteps`). ⑥은 검토(선택 요약) + 비교하기/생성하기.
- **CDN 실입력 폼**: 공통 설정 스텝 없이 ③에서 CDN 선택 시 곧바로 플랫폼별(⑤) 폼만 렌더
  (AWS→Azure→GCP 순). 필드 확정 근거는 `docs/멀티클라우드 3사 기능 맵핑 — 설정값 입력 범위
  (2026-09-10).md` **4절 "구현용 필드 확정"(2026-09-11)** — 원칙은 "Terraform으로 설정 가능한
  항목은 전부 입력받는다". 제외는 **Endpoint(결과값)·Scope(Global 고정)·Raw status(조회전용)**
  세 가지뿐. 3사 필드셋이 완전히 다름:
  - AWS(CloudFront): Origin(필수)·캐시 정책·Path routing·Compression·Viewer Protocol Policy·
    Price Class. **Health Probe 없음**(CloudFront에 독립 헬스체크 인자 없음).
  - Azure(Front Door): Origin·Resource Group·SKU(**셋 다 필수**)·쿼리스트링 캐시 처리·Compression·
    지원 프로토콜·HTTPS 리다이렉트·Health Probe(경로/간격 기본 240).
  - GCP(LB+Cloud CDN): Backend(필수)·LB stack 확인 체크박스(**필수**)·백엔드 유형(서비스/버킷)·
    enableCdn·Cache Mode·Compression·HTTPS 강제 리다이렉트·Health Probe(**백엔드 서비스일 때만**
    노출, 기본 10). Path routing은 기본 매핑 자동 생성이라 입력 없음.
  - 필수 충족(AWS Origin / Azure Origin·RG·SKU / GCP Backend·LB stack 동의) 시 생성하기 활성화.
- **생성 흐름 + 진행률 애니메이션**: 생성하기 → 생성 확인 모달 → 진행률 모달로 연결. 진행률은 실제
  API 없이 **클라이언트 시뮬레이션**(`startProvisioningSim`)으로, 선택한 대상(플랫폼×계정)별 진행바를
  0→100% 애니메이션하며 대기→진행중→완료/실패로 전환한다. 실패는 **매 실행 랜덤**(데모). 축소형
  카드도 진행 카운트와 연동. 실제 job 상태 폴링은 BE 연동 이후 이 시뮬레이션을 대체한다.

### Assumptions — 순수 JS 나머지 기능 (`solcho/fe-remaining-js`)

API 연동 없이 JS만으로 완성 가능한 나머지 데모 기능들. 서버 통신 없음(Network 요청 0건),
전부 `localStorage`/하드코딩 목업/클라이언트 계산으로 시뮬레이션. API 연동 프롬프트가 아래
자리들을 실제 호출로 교체한다.

- **데모 인증 상태**: 로그인 성공(형식 검증 통과) 시 `localStorage.mcp_demo_session`
  `{email, loggedInAt}` 저장(실제 토큰 아님). `assets/js/auth-guard.js`를 보호 화면 4개
  (dashboard/inventory/provisioning/mypage) `<head>`에서 로드 → 세션 없으면 `login.html`로
  리다이렉트(본문 렌더 전). 사이드바 이메일 표시를 세션 값으로 교체, 사이드바/마이페이지 로그아웃
  버튼(`.sidebar__logout`, `[data-logout]`)은 세션 삭제 후 `login.html`로 이동. login/signup/
  password-reset/main/INTRO는 가드 대상 아님. → API 연동 시 이 파일을 실제 인증(JWT 등)으로 교체.
- **유틸 컨트롤을 사이드바 하단으로**: COMM-01의 흰 전체폭 유틸바(`.topbar`)를 제거하고, 테마·언어·
  알림 세 컨트롤을 **사이드바 하단(footer 위) `.sidebar__utils`**에 개별 캡슐(`border-radius:999px`,
  다크 네이비에 맞춘 은은한 톤)로 배치(shell.css + 4개 화면 마크업). 우상단 플로팅은 다른 콘텐츠와
  겹쳐 사이드바 하단으로 옮김. 본문이 최상단까지 올라오고, 프로비저닝 과금 배너 sticky는 `top-0`.
- **MY-01 계정 순서 드래그**: 연결된 클라우드 계정 표 행을 HTML5 Drag&Drop으로 재정렬
  (`assets/js/mypage.js`), 순서는 `localStorage.mcp_account_order`(행 이름을 키)로 저장해 새로고침
  후 유지. 기존 필터(행 hidden 토글)와 공존. → API 연동 시 서버(계정 display_order 등) 저장으로 교체.

> **진행 순서 메모(2026-09-11)**: 마이페이지 계정 순서까지 완료 후, 나머지 웹 단위(KOR/EN 전환,
> 알림 드롭다운, 인벤토리 CSV·액션, 프로비저닝 보강)는 **키값(credentials) API 연동이 끝난 뒤** 재개.
> 키값 저장·검증 API가 팀원들의 프로비저닝 구현·테스트를 언블록하는 우선 작업이기 때문.

## Pointer — where the planning docs live

The functional spec (기능명세서), screen design (화면설계서), WBS, and the
existing confirmed design documents (프로토타입 개발 프롬프트.md, Tier2 확장
프롬프트 등) are **NOT in this repo** — they live only in the Claude project
context. Don't look for them under `docs/` yet; the team adds copies there as
needed. Flag this to new members during onboarding so they aren't confused.
