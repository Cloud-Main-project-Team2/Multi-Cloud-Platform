# 에러 코드 자연어 카탈로그 — 초안 (2026-09-16)

> 목적: 에러 코드별로 **증상(무슨 일이 일어났나)·원인(왜)·해결책(어떻게)** 을 자연어로 제공하는
> 기능의 문구 초안. 범위는 **실행 작업 3종(프로비저닝·동기화·리소스 액션)** + 그 경로에서 함께
> 나오는 공통 코드. `docs/Error_Catalog_Draft_2026-09-16.md`.
>
> - **1번 작업(백엔드 카탈로그)** 의 소스: 아래 §1 표.
> - **2번 작업(원문 패턴 번역기)** 의 소스: 아래 §2 표.
> - "현재 메시지"는 코드에 이미 있는 값(그대로 두거나 `symptom`으로 승격). 빈 칸은 코드가
>   메시지 없이 코드만 저장하던 자리(리소스 액션·동기화 일부) — 이번에 새로 채운다.
>
> ⚠️ **검토 포인트**: 문구 톤(사용자용), 해결책이 실제 우리 UI 동선과 맞는지, 빠진 코드 없는지.

---

## §1. 코드 카탈로그 (code → 증상·원인·해결책)

### 프로비저닝 (provisioning)

| 코드 | HTTP | 현재 메시지 | 증상 | 원인 | 해결책 |
|---|---|---|---|---|---|
| `IDEMPOTENCY_KEY_REQUIRED` | 400 | Idempotency-Key 헤더가 필요합니다. | 생성 요청이 시작도 못 하고 거부됨 | 중복 생성 방지 키가 요청에 빠짐(대개 프론트 버그) | 페이지 새로고침 후 다시 시도. 반복되면 개발팀에 문의 |
| `SERVICE_NOT_FOUND` | 404 | 서비스를 찾을 수 없습니다. | 선택한 서비스로 생성이 안 됨 | 요청한 provider/service 조합이 카탈로그에 없음 | 지원 목록에 있는 서비스를 다시 선택 |
| `RESOURCE_NOT_PROVISIONABLE` | 422 | 이 서비스는 프로비저닝할 수 없습니다. | 생성 버튼을 눌러도 진행되지 않음 | 조회 전용 등 생성 대상이 아닌 서비스 | 생성 가능한 서비스를 선택 |
| `PROVISIONING_NOT_IMPLEMENTED` | 501 | 이 provider/service 조합은 아직 지원하지 않습니다. | "아직 지원하지 않음"으로 실패 | 해당 조합 러너가 아직 구현되지 않음 | 현재 지원되는 조합(문서 참고)으로 생성하거나 출시 대기 |
| `SECRET_FIELD_NOT_ALLOWED` | 422 | spec에 자격 증명으로 의심되는 필드가 포함되어 있습니다. | 입력값 때문에 요청이 거부됨 | 리소스 설정에 CSP 계정 비밀키 같은 값이 섞여 들어감 | 설정 폼에는 리소스 자체 설정만 입력. 계정 키는 마이페이지에서 관리 |
| `CREDENTIAL_NOT_FOUND` | 404 | 자격 증명을 찾을 수 없습니다. | 생성이 자격 증명 단계에서 멈춤 | 선택한 자격 증명이 없거나 provider가 안 맞음 | 마이페이지에서 해당 클라우드 자격 증명을 확인/등록 후 재시도 |
| `IDEMPOTENCY_KEY_REUSED` | 409 | 같은 Idempotency-Key로 다른 내용의 요청이 이미 존재합니다. | "이미 존재하는 요청"으로 거부됨 | 같은 키로 내용이 다른 요청이 재전송됨 | 새로고침 후 새로 요청. 이전 작업은 작업 목록에서 확인 |
| `PROVISIONING_JOB_NOT_FOUND` | 404 | 프로비저닝 작업을 찾을 수 없습니다. | 작업 상세/취소가 안 됨 | 잘못된 작업 ID이거나 삭제됨 | 작업 목록에서 유효한 작업을 다시 선택 |
| `JOB_NOT_CANCELLABLE` | 409 | 이미 종료된 작업은 취소할 수 없습니다. | 취소 버튼이 먹지 않음 | 작업이 이미 성공/실패/취소로 종료됨 | 취소 불필요. 결과를 작업 목록에서 확인 |
| `CONFIRMATION_REQUIRED` | 428 | 이 작업은 확인이 필요합니다. | 실행 직전에 한 번 막힘 | 되돌리기 어려운 작업이라 재확인 요구 | 확인 창에서 동의 후 다시 실행 |
| `CREDENTIAL_VERIFICATION_FAILED` | 422 | (러너별) 자격 증명에 필요한 필드가 없습니다 | 실행이 자격 증명 문제로 실패 | 등록된 키에 필수 필드가 빠졌거나 형식이 잘못됨 | 마이페이지에서 해당 자격 증명을 "수정"으로 키를 다시 넣고 재검증 |
| `TERRAFORM_ERROR` | (job) | (원문 stderr) | 실제 생성 단계에서 실패 | 리소스 설정값이 CSP 규칙에 안 맞음(이름·형식 등) | **§2 원문 번역**으로 구체 원인 표시 → 지적된 값을 고쳐 재시도 |
| `PROVIDER_AUTHENTICATION_FAILED` | (job) | (원문 stderr) | CSP 로그인 단계에서 실패 | 자격 증명이 만료/무효거나 잘못됨 | 마이페이지에서 자격 증명 재검증. 위임(역할)인 경우 신뢰 정책 점검 |
| `QUOTA_EXCEEDED` | (job) | — | 할당량 초과로 생성 실패 | 계정의 리소스 한도(vCPU·개수 등) 초과 | 불필요한 리소스 정리 또는 CSP에 한도 증설 요청 후 재시도 |
| `PROVIDER_API_ERROR` | (job) | — | CSP 쪽 오류로 실패 | CSP API 호출이 실패(일시 장애 포함) | 잠시 후 재시도. 계속되면 원문/`request_id`로 개발팀 문의 |
| `CLOUD_PERMISSION_DENIED` | (job) | — | 권한 부족으로 실패 | 자격 증명에 해당 작업 권한이 없음 | 필요한 권한을 부여하거나 권한 있는 자격 증명으로 재시도 |

### 동기화 (sync)

| 코드 | HTTP | 현재 메시지 | 증상 | 원인 | 해결책 |
|---|---|---|---|---|---|
| `SYNC_JOB_NOT_FOUND` | 404 | 동기화 작업을 찾을 수 없습니다. | 동기화 상태/취소가 안 됨 | 잘못된 작업 ID이거나 삭제됨 | 인벤토리에서 동기화를 다시 실행 |
| `JOB_ALREADY_RUNNING` | 409 | 이미 진행 중인 동기화 작업이 있습니다. | 새 동기화가 시작되지 않음 | 같은 계정에 진행 중인 동기화가 있음 | 진행 중인 동기화가 끝난 뒤 재시도 |
| `CLOUD_ACCOUNT_NOT_FOUND` | 404 | 클라우드 계정을 찾을 수 없습니다. | 동기화가 계정 단계에서 실패 | 대상 클라우드 계정이 없음 | 마이페이지에서 계정/자격 증명을 확인 |
| `JOB_NOT_CANCELLABLE` | 409 | 이미 종료된 작업은 취소할 수 없습니다. | 취소 버튼이 먹지 않음 | 동기화가 이미 종료됨 | 취소 불필요. 결과를 확인 |
| `CLOUD_PERMISSION_DENIED` | (item) | — | 특정 계정 동기화만 실패 | 검증된 자격 증명이 없거나 권한 부족 | 마이페이지에서 해당 자격 증명 검증/권한 확인 |
| `PROVIDER_API_ERROR` | (item) | — | 특정 계정에서 리소스 0건/오류 | CSP API 호출 실패(자격 증명 무효 포함) | 자격 증명 재검증. `request_id`로 원문 확인 |

> 참고: 동기화는 CSP 호출이 통째로 실패해도 "0건 발견 success"로 보일 수 있음(기존 결정 기록).
> 이 경우 코드가 없으므로 카탈로그가 아니라 **동기화 결과 안내 문구**로 "0건 = 자격 증명 점검" 힌트 필요.

### 리소스 액션 (start / stop / delete)

| 코드 | HTTP | 현재 메시지 | 증상 | 원인 | 해결책 |
|---|---|---|---|---|---|
| `RESOURCE_NOT_FOUND` | 404 | 리소스를 찾을 수 없습니다. | 액션 대상이 없음 | 잘못된 리소스이거나 이미 제거됨 | 목록 새로고침 후 다시 선택 |
| `UNSUPPORTED_OPERATION` | 422 | (일부) 이 리소스는 …를 지원하지 않습니다. | 특정 버튼이 안 먹음 | 해당 리소스 유형이 그 동작을 지원 안 함 | 지원되는 동작만 사용(예: S3는 삭제만) |
| `RESOURCE_ALREADY_DELETED` | 409 | 삭제된 리소스입니다. | 액션이 거부됨 | 이미 삭제된 리소스 | 목록 새로고침. 추가 작업 불필요 |
| `RESOURCE_STALE` | (item) | — | 액션이 사전 검사에서 막힘 | 로컬 정보가 오래됨(동기화 이후 미갱신) | 동기화 후 최신 상태에서 재시도 |
| `CLOUD_PERMISSION_DENIED` | 422 | 검증된 자격 증명이 없습니다 — 마이페이지에서 검증하세요. | 액션이 권한 문제로 거부 | 검증 자격 증명 없음/권한 부족 | 마이페이지에서 자격 증명 검증 후 재시도 |
| `PROVIDER_API_ERROR` | 422/502 | (일부) 자격 증명을 복호화하지 못했습니다. | CSP 호출이 실패 | CSP API 오류/일시 장애 | 잠시 후 재시도. `request_id`로 원문 확인 |
| `CREDENTIAL_VERIFICATION_FAILED` | (item) | — | 액션이 자격 증명 문제로 실패 | 키 필드 누락/무효 | 마이페이지에서 자격 증명 수정·재검증 |

> ⚠️ 리소스 액션은 현재 **코드만 저장하고 원인 메시지를 버림** → 5번 작업에서 `message` 저장 추가.

### 비용 (cost) — 2026-09-19 채택

`docs/비용_개발문서/05_API계약.md` §2-5 "추가 제안 9개" 중 `*_NOT_FOUND` 관례를 따르는 4개를 채택했다(3개는 PR 7, `COST_REVIEW_ITEM_NOT_FOUND`는 PR 8 · 2026-09-19).
`ACCOUNT_ALREADY_IN_TEAM`·`BUDGET_PERIOD_OVERLAP`은 `409 CONFLICT` + `details.reason`으로,
`BUDGET_PERIOD_TOO_LONG`은 `422 VALIDATION_ERROR`로 흡수한다. `BUDGET_EXCEEDED`는 ADR-042로 보류,
`COST_SETUP_REQUIRED`는 아직 쓰는 곳이 없다.

| 코드 | HTTP | 서버 메시지 | 증상 | 원인 | 해결책 |
|---|---|---|---|---|---|
| `TEAM_NOT_FOUND` | 404 | 팀을 찾을 수 없습니다. | 팀 조회/수정/예산이 안 됨 | 잘못된 팀 ID이거나 삭제됨 | 팀 목록 새로고침 후 다시 선택 |
| `TEAM_BUDGET_NOT_FOUND` | 404 | 예산을 찾을 수 없습니다. | 예산 수정/삭제가 안 됨 | 잘못된 예산 ID이거나 삭제됨 | 예산 목록 새로고침 후 다시 선택 |
| `COST_INGESTION_RUN_NOT_FOUND` | 404 | 비용 수집 실행을 찾을 수 없습니다. | 수집 실행 상세 조회가 안 됨 | 잘못된 실행 ID이거나 타 사용자 | 수집을 다시 실행한 뒤 그 ID로 조회 |
| `COST_REVIEW_ITEM_NOT_FOUND` | 404 | 검토 항목을 찾을 수 없습니다. | 검토 상태 변경이 안 됨 | 잘못된 항목 ID이거나 타 사용자 | 비용 작업 큐 새로고침 후 다시 선택 |

### 공통 (cross-cutting)

| 코드 | HTTP | 현재 메시지 | 증상 | 원인 | 해결책 |
|---|---|---|---|---|---|
| `VALIDATION_ERROR` | 422 | (details 동봉) | 입력값이 거부됨 | 필수 누락·형식 위반 등 | `details`가 가리키는 항목을 수정 후 재시도 |
| `AUTHENTICATION_REQUIRED` | 401 | (인증 필요) | 로그인 화면으로 튕김 | 로그인 안 됨/세션 만료 | 다시 로그인 |
| `CONFLICT` | 409 | (상태 충돌) | 요청이 현재 상태와 충돌 | 이미 처리됨/동시 변경 | 새로고침 후 재시도 |
| `INTERNAL_ERROR` | 500 | 예상하지 못한 오류가 발생했습니다. | 원인 불명 실패 | 서버 내부 오류 | `request_id`를 첨부해 개발팀에 문의 |

---

## §2. 원문 패턴 번역 시드 (raw stderr/CSP 메시지 → 친절 한글)

> `TERRAFORM_ERROR` 등 뭉뚱그려진 코드의 저장된 원문(마지막 2000자)을 패턴 매칭해 구체 원인으로
> 승격. 원문은 지우지 않고 위에 얹는다. 대소문자 무시(`lower()`) 매칭 권장.
> **당신 예시(대문자 이름)** 는 첫 행.

| 패턴(부분 문자열/정규식, 소문자 기준) | 친절 한글(증상+해결) | 예상 상황 |
|---|---|---|
| `invalidbucketname` · `bucket name` + 대문자/`_`/공백 포함 | 이름 규칙 위반: 소문자·숫자·하이픈(-)만 쓸 수 있어요. 대문자·공백·언더스코어는 불가합니다. | S3 버킷 이름 대문자 |
| `bucketalreadyexists` · `already exists` · `alreadyexists` · `entity already exists` · `name .* is already` | 이미 같은 이름이 존재합니다. 다른 이름으로 다시 시도하세요.(전역 고유 필요) | 버킷/계정명 중복 |
| `quotaexceeded` · `limitexceeded` · `vcpulimitexceeded` · `exceeded .* quota` · `too many` | 계정 할당량(quota)을 초과했습니다. 리소스를 정리하거나 CSP에 한도 증설을 요청하세요. | vCPU/개수 한도 |
| `addresslimitexceeded` | 공인 IP(EIP) 할당량을 초과했습니다. 미사용 IP를 회수 후 재시도하세요. | EIP 한도 |
| `unsupported .* region` · `invalidparametervalue.* region` · `optin` · `not opted in` | 선택한 리전에서 사용할 수 없거나 활성화되지 않은 리전입니다. 다른 리전을 선택하세요. | 리전 미지원/미활성 |
| `password .* (policy|conform|requirement)` · `master password` · `invalidparametervalue.*password` | 비밀번호 규칙 위반: 길이·문자 종류 조건을 확인하세요(마스터/관리자 비밀번호). | DB/VM 비밀번호 규칙 |
| `no default vpc` · `invalidvpcid` · `invalidsubnet` · `subnet .* not found` | 네트워크(VPC/서브넷) 설정 문제입니다. 네트워크 선택을 확인하세요. | 네트워크 |
| `accessdenied` · `unauthorizedoperation` · `is not authorized to perform` · `does not have permission` | 권한이 부족합니다. 자격 증명에 이 작업 권한을 추가하거나 권한 있는 자격 증명으로 시도하세요. | 권한(→ 코드도 CLOUD_PERMISSION_DENIED) |
| `invalidclienttokenid` · `authfailure` · `signaturedoesnotmatch` · `invalid_grant` | 자격 증명이 만료/무효입니다. 마이페이지에서 재검증하세요. | 인증 실패(→ PROVIDER_AUTHENTICATION_FAILED) |
| `invalidamiid` · `ami .* does not exist` | 지정한 이미지(AMI)를 찾을 수 없습니다. 이미지 선택을 확인하세요. | 잘못된 AMI |
| `timeout` · `context deadline exceeded` · `deadline` | 시간 초과로 실패했습니다. 잠시 후 다시 시도하세요. | 타임아웃 |
| `insufficient` + `capacity` · `insufficientinstancecapacity` | 해당 리전/타입의 CSP 용량이 부족합니다. 다른 타입이나 리전으로 시도하세요. | 용량 부족 |

> **폴백**: 어느 패턴에도 안 걸리면 코드 카탈로그의 고정 설명(증상/해결)만 보여주고, 원문은
> "상세 원인" 접기로 그대로 노출(개발자용).

---

## 다음 단계
- 이 표 확정 → **1번 작업(`app/error_catalog.py`)** 은 §1을, **2번 작업(`app/error_patterns.py`)** 은 §2를 그대로 옮겨 담는다.
- 문구 톤/해결책 동선만 검토해 주면 됨.
