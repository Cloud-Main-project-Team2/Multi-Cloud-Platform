# 멀티 클라우드 관리 서비스 API 명세서 (v1.2)

> 기준일: 2026-09-18
> 기준 문서: `01_API_Specification_v1.1.md` + `docs/비용_개발문서/05_API계약.md`(2026-09-17/18, 이승현 작성)
> 이전 버전: `01_API_Specification_v1.1.md`(provider별 개별 엔드포인트 유지 확정판)
> 대상: FastAPI 기반 REST API
> 상태: 목표 명세(Draft). **§11(비용)과 §11 관련 변경분(§1.1·§2.4·§3.5·§14·§15·§16·§17·§19)은
> 팀 승인 전 제안이며, 그 외 절(§2~§10, §12~§13, §18, §20)은 `v1.1`에서 변경하지 않았다.**
> 이 문서 하나만으로 "무엇이 이미 맞춰진 확정 계약이고 무엇이 아직 제안인지" 구분할 수 있도록
> 절마다 상태를 표시한다.

## 0. v1.1 → v1.2 변경 요약

**`v1.1`은 그대로 팀 공식 문서로 유지된다.** 이 문서(`v1.2`)는 비용(Cost) 기능을 위해 `v1.1`에
제안되는 변경을 전부 반영한 완성본이며, 팀이 검토·승인하면 `v1.1`을 대체하는 것을 전제로 작성했다.
아직 아무 라우터도 구현되지 않았다 — "최종 문서 형태"이지 "구현 완료"가 아니다.

| # | 절 | 변경 | 상태 |
|---:|---|---|---|
| 1 | §1.1 구현 범위 표 | 비용 상세를 `확장(보류)` → `구현 대상(제안)`으로 전환 | **제안** — 승인자: 팀 회의 |
| 2 | §2.4 확인이 필요한 작업 | `X-Budget-Override: true` 헤더를 예산 초과 override 확인용으로 추가 | **설계만, 미구현** — ADR-042로 이번 라운드 범위에서 제외(§11-7-3) |
| 3 | §3.5 Resource `cost_summary` | `period_end`가 제외 경계임을 명시 | **제안**(응답 모양은 안 바뀐다) |
| 4 | §11 비용 | 전면 교체 — 조회 6종·수집 3종·팀/예산 9종·검토/보고서 부품 계약 신설 | **제안**, 신규 테이블 6개 필요(`DB_ERD_v1.2.md` Part B) |
| 5 | §14 감사 이벤트 | `cost_review.update` 행 추가 제안 | **제안, 승인 전에는 기록 안 함** |
| 6 | §15 인가 및 소유권 검사 | team·team budget·account cost·ingestion run·cost review item 5줄 추가 | **제안** |
| 7 | §16 상태 전이 | §16.3 비용 수집 run 신설(기존 동기화 job과 같은 6종 상태) | **제안** |
| 8 | §17 주요 오류 코드 | 비용 행에 신규 코드 9개 추가 제안 | **제안, 채택 전엔 미사용** |
| 9 | §19 구현 전 결정 필요 목록 | "GCP 실제 비용 수집 방식"·"환율 변환 정책" 2줄을 해소로 표시, 비용 관련 신규 미결 항목 추가 | 아래 §19 참고 |

**바꾸지 않은 것**: §2.1(데이터 형식)·§2.7(성공 응답)·§2.8(오류 응답)·§8(인벤토리 `GET /resources`
응답 모양)·§10.2(`price-comparisons` 계약)·§20(구현 완료 조건)은 그대로 쓴다. 고칠 것이 없다고
판단했다.

**CSP 호출 경계 (§11 전체에 적용되는 가장 중요한 원칙)**: 비용 조회 API·capability 판정·급증
탐지·보고서 부품은 **전부 DB만 읽는다.** CSP API(AWS Cost Explorer 등)를 실제로 부르는 곳은 수집
실행(`POST /cost-ingestion-runs`와 자동 스케줄) 한 곳뿐이다 — Cost Explorer가 요청당 $0.01이라
조회할 때마다 CSP를 부르면 화면을 새로고침할 때마다 과금된다.

---

## 0-1. v1.0 → v1.1 변경 요약 (참고 — 유지)

팀은 API 설계를 "이미 구현되어 있던 방식"(provider별 개별 엔드포인트, 리소스 유형 정규화 없음)으로 유지하기로 확정했다. 이에 따라 v1.0에서 신규 통합 API를 위해 추가됐던 두 구조를 되돌렸다.

| 구분 | v1.0 | v1.1 |
|---|---|---|
| 프로비저닝 생성 | `POST /provisioning/requests` — `targets[]` 배열로 여러 계정·서비스를 한 요청에 묶음, `provisioning_requests`(부모)-`provisioning_jobs`(자식) 구조 | `POST /provisioning/{provider}/{service}` — 계정(credential) 하나·서비스 하나당 한 번 호출. 여러 계정/플랫폼 선택 시 클라이언트가 필요한 만큼 병렬 호출. `provisioning_jobs`가 다시 최상위 |
| 리소스 종류 정규화 | `resource_types` 테이블 + `resources.resource_type_id`로 CSP 원본 유형을 정규화하고 `supports_start/stop/delete`를 일반화 검사 | `resource_types` 없음. `resources.original_resource_type`(원본 문자열)만 사용, start/stop/delete 지원 여부는 서버 코드가 서비스별로 직접 판단 |
| 계정·자격증명 등록 | `POST /cloud-accounts`(계정 생성) → `POST /cloud-accounts/{id}/credentials`(자격증명 생성) 2단계 | `POST /credentials/{provider}` 1단계 — 서버가 `(user, provider, external_account_id)`로 계정을 찾아 없으면 생성하고 그 아래 자격증명을 등록 |

**변경하지 않은 것**: credentials 암호화(`encrypted_payload`/`encryption_nonce`/`encryption_key_version`), cloud_accounts-credentials 분리 구조, 인증·사용자·인벤토리 캐시(동기화)·알림·감사 API는 v1.0과 동일하다. 이 문서에서 언급하지 않는 절은 v1.0 내용을 그대로 따른다.

## 1. 목적과 범위

이 문서는 AWS, Azure, GCP 계정 연결, 통합 인벤토리, 리소스 제어, Terraform 프로비저닝, 비용, 알림 및 대시보드에 필요한 외부 API 계약을 정의한다.

DB bigint PK를 외부 식별자로 사용하되 모든 사용자 소유 데이터는 인증 사용자 기준으로 접근 권한을 검사한다. 클라우드 자격 증명, 비밀번호, JWT 비밀값, 비밀번호 재설정 원본 토큰, Terraform secret·state·민감 output은 응답, 오류, 로그, 감사 metadata에 포함하지 않는다.

### 1.1 구현 범위

| 구분 | 범위 | DB 선행 조건 |
|---|---|---|
| 핵심 | 인증, 사용자, 클라우드 계정·자격 증명, 서비스 카탈로그, 인벤토리, 동기화, 프로비저닝 job, 알림, 상태 확인 | `users`/`social_accounts`/`password_reset_tokens`/`email_verifications`/`refresh_tokens`/`cloud_accounts`/`credentials`/`service_catalog`/`provisioning_jobs`/`notifications`/`resources`/`resource_sync_jobs`/`resource_sync_job_items` — 완료 |
| **구현 대상(제안)** | **비용 상세**(§11) — 조회 6종·수집 3종·팀/예산 9종·검토 큐·보고서 부품 3종 | `cloud_resource_costs`(기존, 미변경) + **신규 제안 6개**(`teams`/`team_budgets`/`team_budget_notifications`/`cloud_account_costs`/`cost_ingestion_runs`/`cost_review_items`, `DB_ERD_v1.2.md` Part B) — **팀 승인 및 구현 전** |
| 확장(보류) | 보안 점검, AI 리포트(비용 문맥 주입 제외), 인수인계 | 화면·정책 확정 전. `audit_events`는 스키마상 존재하나 이 문서에서 상세 계약을 다루지 않는다 |

`provisioning_jobs`는 부모 `provisioning_requests` 없이 `user_id`/`credential_id`/`service_catalog_id`를 직접 참조하는 최상위 테이블이다. `resources`에는 `resource_type_id`가 없다(원본 유형은 `original_resource_type` 문자열로만 보관).

### 1.2 명세에서 확정하지 않는 사항

다음 항목은 제품 결정 전까지 API 계약을 확정하지 않는다.

- 목록 API의 pagination 방식과 기본 정렬
- 태그의 값 타입, 비교 연산 및 query 직렬화 방식
- 소셜 로그인 공급자 목록과 OAuth callback 세부 규격
- 서비스별 provider_spec 필드 전체 목록
- 알림 종류, 보존 기간 및 일괄 읽음 정책
- 보고서 파일 형식, 생성 시점 및 이메일 발송 정책(§11-7-4의 비용 섹션 부품 3종은 예외 — 계약 확정)
- 예산 초과 시 프로비저닝 생성 차단(§11-7-3) — 설계는 확정했으나 이번 라운드 구현 범위에서 제외(ADR-042)
- 신규 비용 오류 코드 9개의 채택 여부(§17)

본문의 `정책 확정 필요` 표시는 구현 전 사용자 결정이 필요한 항목이다.

## 2. 공통 규약

### 2.1 기본 URL과 데이터 형식

- Base path: `/api/v1`
- 요청·응답: `application/json; charset=utf-8`
- 시간: ISO 8601 UTC 문자열. 예: `2026-09-09T03:15:30Z`
- 날짜: ISO 8601 날짜. 예: `2026-09-01`
- 금액: 정밀도 손실을 막기 위해 JSON 문자열로 반환하며, **소수 6자리로 고정**한다. 예: `"1234.560000"`
- 통화: ISO 4217 대문자 3자리 코드. 예: `KRW`, `USD`
- ID: DB의 양수 bigint. JavaScript 정밀도 문제를 피하기 위해 응답에서는 문자열로 반환한다.
- provider: `aws | azure | gcp`
- API 필드명: `snake_case`

### 2.2 인증

보호 API는 다음 헤더를 요구한다.

```http
Authorization: Bearer <access_token>
```

JWT access token은 응답 body에 반환할 수 있으나 서버 로그에는 기록하지 않는다. refresh token은 opaque 토큰으로 DB(`refresh_tokens.token_hash`)에 해시로 저장하며 `POST /auth/refresh`에서 회전(기존 토큰 폐기 + 새 쌍 발급)한다.

### 2.3 언어

클라이언트는 선택적으로 다음 헤더를 보낸다.

```http
Accept-Language: ko-KR
```

지원 언어는 한국어와 영어다. API 오류의 안정적인 식별자는 `code`이며, `message`는 사용자 표시용 번역 결과다. 알림은 번역 가능한 `message_key`와 `message_params`를 원형으로 반환한다.

### 2.4 확인이 필요한 작업

삭제, 회원 탈퇴, 리소스 시작·중지, 유료 리소스 생성은 클라이언트가 사용자 확인 UI를 거친 뒤 다음 헤더를 보낸다.

```http
X-Action-Confirmed: true
```

서버는 헤더가 없거나 `true`가 아니면 `428 CONFIRMATION_REQUIRED`로 거부한다. 이 헤더는 인증·인가를 대체하지 않는다. 해당 요청과 결과는 `audit_events`에 남긴다.

> **제안(설계만, 미구현) — `X-Budget-Override: true`**: 예산 초과 상태에서도 프로비저닝 생성을 계속
> 진행하겠다는 별도 확인 헤더다. `create_provisioning_job`이 이미 `X-Action-Confirmed`를 **항상**
> 요구하므로(없으면 428), 예산 초과 확인에 같은 헤더를 재사용하면 예산 거절 자체가 무력화된다 —
> 그래서 별도 헤더로 설계했다. **2026-09-18 ADR-042 결정으로 이번 라운드 구현 범위에서 제외됐다.**
> `routers/provisioning.py`는 이 헤더를 아직 읽지 않는다. 계약은 §11-7-3에 그대로 남겨 둔다.

### 2.5 멱등성

프로비저닝 생성 요청(`POST /provisioning/{provider}/{service}`)은 필수로 다음 헤더를 받는다.

```http
Idempotency-Key: <client-generated-unique-key>
```

- 사용자별로 같은 키와 같은 canonical payload가 다시 오면 기존 job을 반환한다.
- 같은 키에 다른 payload를 보내면 `409 IDEMPOTENCY_KEY_REUSED`를 반환한다.
- DB에는 `provisioning_jobs.idempotency_key`의 `(user_id, idempotency_key)` unique constraint로 보강한다.

리소스 시작·중지·삭제의 멱등성 키 적용 여부와 보존 기간은 추후 확정한다.

### 2.6 비동기 작업

동기화와 프로비저닝은 요청 생성 후 `202 Accepted`를 반환한다. 응답에는 상태 조회 URL을 포함한다.

```json
{
  "data": {
    "id": "901",
    "status": "pending",
    "status_url": "/api/v1/sync-jobs/901"
  }
}
```

정확한 진행률을 알 수 없는 프로비저닝은 UI가 90%까지 추정 표시할 수 있으나, API의 `progress_percent`는 저장된 작업 진행값이며 성공하지 않은 정확도를 주장하지 않는다. 실제 완료 시에만 100이다.

### 2.7 성공 응답

```json
{
  "data": {}
}
```

목록 응답의 최종 pagination envelope는 정책 확정 후 결정한다. 확정 전 예시는 다음 의미만 가진다.

```json
{
  "data": {
    "items": [],
    "total": 0,
    "pagination": null
  }
}
```

`pagination`의 cursor/page 필드는 아직 계약이 아니다.

### 2.8 오류 응답

```json
{
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "리소스를 찾을 수 없습니다.",
    "request_id": "01J7E9M9YQ8R6V2M5C4K3H1N0P",
    "details": [
      {
        "field": "resource_ids[0]",
        "reason": "not_found"
      }
    ]
  }
}
```

`details`는 선택 사항이다. provider SDK 원문 요청·응답, DB 연결 문자열, secret, Terraform 민감 output은 포함하지 않는다.

| HTTP | 의미 | 대표 code |
|---:|---|---|
| 400 | 요청 의미 오류 | `INVALID_REQUEST`, `UNSUPPORTED_OPERATION` |
| 401 | 인증 실패 | `AUTHENTICATION_REQUIRED`, `INVALID_TOKEN` |
| 403 | 소유권·권한 부족 | `FORBIDDEN`, `CLOUD_PERMISSION_DENIED` |
| 404 | 대상 없음 또는 타 사용자 소유 대상을 비공개 처리 | `*_NOT_FOUND` |
| 409 | unique·상태 충돌 | `DUPLICATE_RESOURCE`, `JOB_ALREADY_RUNNING`, `IDEMPOTENCY_KEY_REUSED` |
| 422 | 필드 검증 실패 | `VALIDATION_ERROR` |
| 428 | 사용자 확인 누락 | `CONFIRMATION_REQUIRED` |
| 429 | 요청 제한 | `RATE_LIMITED` |
| 502 | CSP/Terraform 외부 연동 실패 | `PROVIDER_API_ERROR`, `TERRAFORM_ERROR` |
| 503 | DB 또는 필수 의존성 준비 안 됨 | `SERVICE_NOT_READY` |

## 3. 공통 리소스 표현

### 3.1 User

```json
{
  "id": "12",
  "email": "user@example.com",
  "name": "홍길동",
  "affiliation_type": "company",
  "affiliation_name": "Example Corp",
  "status": "active",
  "created_at": "2026-09-09T03:15:30Z",
  "updated_at": "2026-09-09T03:15:30Z",
  "withdrawn_at": null
}
```

`normalized_email`과 `password_hash`는 반환하지 않는다.

### 3.2 CloudAccount

```json
{
  "id": "31",
  "provider": "aws",
  "external_account_id": "123456789012",
  "account_label": "운영 AWS",
  "created_at": "2026-09-09T03:15:30Z",
  "updated_at": "2026-09-09T03:15:30Z"
}
```

> **제안** — 비용 팀 배정이 채택되면 `team_id`(nullable)가 추가된다. §11-6-1 참고.

### 3.3 Credential

```json
{
  "id": "44",
  "cloud_account_id": "31",
  "name": "inventory-reader",
  "masked_public_identifier": "AKIA••••••••••••••••",
  "permission_scope": {
    "inventory_read": true,
    "provision": false
  },
  "verified": true,
  "verified_at": "2026-09-09T03:20:00Z",
  "tags": {
    "environment": "production"
  },
  "display_order": 0,
  "created_at": "2026-09-09T03:15:30Z",
  "updated_at": "2026-09-09T03:20:00Z"
}
```

`encrypted_payload`, `encryption_nonce`, `encryption_key_version`, 원본 `public_identifier` 및 secret payload는 반환하지 않는다. 마스킹 정책은 공개 가능한 식별자의 앞 4자만 노출하는 것을 기본으로 한다.

### 3.4 ServiceCatalogItem

```json
{
  "id": "1",
  "provider": "aws",
  "service_code": "ec2",
  "category": "compute",
  "display_name": "EC2",
  "provisionable": true
}
```

### 3.5 Resource

```json
{
  "id": "701",
  "cloud_account": {
    "id": "31",
    "provider": "aws",
    "external_account_id": "123456789012",
    "account_label": "운영 AWS"
  },
  "service": {
    "id": "1",
    "service_code": "ec2",
    "category": "compute",
    "display_name": "EC2"
  },
  "external_resource_id": "i-0123456789abcdef0",
  "original_resource_type": "AWS::EC2::Instance",
  "name": "web-01",
  "region": "ap-northeast-2",
  "status": "running",
  "cost_summary": {
    "estimated_monthly_cost": "52.340000",
    "collected_cost_amount": "18.120000",
    "currency": "USD",
    "period_start": "2026-09-01",
    "period_end": "2026-09-09",
    "as_of": "2026-09-09T01:00:00Z",
    "source": "aws_cost_explorer"
  },
  "tags": {
    "environment": "production"
  },
  "first_seen_at": "2026-08-01T00:00:00Z",
  "last_seen_at": "2026-09-09T02:00:00Z",
  "is_stale": false,
  "deleted_at": null,
  "last_synced_at": "2026-09-09T02:00:00Z"
}
```

> **제안(응답 모양은 바뀌지 않는다)** — `cost_summary.period_end`는 **제외 경계**다(포함이 아니다).
> 확정 6(§11)이 이 규칙을 정했고, 신규 비용 테이블(`cloud_account_costs`)과 의미를 맞추기 위해 이
> 문서에도 명시한다.

v1.0에 있던 `resource_type`(정규화된 유형) 중첩 객체는 없다 — CSP 원본 유형은 `original_resource_type` 문자열 하나로만 노출한다. 이 리소스가 start/stop/delete 중 무엇을 지원하는지는 응답에 별도로 내려주지 않는다: 프론트는 항상 버튼을 보여줄 수 있고, 실제로 지원하지 않는 서비스 종류에 대해 시도하면 서버가 `422 UNSUPPORTED_OPERATION`으로 거부한다(서버 코드가 서비스별로 직접 판단 — 8.5절 참고).

`provider_resource_key`, 수집 credential ID, `raw_metadata`는 기본 목록 응답에서 제외한다. 상세 API에서 `raw_metadata`를 제공할 경우 허용 목록 기반으로 비밀을 제거한 데이터만 반환한다.

## 4. 상태 확인

### 4.1 `GET /health`

프로세스 생존 여부를 반환한다. 인증이 필요 없다.

**200**

```json
{"status": "ok"}
```

### 4.2 `GET /ready`

`SELECT 1`로 DB 연결을 확인한다. 인증이 필요 없다.

**200**

```json
{"status": "ready"}
```

**503**

```json
{
  "error": {
    "code": "SERVICE_NOT_READY",
    "message": "서비스가 아직 준비되지 않았습니다.",
    "request_id": "01J7E9M9YQ8R6V2M5C4K3H1N0P"
  }
}
```

## 5. 인증과 사용자

### 5.1 엔드포인트 요약

| Method | Path | 인증 | 설명 |
|---|---|---:|---|
| POST | `/auth/sign-up` | 아니요 | 일반 회원가입(이메일 검증 완료 후) |
| POST | `/auth/login` | 아니요 | 이메일·비밀번호 로그인 (access+refresh 발급) |
| POST | `/auth/refresh` | 아니요(refresh token) | refresh token 회전 + 새 access 발급 |
| POST | `/auth/logout` | 아니요(refresh token) | refresh token 폐기(멱등) |
| POST | `/auth/email-verifications` | 아니요 | 이메일 인증 코드 발송(회원가입 전) |
| POST | `/auth/email-verifications/verify` | 아니요 | 인증 코드 확인 |
| POST | `/auth/password-reset-requests` | 아니요 | 비밀번호 재설정 메일 요청 |
| POST | `/auth/password-resets` | 아니요 | 토큰으로 비밀번호 재설정 |
| GET | `/auth/me` | 예 | 내 정보 조회 |
| PATCH | `/me` | 예 | 이름·소속 정보 수정 |
| DELETE | `/me` | 예 + 확인 | 회원 탈퇴 |

소셜 로그인 URL과 callback API는 공급자 목록 및 OAuth 정책 확정 후 추가한다. 이 절은 실제 구현(2026-09-14
`solcho/be-auth-enhancements`)을 반영해 `v1.1` 대비 갱신했다 — `refresh`/`logout`/`email-verifications`/
`GET /auth/me`는 이미 코드에 있다(비용 파트 변경 아님, 문서 누락 보정).

### 5.2 `POST /auth/sign-up`

```json
{
  "email": "user@example.com",
  "password": "client-input-only",
  "name": "홍길동",
  "affiliation_type": "company",
  "affiliation_name": "Example Corp"
}
```

- 이메일은 trim 및 casefold/소문자 정규화 후 `normalized_email` 기준으로 중복 검사한다.
- `affiliation_type=company`일 때 단체명은 **필수**다. 비어 있으면 `422 VALIDATION_ERROR`.
- 비밀번호 정책: 8자 이상 + 영문/숫자/기호 중 2종 이상.
- 최근 30분 내 이메일 검증 완료 레코드가 없으면 `422 EMAIL_NOT_VERIFIED`. 성공 시 그 레코드를 소비(삭제)한다.

**201**: `User` 반환.
**409**: `EMAIL_ALREADY_EXISTS`.

### 5.3 `POST /auth/login`

```json
{
  "email": "user@example.com",
  "password": "client-input-only"
}
```

**200**

```json
{
  "data": {
    "access_token": "<jwt>",
    "refresh_token": "<opaque>",
    "token_type": "Bearer",
    "expires_in": 3600,
    "refresh_expires_in": 1209600,
    "user": {}
  }
}
```

계정 존재 여부를 노출하지 않도록 이메일·비밀번호 불일치는 동일한 `INVALID_CREDENTIALS`를 반환한다. 탈퇴 사용자의 로그인은 거부한다.

### 5.4 `POST /auth/refresh` / `POST /auth/logout`

refresh token은 opaque 토큰이며 DB에 해시로만 저장한다(`refresh_tokens.token_hash`). `refresh`는 기존
토큰을 폐기(`revoked_at`)하고 새 access+refresh 쌍을 발급한다(회전). `logout`은 refresh를 폐기한다(멱등).

### 5.5 `POST /auth/email-verifications` / `.../verify`

회원가입 전 이메일 소유 확인. 코드는 6자리, 유효 10분, 시도 5회 제한, 재전송 60초 rate-limit. 이미
가입된 메일이면 발송 요청 자체가 `409`로 중복확인을 흡수한다.

### 5.6 `POST /auth/password-reset-requests`

```json
{"email": "user@example.com"}
```

계정 존재 여부와 무관하게 **202**와 같은 메시지를 반환한다. DB에는 원본 토큰이 아니라 `token_hash`, 만료 시각만 저장한다. 성공 시 해당 유저의 활성 refresh token을 전부 폐기한다.

### 5.7 `POST /auth/password-resets`

```json
{
  "token": "one-time-token",
  "new_password": "client-input-only"
}
```

성공 시 토큰의 `used_at`을 기록한다. 만료·사용 완료·불일치는 `INVALID_OR_EXPIRED_RESET_TOKEN`으로 통합한다.

### 5.8 `GET /auth/me` / `PATCH /me`

```json
{
  "name": "홍길동",
  "affiliation_type": "individual",
  "affiliation_name": null
}
```

이메일 변경은 이 API 범위에 포함하지 않는다. **200**으로 갱신된 `User`를 반환한다.

### 5.9 `DELETE /me`

`X-Action-Confirmed: true`가 필수다. 사용자를 물리 삭제하지 않고 `status=withdrawn`, `withdrawn_at`을 기록하는 soft withdrawal을 기본으로 한다. credential 처리와 법적 보존 정책은 별도 확정이 필요하다.

**204 No Content**.
감사 action: `user.withdraw`.

## 6. 클라우드 계정과 자격 증명

### 6.1 엔드포인트 요약

| Method | Path | 설명 |
|---|---|---|
| GET | `/cloud-accounts` | 내 연결 계정 목록 |
| GET | `/cloud-accounts/{cloud_account_id}` | 연결 계정 조회 |
| PATCH | `/cloud-accounts/{cloud_account_id}` | 표시 이름 수정 |
| DELETE | `/cloud-accounts/{cloud_account_id}` | 연결 계정 삭제(**501** 고정 — snapshot 보존 정책 미확정) |
| GET | `/cloud-accounts/{cloud_account_id}/credentials` | credential 목록 |
| GET | `/credentials/aws/delegation-setup` | AWS 역할 위임 온보딩 정보(플랫폼 계정 ID·ExternalId·신뢰 정책) |
| POST | `/credentials/{provider}` | **계정 자동 find-or-create + credential 등록·실제 검증(한 번에)** |
| PATCH | `/credentials/{credential_id}` | 비민감 metadata 수정 또는 secret 교체 |
| POST | `/credentials/{credential_id}/verify` | 재검증 |
| DELETE | `/credentials/{credential_id}` | credential 삭제 |
| PUT | `/credentials/order` | 사용자 정의 순서 변경 |

모두 인증이 필요하다. v1.0에 있던 `POST /cloud-accounts`(계정 단독 생성)는 없다 — 계정은 항상 첫 credential 등록 시점에 함께 생성된다(6.2절).

### 6.2 `POST /credentials/{provider}`

기존 v1.0의 `POST /cloud-accounts` + `POST /cloud-accounts/{id}/credentials` 2단계를 한 번의 호출로 합친 것이다. 저장과 실제 CSP 검증도 하나의 흐름으로 수행한다.

```json
{
  "external_account_id": "123456789012",
  "account_label": "운영 AWS",
  "name": "inventory-reader",
  "public_identifier": "AKIA...",
  "secret_payload": {
    "access_key_id": "AKIA...",
    "secret_access_key": "client-input-only",
    "session_token": null
  },
  "tags": {
    "environment": "production"
  },
  "display_order": 0
}
```

- 서버는 인증 사용자의 `(provider, external_account_id)`로 기존 `cloud_accounts` 행을 찾는다. 있으면 재사용, 없으면 새로 생성한다.
- AWS는 `secret_payload.auth_type`이 `assume_role`이면 장기 Access Key 대신 IAM Role 위임(`sts:AssumeRole`)으로 매 요청 임시 자격증명을 발급한다(2026-09-15 `solcho/be-assume-role`). `auth_type`이 없으면 레거시(`access_key`)로 간주한다 — 기존 데이터 호환.
- 서버는 payload를 AES-256-GCM으로 암호화하고 평문을 저장하지 않는다.
- 검증 성공 시 **201**과 `verified=true`인 `Credential`을 반환한다. **검증 실패해도 암호화 저장하고 `verified=false`로 반환한다**(사용자가 재시도/수정할 수 있어야 하므로 — 확정 정책).
- 같은 사용자의 같은 `(provider, external_account_id)`에 이미 계정이 있고 그 아래 같은 `name`의 credential이 있으면 `409 CREDENTIAL_ALREADY_EXISTS`.
- 오류에는 secret 또는 원본 SDK 응답을 넣지 않는다. 감사 action: `credential.create`, `credential.verify`(계정이 새로 생성된 경우 `cloud_account.create`도 함께 기록).

### 6.3 `GET /cloud-accounts`

확정 필터:

- `provider`: 다중 선택 가능
- `name`: `account_label`, credential 이름 대상 검색
- `tag`: 태그 query 형식 정책 확정 필요
- `verified`: 연결 credential의 검증 상태 기준

검색과 필터는 모두 교집합으로 적용한다.

### 6.4 `PATCH /cloud-accounts/{cloud_account_id}`

```json
{"account_label": "개발 AWS"}
```

`provider`, `external_account_id`, 소유자는 변경할 수 없다.

### 6.5 `DELETE /cloud-accounts/{cloud_account_id}`

파괴적 동작이므로 `X-Action-Confirmed: true`가 필수다. 연결 계정 삭제가 resource snapshot을 cascade 삭제하므로, 운영 정책상 보존·비활성화 방식이 확정되기 전에는 이 엔드포인트가 항상 `501 CLOUD_ACCOUNT_DELETE_NOT_IMPLEMENTED`를 반환한다(소유권 검사까지는 정상 수행).

### 6.6 `GET /cloud-accounts/{cloud_account_id}/credentials`

해당 계정 소유 credential 목록을 `display_order` 기준으로 반환한다.

### 6.7 `PATCH /credentials/{credential_id}`

비민감 metadata만 수정하는 예:

```json
{
  "name": "provisioner",
  "tags": {"environment": "production"},
  "display_order": 1
}
```

secret 교체 시 `secret_payload` 전체를 새로 받아 새 nonce로 다시 암호화한다. 부분 secret patch는 지원하지 않는다. secret이 교체되면 즉시 재검증하며(`X-Action-Confirmed: true` 필요) 성공·실패 저장 정책은 6.2절과 동일하다.

### 6.8 `POST /credentials/{credential_id}/verify`

body 없음. 암호화된 payload를 메모리에서 복호화해 provider API로 검증하고 결과만 저장한다.

**200**

```json
{
  "data": {
    "credential_id": "44",
    "verified": true,
    "verified_at": "2026-09-09T03:20:00Z",
    "permission_scope": {
      "inventory_read": true,
      "resource_control": true,
      "provision": false,
      "cost_read": true
    }
  }
}
```

`permission_scope.cost_read`는 **AWS만** 실제로 프로빙한다(Cost Explorer 조회 권한). Azure·GCP는 이번
비용 확장(§11)에서도 `cost_read`를 프로빙하지 않는다 — capability 판정(§11-4-1)은 이 값을 그대로 쓰지
않고 어댑터 구현 여부를 먼저 본다.

### 6.9 `DELETE /credentials/{credential_id}`

`X-Action-Confirmed: true`가 필수다. 진행 중 sync/provisioning job이 참조하면 `409 CREDENTIAL_IN_USE`로 거부한다. 과거 resource의 수집 FK는 `SET NULL`이며 snapshot은 보존한다. 감사 action: `credential.delete`.

### 6.10 `PUT /credentials/order`

```json
{
  "items": [
    {"credential_id": "44", "display_order": 0},
    {"credential_id": "45", "display_order": 1}
  ]
}
```

요청된 모든 credential이 인증 사용자 소유인지 한 transaction에서 검증한다.

## 7. 서비스 카탈로그

| Method | Path | 설명 |
|---|---|---|
| GET | `/service-catalog` | provider 서비스와 공통 분류 조회 |
| GET | `/provisioning/options` | 선택 계정 권한을 반영한 생성 가능 서비스 조회 |

v1.0의 `GET /resource-types`는 없다 — 리소스 유형 정규화 테이블(`resource_types`) 자체가 없기 때문이다.

### 7.1 `GET /service-catalog`

query:

- `provider`: `aws | azure | gcp`, 다중 선택
- `category`: 다중 선택
- `provisionable`: boolean

초기 provisionable 데이터는 Compute, DB_RDBMS, Storage_Object, CDN에 대한 3개 provider 조합 12개다.

### 7.2 `GET /provisioning/options`

query 예:

```text
/api/v1/provisioning/options?credential_id=44&credential_id=45
```

선택한 credential들의 실제 검증 상태와 `permission_scope`를 확인한다. 여러 계정을 선택하면 공통으로 허용되는 최소 권한 범위만 `enabled=true`로 반환하고, 권한 없는 옵션도 숨기지 않는다. 이 엔드포인트는 순수 조회(side effect 없음)이므로 화면에서 "이 계정 조합으로 무엇을 만들 수 있는지" 미리 보여주는 용도로만 쓴다 — 실제 생성은 10절의 계정별 개별 호출로 이루어진다.

```json
{
  "data": {
    "items": [
      {
        "service": {},
        "enabled": false,
        "disabled_reason_code": "INSUFFICIENT_COMMON_PERMISSION"
      }
    ]
  }
}
```

v1.0의 `resource_type` 필드는 `service`(ServiceCatalogItem)로 대체된다.

## 8. 인벤토리

### 8.1 엔드포인트 요약

| Method | Path | 설명 |
|---|---|---|
| GET | `/resources` | 저장된 최신 resource snapshot 목록 |
| GET | `/resources/summary` | 전체 수와 마지막 동기화 시각 |
| GET | `/resources/utilization/top` | CPU 사용률 상위 N개(실시간 CSP Monitoring 조회) |
| GET | `/resources/{resource_id}` | resource 상세 |
| POST | `/resources/{resource_id}/cli-access` | CLI 접속 정보 발급 |
| POST | `/resources/action` | 선택 리소스 일괄 시작·중지·삭제 요청 |

v1.0의 `POST /resource-actions`는 이 문서에서 `POST /resources/action`으로 이름이 바뀌었다(동작·계약은 8.6절 참고). **이 절은 비용 파트에서 변경하지 않는다** — 동기화가 `estimated_monthly_cost`를 더 많이 채우게 될 뿐이다.

### 8.2 `GET /resources`

DB에 저장된 최신 snapshot을 우선 반환하며 요청 시 CSP API를 직접 호출하지 않는다.

확정 query:

- `provider`: 다중 선택
- `service_catalog_id`: 다중 선택
- `cloud_account_id`: 다중 선택
- `status`: 다중 선택
- `region`: 다중 선택
- `tag`: 다중 선택 형식 정책 확정 필요
- `search_field`: `resource | service | region | account`
- `q`: 단일 검색어
- `include_stale`: 기본 `false`
- `include_deleted`: 기본 `false`

`q`와 모든 필터는 교집합으로 적용한다. `search_field=resource`는 이름과 `external_resource_id`를 대상으로 하며, CSP 원본 리소스 유형("CSP 원본 리소스 유형" 컬럼)은 `original_resource_type` 문자열에 대한 부분 일치로 검색한다.

최종 pagination과 정렬 query는 정책 확정 후 추가한다.

### 8.3 `GET /resources/summary`

현재 필터와 같은 query를 받을 수 있다.

```json
{
  "data": {
    "total_resources": 126,
    "active_resources": 120,
    "stale_resources": 6,
    "last_synced_at": "2026-09-09T02:00:00Z",
    "by_provider": [
      {"provider": "aws", "count": 70},
      {"provider": "azure", "count": 36},
      {"provider": "gcp", "count": 20}
    ]
  }
}
```

### 8.4 `GET /resources/{resource_id}`

인증 사용자 소유 resource만 반환한다. 인벤토리 상세 UI가 모달인지 별도 페이지인지는 프론트엔드 결정이며 API 경로에는 영향을 주지 않는다.

### 8.5 `POST /resources/{resource_id}/cli-access`

리소스에 CLI로 접속하기 위한 임시 자격증명·접속 정보를 발급한다(구현 완료, 비용 파트 변경 없음).

### 8.6 `POST /resources/action`

SDK가 담당하며 Terraform을 사용하지 않는다.

```json
{
  "action": "stop",
  "resource_ids": ["701", "702"]
}
```

`action`: `start | stop | delete`. 모든 동작에 `X-Action-Confirmed: true`가 필요하다.

서버는 실행 전 각 resource에 대해 다음을 검증한다.

1. 인증 사용자 소유인가
2. 해당 서비스 종류(`service.service_code`)를 다루는 서버 코드가 이 동작(start/stop/delete)을 실제로 지원하는가 — 서비스별 SDK 어댑터(`app/providers/{aws,azure,gcp}.py`)가 직접 분기한다
3. 사용할 수 있는 검증 credential과 provider 권한이 있는가
4. stale/deleted 상태가 동작을 허용하는가

하나라도 지원하지 않으면 해당 항목을 실행하지 않고 `UNSUPPORTED_OPERATION`으로 표시한다. **일괄 요청은 항목별 부분 성공**이다 — 리소스 하나가 실패해도 나머지는 계속 처리하고, 각 항목은 `success | rejected | failed` 중 하나로 결과에 남는다(확정 정책).

```json
{
  "data": {
    "action": "stop",
    "results": [
      {"resource_id": "701", "status": "success", "error": null},
      {
        "resource_id": "702",
        "status": "rejected",
        "error": {"code": "UNSUPPORTED_OPERATION"}
      }
    ]
  }
}
```

감사 action: `resource.start`, `resource.stop`, `resource.delete`.

## 9. 리소스 동기화

### 9.1 엔드포인트 요약

| Method | Path | 설명 |
|---|---|---|
| POST | `/sync-jobs` | 전체 또는 선택 계정 동기화 생성 |
| GET | `/sync-jobs` | 내 동기화 이력 |
| GET | `/sync-jobs/{sync_job_id}` | job과 계정별 상태 조회 |
| POST | `/sync-jobs/{sync_job_id}/cancel` | 취소 요청 |

v1.0과 변경 없음 — `resource_types`/`provisioning_requests` 제거는 동기화 구조에 영향을 주지 않는다. **비용 파트도 이 절을 변경하지 않는다** — 신규 비용 수집(§11-5)은 계정당 run 1행짜리 별도 API(`/cost-ingestion-runs`)를 쓰고 이 절의 `/sync-jobs`와는 구조가 다르다(§11-5 참고).

### 9.2 `POST /sync-jobs`

```json
{
  "cloud_account_ids": null
}
```

- `null` 또는 생략: 인증 사용자의 검증 완료 연결 계정 전체
- 배열: 지정한 내 연결 계정만
- 계정마다 사용할 검증 credential은 서비스가 결정하며 item에 `credential_id`를 기록한다.
- 같은 사용자에게 `pending|running` job이 이미 있으면 `409 JOB_ALREADY_RUNNING`으로 거부한다.

**202**

```json
{
  "data": {
    "id": "901",
    "status": "pending",
    "requested_at": "2026-09-09T04:00:00Z",
    "status_url": "/api/v1/sync-jobs/901"
  }
}
```

### 9.3 `GET /sync-jobs/{sync_job_id}`

```json
{
  "data": {
    "id": "901",
    "status": "running",
    "requested_at": "2026-09-09T04:00:00Z",
    "started_at": "2026-09-09T04:00:01Z",
    "finished_at": null,
    "provider_summary": [
      {"provider": "aws", "status": "success", "completed": 1, "total": 1},
      {"provider": "azure", "status": "running", "completed": 0, "total": 1},
      {"provider": "gcp", "status": "pending", "completed": 0, "total": 1}
    ],
    "items": [
      {
        "id": "1001",
        "cloud_account_id": "31",
        "credential_id": "44",
        "provider": "aws",
        "status": "success",
        "resources_discovered": 70,
        "resources_created": 3,
        "resources_updated": 65,
        "resources_marked_stale": 2,
        "error": null,
        "started_at": "2026-09-09T04:00:01Z",
        "finished_at": "2026-09-09T04:00:25Z"
      }
    ]
  }
}
```

item 오류는 정제된 `code`, `message`만 반환한다. 전체 job status는 item 상태를 집계해 `pending | running | success | partial_success | failed | cancelled` 중 하나로 저장한다. 이번 실행에서 보지 못한 resource는 물리 삭제하지 않고 `is_stale=true`로만 표시한다(`deleted_at`은 건드리지 않는다 — 실제 삭제는 `POST /resources/action`을 통해서만 일어난다).

### 9.4 `POST /sync-jobs/{sync_job_id}/cancel`

`pending|running`에서만 허용한다. best-effort이며 이미 시작된 항목 하나는 끝까지 진행된다. **202** 반환.

## 10. 프로비저닝

Terraform은 생성만 담당한다. 기존 리소스의 조회·시작·중지·삭제에는 사용하지 않는다.

### 10.1 엔드포인트 요약

| Method | Path | 설명 |
|---|---|---|
| POST | `/provisioning/price-comparisons` | 유사 사양 가격 비교 |
| POST | `/provisioning/{provider}/{service}` | 계정(credential) 하나·서비스 하나에 대한 생성 job 생성 |
| GET | `/provisioning/jobs` | 내 프로비저닝 job 이력 |
| GET | `/provisioning/jobs/{job_id}` | job 진행 조회 |
| POST | `/provisioning/jobs/{job_id}/cancel` | 취소 요청 |

v1.0의 `POST /provisioning/requests`(여러 계정·서비스를 `targets[]`로 한 요청에 묶는 방식)와 `provisioning_requests` 부모 개념은 없다. 여러 계정이나 여러 플랫폼에 동시에 생성하고 싶으면 클라이언트가 이 엔드포인트를 필요한 조합 수만큼 병렬로 호출한다.

`provisioning_jobs` 테이블은 부모 없이 `user_id`/`credential_id`/`service_catalog_id`를 직접 참조한다.

> **제안(설계만, 미구현) — 예산 게이트**: 비용 확장(§11-7-3)이 이 엔드포인트에 `X-Budget-Override`
> 헤더 처리와 예산 초과 검사를 추가하는 것을 제안했으나, **2026-09-18 ADR-042로 이번 라운드 범위에서
> 뺐다.** 이 절의 나머지 계약은 `v1.1`과 동일하며 아무것도 바뀌지 않았다.

### 10.2 `POST /provisioning/price-comparisons`

```json
{
  "service_catalog_id": "1",
  "common_spec": {
    "region_group": "northeast_asia",
    "vcpu": 2,
    "memory_gib": 8,
    "usage_hours_per_month": 730
  },
  "providers": ["aws", "azure", "gcp"]
}
```

**200**

```json
{
  "data": {
    "currency": "USD",
    "as_of": "2026-09-09T04:10:00Z",
    "items": [
      {
        "provider": "aws",
        "service_code": "ec2",
        "sku": "example-sku",
        "estimated_monthly_cost": "52.340000",
        "cost_kind": "list_price_estimate",
        "source": "provider_price_catalog",
        "assumptions": ["730 hours/month"]
      }
    ]
  }
}
```

서비스 매핑과 provider별 필수 spec이 확정된 뒤 실제 schema를 고정한다. 비교값은 청구액이 아니라 추정치임을 명시한다. **이 계약은 비용 확장에서도 바꾸지 않는다** — 월간 정가 차이만 내고 당월 영향은 산출하지 않으며, 미지원 조합은 `estimated_monthly_cost: null`(0으로 채우지 않는다).

### 10.3 `POST /provisioning/{provider}/{service}`

`provider`는 `aws|azure|gcp`, `service`는 `service_catalog.service_code`(예: `ec2`, `rds`, `s3`, `cloudfront`)다. 서버는 이 조합으로 `service_catalog` 행을 조회하고, 없거나 `provisionable=false`면 `404 SERVICE_NOT_FOUND` 또는 `422 RESOURCE_NOT_PROVISIONABLE`을 반환한다.

필수 헤더: `Idempotency-Key`, `X-Action-Confirmed: true`.

```json
{
  "credential_id": "44",
  "common_spec": {
    "name": "web-01",
    "vcpu": 2,
    "memory_gib": 8
  },
  "provider_spec": {
    "region": "ap-northeast-2",
    "instance_type": "t3.large"
  }
}
```

`credential_id`는 정확히 하나만 받는다. `common_spec`과 `provider_spec`에는 secret을 허용하지 않는다. secret 필드가 감지되면 `422 SECRET_FIELD_NOT_ALLOWED`로 거부한다(Azure VM `admin_password`처럼 "생성될 리소스 자체의 OS 접속 정보"는 예외로 허용하고 `spec_json`에는 남기지 않는다 — 러너의 `SENSITIVE_PROVIDER_SPEC_FIELDS`).

**202**

```json
{
  "data": {
    "id": "1301",
    "status": "queued",
    "created_at": "2026-09-09T04:20:00Z",
    "status_url": "/api/v1/provisioning/jobs/1301"
  }
}
```

감사 action: `provisioning.request`.

### 10.4 `GET /provisioning/jobs`

내 프로비저닝 job 이력을 반환한다. 확정 query: `provider`, `service_catalog_id`, `status`, `credential_id`.

### 10.5 `GET /provisioning/jobs/{job_id}`

```json
{
  "data": {
    "id": "1301",
    "credential_id": "44",
    "service_catalog_id": "1",
    "workspace_name": "user-12-job-1301",
    "common_spec": {
      "name": "web-01",
      "vcpu": 2,
      "memory_gib": 8
    },
    "provider_spec": {
      "region": "ap-northeast-2",
      "instance_type": "t3.large"
    },
    "status": "running",
    "progress_percent": 62,
    "created_resource_count": 0,
    "result": null,
    "error": null,
    "created_at": "2026-09-09T04:20:00Z",
    "started_at": "2026-09-09T04:20:01Z",
    "finished_at": null
  }
}
```

- `terraform_state_ref`는 내부 참조이므로 외부 응답에 반환하지 않는다.
- `spec_json`(common_spec+provider_spec)과 `result_json`은 허용 목록으로 직렬화하며 민감 output을 제거한다.
- `success|failed|cancelled` job의 progress는 각각 정책에 맞게 종결하며 `success`만 반드시 100이다.
- 완료 시 알림을 만들고 감사 action `provisioning.complete`를 기록한다.

### 10.6 `POST /provisioning/jobs/{job_id}/cancel`

`queued|running`에서만 허용한다. **202** 반환.

## 11. 비용

> ⚠️ **이 절 전체가 제안이다 — 팀 승인 전이며 구현 완료를 뜻하지 않는다.**
> 근거: `docs/비용_개발문서/`(01·02·03·05·06, 2026-09-17/18, 이승현 작성). 신규 테이블 6개가
> 선행돼야 한다(`docs/DB_ERD_v1.2.md` Part B). 이 절이 팀 승인을 받으면 §11 본문을 그대로 두고 이
> 배너만 지운다.

### 11-0. 이 절을 읽는 법

- **§11-1 공통 규약**부터 실제 계약이다. 구현 시 여기서부터 읽으면 된다.
- 개수 표기는 전부 **"추가 제안 N개"**다. "확정 N개"나 "구현 완료"로 쓰지 않는다.
- 경로는 설계 문서에서 계획한 것만 적었다 — 실제 라우터가 아직 없다.

### 11-1. 공통 규약

#### 11-1-1. 기존 명세를 그대로 쓴다

Base path·성공/오류 envelope·인증·필드명 규칙(§2)은 전부 그대로 따른다. 이 절에서 추가하는 것만 아래에 적는다.

| 항목 | 값 |
|---|---|
| 금액 | 문자열, 소수 6자리 고정 — `"128.400000"` |
| 날짜 | `YYYY-MM-DD`. `period_end`는 **제외 경계** |
| 시각 | ISO 8601 UTC |

#### 11-1-2. ⚠️ CSP 호출 경계 — 이 절에서 가장 중요한 규칙

> **CSP API를 호출하는 곳은 수집 실행(`POST /cost-ingestion-runs`와 자동 스케줄) 한 곳뿐이다.**
> 비용 조회 6종·capability 판정·급증 탐지·보고서 부품은 **전부 DB만 읽는다.**

- AWS Cost Explorer는 **요청당 $0.01**이다. 조회 API가 CSP를 부르면 화면을 새로 고칠 때마다 과금된다.
- 구현 시 `app/routers/costs.py`에 `boto3`·`azure`·`google` import가 있으면 잘못된 것이다 — 어댑터는 `app/cost/providers/`에만 있고 수집기만 그것을 부른다.
- capability 판정도 프로빙 호출을 하지 않는다 — 이미 DB에 있는 값으로만 판정한다(§11-4-1).

#### 11-1-3. 필수 3필드와 지연 임계

계정 단위 항목을 담는 **모든 응답**은 다음을 포함한다.

| 필드 | 타입 | 뜻 |
|---|---|---|
| `status` | string | 아래 §11-1-6의 9종 중 하나 |
| `as_of` | ISO 8601 UTC \| `null` | 이 금액의 수집 시각 |
| `ingestion_running` | boolean | 그 계정에 `cost_ingestion_runs.status='running'` 행이 있는가 |

응답의 `data` 최상위에는 다음을 넣는다.

| 필드 | 타입 | 뜻 |
|---|---|---|
| `staleness_threshold_hours` | integer | 지연 판정 임계. `app/config.py`의 `cost_stale_after_hours`(기본 36) |

프론트는 이 임계값을 하드코딩하지 않고 응답에서 받아 쓴다.

#### 11-1-4. 소유권 검사 — §15 표에 추가되는 5줄 (§15 참고)

| 대상 | 소유권 경로 |
|---|---|
| team | `teams.user_id = current_user.id` |
| team budget | `team_budgets.team_id -> teams.user_id` |
| account cost | `cloud_account_costs.cloud_account_id -> cloud_accounts.user_id` |
| ingestion run | `cost_ingestion_runs.user_id` |
| cost review item | `cost_review_items.user_id` |

타 사용자 소유 ID는 §15대로 `404`로 응답한다.

#### 11-1-5. 오류 코드

기존 §17 오류 코드를 우선 재사용한다: `JOB_ALREADY_RUNNING`(409)·`JOB_NOT_CANCELLABLE`(409)·
`RATE_LIMITED`(429)·`VALIDATION_ERROR`(422)·`CONFIRMATION_REQUIRED`(428)·`CONFLICT`(409)·
`FORBIDDEN`(403)·`CLOUD_ACCOUNT_NOT_FOUND`(404)·`AGENT_NOT_CONFIGURED`(503)·
`IDEMPOTENCY_KEY_REUSED`(409). 새로 필요한 것 3개는 §17에 이미 있는 것으로 채택 제안한다:
`COST_DATA_UNAVAILABLE`(404, 단건 조회 대상 없음 — 목록·요약은 오류가 아니라 `status`로 표현),
`COST_PERMISSION_REQUIRED`(403), `CURRENCY_MISMATCH`(409, 합산·예산 대비를 강제 요청했는데 통화가
섞였을 때). **추가 제안 9개**는 §17을 참고한다 — 채택 전에는 쓰지 않는다.

#### 11-1-6. 상태값 9종 (모든 화면·API가 공유)

`CONNECTED_OK` · `CONNECTED_EMPTY` · `CONNECTED_PARTIAL` · `NOT_CONNECTED` · `PENDING` ·
`SETUP_REQUIRED` · `PERMISSION_DENIED` · `COLLECT_FAILED` · `UNSUPPORTED`. 여기 없는 값을 코드에
쓰지 않는다.

#### 11-1-7. 신규 라우터 파일 (제안)

| 파일 | 담는 것 |
|---|---|
| `backend/app/routers/costs.py` | `/costs/*` 6종 · `/cost-ingestion-runs` 3종 |
| `backend/app/routers/teams.py` | `/teams/*` · `/team-budgets/*` |
| `backend/app/routers/cost_review.py` | `/cost-anomalies` · `/cost-review-items/*` |
| `backend/app/routers/cost_reports.py` | `/cost-reports/*` |

### 11-2. 공통 query (조회 6종 공통)

| 이름 | 타입 | 필수 | 기본값 | 허용 |
|---|---|---|---|---|
| `period_start` | date | N | 이번 달 1일 | — |
| `period_end` | date | N | 오늘+1 | 제외 경계. `period_start`보다 커야 함 |
| `provider` | string 다중 | N | 전체 | `aws` `azure` `gcp` |
| `cloud_account_id` | string 다중 | N | 전체 | 소유 계정만 |
| `team_id` | string 다중 | N | 전체 | `unassigned`로 미배정 지정 |
| `currency` | string | N | 없음 = 전체 | ISO 4217. '청구 통화' 필터 |
| `charge_category` | string 다중 | N | `usage` | `usage` `credit` `refund` `tax` `other` |
| `display_currency` | string | N | 없음 = 원통화 | ISO 4217. **환율 변환 — 이번 라운드에는 없다**(§11-9-1) |

모든 필터는 교집합이다. `period_end - period_start > 366일`이면 `422 VALIDATION_ERROR`. 전부 DB만 읽는다.

### 11-3. `GET /costs/capabilities`

이 CSP에서 비용을 볼 수 있나 — 미구현과 권한 없음을 구분하는 것이 존재 이유다. CSP를 호출하지 않는다.

**판정 순서**: ① 어댑터가 그 provider의 수집을 구현했는가(코드 상수) → 아니면 `UNSUPPORTED`(판정 끝).
② 그 계정의 `credentials.verified` → `false`면 `NOT_CONNECTED`. ③ `cost_ingestion_runs`의 그 계정
마지막 행의 `status`/`error_code` → 아래 매핑.

| 마지막 run | `error_code` | `status` |
|---|---|---|
| (행 없음) | — | `PENDING` |
| `success` | — | 행 있으면 `CONNECTED_OK`, 0건이면 `CONNECTED_EMPTY` |
| `partial_success` | — | `CONNECTED_PARTIAL` |
| `failed` | `CLOUD_PERMISSION_DENIED` | `PERMISSION_DENIED` |
| `failed` | `COST_SETUP_REQUIRED` | `SETUP_REQUIRED` |
| `failed` | `PROVIDER_RATE_LIMITED` | `COLLECT_FAILED`(`PERMISSION_DENIED`로 분류하지 않는다) |
| `failed` | 그 외 전부 | `COLLECT_FAILED` |
| `running` \| `pending` | — | 이전 상태 유지 + `ingestion_running: true` |

**200**

```json
{
  "data": {
    "staleness_threshold_hours": 36,
    "items": [
      { "cloud_account_id": "31", "provider": "aws", "external_account_id": "123456789012",
        "account_label": "운영 AWS", "team_id": "7", "status": "CONNECTED_OK",
        "as_of": "2026-09-17T06:00:00Z", "ingestion_running": false, "cost_read": true,
        "capability_source": "probed", "currency": "USD", "setup_hint": null, "last_error_code": null },
      { "cloud_account_id": "32", "provider": "gcp", "external_account_id": "demo-project",
        "account_label": "개발 GCP", "team_id": null, "status": "UNSUPPORTED",
        "as_of": null, "ingestion_running": false, "cost_read": null,
        "capability_source": "not_implemented", "currency": null,
        "setup_hint": "GCP 비용 수집은 아직 구현되지 않았습니다.", "last_error_code": null }
    ],
    "total": 2, "pagination": null
  }
}
```

`capability_source`: `probed`(등록·재검증 때 실제 프로빙됨) \| `not_implemented`(아직 어댑터 없음) \|
`declared`(사용자 자기신고). **`cost_read`가 `null`이면 `status`는 반드시 `UNSUPPORTED`다.** `false`는
"권한이 없음을 확인했다"는 뜻이라 미구현과 구분한다.

### 11-4. `GET /costs/summary`

**200**

```json
{
  "data": {
    "period": { "start": "2026-09-01", "end": "2026-09-18", "display": "2026-09-01 ~ 2026-09-17" },
    "as_of": "2026-09-17T06:00:00Z",
    "staleness_threshold_hours": 36,
    "kpis": {
      "mtd_actual": [ { "cost_kind": "actual", "currency": "USD", "amount": "128.400000",
                        "is_estimated": true, "basis": "usage_before_credits" } ],
      "mtd_net": [ { "cost_kind": "actual", "currency": "USD", "amount": "98.400000", "is_estimated": true } ],
      "list_price_monthly": [ { "cost_kind": "list_price_estimate", "currency": "USD", "amount": "204.700000",
                                 "assumptions": ["730 hours/month"], "missing_count": 3 } ],
      "forecast_month_end": [ { "cost_kind": "actual", "currency": "USD", "amount": "241.900000",
                                 "method": "mtd_prorated", "based_through": "2026-09-16" } ],
      "forecast_status": { "state": "computed", "based_through": "2026-09-16",
                            "required_accounts": 2, "incomplete_accounts": [] }
    },
    "accounts": [
      { "cloud_account_id": "31", "provider": "aws", "account_label": "운영 AWS", "team_id": "7",
        "status": "CONNECTED_OK", "as_of": "2026-09-17T06:00:00Z", "ingestion_running": false,
        "currency": "USD", "actual": "128.400000", "list_price_estimate": "160.200000",
        "resource_count": 12, "resources_synced_at": "2026-09-17T05:12:00Z", "is_estimated": true,
        "filter_excluded": null,
        "coverage": { "start": "2026-09-01", "end": "2026-09-17", "days": 17, "covered": 15,
                      "missing_count": 2, "missing_days": ["2026-09-14", "2026-09-15"],
                      "truncated": false, "pending_days": 1 } },
      { "cloud_account_id": "33", "provider": "azure", "account_label": "개발 Azure", "team_id": null,
        "status": "UNSUPPORTED", "as_of": null, "ingestion_running": false, "currency": null,
        "actual": null, "list_price_estimate": "44.500000", "resource_count": 3,
        "resources_synced_at": "2026-09-17T05:12:00Z", "is_estimated": false,
        "filter_excluded": null, "coverage": null }
    ],
    "excluded": { "accounts": 1, "reason_counts": { "UNSUPPORTED": 1 } },
    "warnings": [ { "code": "PARTIAL_PERIOD",
                     "message": "2026-09-14 ~ 2026-09-15 구간이 수집되지 않았습니다.",
                     "missing_days": ["2026-09-14", "2026-09-15"],
                     "missing_count": 2,
                     "accounts": [ { "cloud_account_id": "31", "missing_count": 2 } ] } ]
  }
}
```

**계약**

- 모든 금액 필드는 배열 또는 `null`이다 — 통화가 섞이면 줄이 늘기 때문에 하나로 강제 합치지 않는다.
- `actual`과 `list_price_estimate`는 별도 필드다. 서버가 더해서 내려주지 않는다.
- `mtd_actual.basis`는 `usage_before_credits` 고정. `mtd_net`은 크레딧·환불까지 반영한 순액이며 참고용이다.
- `list_price_monthly.missing_count`는 정가표에 없어 추정하지 못한 리소스 수다 — 0으로 세지 않고 개수로 보고한다.
- `forecast_month_end.method`는 `mtd_prorated` 고정, `based_through`는 어제 날짜다(오늘은 미완성 구간).
  계산식은 이번 달 실제로 수집된 날짜의 누적 실측 ÷ 오늘까지의 달력 경과일수 × 이달 총일수다.
  수집되지 않은 날은 0원으로 채우지 않고 합계에서 뺀다. 대상 계정 일부가 이달 수집을 빠짐없이
  마치지 못했어도 계산 자체는 막지 않는다(2026-09-22 결정) — `kpis.forecast_status.incomplete_accounts`가
  어느 계정에서 며칠이 빠졌는지 안내만 한다.
- `excluded`는 합계에서 빠진 계정 수와 사유다. 조용히 빼지 않는다. `reason_counts`는 **계정당 한 번만**
  세며 우선순위는 `UNSUPPORTED` > `CURRENCY_FILTERED` > 상태/`PERIOD_NOT_COVERED`다(같은 계정이 두 사유로
  두 번 세어지지 않는다).
- 데이터가 전혀 없어도 `200`이다. `COST_DATA_UNAVAILABLE`을 던지지 않고 `status`로 표현한다.

**2026-09-18 추가 필드 (구현됨 — 화면이 "0원"과 "모름"을 구분하는 근거, PR #118)**

- `accounts[].coverage` — 조회 기간에서 **수집이 확인된 날**을 센다. `null`이면 이 기간에 판정 대상이
  아니라는 뜻이다(미지원 계정 등). 확인 기준은 "그 날 행이 있거나, 성공한 수집 run의 범위에 든 날"이다
  — $0인 날은 행이 생기지 않으므로 행 유무만 보면 정상 0원 계정이 영원히 결측으로 보인다.
  - `days` 조회 기간의 판정 대상 일수 · `covered` 확인된 날 수 · `missing_count` **정확한 결측 일수**
  - `missing_days` 결측일 목록이지만 **최대 31개까지만** 담기고 잘리면 `truncated: true`다 — 개수는
    반드시 `missing_count`를 쓴다.
  - `pending_days` 오늘·미래처럼 아직 끝나지 않아 결측으로 세지 않는 날 수.
  - 날짜 경계는 **UTC**다(§11-1). 서버 로컬 시각이 아니다.
- `accounts[].filter_excluded` — `"currency"`이면 **통화 조건 때문에** 합계에서 빠졌다는 뜻이다(수집
  문제가 아니다). 그 밖에는 `null`. 계정을 목록에서 지우지 않는다.
- `kpis.forecast_status` — 전망을 **왜 냈는지/못 냈는지**를 응답 전체 단위로 알린다(통화별이 아니다).
  - `state`: `computed` \| `not_current_month` \| `first_day` \| `no_accounts` \| `insufficient_coverage`
    \| `currency_unknown`. 화면은 모르는 값이 와도 "이 조건에서는 전망을 내지 않습니다"로 표시한다.
  - `based_through` 근거 마지막 날 · `required_accounts` 전망 대상 계정 수 ·
    `incomplete_accounts[] {cloud_account_id, missing_count}` 이달 수집이 빠진 계정.
  - ⚠️ **`insufficient_coverage`의 의미가 2026-09-22(#128)에 바뀌었다** — 아래 "전망 계약" 참고.
- `warnings[PARTIAL_PERIOD]`에 `missing_count`(정확한 개수)와 `accounts[]`(계정별 결측 수)가 있다.
- 전망 창: `period_start`가 이달 1일이고 `period_end`가 **UTC 오늘 또는 오늘+1**일 때만 계산한다
  (화면이 "오늘까지"를 exclusive 경계로 보내는 경우와 inclusive로 보내는 경우를 모두 받아들인다).

> ⚠️ **전망 계약 상충 — 확인 필요(2026-09-23, 이승현)**
> 위 "계산 자체를 막지 않는다"(2026-09-22, #128)는 **1단계에서 승인받은 결정과 반대**다. 원래 계약은
> "대상 계정 전부가 이달 1일~어제를 빠짐없이 수집 확인했을 때만 계산하고, 하나라도 빠지면
> `insufficient_coverage`로 값을 내지 않는다"였다(`docs/비용_개발문서/03` §5 · `10` QA-08 ⑤
> "미수집일을 0으로 평균 내지 않는다"). 지금 구현은 분모를 **달력 경과일**로 쓰므로, 수집이 빠진 날이
> 있으면 전망이 **실제보다 낮게** 나온다(빠진 날의 비용이 분자에서만 빠지고 분모에는 남는다).
> 어느 쪽을 최종 계약으로 할지 정해야 하며, 정할 때까지 이 값은 **참고치**로 본다.

### 11-5. `GET /costs/trend`

추가 query: `granularity` = `daily`(기본) \| `weekly` \| `monthly` \| `quarterly` · `group_by` =
`provider`(기본) \| `account` \| `team` \| `service` · `include_budget_line` = `false`(기본).

- `weekly`·`quarterly`는 보고서 주기 4종(일간 7일/주간 5주/월간 6개월/반기 4반기)을 위해 서버가 맡는다 — 보고서 쪽이 `daily`를 합치게 하면 주·분기 경계가 두 곳에서 갈릴 수 있다.
- 주 시작 요일 — **미결**, 임시 기본값 월요일(ISO 8601), `app/cost/query.py`의 `WEEK_START`.
- 분기는 달력 기준(1/1·4/1·7/1·10/1)이다.

**200**

```json
{
  "data": {
    "granularity": "daily", "group_by": "provider", "currency": "USD",
    "currency_selection": { "reason": "largest_share", "excluded": [ { "currency": "KRW", "account_count": 1 } ] },
    "staleness_threshold_hours": 36,
    "series": [
      { "key": "aws", "label": "AWS", "cost_kind": "actual",
        "points": [
          { "period_start": "2026-09-15", "period_end": "2026-09-16", "amount": "8.120000", "is_estimated": false },
          { "period_start": "2026-09-16", "period_end": "2026-09-17", "amount": "9.340000", "is_estimated": true }
        ] },
      { "key": "gcp", "label": "GCP", "cost_kind": "actual", "points": [], "omitted_reason": "UNSUPPORTED" }
    ],
    "budget_line": null, "missing_days": ["2026-09-14"]
  }
}
```

**계약**

- 통화를 하나만 그린다. 기본값은 선택 범위에서 금액 비중이 가장 큰 통화다(`currency` query로 변경 가능). `currency_selection.reason`: `largest_share` \| `requested` \| `only_one`. USD를 하드코딩하지 않는다.
- 빠진 날을 0으로 채우지 않는다 — `points`에 없고 `missing_days`에 들어간다(화면은 선을 끊는다).
- 데이터 없는 계열은 `points: []` + `omitted_reason`(상태값 9종 중 하나).
- y축은 하나다 — 이중축은 없는 상관관계를 만든다.

### 11-6. `GET /costs/breakdown`

추가 query: `dimension` = `provider` \| `service` \| `category` \| `account` \| `team` \| `tag` ·
`top_n`(기본 6, 최대 20) · `tag_key`(dimension=tag일 때 필수).

- `category`는 보고서 도넛(§11-7-4)이 쓴다 — `Compute`/`Database`/`Storage`/`Network`/`기타` 5종. `service_catalog.category`로 묶는다. CSP 서비스 이름과 `service_catalog`가 1:1이 아니므로(CE는 `AmazonEC2`, 카탈로그는 `ec2`) 매핑표를 `app/cost/query.py` 한 곳에만 둔다.
- 매핑에 없는 서비스는 `기타`가 아니라 `unallocated`로 보낸다. `기타`는 상위 N 초과분, `unallocated`는 분류 규칙 자체가 없는 것이다.

**200**

```json
{
  "data": {
    "dimension": "service", "currency": "USD",
    "currency_selection": { "reason": "largest_share", "excluded": [] },
    "cost_kind": "actual", "total": "128.400000",
    "items": [
      { "key": "AmazonEC2", "label": "EC2", "amount": "74.200000", "share_pct": "57.8" },
      { "key": "AmazonRDS", "label": "RDS", "amount": "31.100000", "share_pct": "24.2" }
    ],
    "rest": { "label": "기타(4종)", "amount": "23.100000", "count": 4, "share_pct": "18.0" },
    "unallocated": { "amount": "0.000000", "reason": null },
    "estimate_unavailable_count": 0
  }
}
```

**계약**

- `items` 합 + `rest` + `unallocated` = `total`이 반드시 성립한다.
- `share_pct`는 `charge_category='usage'` 기준으로 계산한다(음수가 섞인 순액으로 내면 100%를 넘거나 음수 조각이 생긴다).
- `dimension=tag`는 이번 범위에서 `501`을 반환한다 — 태그 배분은 설계만.

### 11-7. `GET /costs/changes`

추가 query: `compare` = `previous_period`(기본) \| `previous_month` · `dimension` = `service`(기본) \| `account` \| `provider` · `top_n`(기본 10).

**200**

```json
{
  "data": {
    "current": { "start": "2026-09-01", "end": "2026-09-18", "days": 17 },
    "previous": { "start": "2026-08-01", "end": "2026-08-18", "days": 17 },
    "comparable": true, "currency": "USD",
    "totals": { "current": "128.400000", "previous": "116.100000", "delta": "12.300000", "delta_pct": "10.6" },
    "increases": [ { "key": "AmazonRDS", "label": "RDS", "current": "31.100000", "previous": "12.400000",
                       "delta": "18.700000", "delta_pct": "150.8", "is_new": false } ],
    "decreases": [],
    "new_items": [ { "key": "AmazonS3", "label": "S3", "current": "4.200000", "previous": null } ]
  }
}
```

- 당월 17일까지와 전월 전체를 비교하지 않는다 — 서버가 `previous`를 같은 길이로 잘라 준다. 자를 수 없으면 `comparable: false`이고 화면은 비교를 표시하지 않는다.
- 새로 생긴 항목은 `increases`가 아니라 `new_items`로 분리한다.
- `previous`가 0 이하이면 `delta_pct`는 `null`이다.

**2026-09-18 추가 (구현됨, PR #118)**

- query에 `currency`를 받는다(조회 6종 공통 필터와 같은 의미).
- `comparability` — `comparable`이 `false`인 **이유**를 담는다. 화면이 "왜 비교를 못 하는지"를 말할 수
  있어야 하기 때문이다. `comparable`은 `reasons`가 빈 배열인 것과 같은 뜻이다.

```json
"comparability": {
  "same_length": true, "completed_period": true, "current_covered": true, "previous_covered": false,
  "currency": "USD", "charge_category": "usage",
  "reasons": ["PREVIOUS_COVERAGE"],
  "accounts": [ { "cloud_account_id": "31", "current_missing_count": 0, "previous_missing_count": 21 } ]
}
```

  - `reasons` 값: `LENGTH_MISMATCH`(일수가 다름) · `INCOMPLETE_PERIOD`(아직 끝나지 않은 날 포함) ·
    `NO_ACCOUNTS` · `CURRENT_COVERAGE`(조회 기간 결측) · `PREVIOUS_COVERAGE`(이전 기간 결측) ·
    `NO_CURRENCY`(비교할 통화 없음). 화면은 모르는 값이 와도 일반 문구로 표시한다.
  - `charge_category`는 `usage` 고정이다 — 비교는 요금 분류 필터를 따르지 않는다.
- 서비스가 지정되지 않은 금액은 키 `__unallocated__`(라벨 `미분류`)로 내려간다. 합계에 포함되며,
  상위 N을 넘겨 묶인 `기타`와는 다른 것이다.

### 11-8. `GET /costs/collection-status`

**200**

```json
{
  "data": {
    "staleness_threshold_hours": 36,
    "items": [
      { "cloud_account_id": "31", "provider": "aws", "status": "CONNECTED_OK",
        "as_of": "2026-09-17T06:00:00Z", "ingestion_running": false,
        "last_success_at": "2026-09-17T06:00:00Z", "last_attempt_at": "2026-09-17T06:00:00Z",
        "last_error_code": null, "next_manual_allowed_at": "2026-09-17T07:00:00Z",
        "covered_through": "2026-09-16", "missing_days": [] }
    ],
    "total": 1, "pagination": null
  }
}
```

화면 상단 수집 불가 배너와 '비용 새로고침' 버튼의 활성/비활성이 이 응답만 보고 결정된다.
`next_manual_allowed_at`이 미래면 버튼을 비활성화한다(계정당 1시간 1회).

**2026-09-18 추가 (구현됨, PR #118)**: `period_start`·`period_end`·`currency` query를 받고, 각 항목에
`coverage`(§11-4와 같은 모양)를 함께 내려준다 — 기간을 바꿀 때마다 summary와 다른 기준으로 결측을 세면
화면의 두 블록이 서로 다른 숫자를 말하게 되기 때문이다.

### 11-9. 수집 실행 API (제안 3개)

이름과 상태 전이는 기존 `/sync-jobs`(§9·§16.3)를 그대로 따르되, **계정당 run 1행**이다(부모/자식
2단 구조 아님).

| Method | Path | 설명 |
|---|---|---|
| POST | `/cost-ingestion-runs` | 수동 수집 실행 (202) |
| GET | `/cost-ingestion-runs` | 실행 이력 |
| GET | `/cost-ingestion-runs/{run_id}` | 실행 상태 |

`X-Action-Confirmed`를 요구하지 않는다 — CSP를 읽기만 하고 과금 리소스를 만들지 않는다.

**요청**

```json
{ "cloud_account_ids": ["31", "33"], "period_start": "2026-09-01", "period_end": "2026-09-18" }
```

**202**

```json
{
  "data": {
    "items": [ { "id": "5001", "cloud_account_id": "31", "status": "pending",
                 "status_url": "/api/v1/cost-ingestion-runs/5001" } ],
    "skipped": [ { "cloud_account_id": "33", "reason_code": "RATE_LIMITED",
                    "next_allowed_at": "2026-09-17T07:00:00Z" } ]
  }
}
```

| 상황 | 응답 |
|---|---|
| 일부 계정만 실행 중/제한/설정 미완/미구현 | `202` + `skipped[]`에 사유 |
| **전부** 실행 중 | `409 JOB_ALREADY_RUNNING` |
| **전부** 1시간 제한 | `429 RATE_LIMITED` |

중복 실행은 PostgreSQL advisory lock으로 막는다(`06_DB변경.md` R3 참고) — 상태 컬럼으로 막으면
프로세스가 죽었을 때 `running`으로 굳는다.

`GET /cost-ingestion-runs/{run_id}` 응답의 `status`는 `pending | running | success | partial_success |
failed | cancelled`(§16.3과 동일 6종). `api_calls`를 반드시 기록한다(Cost Explorer 요청당 $0.01).
실패해도 기존 데이터는 남는다(전체 수신 후 한 트랜잭션 교체).

### 11-10. 팀·예산 API (제안 9개)

#### 11-10-1. 팀

| Method | Path | 설명 |
|---|---|---|
| GET | `/teams` | 팀 목록 (+미배정 줄) |
| POST | `/teams` | 팀 생성 |
| PATCH | `/teams/{team_id}` | 이름·통화 변경 |
| DELETE | `/teams/{team_id}` | 삭제 (`X-Action-Confirmed` 필요) |
| PUT | `/teams/{team_id}/accounts` | 소속 계정 일괄 지정 |

**`GET /teams` — 200**

```json
{
  "data": {
    "items": [
      { "id": "7", "name": "운영팀", "currency": "USD", "account_count": 2,
        "accounts": [ { "cloud_account_id": "31", "provider": "aws", "account_label": "운영 AWS",
                        "currency": "USD", "currency_matches_team": true } ],
        "active_budget": { "id": "11", "period_type": "monthly", "limit_amount": "300.000000", "currency": "USD" },
        "created_at": "2026-09-17T00:00:00Z" }
    ],
    "unassigned": { "account_count": 1,
      "accounts": [ { "cloud_account_id": "33", "provider": "azure", "account_label": "개발 Azure",
                      "currency": null, "currency_matches_team": null } ] },
    "total": 1, "pagination": null
  }
}
```

`unassigned` 줄은 팀이 하나도 없어도 항상 있다. `currency_matches_team`이 `false`면 그 계정은
예산 계산에서 빠진다(팀에서 자동으로 빼지는 않는다).

**`PATCH /teams/{team_id}` — 통화 변경 제한**: 그 팀 계정들의 `cloud_account_costs` 행이 1건이라도
있으면 `409 CONFLICT`(`details.reason = "team_has_collected_costs"`) — 이미 수집된 금액이 있는 팀의
통화를 바꾸면 과거 사용률이 전부 거짓이 된다.

**`PUT /teams/{team_id}/accounts`**: 1계정 1팀이다. 다른 팀 소속 계정을 넣으면 `409
ACCOUNT_ALREADY_IN_TEAM`. 목록에서 빠진 계정은 미배정으로 돌아간다.

**`DELETE /teams/{team_id}`**: `X-Action-Confirmed` 필요. 소속 계정은 미배정이 된다(계정 자체는
지워지지 않는다). 예산·알림 중복방지 기록은 함께 삭제되지만 `cloud_account_costs`(과거 비용)는 남는다.

#### 11-10-2. 예산

| Method | Path | 설명 |
|---|---|---|
| GET | `/teams/{team_id}/budgets` | 예산 목록(이력 포함) |
| POST | `/teams/{team_id}/budgets` | 예산 생성 |
| PATCH | `/team-budgets/{budget_id}` | 오타 수정 |
| DELETE | `/team-budgets/{budget_id}` | 삭제 (`X-Action-Confirmed` 필요) |
| GET | `/teams/{team_id}/budget-status` | 소진율·임계 판정 |

**`POST /teams/{team_id}/budgets`**

```json
{ "period_type": "monthly", "start_date": "2026-09-01", "end_date": null,
  "limit_amount": "300.000000", "currency": "USD" }
```

`period_type`: `monthly | quarterly | annual | custom`(4종 모두). `custom`만 `end_date` 필수(제외
경계로 저장). `limit_amount > 0`.

| 위반 | 응답 |
|---|---|
| 이미 활성 반복 주기가 있음 | `409 CONFLICT` |
| `custom`끼리 기간 겹침 | `409 BUDGET_PERIOD_OVERLAP` |
| `custom` 기간 > 1년 | `422 BUDGET_PERIOD_TOO_LONG` |
| `limit_amount <= 0` | `422 VALIDATION_ERROR` |

반복 예산의 한도를 바꾸면 기존 행을 수정하지 않고 새 행을 만든다(이력 보존). `PATCH`는 오타 수정용.

**`GET /teams/{team_id}/budget-status`**

```json
{
  "data": {
    "team_id": "7",
    "budget": { "id": "11", "period_type": "monthly", "limit_amount": "300.000000", "currency": "USD",
                "period_start": "2026-09-01", "period_end": "2026-10-01", "period_state": "in_progress" },
    "usage": { "currency": "USD", "amount": "128.400000", "basis": "usage_before_credits",
               "net_amount": "98.400000", "as_of": "2026-09-17T06:00:00Z", "is_estimated": true },
    "ratio_pct": "42.8",
    "forecast": { "amount": "241.900000", "ratio_pct": "80.6", "based_through": "2026-09-16" },
    "thresholds": [
      { "percent": 80, "crossed": false, "crossed_at": null, "notified": false },
      { "percent": 100, "crossed": false, "crossed_at": null, "notified": false }
    ],
    "excluded_accounts": [], "computable": true, "reason_code": null,
    "staleness_threshold_hours": 36
  }
}
```

`computable: false`이면 `ratio_pct`는 `null`이다. `reason_code`: `MISSING_DAYS`(기간 안에 수집 안 된
날이 있음 — 판정하지 않는다) \| `CURRENCY_MISMATCH` \| `NO_BUDGET`(예산 미설정, **`$0`이 아니다**) \|
`NO_ACCOUNTS` \| `UNSUPPORTED`. `period_state`: `upcoming` \| `in_progress` \| `ended` — 전망은
`in_progress`에서만 낸다.

#### 11-10-3. 예산 알림

기존 `notifications` 테이블을 재사용한다(새 알림 인프라를 만들지 않는다). `type =
"budget_threshold"` · `message_key = "notif.budget.threshold"` · `reference_type = "team_budget"`.
중복 방지는 `team_budget_notifications`의 `UNIQUE (team_budget_id, period_start, threshold)`가 한다
— INSERT가 성공했을 때만 알림을 만들고 같은 트랜잭션에 저장한다. 비용 전용 알림 조회 API는 만들지
않는다(`GET /notifications`를 그대로 쓴다).

### 11-11. 급증·검토·보고서 부품·예산 게이트

#### 11-11-1. `GET /cost-anomalies`

query: 공통 query + `status`(`open`/`resolved`/`all`, 기본 `open`). **저장 테이블이 없다** — 요청할
때마다 `cloud_account_costs`에서 규칙을 적용해 계산하고, 상태는 `cost_review_items`에서 붙여 온다.

```json
{
  "data": {
    "rule": { "baseline_days": 7, "min_delta_amount": "5.000000", "min_increase_pct": 50,
              "min_history_days": 12, "exclude_recent_days": 3, "currency": "USD",
              "charge_category": ["usage"] },
    "items": [
      { "source_key": "31:AmazonRDS:2026-09-13",
        "cloud_account_id": "31", "provider": "aws", "service": "AmazonRDS", "date": "2026-09-13",
        "currency": "USD", "amount": "18.400000", "baseline_amount": "6.100000",
        "delta": "12.300000", "delta_pct": "201.6",
        "review": { "item_id": "88", "status": "open", "resolution": null },
        "related_changes": [ { "type": "provisioning_job", "id": "1301", "service_code": "rds",
                                "finished_at": "2026-09-13T04:20:00Z" } ],
        "label": "원인 확인 필요", "is_sample_data": false }
    ],
    "insufficient_history": [ { "cloud_account_id": "33", "days_available": 4, "days_required": 12 } ],
    "total": 1, "pagination": null
  }
}
```

`label`은 항상 `"원인 확인 필요"`다 — `"비용 누수"`라고 단정하지 않는다. `related_changes`는 같은
날의 프로비저닝 기록이며 인과를 단정하지 않는다("관련 변경 후보"). `insufficient_history`는 판정
자체를 못 한 계정이며 조용히 빠지지 않는다.

#### 11-11-2. 검토 큐

| Method | Path | 설명 |
|---|---|---|
| GET | `/cost-review-items` | 큐 목록 |
| POST | `/cost-review-items` | 항목 생성 (멱등) |
| PATCH | `/cost-review-items/{item_id}` | 상태·사유·메모 변경 |

`POST`는 `(user_id, source_type, source_key)`가 이미 있으면 기존 항목을 그대로 반환한다(`200`, 새로
만들지 않음 — "최초 1회 알림"이 이 UNIQUE로 보장된다). `PATCH`로 `status='resolved'`인데
`resolution`이 없으면 `422`, `resolved → open` 되돌리기는 `409 CONFLICT`(재발은 새 항목).

검토 상태는 금액·합계에 영향을 주지 않는다. 감사 기록(`cost_review.update`)은 §14 승인 후에 켠다 —
승인 전에는 `updated_at`·`resolved_at`만 남긴다.

#### 11-11-3. 예산 게이트 — `POST /provisioning/{provider}/{service}` 수정 · **보류(ADR-042)**

> ⚠️ **2026-09-18 결정: 이번 범위에서 빠진다.** 아래는 설계로만 남긴다. `routers/provisioning.py`를
> 한 줄도 고치지 않고 `BUDGET_EXCEEDED`도 쓰지 않는다. **이 파일은 조은솔님 소유 — 사전 공유 없이
> 수정하지 않는다.**

붙일 때는: `create_provisioning_job`의 `_get_owned_credential(...)` 직후에 헤더 파라미터
(`X-Budget-Override`) 1줄 + `enforce_team_budget(db, cloud_account, override=...)` 호출 1줄만
추가한다. 검사 본체는 신규 파일 `app/cost/budget_gate.py`에 둔다.

**fail-open** — 예산 계산 중 예외가 나면 통과시키고 로그만 남긴다(비용 기능 버그가 프로비저닝을
멈추면 안 된다). `cloud_account.team_id IS NULL`이면 검사를 건너뛴다(기존 테스트는 그대로 통과).

**거절하지 않는 경우**: 미배정 계정, 팀에 활성 예산 없음(예산 미설정 ≠ $0), `budget-status.computable
== false`, `X-Budget-Override: true`, 예산 계산 중 예외.

**409 예시**

```json
{
  "error": {
    "code": "BUDGET_EXCEEDED",
    "message": "운영팀의 이번 달 예산(USD 300.00)을 이미 초과했습니다. 계속 진행하려면 확인 후 다시 요청하세요.",
    "request_id": "01J7...",
    "details": [
      { "field": "team_id", "reason": "7" }, { "field": "team_name", "reason": "운영팀" },
      { "field": "usage_amount", "reason": "312.400000" }, { "field": "limit_amount", "reason": "300.000000" },
      { "field": "currency", "reason": "USD" }, { "field": "budget_id", "reason": "11" },
      { "field": "period_start", "reason": "2026-09-01" }
    ]
  }
}
```

거절도 감사 기록은 기존 action `provisioning.request` + `result="denied"`로 남긴다(새 action을 만들지
않는다). override로 통과한 경우는 `result="success"` + `metadata_json.budget_override: true`.

#### 11-11-4. 보고서 부품 3종

**보고서의 비용 섹션 소유권은 확정됐다(2026-09-18).** 비용 섹션 데이터는 전부 이 부품 3종에서
나오고, `/reports`는 비용 숫자를 자체 계산하지 않는다. 남은 미결은 "끼우는 방식" 하나뿐이다(§11-12).

⚠️ 보고서 화면(`frontend/reports.html`)은 이미 `GET /api/v1/reports`·`/reports/{id}`를 전제로
만들어져 있다 — 여기 정의는 "비용 섹션을 만드는 부품"이고 보고서 전체 API가 아니다.

| Method | Path | 설명 |
|---|---|---|
| GET | `/cost-reports/sections` | 블록별 JSON |
| GET | `/cost-reports/exports` | CSV (상세·비교) |
| GET | `/cost-reports/fragment` | 비용 섹션 HTML 조각 |

**`GET /cost-reports/sections` — 200**

```json
{
  "data": {
    "schema_version": "1.0", "data_version": "2026-09-17T06:00:00Z", "staleness_threshold_hours": 36,
    "conditions": { "period": { "start": "2026-09-01", "end": "2026-09-18" },
                     "providers": ["aws", "azure", "gcp"], "accounts": ["31", "33"],
                     "teams": ["7", "unassigned"], "charge_category": ["usage"] },
    "coverage": { "missing_days": ["2026-09-14"],
                   "excluded_accounts": [ { "cloud_account_id": "33", "status": "UNSUPPORTED" } ] },
    "is_provisional": true,
    "sections": [
      { "key": "summary", "title": "비용 요약", "blocks": [] },
      { "key": "trend", "title": "비용 추이", "blocks": [] },
      { "key": "breakdown", "title": "서비스별 비중", "blocks": [] },
      { "key": "changes", "title": "증감 상위", "blocks": [] },
      { "key": "budget", "title": "예산 대비", "blocks": [] },
      { "key": "anomalies", "title": "확인 필요 항목", "blocks": [] }
    ]
  }
}
```

`conditions` 5종(기간·CSP·계정·팀·요금분류)을 반드시 함께 내려보낸다. `is_provisional`은 구간에
`is_estimated` 값이 하나라도 있으면 `true`다. 각 section의 `blocks`는 위 조회 API와 똑같은 모양을
재사용한다 — 계산 경로가 하나여야 숫자가 어긋나지 않는다.

**`GET /cost-reports/exports`**: query `kind` = `details` \| `comparison`, `format=csv`. **UTF-8
BOM**으로 시작, **수식 주입 방지**(`=`/`+`/`-`/`@`로 시작하면 앞에 `'`), 금액은 6자리 + 통화는 별도
열, 빈 값은 빈 셀(0으로 채우지 않는다). `kind=details`는 FOCUS 표준 근접 열(`ChargePeriodStart` 등)
+ 자체 열(`TeamName`·`IsEstimated`·`ListPriceMonthlyEstimate`·`IsExampleData`).

**`GET /cost-reports/fragment`**: `<section>` 조각만 반환(`<html>`/`<head>`/`<body>` 없음). 인쇄
CSS는 별도 파일(`assets/css/cost-print.css`).

### 11-12. AI 상담 문맥 (기존 엔드포인트, 변경 없음)

`POST /api/v1/agent/chat`을 그대로 쓴다. 서버가 문맥에 비용 요약만 추가한다: 실측 MTD(통화별,
`/costs/summary`) · 예산 사용률(`/teams/{id}/budget-status`, `computable: false`면 "판정 불가") ·
급증 목록 상위 10개(`/cost-anomalies`) · 데이터 없음 구간. **AI가 계산하지 않는다** — 서버가 이미
계산한 값만 넣는다. 모든 금액에 종류·기간·통화를 함께 적고, 리소스 이름·태그는 데이터로만
전달한다(프롬프트 주입 방어). `503 AGENT_NOT_CONFIGURED`면 모달에 안내만 띄운다.

### 11-13. 표시 규칙 요지 (화면·보고서·CSV 공통)

- 상태값 9종(§11-1-6) 밖의 값을 쓰지 않는다.
- `(cost_kind, currency, period)`가 다르면 합치지 않는다 — 합계 가드.
- 값이 없으면 `0`이 아니라 `null`/`—`로 표시한다(예산 미설정 ≠ `$0`, 조회 실패 ≠ `$0`).
- 색 토큰은 `--cost-` 접두사를 쓴다(`--sky`/`--aero`/`--yellow`는 차트 계열로 쓰지 않는다) — 비용
  화면은 증가/감소를 빨강/청록으로 구분한다(적록색약 배려, 보고서의 빨강/초록과는 다른 선택 — 그쪽은
  인쇄물이고 팀 소유라 그대로 둔다).

### 11-14. 이 문서에서 넣지 않은 것과 이유

| 항목 | 이유 |
|---|---|
| 보안 점검 기능 전체 | 비용 파트 범위가 아니다 |
| 태그 기준 비용 배분 | 설계만(§7-1 tags 컬럼은 미리 만들어 둠). "준비 중"으로 남긴다 |
| 3사 프로비저닝 추천 API 연동 | 설계만 — 권한명·응답 필드 미확인 |
| `/api/v1/reports` 전체 계약 | 비용은 부품 3종까지다. 보고서 전체는 다른 파트 소유 |
| PDF 생성·정기 전송·보고서 공유 링크 | 보고서 파트 범위, 팀 논의 중 |
| 리소스 사용률·미사용 리소스 섹션 | 메트릭 수집은 보고서 파트 범위. 비용은 "월 예상 비용" 칸만 먹인다 |
| 시간/리소스 단위 Cost Explorer 조회, CUR/Data Exports | 비용 최소화 원칙과 충돌(요청당 과금) |
| 예산 초과 시 생성 차단 | ADR-042로 이번 라운드 범위 제외(§11-11-3에 설계만 유지) |
| 신규 오류 코드 9개 | 채택 전(§17) |
| `cost_review.update` 감사 기록 | 승인 전(§14) |

---

## 12. 대시보드

이 절은 v1.1과 동일하다(리소스 유형 정규화를 참조하지 않으므로 영향 없음). 비용 파트는 이 절을
바꾸지 않는다 — 대시보드의 "예상 총 비용" 카드는 계속 `/resources`의 `cost_summary`를 클라이언트가
집계한 값을 쓴다. §11의 `/costs/summary`는 **별도의 상세 비용 화면**(`cost.html`, COST-01)을 위한
것이다.

## 13. 알림

이 절은 v1.1과 동일하다. §11-10-3이 여기에 `type='budget_threshold'` 행을 추가하는 것을 제안하지만
`notifications` 테이블 자체나 `GET /notifications`의 계약은 바꾸지 않는다.

## 14. 감사 이벤트

`audit_events`는 내부 기록이며 일반 사용자용 수정·삭제 API를 만들지 않는다. 관리자 조회 API도 역할·보존 정책 확정 전에는 제공하지 않는다.

기본 기록 대상:

| action | 시점 |
|---|---|
| `cloud_account.create` | 계정이 새로 생성됐을 때(6.2절 credential 등록에 포함) |
| `credential.create` | credential 생성 요청·결과 |
| `credential.verify` | 실제 CSP 검증 결과 |
| `credential.delete` | 삭제 요청·결과 |
| `resource.start` | 시작 요청·결과 |
| `resource.stop` | 중지 요청·결과 |
| `resource.delete` | 삭제 요청·결과 |
| `provisioning.request` | 과금 확인 후 생성 요청(job 단위). §11-11-3의 예산 게이트 거절도 이 action + `result="denied"`로 남긴다(제안) |
| `provisioning.complete` | job 종료 |
| `user.withdraw` | 회원 탈퇴 요청·결과 |
| `cost_review.update` | **제안 — 승인 전에는 기록하지 않는다.** 검토 항목 상태 변경 요청·결과(§11-11-2). 승인되면 `cost_review_items` PATCH 경로에서 기록한다 |

`result`: `requested | success | failure | denied`. `metadata_json`에는 resource ID, job ID, 정제된 error code 같은 비민감 정보만 허용한다.

## 15. 인가 및 소유권 검사

모든 보호 API는 URL 또는 body의 ID를 신뢰하지 않고 DB 관계를 따라 인증 사용자 소유권을 확인한다.

| 대상 | 소유권 경로 |
|---|---|
| cloud account | `cloud_accounts.user_id = current_user.id` |
| credential | `credentials.cloud_account_id -> cloud_accounts.user_id` |
| resource | `resources.cloud_account_id -> cloud_accounts.user_id` |
| sync job | `resource_sync_jobs.user_id` |
| sync item | `item.sync_job_id -> resource_sync_jobs.user_id` |
| provisioning job | `provisioning_jobs.user_id` |
| cost | `cloud_resource_costs.resource_id -> resources -> cloud_accounts.user_id` |
| notification | `notifications.user_id` |
| **team**(제안) | `teams.user_id = current_user.id` |
| **team budget**(제안) | `team_budgets.team_id -> teams.user_id` |
| **account cost**(제안) | `cloud_account_costs.cloud_account_id -> cloud_accounts.user_id` |
| **ingestion run**(제안) | `cost_ingestion_runs.user_id` |
| **cost review item**(제안) | `cost_review_items.user_id` |

타 사용자 소유 ID는 정보 노출을 줄이기 위해 일반적으로 `404`로 응답한다. body에 여러 ID가 있으면 모두 검사하며, 하나라도 타 사용자 소유인 경우 해당 ID의 존재 여부를 노출하지 않는다.

## 16. 상태 전이

### 16.1 동기화 job

```text
pending -> running -> success
                   -> partial_success
                   -> failed
pending/running    -> cancelled
```

item은 `pending -> running -> success|failed|cancelled`로 전이한다. 부모 상태는 item들을 집계한다.

### 16.2 프로비저닝 job

```text
queued -> running -> success|failed
queued/running    -> cancelled
```

종결 상태에서 이전 상태로 되돌리지 않는다. 재시도는 기존 row를 되살리지 않고 명시적인 새 요청(새 job) 또는 별도 retry 정책으로 처리한다.

### 16.3 비용 수집 run (제안)

`cost_ingestion_runs.status`는 **16.1과 같은 6종**을 쓴다 — 새 값 집합을 만들지 않는다.

```text
pending -> running -> success
                   -> partial_success
                   -> failed
pending/running    -> cancelled
```

동기화 job(16.1)과 다른 점: 부모/자식 2단 구조가 아니라 **계정당 run 1행**이다(§11-9 참고). 동시
실행 방지는 상태 컬럼이 아니라 PostgreSQL advisory lock으로 한다.

## 17. 주요 오류 코드

| 영역 | code |
|---|---|
| 인증 | `INVALID_CREDENTIALS`, `INVALID_TOKEN`, `USER_WITHDRAWN`, `INVALID_OR_EXPIRED_RESET_TOKEN`, `EMAIL_NOT_VERIFIED` |
| 계정 | `CLOUD_ACCOUNT_NOT_FOUND`, `CLOUD_ACCOUNT_ALREADY_EXISTS`, `CLOUD_ACCOUNT_DELETE_NOT_IMPLEMENTED` |
| credential | `CREDENTIAL_NOT_FOUND`, `CREDENTIAL_ALREADY_EXISTS`, `CREDENTIAL_VERIFICATION_FAILED`, `CREDENTIAL_IN_USE` |
| 인벤토리 | `RESOURCE_NOT_FOUND`, `UNSUPPORTED_OPERATION`, `RESOURCE_STALE`, `RESOURCE_ALREADY_DELETED` |
| CSP | `CLOUD_PERMISSION_DENIED`, `PROVIDER_AUTHENTICATION_FAILED`, `PROVIDER_API_ERROR`, `PROVIDER_RATE_LIMITED` |
| 동기화 | `SYNC_JOB_NOT_FOUND`, `JOB_ALREADY_RUNNING`, `JOB_NOT_CANCELLABLE` |
| 프로비저닝 | `PROVISIONING_JOB_NOT_FOUND`, `SERVICE_NOT_FOUND`, `RESOURCE_NOT_PROVISIONABLE`, `IDEMPOTENCY_KEY_REQUIRED`, `IDEMPOTENCY_KEY_REUSED`, `SECRET_FIELD_NOT_ALLOWED`, `TERRAFORM_ERROR` |
| 비용(기존, 구현 완료 시 사용) | `COST_DATA_UNAVAILABLE`, `COST_PERMISSION_REQUIRED`, `CURRENCY_MISMATCH` |
| **비용(제안, 채택 전 미사용)** | `BUDGET_EXCEEDED`(409, 보류 — ADR-042) · `BUDGET_PERIOD_OVERLAP`(409, `CONFLICT`로 합칠 수 있음) · `BUDGET_PERIOD_TOO_LONG`(422, `VALIDATION_ERROR`로 합칠 수 있음) · `TEAM_NOT_FOUND`(404) · `TEAM_BUDGET_NOT_FOUND`(404) · `COST_INGESTION_RUN_NOT_FOUND`(404) · `COST_REVIEW_ITEM_NOT_FOUND`(404) · `ACCOUNT_ALREADY_IN_TEAM`(409, `CONFLICT`로 합칠 수 있음) · `COST_SETUP_REQUIRED`(409) |
| 공통 | `VALIDATION_ERROR`, `FORBIDDEN`, `CONFIRMATION_REQUIRED`, `CONFLICT`, `INTERNAL_ERROR` |

> **채택되면** `backend/app/errors.py`와 `backend/app/error_catalog.py`(사용자 설명 4줄 형식), 그리고
> `docs/Error_Catalog_Draft_2026-09-16.md`에 **같은 코드·같은 HTTP**로 넣는다. 세 곳 중 한 곳만
> 고치지 않는다.

내부 예외 메시지는 `INTERNAL_ERROR` 응답에 그대로 넣지 않는다. 모든 오류는 추적 가능한 `request_id`를 가진다.

## 18. 로깅과 보안 요구사항

- `logs/access.log`: request ID, method, path template, status, duration, user ID(가능한 경우), `ts`/`level`을 KST(+09:00) 고정 오프셋으로 기록한다. Authorization, Cookie, request body는 기본 기록하지 않는다.
- `logs/app.log`: 비즈니스 상태 전이와 CSP API 호출의 공급자, 연산명, 정제된 결과·오류 code를 기록한다. 처리되지 않은 예외(`http.unhandled_exception`)와 백그라운드 태스크 시작/종료/크래시도 남긴다.
- 이메일 등 개인정보는 필요 최소한으로 기록하며 secret redaction을 공통 필터로 적용한다.
- credential payload의 복호화 범위는 provider adapter 호출 직전부터 직후까지로 최소화한다.
- Pydantic response schema에 비밀 저장 컬럼을 선언하지 않는다.
- `raw_metadata`, `spec_json`, `result_json`, `message_params`, `audit_events.metadata_json`은 저장 전 secret key denylist와 크기 제한을 적용한다.
- provider adapter는 공통 domain error로 변환하며 SDK 원문 오류를 외부로 전달하지 않는다.
- **비용 확장(제안)**: `POST /cost-ingestion-runs` 등 수집 실행 경로의 terraform/CSP 원문 응답은 로그에 남기지 않는다. `cost_ingestion_runs.error_message`에 저장되는 값은 `_safe_error_message()`로 정제된 값만이다(§11-9 참고).

## 19. 구현 전 결정 필요 목록

| 우선순위 | 결정 | 영향 API | 상태 |
|---:|---|---|---|
| 높음 | pagination 방식과 기본 정렬 | 모든 목록 API | 미결 |
| 높음 | provider별 서비스 spec 필드 전체 목록(common/provider_spec) | catalog, provisioning | 미결 |
| 중간 | 태그 타입과 query 문법 | 계정·credential·resource 목록, `/costs/breakdown?dimension=tag` | 미결(비용 쪽은 `501` 고정으로 우회) |
| ~~중간~~ | ~~GCP 실제 비용 수집 방식·권한~~ | 비용·대시보드 | **해소(제안)** — BigQuery Billing Export 기준으로 설계함(§11). 구현은 Azure/AWS 다음 순서 |
| ~~중간~~ | ~~환율 변환 정책~~ | 통합 비용·대시보드 | **부분 해소** — 원통화 저장 + 환산 없음으로 확정(§11-2 `display_currency`). **완전히 닫히지는 않았다** — 팀 통화를 하나로 고정(§11-10-1)해 우회했을 뿐, 다통화 환산 자체는 아직 없음 |
| 낮음 | 알림 type·보존·읽음 되돌리기 | 알림 | 미결 |
| 낮음 | 보고서 형식·주기·전송 정책 | 향후 보고서 API. 비용 섹션 부품 3종(§11-11-4)만 계약 확정, 전체 계약은 미결 | 미결 |
| **신규(비용)** | 신규 오류 코드 9개 채택 범위 | §17 | 미결 — 임시 기본값: 9개 전부 |
| **신규(비용)** | `cost_review.update` 감사 기록 여부 | §14 | 미결 — 임시 기본값: 기록하지 않음 |
| **신규(비용)** | 보고서에 비용 섹션을 끼우는 방식(①서버 호출 ②프론트 합침 ③HTML 조각) | §11-11-4 | 미결 — 임시 기본값: ③ HTML 조각 삽입. 소유권 자체는 확정(비용 파트가 부품만 제공) |
| **신규(비용)** | 예산 초과 시 생성 차단(`X-Budget-Override`, `BUDGET_EXCEEDED`) | §10, §11-11-3 | **보류(ADR-042)** — 설계만 유지, 이번 라운드 구현 안 함 |
| **신규(비용)** | `granularity=weekly/quarterly`의 주 시작 요일 | §11-5 | 미결 — 임시 기본값: 월요일(ISO 8601) |
| **신규(비용)** | 재수집 창(window) 길이 | §11-9 배경 DB 설계(`DB_ERD_v1.2.md` R3) | 미결 — 임시 기본값: 당월 + 최근 3일 |
| **신규(비용)** | 리소스 단위 실측 비용 수집 여부 | §11 전체, 인벤토리(`resources.collected_cost_amount`) | 미결 — 2026-09-18 대시보드 연동 중 확인. 임시 기본값: 계정·서비스 단위까지만(현재 `app/cost/aws_cost.py`, AWS `ce:GetCostAndUsage`), 리소스 단위는 미지원. 기술적으로 막힌 건 아니고 3사가 각각 다른 확장이 필요함 — **AWS**: `ce:GetCostAndUsageWithResources`(계정에서 "Resource-level data" 사전 활성화 필요, 조회 가능 기간 최근 14일 한정, 호출당 과금이 더 큼 — 이미 §11 도입 시 "확정 10, 비용 최소화"로 의도적으로 뺀 API). **Azure**: Cost Management의 usage details export(줄 단위 `resourceId` 포함, Cost Explorer 요약 API와는 별도 연동). **GCP**: BigQuery Billing Export를 먼저 켜야 함(단순 API 호출이 아니라 사전 설정 필요). `resources.collected_cost_amount`/`cloud_resource_costs` 테이블은 스키마상 이미 있으나 실측 파이프라인이 채우지 않음(seed_mock_data.py만 채움) — 인벤토리 화면의 "실측" 배지는 이 결정이 나기 전까지 데모 계정에서만 보인다. |

## 20. 구현 완료 조건

각 API 영역은 다음을 충족해야 완료로 본다.

1. FastAPI request/response schema와 OpenAPI 문서가 이 명세에 일치한다.
2. 모든 ID 접근에 인증 사용자 소유권 테스트가 있다.
3. unique, FK, CHECK 위반이 안정적인 API 오류 code로 변환된다.
4. secret이 성공 응답, 오류, access/app log, 감사 metadata에 노출되지 않는 테스트가 있다.
5. 동기화와 프로비저닝은 `202` 이후 worker 상태 전이를 추적할 수 있다.
6. 멱등성 및 동시 요청 테스트가 PostgreSQL 16에서 통과한다.
7. 실제·추정·정가 추정 비용의 종류, 통화, 기간, 기준 시각, source가 구분된다.
8. `/health`, `/ready`가 생존과 DB 준비 상태를 각각 정확히 반환한다.
9. 비용·보안 등 아직 화면 명세가 확정되지 않은 확장 API는 구현 여부와 무관하게 이 문서의 핵심 API 동작에 영향을 주지 않는다.
10. 미확정 정책은 구현자가 임의로 결정하지 않고 결정 기록 후 명세를 갱신한다.
11. **(§11 제안 채택 시)** CSP API 호출 경계(§11-1-2)가 코드 리뷰로 검증된다 — `app/routers/costs.py`류 조회 라우터에 provider SDK import가 없다.
12. **(§11 제안 채택 시)** 예산·비용 계산 실패가 프로비저닝(fail-open) 또는 조회(status로 표현)를 막지 않는다.

---

## 부록 — 원본 근거

이 문서의 §11과 §1.1·§2.4·§3.5·§14·§15·§16·§17·§19 변경분은
`docs/비용_개발문서/05_API계약.md`(2026-09-17/18, 이승현)를 그대로 옮기되 "개정안 제안 표" 형식을
이 문서의 본문 형식(직접 서술)으로 풀어썼다. 그 문서에는 §1 "기존 명세 §11 개정안" 표, 근거
ADR 번호, "입력 → 기대 출력" 검증표가 더 상세히 있다. 이 문서는 **팀 검토 전 개인 작업 문서**다.
