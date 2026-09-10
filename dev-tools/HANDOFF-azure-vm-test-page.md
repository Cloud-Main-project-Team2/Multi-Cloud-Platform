# 핸드오프: Azure VM 프로비저닝 테스트 페이지 제작 요청

다른 AI/개발자에게 이 문서를 그대로 전달하면 됩니다. 목적은 **이미 구현된 백엔드 API가
실제로 동작하는지 웹페이지로 확인(테스트)**하는 것입니다 — 새 백엔드 기능을 만드는 게
아니라, 이미 있는 API를 호출하는 프론트만 만들면 됩니다.

이미 참고용 구현이 하나 있습니다: `dev-tools/azure-vm-provisioning-test.html`. 그대로 써도
되고, 더 잘 만들거나 다른 방식(React 등)으로 새로 만들어도 됩니다.

---

## 0. 프로젝트 컨텍스트

- 저장소: Multi-Cloud-Platform (멀티클라우드 통합 운영 대시보드)
- 백엔드: FastAPI + PostgreSQL, `docker compose up -d`로 로컬 구동 (API: `http://localhost:8000`)
- **인증은 아직 임시**입니다 — 실제 JWT/로그인이 없고, `Authorization: Bearer <user_id>`
  헤더에 숫자만 넣으면 그 user_id로 인증된 것처럼 처리됩니다(`app/security/auth.py`).
- **CORS 전체 허용**(`app/main.py`) — 어떤 origin에서 fetch해도 되고, `file://`로 페이지를
  직접 열어도 됩니다(빌드/서버 불필요).
- 공식 API 계약 문서: `docs/01_API_명세서_v1.1.md` (10절 프로비저닝, 6.2절 credential)

## 1. 실행 방법

```bash
cd Multi-Cloud-Platform
docker compose up -d
curl http://localhost:8000/health   # {"status":"ok"} 확인
```

## 2. 순서대로 호출해야 하는 API 3+1개

### (1) 테스트 사용자 생성 — `POST /api/v1/dev/users`

⚠️ 정식 스펙이 아닌 dev 전용 엔드포인트(회원가입 API가 없어서 임시로 만든 것).

```http
POST /api/v1/dev/users
Content-Type: application/json

{"email": "tester@example.com", "name": "Tester"}
```

**201**:
```json
{"data": {"id": "1", "email": "tester@example.com", "name": "Tester"}}
```

→ 이 `id`를 이후 모든 요청의 `Authorization: Bearer <id>` 헤더 값으로 쓴다.
이메일 중복 시 `409 EMAIL_ALREADY_EXISTS`.

### (2) Azure credential 등록 — `POST /api/v1/credentials/azure`

```http
POST /api/v1/credentials/azure
Authorization: Bearer <user_id>
Content-Type: application/json

{
  "external_account_id": "<Azure subscription id>",
  "account_label": "표시용 이름",
  "name": "provisioner",
  "public_identifier": "<client_id>",
  "secret_payload": {
    "tenant_id": "<Azure AD tenant id>",
    "client_id": "<서비스 프린시펄 client id>",
    "client_secret": "<서비스 프린시펄 client secret>",
    "subscription_id": "<Azure subscription id>"
  },
  "tags": {},
  "display_order": 0
}
```

**201**:
```json
{
  "data": {
    "id": "1",
    "cloud_account_id": "1",
    "name": "provisioner",
    "masked_public_identifier": "abcd••••••",
    "permission_scope": {},
    "verified": false,
    "verified_at": null,
    "tags": {},
    "display_order": 0,
    "created_at": "...",
    "updated_at": "..."
  }
}
```

→ 이 `id`(credential_id)를 다음 단계에 쓴다. **실제 Azure 검증은 하지 않는다** — 항상
`verified=false`로 저장만 한다(정책 미확정 상태이기 때문, 의도된 동작).

에러: `401 AUTHENTICATION_REQUIRED`(헤더 없음), `422 VALIDATION_ERROR`(지원 안 하는
provider), `422 SECRET_FIELD_NOT_ALLOWED`(`tags`에 비밀스러운 이름의 필드를 넣은 경우),
`409 CREDENTIAL_ALREADY_EXISTS`(같은 계정에 같은 이름의 credential이 이미 있음).

### (3) Azure VM 생성 요청 — `POST /api/v1/provisioning/azure/vm`

```http
POST /api/v1/provisioning/azure/vm
Authorization: Bearer <user_id>
Idempotency-Key: <요청마다 새로 생성하는 UUID>
X-Action-Confirmed: true
Content-Type: application/json

{
  "credential_id": 1,
  "common_spec": {"name": "mcp-test-vm"},
  "provider_spec": {
    "location": "koreacentral",
    "vm_size": "Standard_B1s",
    "admin_username": "azureuser",
    "ssh_public_key": "ssh-rsa AAAA... 또는 ssh-ed25519 AAAA...",
    "create_public_ip": true
  }
}
```

**202**:
```json
{
  "data": {
    "id": "1",
    "status": "queued",
    "created_at": "...",
    "status_url": "/api/v1/provisioning/jobs/1"
  }
}
```

에러: `400 IDEMPOTENCY_KEY_REQUIRED`(헤더 없음), `428 CONFIRMATION_REQUIRED`
(`X-Action-Confirmed: true` 없음), `422 SECRET_FIELD_NOT_ALLOWED`, `422 VALIDATION_ERROR`
(`common_spec.name` 없음/`provider_spec` 필드 오류), `404 SERVICE_NOT_FOUND`,
`404 CREDENTIAL_NOT_FOUND`(다른 사용자 소유 포함), `409 IDEMPOTENCY_KEY_REUSED`.

### (4) job 상태 폴링 — `GET /api/v1/provisioning/jobs/{job_id}`

```http
GET /api/v1/provisioning/jobs/1
Authorization: Bearer <user_id>
```

**200**:
```json
{
  "data": {
    "id": "1",
    "credential_id": "1",
    "service_catalog_id": "5",
    "workspace_name": "user-1-job-1",
    "common_spec": {"name": "mcp-test-vm"},
    "provider_spec": {"...": "..."},
    "status": "queued | running | success | failed",
    "progress_percent": 0,
    "created_resource_count": 0,
    "result": null,
    "error": null,
    "created_at": "...",
    "started_at": null,
    "finished_at": null
  }
}
```

`status`가 `success`/`failed`/`cancelled`가 될 때까지 (예: 3초 간격으로) 반복 호출한다.
백그라운드에서 실제 Terraform이 실행되는 데 시간이 걸린다(정상 케이스 수십 초~수 분,
자격증명이 잘못됐으면 더 빨리 `failed`로 끝난다).

## 3. 참고할 실제 코드

- `backend/app/routers/provisioning.py`, `credentials.py`, `dev_tools.py` — 위 API들의 실제 구현
- `backend/app/schemas/provisioning.py`, `credentials.py` — 요청 body 스키마
- `dev-tools/azure-vm-provisioning-test.html` — 이미 만들어진 참고 페이지(순수 HTML+fetch,
  빌드 없음). 그대로 재사용하거나 개선해도 된다.
- `docs/01_API_명세서_v1.1.md` — 공식 API 계약(10절 프로비저닝, 6.2절 credential, 2.8절
  오류 응답 형식)

## 4. 주의사항

- **실제 Azure 구독/Service Principal**이 있어야 진짜 VM 생성 성공까지 확인할 수 있다.
  가짜 자격증명을 넣으면 Terraform apply 단계에서 Azure AD 인증이 실패해 `failed`로
  끝난다 — 이것도 API가 올바르게 오류를 반영하는지 확인하는 유효한 테스트다.
- (3)번 요청은 **실제 과금 리소스 생성을 시도**한다. 테스트 후 Azure 포털에서 직접 리소스
  그룹을 지워야 한다(이 프로젝트엔 아직 삭제 API가 없음).
- `id`류 필드는 응답에서 문자열로 오지만(JS 숫자 정밀도 문제 회피), 요청 body의
  `credential_id`는 숫자로 보내야 한다(`Number(...)` 변환 필요).
- 응답 오류는 전부 `{"error": {"code", "message", "request_id"}}` 형태다.
