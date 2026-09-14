# IAM 인증 전환 — 실제 코드 적용 가능성 검증 결과

작성일: 2026-09-14
대상 문서: **"멀티 클라우드 연결 인증 전환 설계안"(2026-09-14)**
방법: Claude Code(저장소 직접 접근 세션)가 저장소를 직접 읽어 설계안을 실제 코드베이스에 반영 가능한지 검증.
전제: 모든 판정에 코드 근거를 붙인다. 실제 코드에서 확인하지 못한 내용은 확정하지 않는다.

---

## 0. 한 줄 결론 + 문서 전제 정정

설계안의 방향(장기 키 제거 → 위임 기반 임시 인증)은 이 코드베이스에 **개념적으로 부합하고 토큰 주입 계층 일부는 이미 준비돼 있으나**, 세 클라우드 모두 "그대로 바로 적용"은 불가능하다. **가장 큰 미결정 변수 — 백엔드가 프로덕션에서 어디서 어떤 신원으로 도는가 — 가 저장소에 아예 정의돼 있지 않기** 때문이다.

**설계안 §2의 스택 단서는 이 저장소와 틀리다.** `package.json`·`wrangler`·`cloudflare`·React·Vinext는 **저장소에 존재하지 않는다**(검색 0건). 실제는 **FastAPI(Docker 컨테이너) + 정적 Vanilla JS(nginx) + Terraform CLI**다. 설계안이 상상한 "Cloudflare/서버리스" 전제로 검증하면 안 된다.

---

## 1. 저장소 직접 확인 결과 (코드 근거)

### ① 백엔드 / 클라우드 호출 런타임
- 단일 **FastAPI(uvicorn) 프로세스 = Docker 컨테이너**(`backend/Dockerfile`, `docker-compose.yml`). 별도 워커/큐 없음 — 리소스 동기화·프로비저닝은 **같은 프로세스의 FastAPI `BackgroundTasks`**에서 실행(`app/terraform_runner.py` docstring 3–13행, `app/routers/sync_jobs.py`, `app/routers/provisioning.py`). Terraform은 컨테이너 안 **subprocess**로 구동.
- **프로덕션 호스팅/배포 신원 설정이 저장소에 전혀 없음**: wrangler·serverless·k8s·ECS/Lambda·CI-CD 배포 워크플로우 모두 0건. `docker-compose.yml`은 로컬 개발용. `backend/terraform/{aws,azure,gcp}`는 **고객 리소스 생성용 모듈**이지 서비스 호스팅 인프라가 아니다. → **"백엔드가 어디서 도는가"는 코드상 미정.**

### ② 프런트엔드 스택
- 빌드 도구 없는 **순수 정적 HTML + Vanilla JS + Tailwind CDN**, nginx 서빙(`docker-compose.yml` web). React/Vinext/번들러 없음.

### ③ 자격증명 저장 구조
- `credentials` 테이블(`app/models.py:135`): `encrypted_payload`(bytes) + `encryption_nonce` + `encryption_key_version` — **AES-256-GCM 암호화 저장**(`app/security/credential_crypto.py`). 평문 아님. `permission_scope`(JSONB)·`verified`도 보유.
- 저장하는 비밀의 실체(`app/providers/__init__.py:33` `REQUIRED_SECRET_FIELDS`): **AWS** `access_key_id`/`secret_access_key`(장기 IAM 키), **Azure** `client_id`/`client_secret`/`tenant_id`(앱 장기 시크릿), **GCP** 서비스계정 키 JSON 전체(`private_key` 포함). → **설계안이 없애려는 바로 그 장기 비밀 3종을 지금 수집·저장 중.**

### ④ 그 자격증명의 실제 사용처
- 복호화(`decrypt_credential_json`) 호출부: `routers/credentials.py:399`, `routers/resources.py:337`, `routers/sync_jobs.py:232`, `routers/provisioning.py:446`, `dev_destroy_job.py:41`.
- SDK 세션: AWS `boto3.client(..., aws_session_token=...)`(`providers/aws.py:19–27`), Azure `ClientSecretCredential`(`providers/azure.py:23,57`), GCP `service_account.Credentials.from_service_account_info`(`providers/gcp.py:13,49,68`).
- Terraform 주입: AWS/Azure는 `credential_env`(env var), GCP는 `credentials_file`(0600 임시 파일 → `GOOGLE_APPLICATION_CREDENTIALS`) — `terraform_runner.run_apply()` 및 각 러너의 `_credential_env()`.

### 문서 §2 대조
- "Access Key 수집은 사용자 제공 정보" → **코드로 사실 확인됨**(장기 비밀 저장 중).
- "백엔드 실행 위치 미확정" → **사실**(배포 설정 없음).
- "package.json/Cloudflare 단서" → **이 저장소엔 거짓**.

---

## 2. 클라우드별 적용 가능성 판정

세 클라우드 모두 공통 전제: "장기 비밀 완전 제거"는 백엔드가 **자기 자신의 신뢰된 클라우드 신원**(실행 Role/워크로드 ID)을 가질 때만 성립 — 이건 §1①의 미정 사항에 종속된다.

### AWS — 「코드·스키마 변경 필요, 실행환경상 불가능하지는 않음」 (셋 중 가장 유리)
- 근거: 주입 계층이 **이미 임시 자격증명 호환**. SDK `_client`가 `session_token`을 이미 받고(`providers/aws.py:24`), 프로비저닝 `_credential_env`도 `AWS_SESSION_TOKEN`을 이미 주입(`aws_provisioning.py:116`). 즉 **STS AssumeRole 결과(임시 키+세션토큰)를 소비하는 하류는 거의 그대로 재사용** 가능.
- 필요 변경: 저장 모델을 키 → `role_arn`+`external_id`로, AssumeRole 실행 단계 추가. **단, AssumeRole을 호출할 "출발 신원"이 필요** — 백엔드가 AWS 위(ECS Task Role/EC2 instance profile)면 무(無)비밀로 깔끔, AWS 밖이면 최소한의 부트스트랩 자격(또는 OIDC)이 여전히 필요. → "장기 키 0" 여부는 **호스팅에 종속**.

### Azure — 「코드·스키마 변경 필요 + 실행환경 결정 먼저 필요」
- 현재는 **장기 앱 시크릿**(`ClientSecretCredential`, `providers/azure.py`). 무(無)시크릿으로 가려면 워크로드 ID 연동(Entra) 또는 Lighthouse 위임이 필요한데, **어느 쪽이든 우리 실행 주체가 시크릿 없이 토큰을 얻을 수 있는 환경**이 전제. 설계안 스스로도 Lighthouse vs Entra를 미확정으로 둠(§5) — 그 결정은 리소스 관리 API와 비용 API 지원 범위를 각각 검증한 뒤에 가능. SDK 어댑터를 `ClientSecretCredential` → 연동/관리ID 자격으로 교체해야 함.

### GCP — 「코드·스키마 변경 필요 + 실행환경 결정 먼저 필요」
- 현재는 **서비스계정 키 JSON 전체 저장**(`from_service_account_info`, `providers/gcp.py`). WIF로 가려면 백엔드가 **연동 가능한 외부 신원(OIDC/AWS 등)**을 제시할 수 있어야 함 → 환경 종속. GCP 위 실행이면 연결 SA + 가장(impersonation)으로 깔끔.
- 유리한 점: Terraform GCP 경로가 이미 **자격 파일 주입 방식**(`_write_credentials_file`)이라, "키 JSON 파일" → "WIF 설정 파일"로 **내용만 교체**하면 기제는 재사용 가능. 설계안 §6의 "WIF 설정 파일에 임의 URL/실행 명령 미검증 전달 금지" 지적은 타당(입력 검증 필요).

> **종합**: 셋 중 어느 것도 "인프라 변경 없이 바로 적용"에 해당하지 않는다. 모두 (a) 임시 자격 획득 단계 신설 + (b) 백엔드의 신뢰된 first-party 클라우드 신원이 필요하고, (b)는 호스팅 미정이라 지금 확정 불가.

---

## 3. 반영 시 건드릴 구체 대상 (신규 vs 수정)

### 신규
- 클라우드별 **임시자격 획득 어댑터**(예: `app/cloud_identity/{aws,azure,gcp}.py`) — 연결 메타데이터로 AssumeRole/토큰 교환 수행.
- **단기 토큰 캐시**(연결×권한범위별 격리 + 동시 갱신 제어) — 지금은 단일 프로세스라 in-memory도 가능하나 워커 분리 시 재설계 필요.
- **연결 설정 엔드포인트**: External ID 생성, CFN 템플릿 / Lighthouse 오퍼 / WIF 풀 설정 제공, `role_arn`·위임 식별자 등록·검증.

### 수정
- `app/models.py`(`Credential`/`CloudAccount`) + **신규 Alembic 마이그레이션**: AWS `role_arn`/`external_id`, Azure 위임/앱 식별자, GCP `pool/provider`/`service_account_email`. 장기 비밀 필드 제거.
- `app/providers/__init__.py`(`REQUIRED_SECRET_FIELDS`, `verify_credential`), `app/providers/{aws,azure,gcp}.py`(세션 생성부를 어댑터 경유로).
- **프로비저닝 러너 8개**의 `_credential_env`/`credentials_file`: `aws_provisioning.py`·`aws_rds_provisioning.py`·`aws_s3_provisioning.py`·`aws_cloudfront_provisioning.py`·`azure_provisioning.py`·`azure_storage_provisioning.py`·`azure_database_provisioning.py`·`gcp_*` — 임시 자격/연동 토큰을 받도록.
- 복호화 호출부 4곳(`routers/{credentials,resources,sync_jobs,provisioning}.py`) → "임시 자격 획득"으로 대체.
- 프런트 `frontend/assets/js/mypage.js` + `frontend/mypage.html`: "키 붙여넣기" UI → 연결 플로우(콘솔 링크·템플릿·식별자 등록·상태). 설계안 §7 그대로.

---

## 4. 팀이 먼저 결정해야 할 것 (추측하지 않음)

1. **백엔드 프로덕션 호스팅 위치/신원** — 저장소에 근거 없음. 갈래:
   - **어느 한 클라우드 위**: 그 클라우드는 네이티브 실행 신원으로 무(無)비밀 가능. 그러나 멀티클라우드라 **나머지 두 클라우드로는 여전히 연동(WIF/federation)이 필요**.
   - **클라우드 밖(자체 VM 등)**: WIF용 OIDC 발급 능력이 있거나, 최소 부트스트랩 자격이 필요 — "장기 키 완전 제거"가 조건부가 됨.
2. **연결 소유권 = 개인 vs 조직**: 현재 스키마는 `cloud_accounts.user_id`(개인 단위, `models.py:117`)뿐, **조직 개념이 없음**. 설계안 §7은 조직 소유를 가정 — 데이터 모델 결정 필요.
3. **단일 프로세스 유지 vs 워커 분리**: 토큰 캐시·동시 갱신 설계가 갈린다.

---

## 5. 최종 결론 (4버킷)

### A. 지금 바로 반영 가능
- 거의 없음(순수 인프라 무변경 기준). 유일하게 근접: **AWS 하류 소비 계층이 이미 임시자격 호환**이라 별도 손댈 게 적음(`providers/aws.py:24`, `aws_provisioning.py:116`).

### B. 코드·스키마 변경 후 반영 가능 (실행환경이 "클라우드 위"로 정해졌다는 전제)
- AWS AssumeRole+External ID 전환(모델/어댑터/러너/등록 UI). Azure·GCP도 어댑터를 연동 자격으로 교체 + 스키마 변경.
- 기존 강점 재사용: AES-GCM 저장소, 앱 계층 소유권 검사(`current_user`), `permission_scope`에서 **cost_read를 이미 별도 취급**(`providers/aws.py` cost_read = Cost Explorer, Azure/GCP는 False 고정 — 설계안 "연결 성공 ≠ 비용 접근"과 일치), 에러 분류(auth/permission/timeout, `terraform_runner._classify_error`), secret redaction.

### C. 실행 환경 결정이 먼저 필요 (그 전엔 확정 불가)
- 세 클라우드의 "무(無)비밀" 성립 여부 전부 — §4-1 호스팅 결정에 종속.
- Azure Lighthouse vs Entra WIF 택일(설계안도 미확정) — 리소스/비용 API 지원을 각각 PoC 검증 후.
- 토큰 캐시 아키텍처(프로세스/워커).

### D. 설계안 자체를 수정해야 함
- **§2의 "package.json/React/Wrangler/Cloudflare" 단서 삭제** — 이 저장소 사실무근. 수정 문안 제안:
  > *"실행 환경: 백엔드는 FastAPI 단일 프로세스(Docker), 클라우드 호출은 동일 프로세스의 BackgroundTasks + Terraform subprocess로 수행. 프로덕션 호스팅/실행 신원은 저장소에 미정의 — PoC 전 결정 필요. (이전 문서의 Cloudflare/Wrangler 언급은 본 저장소와 무관, 삭제.)"*
- **§7 조직 소유 전제**는 현재 개인 단위 스키마와 어긋남 — "조직 모델 도입은 스키마 변경 선행"이라고 명시.
- **최소 권한 정책은 완성본으로 제시 금지**(설계안도 이미 경고): 실제 호출 API 목록이 `resource_actions.py`/각 러너/`providers/*`에 흩어져 있으므로, 그 목록을 먼저 추출해 기능별(inventory_read/resource_control/provision/cost_read — 이미 `permission_scope` 4분류 존재)로 매핑한 뒤 확정.

### PoC로 먼저 증명할 것
1. 정해진 호스팅에서 백엔드의 first-party 신원 확보.
2. AWS AssumeRole(External ID)로 임시키 → 기존 `_client`/`_credential_env`에 그대로 흘려 실제 읽기 1건.
3. Azure/GCP 각 1건 무(無)비밀 토큰 획득.
4. 토큰 만료 자동 갱신.
5. 타 조직 connection_id / External ID 불일치 거부.

---

> 본 문서는 검증 결과 기록이며, 설계안 자체의 수정본이 아니다. 후속으로 (1) 설계안 §2·§7·§11 수정 패치, 또는 (2) AWS 단일 대상 PoC 브랜치로 코드 증명을 진행할 수 있다.
