# 멀티 클라우드 관리 서비스 API 명세서

> 기준일: 2026-09-10 (2차 DB migration `2964dfe0a706` 반영)  
> 기준 문서: `AGENTS.md`의 확정 DB 스키마 컨텍스트  
> 대상: FastAPI 기반 REST API  
> 상태: 목표 명세(Draft). 현재 저장소에는 API 구현 코드가 없으므로 구현 완료를 의미하지 않는다.

## 1. 목적과 범위

이 문서는 AWS, Azure, GCP 계정 연결, 통합 인벤토리, 리소스 제어, Terraform 프로비저닝, 비용, 알림 및 대시보드에 필요한 외부 API 계약을 정의한다.

DB bigint PK를 외부 식별자로 사용하되 모든 사용자 소유 데이터는 인증 사용자 기준으로 접근 권한을 검사한다. 클라우드 자격 증명, 비밀번호, JWT 비밀값, 비밀번호 재설정 원본 토큰, Terraform secret·state·민감 output은 응답, 오류, 로그, 감사 metadata에 포함하지 않는다.

### 1.1 구현 단계

| 단계 | 범위 | DB 선행 조건 |
|---|---|---|
| 1차 | 인증, 사용자, 클라우드 계정·자격 증명, 서비스 카탈로그, 인벤토리, 동기화, 프로비저닝 job, 알림, 상태 확인 | 1차 DB 구축 테이블 — 완료 (`7bf7892874c3`) |
| 2차 | resource type 기반 지원 동작, provisioning request 부모 흐름, 비용 이력, 감사 이벤트 | `resource_types`, `provisioning_requests`, `cloud_resource_costs`, `audit_events` migration — 완료 (`2964dfe0a706`) |
| 확장 | 대시보드 집계, 가격 비교, 실제 비용 고도화, 보고서 | 2차 DB 및 별도 정책 확정 |

최종 API 계약은 2차 DB까지 포함한다. 2차 테이블의 migration은 이미 적용되어 있으므로(`2964dfe0a706`), 2차 엔드포인트를 막는 조건은 더 이상 "DB 미비"가 아니라 "서비스 코드 미구현"이다. 아래 개별 절의 "2차 DB migration 이후 제공한다"는 문구는 이 의미로 읽는다.

### 1.2 명세에서 확정하지 않는 사항

다음 항목은 제품 결정 전까지 API 계약을 확정하지 않는다.

- 목록 API의 pagination 방식과 기본 정렬
- 태그의 값 타입, 비교 연산 및 query 직렬화 방식
- 소셜 로그인 공급자 목록과 OAuth callback 세부 규격
- 여러 연결 계정을 선택한 프로비저닝의 생성 개수와 조합 규칙
- 서비스 매핑별 공통·공급자별 spec 필드 전체 목록
- 알림 종류, 보존 기간 및 일괄 읽음 정책
- GCP 실제 비용 수집 방식과 필요한 Billing Export/API 권한
- 보고서 파일 형식, 생성 시점 및 이메일 발송 정책

본문의 `정책 확정 필요` 표시는 구현 전 사용자 결정이 필요한 항목이다.

## 2. 공통 규약

### 2.1 기본 URL과 데이터 형식

- Base path: `/api/v1`
- 요청·응답: `application/json; charset=utf-8`
- 시간: ISO 8601 UTC 문자열. 예: `2026-09-09T03:15:30Z`
- 날짜: ISO 8601 날짜. 예: `2026-09-01`
- 금액: 정밀도 손실을 막기 위해 JSON 문자열로 반환. 예: `"1234.560000"`
- 통화: ISO 4217 대문자 3자리 코드. 예: `KRW`, `USD`
- ID: DB의 양수 bigint. JavaScript 정밀도 문제를 피하기 위해 응답에서는 문자열로 반환한다.
- provider: `aws | azure | gcp`
- API 필드명: `snake_case`

### 2.2 인증

보호 API는 다음 헤더를 요구한다.

```http
Authorization: Bearer <access_token>
```

JWT access token은 응답 body에 반환할 수 있으나 서버 로그에는 기록하지 않는다. refresh token의 전달·폐기 방식은 인증 구현 전에 별도 확정한다.

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

서버는 헤더가 없거나 `true`가 아니면 `428 CONFIRMATION_REQUIRED`로 거부한다. 이 헤더는 인증·인가를 대체하지 않는다. 해당 요청과 결과는 2차 DB의 `audit_events`에 남긴다.

### 2.5 멱등성

프로비저닝 생성 요청은 필수로 다음 헤더를 받는다.

```http
Idempotency-Key: <client-generated-unique-key>
```

- 사용자별로 같은 키와 같은 canonical payload가 다시 오면 기존 요청을 반환한다.
- 같은 키에 다른 payload를 보내면 `409 IDEMPOTENCY_KEY_REUSED`를 반환한다.
- DB에는 `provisioning_requests.request_key` 및 job별 `provisioning_jobs.idempotency_key` unique constraint로 보강한다.

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

### 3.5 ResourceType

```json
{
  "id": "101",
  "service_catalog_id": "1",
  "type_code": "instance",
  "display_name": "Instance",
  "category": "compute",
  "provisionable": true,
  "supports_start": true,
  "supports_stop": true,
  "supports_delete": true
}
```

지원 여부가 확정되지 않은 동작은 모두 `false`다.

### 3.6 Resource

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
  "resource_type": {
    "id": "101",
    "type_code": "instance",
    "display_name": "Instance",
    "supports_start": true,
    "supports_stop": true,
    "supports_delete": true
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

`provider_resource_key`, 수집 credential ID, `raw_metadata`는 기본 목록 응답에서 제외한다. 상세 API에서 `raw_metadata`를 제공할 경우 허용 목록 기반으로 비밀을 제거한 데이터만 반환한다. `resource_type`은 정확히 매핑되지 않은 기존 데이터에서는 `null`일 수 있으며 이 경우 동작 API는 안전하게 거부한다.

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
| POST | `/auth/sign-up` | 아니요 | 일반 회원가입 |
| POST | `/auth/login` | 아니요 | 이메일·비밀번호 로그인 |
| POST | `/auth/password-reset-requests` | 아니요 | 비밀번호 재설정 메일 요청 |
| POST | `/auth/password-resets` | 아니요 | 토큰으로 비밀번호 재설정 |
| GET | `/me` | 예 | 내 정보 조회 |
| PATCH | `/me` | 예 | 이름·소속 정보 수정 |
| DELETE | `/me` | 예 + 확인 | 회원 탈퇴 |

소셜 로그인 URL과 callback API는 공급자 목록 및 OAuth 정책 확정 후 추가한다.

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
- `affiliation_type=company`일 때 단체명 필수 여부는 정책 확정이 필요하다.
- 비밀번호 정책은 인증 구현 전에 확정한다.

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
    "token_type": "Bearer",
    "expires_in": 3600,
    "user": {}
  }
}
```

계정 존재 여부를 노출하지 않도록 이메일·비밀번호 불일치는 동일한 `INVALID_CREDENTIALS`를 반환한다. 탈퇴 사용자의 로그인은 거부한다.

### 5.4 `POST /auth/password-reset-requests`

```json
{"email": "user@example.com"}
```

계정 존재 여부와 무관하게 **202**와 같은 메시지를 반환한다. DB에는 원본 토큰이 아니라 `token_hash`, 만료 시각만 저장한다.

### 5.5 `POST /auth/password-resets`

```json
{
  "token": "one-time-token",
  "new_password": "client-input-only"
}
```

성공 시 토큰의 `used_at`을 기록한다. 만료·사용 완료·불일치는 `INVALID_OR_EXPIRED_RESET_TOKEN`으로 통합한다.

### 5.6 `PATCH /me`

```json
{
  "name": "홍길동",
  "affiliation_type": "individual",
  "affiliation_name": null
}
```

이메일 변경은 이 API 범위에 포함하지 않는다. **200**으로 갱신된 `User`를 반환한다.

### 5.7 `DELETE /me`

`X-Action-Confirmed: true`가 필수다. 사용자를 물리 삭제하지 않고 `status=withdrawn`, `withdrawn_at`을 기록하는 soft withdrawal을 기본으로 한다. credential 처리와 법적 보존 정책은 별도 확정이 필요하다.

**204 No Content**.  
감사 action: `user.withdraw`.

## 6. 클라우드 계정과 자격 증명

### 6.1 엔드포인트 요약

| Method | Path | 설명 |
|---|---|---|
| GET | `/cloud-accounts` | 내 연결 계정 목록 |
| POST | `/cloud-accounts` | 연결 계정 생성 |
| GET | `/cloud-accounts/{cloud_account_id}` | 연결 계정 조회 |
| PATCH | `/cloud-accounts/{cloud_account_id}` | 표시 이름 수정 |
| DELETE | `/cloud-accounts/{cloud_account_id}` | 연결 계정 삭제 |
| GET | `/cloud-accounts/{cloud_account_id}/credentials` | credential 목록 |
| POST | `/cloud-accounts/{cloud_account_id}/credentials` | 암호화 저장 및 실제 검증 |
| PATCH | `/credentials/{credential_id}` | 비민감 metadata 수정 또는 secret 교체 |
| POST | `/credentials/{credential_id}/verify` | 재검증 |
| DELETE | `/credentials/{credential_id}` | credential 삭제 |
| PUT | `/credentials/order` | 사용자 정의 순서 변경 |

모두 인증이 필요하다.

### 6.2 `POST /cloud-accounts`

```json
{
  "provider": "aws",
  "external_account_id": "123456789012",
  "account_label": "운영 AWS"
}
```

**201**: `CloudAccount`.  
**409**: 같은 사용자의 `(provider, external_account_id)` 중복 시 `CLOUD_ACCOUNT_ALREADY_EXISTS`.

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

파괴적 동작이므로 `X-Action-Confirmed: true`가 필수다. 연결 계정 삭제가 resource snapshot을 cascade 삭제하므로, 운영 정책상 보존·비활성화 방식이 확정되기 전에는 이 엔드포인트 구현을 보류한다.

### 6.6 `POST /cloud-accounts/{cloud_account_id}/credentials`

저장과 실제 CSP 검증을 하나의 흐름으로 수행한다.

```json
{
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

`secret_payload`의 구체 필드는 provider별 discriminated schema로 정의하되 서비스 내부의 공통 DTO로 억지 통합하지 않는다. 서버는 payload를 AES-256-GCM으로 암호화하고 평문을 저장하지 않는다.

검증 성공 시 **201**과 `verified=true`인 `Credential`을 반환한다. 검증 실패 시 저장 정책은 다음 중 하나로 구현 전에 확정해야 한다.

1. 실패 credential도 암호화 저장하고 `verified=false`로 반환
2. transaction을 rollback하고 저장하지 않음

오류에는 secret 또는 원본 SDK 응답을 넣지 않는다. 감사 action: `credential.create`, `credential.verify`.

### 6.7 `PATCH /credentials/{credential_id}`

비민감 metadata만 수정하는 예:

```json
{
  "name": "provisioner",
  "tags": {"environment": "production"},
  "display_order": 1
}
```

secret 교체 시 `secret_payload` 전체를 새로 받아 새 nonce로 다시 암호화한다. 부분 secret patch는 지원하지 않는다. secret이 교체되면 즉시 재검증하며 성공·실패 저장 정책은 생성 API와 동일하게 확정해야 한다.

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

## 7. 서비스 카탈로그와 리소스 유형

| Method | Path | 설명 |
|---|---|---|
| GET | `/service-catalog` | provider 서비스와 공통 분류 조회 |
| GET | `/resource-types` | 원본 리소스 유형과 지원 동작 조회 |
| GET | `/provisioning/options` | 선택 계정 권한을 반영한 생성 가능 옵션 조회 |

### 7.1 `GET /service-catalog`

query:

- `provider`: `aws | azure | gcp`, 다중 선택
- `category`: 다중 선택
- `provisionable`: boolean

초기 provisionable 데이터는 Compute, DB_RDBMS, Storage_Object, CDN에 대한 3개 provider 조합 12개다.

### 7.2 `GET /resource-types`

query:

- `service_catalog_id`: 다중 선택
- `category`: 다중 선택
- `provisionable`: boolean
- `operation`: `start | stop | delete`

`resource_types` migration은 적용되어 있다(`2964dfe0a706`, 12개 provisionable 서비스 각 1개씩 최소 시드). 서비스 구현 시점에 제공한다.

### 7.3 `GET /provisioning/options`

query 예:

```text
/api/v1/provisioning/options?credential_id=44&credential_id=45
```

선택한 credential들의 실제 검증 상태와 `permission_scope`를 확인한다. 여러 계정을 선택하면 공통으로 허용되는 최소 권한 범위만 `enabled=true`로 반환하고, 권한 없는 옵션도 숨기지 않는다.

```json
{
  "data": {
    "items": [
      {
        "resource_type": {},
        "enabled": false,
        "disabled_reason_code": "INSUFFICIENT_COMMON_PERMISSION"
      }
    ]
  }
}
```

## 8. 인벤토리

### 8.1 엔드포인트 요약

| Method | Path | 설명 |
|---|---|---|
| GET | `/resources` | 저장된 최신 resource snapshot 목록 |
| GET | `/resources/summary` | 전체 수와 마지막 동기화 시각 |
| GET | `/resources/{resource_id}` | resource 상세 |
| POST | `/resource-actions` | 선택 리소스 일괄 시작·중지·삭제 요청 |

### 8.2 `GET /resources`

DB에 저장된 최신 snapshot을 우선 반환하며 요청 시 CSP API를 직접 호출하지 않는다.

확정 query:

- `provider`: 다중 선택
- `service_catalog_id`: 다중 선택
- `resource_type_id`: 다중 선택
- `cloud_account_id`: 다중 선택
- `status`: 다중 선택
- `region`: 다중 선택
- `tag`: 다중 선택 형식 정책 확정 필요
- `search_field`: `resource | service | region | account`
- `q`: 단일 검색어
- `include_stale`: 기본 `false`
- `include_deleted`: 기본 `false`

`q`와 모든 필터는 교집합으로 적용한다. `search_field=resource`는 이름과 `external_resource_id`를 대상으로 한다. 상세 식별용 `provider_resource_key`는 검색에는 사용할 수 있으나 기본 응답에 노출하지 않는다.

`resource_type_id`는 `resources.resource_type_id` 컬럼(nullable)을 그대로 필터링한다. 이 컬럼은 서비스당 매핑되는 리소스 유형이 하나로 확실한 기존 행만 채워져 있고, 그 외에는 `NULL`이다(수집 코드가 항상 채우게 되기 전까지 계속 그렇다). `resource_type_id`로 필터링할 때 `NULL`인 행(아직 유형이 매핑되지 않은 리소스)을 제외할지, `unmapped` 같은 별도 값으로 조회 가능하게 할지는 `정책 확정 필요`.

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

### 8.5 `POST /resource-actions`

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
2. `resource_type_id`가 정확히 매핑되었는가
3. 해당 `supports_start|stop|delete`가 true인가
4. 사용할 수 있는 검증 credential과 provider 권한이 있는가
5. stale/deleted 상태가 동작을 허용하는가

하나라도 지원하지 않으면 해당 항목을 실행하지 않는다. 일괄 요청의 원자성(전체 실패 또는 항목별 부분 성공)은 정책 확정이 필요하다. 비동기 job 테이블이 현재 확정 스키마에 없으므로, 장시간 또는 일괄 제어의 영속 상태 추적을 구현하려면 별도 migration 설계가 선행되어야 한다.

동기 실행을 택한 임시 응답 예:

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

### 9.2 `POST /sync-jobs`

```json
{
  "cloud_account_ids": null
}
```

- `null` 또는 생략: 인증 사용자의 검증 완료 연결 계정 전체
- 배열: 지정한 내 연결 계정만
- 계정마다 사용할 검증 credential은 서비스가 결정하며 item에 `credential_id`를 기록한다.
- 같은 사용자·계정 범위의 실행 중 job 중복 정책은 구현 전에 확정한다. 기본 안전 동작은 `409 JOB_ALREADY_RUNNING`이다.

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

item 오류는 정제된 `code`, `message`만 반환한다. 전체 job status는 item 상태를 집계해 `pending | running | success | partial_success | failed | cancelled` 중 하나로 저장한다. 이번 실행에서 보지 못한 resource는 물리 삭제하지 않고 `is_stale`, `last_seen_at`, 필요 시 `deleted_at` 정책으로 처리한다.

### 9.4 `POST /sync-jobs/{sync_job_id}/cancel`

`pending|running`에서만 허용한다. provider API 호출이 이미 진행 중이면 취소는 best-effort이며 최종 상태는 worker가 반영한다. **202** 반환.

## 10. 프로비저닝

Terraform은 생성만 담당한다. 기존 리소스의 조회·시작·중지·삭제에는 사용하지 않는다.

### 10.1 엔드포인트 요약

| Method | Path | 설명 |
|---|---|---|
| POST | `/provisioning/price-comparisons` | 유사 사양 가격 비교 |
| POST | `/provisioning/requests` | 승인된 생성 의도와 실행 job 생성 |
| GET | `/provisioning/requests` | 내 요청 이력 |
| GET | `/provisioning/requests/{request_id}` | 요청과 job 진행 조회 |
| POST | `/provisioning/requests/{request_id}/cancel` | 취소 요청 |

`provisioning_requests` 테이블과 `provisioning_jobs.provisioning_request_id`(NOT NULL FK) migration은 적용되어 있다(`2964dfe0a706`). 남은 조건은 서비스 구현이다. `provisioning_requests` 기반 API 구현 전에는 `provisioning_jobs`를 직접 노출하는 임시 API를 만들지 않는 것을 권장한다.

### 10.2 `POST /provisioning/price-comparisons`

```json
{
  "resource_type_id": "101",
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

서비스 매핑과 provider별 필수 spec이 확정된 뒤 실제 schema를 고정한다. 비교값은 청구액이 아니라 추정치임을 명시한다.

### 10.3 `POST /provisioning/requests`

필수 헤더: `Idempotency-Key`, `X-Action-Confirmed: true`.

```json
{
  "resource_type_id": "101",
  "common_spec": {
    "name": "web-01",
    "vcpu": 2,
    "memory_gib": 8
  },
  "targets": [
    {
      "credential_id": "44",
      "service_catalog_id": "1",
      "provider_spec": {
        "region": "ap-northeast-2",
        "instance_type": "t3.large"
      }
    }
  ]
}
```

`common_spec`과 `provider_spec`에는 secret을 허용하지 않는다. secret 필드가 감지되면 `422 SECRET_FIELD_NOT_ALLOWED`로 거부한다.

다중 target의 생성 개수·조합 규칙이 확정되기 전에는 `targets`를 1개로 제한하거나 엔드포인트 구현을 보류해야 한다. 서버가 배열을 임의로 Cartesian product로 확장해서는 안 된다.

**202**

```json
{
  "data": {
    "id": "1201",
    "request_key": "client-key-01",
    "status": "queued",
    "created_at": "2026-09-09T04:20:00Z",
    "status_url": "/api/v1/provisioning/requests/1201"
  }
}
```

감사 action: `provisioning.request`.

### 10.4 `GET /provisioning/requests/{request_id}`

```json
{
  "data": {
    "id": "1201",
    "request_key": "client-key-01",
    "resource_type_id": "101",
    "common_spec": {
      "name": "web-01",
      "vcpu": 2,
      "memory_gib": 8
    },
    "status": "running",
    "created_at": "2026-09-09T04:20:00Z",
    "started_at": "2026-09-09T04:20:01Z",
    "finished_at": null,
    "jobs": [
      {
        "id": "1301",
        "credential_id": "44",
        "service_catalog_id": "1",
        "workspace_name": "user-12-request-1201-job-1",
        "status": "running",
        "progress_percent": 62,
        "created_resource_count": 0,
        "result": null,
        "error": null,
        "started_at": "2026-09-09T04:20:01Z",
        "finished_at": null
      }
    ]
  }
}
```

- `terraform_state_ref`는 내부 참조이므로 외부 응답에 반환하지 않는다. DB에도 state 본문이나 민감 output이 아니라 안전한 외부 저장소 참조만 저장한다(`provisioning_jobs.terraform_state_ref`).
- `spec_json`과 `result_json`은 허용 목록으로 직렬화하며 민감 output을 제거한다.
- request status는 자식 job 상태를 transaction으로 집계한다.
- `success|failed|cancelled` job의 progress는 각각 정책에 맞게 종결하며 `success`만 반드시 100이다.
- 완료 시 알림을 만들고 감사 action `provisioning.complete`를 기록한다.
- `progress_percent`(0~100 CHECK), `created_resource_count`(0 이상 CHECK), `terraform_state_ref`는 `provisioning_jobs`에 이미 컬럼으로 존재한다(`2964dfe0a706`). 새 migration 없이 이 API를 구현할 수 있다.

### 10.5 `POST /provisioning/requests/{request_id}/cancel`

`queued|running`에서만 허용한다. 실행 중 Terraform 취소와 state consistency 정책을 구현한 뒤 제공한다. **202** 반환.

## 11. 비용

### 11.1 엔드포인트 요약

| Method | Path | 설명 |
|---|---|---|
| GET | `/costs/summary` | provider별 실제·추정 비용 요약 |
| GET | `/costs/trends` | 기간별 비용 추이 |
| GET | `/costs/by-service` | 서비스별 비용 비중 |
| GET | `/resources/{resource_id}/costs` | 리소스 비용 이력 |

`cloud_resource_costs` migration은 적용되어 있다(`2964dfe0a706`, `(provider, source_record_key)` unique로 재수집 멱등 처리). 상세 이력 API는 서비스 구현 시점에 제공한다. 실제 비용 데이터를 채우는 수집 로직(특히 GCP)은 이번 범위에 없다.

### 11.2 비용 공통 표현

```json
{
  "amount": "18.120000",
  "currency": "USD",
  "cost_kind": "actual",
  "period_start": "2026-09-01",
  "period_end": "2026-09-09",
  "as_of": "2026-09-09T01:00:00Z",
  "source": "aws_cost_explorer"
}
```

`cost_kind`: `actual | estimated | list_price_estimate`. 서로 다른 의미나 통화를 단순 합산하지 않는다. 통화 변환을 제공하려면 환율 source와 기준 시각을 별도 명시해야 하며 현재 범위에는 포함하지 않는다.

### 11.3 `GET /costs/summary`

query:

- `provider`: 다중 선택
- `cloud_account_id`: 다중 선택
- `period_start`, `period_end`

```json
{
  "data": {
    "as_of": "2026-09-09T01:00:00Z",
    "groups": [
      {
        "provider": "aws",
        "currency": "USD",
        "resource_count": 70,
        "actual_accumulated_cost": "418.120000",
        "estimated_monthly_cost": "721.340000",
        "actual_source": "aws_cost_explorer",
        "estimate_source": "resource_estimates"
      }
    ]
  }
}
```

비용 데이터가 없으면 금액 `"0.000000"`과 resource 수 0을 명시하되, 권한 부족·수집 실패를 0으로 위장하지 않고 `availability`와 오류 code를 별도로 반환한다.

GCP actual 비용 그룹은 수집 방식과 권한이 확정되기 전 `availability="unsupported"` 또는 `unavailable`로 표시하고 임의 값을 만들지 않는다.

### 11.4 `GET /resources/{resource_id}/costs`

query:

- `cost_kind`: 다중 선택
- `period_start`, `period_end`

`cloud_resource_costs`의 기간별 이력을 반환한다. `source_record_key`와 내부 `metadata_json`은 기본 응답에 포함하지 않는다.

## 12. 대시보드

| Method | Path | 설명 |
|---|---|---|
| GET | `/dashboard/overview` | 핵심 지표와 provider 카드 |
| GET | `/dashboard/cost-trends` | 월별 통합 비용 추이 |
| GET | `/dashboard/resource-distribution` | 서비스별 리소스·비용 분포 |
| GET | `/dashboard/recent-provisioning` | 최근 프로비저닝 활동 |
| GET | `/dashboard/regions` | 지도용 region 집계 |

대시보드 API는 기존 resource, cost, provisioning 데이터를 읽기 전용으로 집계한다. 서로 다른 통화를 하나의 총액으로 보여주려면 환율 정책이 먼저 필요하다. 예산 임계값 API는 설정 위치와 경고 규칙 확정 후 추가한다.

`GET /dashboard/overview` 예:

```json
{
  "data": {
    "resource_count": 126,
    "region_count": 14,
    "estimated_monthly_costs": [
      {"amount": "721.340000", "currency": "USD"}
    ],
    "cost_disclaimer_key": "cost.estimate.disclaimer",
    "by_provider": [
      {
        "provider": "aws",
        "resource_count": 70,
        "actual_accumulated_cost": {"amount": "418.120000", "currency": "USD"},
        "estimated_monthly_cost": {"amount": "721.340000", "currency": "USD"},
        "availability": "available"
      }
    ]
  }
}
```

AWS/Azure/GCP 공식 콘솔 URL은 API가 secret이 포함된 URL을 생성하지 않고 프론트엔드의 신뢰된 고정 URL 또는 서버의 allowlist 응답으로 제공한다.

## 13. 알림

| Method | Path | 설명 |
|---|---|---|
| GET | `/notifications` | 내 알림 목록 |
| GET | `/notifications/unread-count` | 읽지 않은 수 |
| PATCH | `/notifications/{notification_id}` | 읽음 상태 변경 |

### 13.1 `GET /notifications`

query:

- `is_read`: boolean
- `type`: 알림 type 정책 확정 후 허용 값 정의

```json
{
  "data": {
    "items": [
      {
        "id": "1501",
        "type": "provisioning_completed",
        "reference_type": "provisioning_request",
        "reference_id": "1201",
        "message_key": "notification.provisioning.completed",
        "message_params": {"request_id": "1201", "success_count": 1},
        "is_read": false,
        "created_at": "2026-09-09T04:25:00Z",
        "read_at": null
      }
    ],
    "total": 1,
    "pagination": null
  }
}
```

`reference_type`과 `reference_id`는 느슨한 참조이므로 클라이언트가 임의 URL로 변환하지 않고 허용된 type mapping을 사용한다.

### 13.2 `PATCH /notifications/{notification_id}`

```json
{"is_read": true}
```

true로 변경하면 `read_at`을 기록하고 false로 되돌리면 `read_at=null`로 처리한다. 읽음 상태를 되돌릴 수 있는지 여부는 정책 확정이 필요하다.

## 14. 감사 이벤트

`audit_events`는 내부 기록이며 일반 사용자용 수정·삭제 API를 만들지 않는다. 관리자 조회 API도 역할·보존 정책 확정 전에는 제공하지 않는다.

기본 기록 대상:

| action | 시점 |
|---|---|
| `credential.create` | credential 생성 요청·결과 |
| `credential.verify` | 실제 CSP 검증 결과 |
| `credential.delete` | 삭제 요청·결과 |
| `resource.start` | 시작 요청·결과 |
| `resource.stop` | 중지 요청·결과 |
| `resource.delete` | 삭제 요청·결과 |
| `provisioning.request` | 과금 확인 후 생성 요청 |
| `provisioning.complete` | 전체 요청 종료 |
| `user.withdraw` | 회원 탈퇴 요청·결과 |

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
| provisioning request | `provisioning_requests.user_id` |
| provisioning job | `provisioning_jobs.user_id` 및 부모 request 소유자 일치 |
| cost | `cloud_resource_costs.resource_id -> resources -> cloud_accounts.user_id` |
| notification | `notifications.user_id` |

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

### 16.2 프로비저닝 request

```text
queued -> running -> success
                  -> partial_success
                  -> failed
queued/running    -> cancelled
```

### 16.3 프로비저닝 job

```text
queued -> running -> success|failed
queued/running    -> cancelled
```

종결 상태에서 이전 상태로 되돌리지 않는다. 재시도는 기존 row를 되살리지 않고 명시적인 새 요청 또는 별도 retry 정책으로 처리한다.

## 17. 주요 오류 코드

| 영역 | code |
|---|---|
| 인증 | `INVALID_CREDENTIALS`, `INVALID_TOKEN`, `USER_WITHDRAWN`, `INVALID_OR_EXPIRED_RESET_TOKEN` |
| 계정 | `CLOUD_ACCOUNT_NOT_FOUND`, `CLOUD_ACCOUNT_ALREADY_EXISTS` |
| credential | `CREDENTIAL_NOT_FOUND`, `CREDENTIAL_ALREADY_EXISTS`, `CREDENTIAL_VERIFICATION_FAILED`, `CREDENTIAL_IN_USE` |
| 인벤토리 | `RESOURCE_NOT_FOUND`, `RESOURCE_TYPE_UNMAPPED`, `UNSUPPORTED_OPERATION`, `RESOURCE_STALE`, `RESOURCE_ALREADY_DELETED` |
| CSP | `CLOUD_PERMISSION_DENIED`, `PROVIDER_AUTHENTICATION_FAILED`, `PROVIDER_API_ERROR`, `PROVIDER_RATE_LIMITED` |
| 동기화 | `SYNC_JOB_NOT_FOUND`, `JOB_ALREADY_RUNNING`, `JOB_NOT_CANCELLABLE` |
| 프로비저닝 | `PROVISIONING_REQUEST_NOT_FOUND`, `RESOURCE_NOT_PROVISIONABLE`, `INSUFFICIENT_COMMON_PERMISSION`, `IDEMPOTENCY_KEY_REQUIRED`, `IDEMPOTENCY_KEY_REUSED`, `SECRET_FIELD_NOT_ALLOWED`, `TERRAFORM_ERROR` |
| 비용 | `COST_DATA_UNAVAILABLE`, `COST_PERMISSION_REQUIRED`, `CURRENCY_MISMATCH` |
| 공통 | `VALIDATION_ERROR`, `FORBIDDEN`, `CONFIRMATION_REQUIRED`, `CONFLICT`, `INTERNAL_ERROR` |

내부 예외 메시지는 `INTERNAL_ERROR` 응답에 그대로 넣지 않는다. 모든 오류는 추적 가능한 `request_id`를 가진다.

## 18. 로깅과 보안 요구사항

- `logs/access.log`: request ID, method, path template, status, duration, user ID(가능한 경우)를 기록한다. Authorization, Cookie, request body는 기본 기록하지 않는다.
- `logs/app.log`: 비즈니스 상태 전이와 CSP API 호출의 공급자, 연산명, 정제된 결과·오류 code를 기록한다.
- 이메일 등 개인정보는 필요 최소한으로 기록하며 secret redaction을 공통 필터로 적용한다.
- credential payload의 복호화 범위는 provider adapter 호출 직전부터 직후까지로 최소화한다.
- Pydantic response schema에 비밀 저장 컬럼을 선언하지 않는다.
- `raw_metadata`, `spec_json`, `result_json`, `message_params`, `audit_events.metadata_json`은 저장 전 secret key denylist와 크기 제한을 적용한다.
- provider adapter는 공통 domain error로 변환하며 SDK 원문 오류를 외부로 전달하지 않는다.

## 19. 구현 전 결정 필요 목록

| 우선순위 | 결정 | 영향 API |
|---:|---|---|
| 높음 | pagination 방식과 기본 정렬 | 모든 목록 API |
| 높음 | credential 검증 실패 시 저장 또는 rollback | credential 생성·수정 |
| 높음 | cloud account 삭제 시 snapshot 보존 정책 | 계정 삭제 |
| 높음 | resource 일괄 동작의 원자성 및 비동기 job 모델 | resource actions |
| 높음 | 다중 계정 프로비저닝 조합 규칙 | provisioning request |
| 높음 | provider별 resource type과 spec schema | catalog, resource, provisioning |
| 중간 | 태그 타입과 query 문법 | 계정·credential·resource 목록 |
| 중간 | refresh token 전달·회전·폐기 정책 | 인증 |
| 중간 | GCP 실제 비용 수집 방식·권한 | 비용·대시보드 |
| 중간 | 환율 변환 정책 | 통합 비용·대시보드 |
| 낮음 | 알림 type·보존·읽음 되돌리기 | 알림 |
| 낮음 | 보고서 형식·주기·전송 정책 | 향후 보고서 API |

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
9. 2차 API는 필요한 migration이 적용되지 않은 환경에서 조용히 오동작하지 않고 배포 단위에서 비활성화된다.
10. 미확정 정책은 구현자가 임의로 결정하지 않고 결정 기록 후 명세를 갱신한다.
