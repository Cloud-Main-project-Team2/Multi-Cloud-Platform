# backend

FastAPI + PostgreSQL 16 + SQLAlchemy 2.0 + Alembic. `db`/`api`/`web` 세 서비스를 Docker Compose로 실행한다.

## 개발 환경 실행

```bash
cp backend/.env.example backend/.env   # 값을 직접 채워 넣는다(아래 "암호화 키 생성" 참고)
docker compose up --build
```

- API: http://localhost:8000 (`/health`, `/ready`)
- Web(정적 파일 골격): http://localhost:8080
- DB: localhost:5432

`api` 컨테이너는 기동 시 `docker-entrypoint.sh`가 순서대로 실행한다: `alembic upgrade head` → `python -m app.seed` → `uvicorn`.
`docker-compose.yml`의 `api`/`db` 서비스에는 로컬 개발이 바로 되도록 `.env.example`과 동일한 값의 기본값이 박혀 있다. 실제 값을 쓰려면 `backend/.env`를 만들고 compose 실행 전에 셸에서 export하거나 `docker compose --env-file backend/.env up`처럼 주입한다.

## 암호화 키 생성과 주입

`credentials.encrypted_payload`는 애플리케이션 계층에서 AES-256-GCM(`cryptography` 패키지)으로 암호화한다. 키는 base64로 인코딩된 32바이트(256비트) 값이어야 한다.

```bash
python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
```

생성한 값을 `CREDENTIAL_ENCRYPTION_KEY`에 넣는다(실제로 생성한 키 값을 이 문서나 커밋에 남기지 않는다). `CREDENTIAL_ENCRYPTION_KEY_VERSION`은 키 로테이션을 대비한 라벨(`v1`, `v2`, ...)로, 각 credential 행에 어떤 키 버전으로 암호화됐는지 같이 저장한다.

키가 없거나 base64로 디코딩되지 않거나 32바이트가 아니면 `app/security/credential_crypto.py`는 `CredentialEncryptionError`를 즉시 발생시킨다. 키 값이나 복호화된 평문은 예외 메시지·로그에 포함하지 않는다.

## 인증(JWT) — 최소 구현

`solcho/be-credentials-api` 세션에서 credentials API가 동작하려면 최소한의 로그인이 필요해
`POST /api/v1/auth/login`만 추가했다(회원가입·비밀번호 재설정·`/me`는 범위 밖 — `CLAUDE.md`
"핵심 아키텍처 결정" 참고). `JWT_SECRET_KEY`는 HMAC 서명 키로, 운영 환경에서는 반드시 긴
무작위 문자열로 교체한다.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

목업 데이터의 데모 계정으로 로그인하려면:

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@multicloud.example","password":"demo-pass-1234"}'
```

응답의 `data.access_token`을 `Authorization: Bearer <token>` 헤더로 credentials API에 사용한다.

## Migration 생성 및 적용

모델은 `app/models.py`, 마이그레이션은 `alembic/versions/`에 있다. 스키마 변경은 항상 Alembic으로만 한다 — 애플리케이션은 기동 시 `Base.metadata.create_all()`을 호출하지 않는다.

```bash
# 컨테이너 안에서 실행(DATABASE_URL이 db 서비스를 가리켜야 함)
docker compose run --rm api alembic revision --autogenerate -m "설명"
docker compose run --rm api alembic upgrade head
docker compose run --rm api alembic current
```

여러 `api` replica가 동시에 `alembic upgrade head`를 실행하면 동시 마이그레이션 문제가 생길 수 있다. 로컬 Compose 구성은 `api` 1개를 전제로 하며, 이 이상으로 replica를 늘리는 배포에서는 마이그레이션을 별도 1회성 job으로 분리해야 한다.

## Seed 실행 방식

`app/seed.py`의 `seed_service_catalog()`는 `service_catalog` 12개 행을 `INSERT ... ON CONFLICT (provider, service_code) DO UPDATE`로 원자적으로 upsert한다. 여러 프로세스가 동시에 실행해도 유니크 제약 위반 없이 안전하다. `docker-entrypoint.sh`가 매 기동마다 자동 실행하므로 별도 조작이 필요 없다. 수동 실행:

```bash
docker compose run --rm api python -m app.seed
```

## `/health` vs `/ready`

- `GET /health` — 프로세스 생존만 확인, DB에 접근하지 않는다. 항상 `{"status": "ok"}`.
- `GET /ready` — `SELECT 1`로 DB 연결을 확인한다. 실패 시 503과 `{"status": "unavailable"}`(비밀정보 없는 일반화된 메시지)을 반환한다.

## 자격 증명·재설정 토큰 보안 처리

- `credentials.encrypted_payload`/`encryption_nonce`: 평문 자격 증명 컬럼은 존재하지 않는다. provider별 자격 증명 JSON을 UTF-8 바이트로 직렬화 후 AES-256-GCM으로 암호화하며, nonce는 암호화마다 새로 생성한다(재사용 없음). 인증 태그는 암호문에 포함되어 저장되므로 변조된 암호문은 복호화 단계에서 실패한다.
- `password_reset_tokens.token_hash`: 원본 재설정 토큰은 저장하지 않는다. 발급 시 안전한 난수 토큰을 생성해 사용자에게만 전달하고, DB에는 SHA-256 이상의 단방향 해시만 저장한다. 검증 시 입력 토큰을 동일하게 해시해 `token_hash`와 비교한다(이번 범위에는 발급/검증 API가 없고 스키마만 구현됨).
- 비밀번호는 `users.password_hash`에 해시만 저장한다(해시 알고리즘 선택은 인증 라우터 구현 시 결정, 이번 범위 밖).


## DB 확장(2964dfe0a706) — 프로비저닝 요청/실행 분리, 리소스 유형, 비용 이력, 감사 이벤트
### provisioning_requests vs provisioning_jobs

- `provisioning_requests`: 사용자의 한 번의 프로비저닝 **의도**(부모). `common_spec_json`, `resource_type_id`, `request_key`(멱등 키)를 가진다.
- `provisioning_jobs`: 한 credential·한 provider·한 Terraform workspace에 대한 실제 **실행 단위**(자식). `provisioning_request_id`로 부모를 참조한다.
- 한 요청에 여러 실행(job)을 연결할 수 있도록만 설계했다. **여러 계정을 선택했을 때 실제로 몇 개의 job이 어떤 조합 규칙으로 생성되는지는 아직 정책이 확정되지 않았다** — 이 migration은 그 조합 규칙을 구현하지 않는다.
- 요청(`provisioning_requests.status`)은 자식 job들의 상태를 집계한 값이다. DB trigger로 자동 계산하지 않으며, 서비스 계층이 하나의 transaction 안에서 자식 job 상태를 읽고 부모 상태를 갱신해야 한다.
- `provisioning_jobs.terraform_state_ref`에는 Terraform state 본문이나 민감 output을 직접 저장하지 않는다. **안전한 외부 저장소(S3/GCS backend 등)의 참조(키·경로)만** 저장한다.

### service_catalog vs resource_types

- `service_catalog`: provider의 서비스 단위(예: "EC2")에 대한 공통 분류.
- `resource_types`: 그 서비스가 실제로 다루는 **CSP 원본 리소스 종류**(예: "EC2 Instance")를 정규화한 테이블. `type_code`는 안정적인 코드이고, `resources.original_resource_type`은 수집 원문 문자열 보존용으로 그대로 유지된다.
- 이번 migration은 12개 provisionable 서비스 각각에 최소 1개 타입만 시드했다(전부 `provisionable=true`, `supports_start/stop/delete`는 안전하게 기본값 `false`). `gcp/cloud_cdn`만 자체 CDN 리소스 개념이 없어 형제 CDN 서비스(CloudFront distribution, Azure CDN endpoint)와 일관되게 "distribution"으로 이름 붙였다 — 실제 CSP 용어 확인 후 조정될 수 있다.
- `resources.resource_type_id`는 **nullable로 유지**한다. 기존 행은 `service_catalog_id`로 확실하게(1서비스-1타입) 매핑 가능한 경우에만 migration에서 backfill했고, 애매한 매핑은 만들지 않았다. 리소스 수집 코드가 항상 이 값을 채우게 된 뒤에야 별도 migration에서 NOT NULL 전환을 검토할 수 있다.

### 리소스 최신 비용 요약 vs 비용 이력

- `resources.estimated_monthly_cost`/`collected_cost_amount`/`cost_currency`/... : 화면 빠른 조회용 **최신 스냅샷 하나**.
- `cloud_resource_costs`: 기간별 비용 레코드의 **전체 이력**. `(provider, source_record_key)`로 재수집 시 멱등 처리한다(같은 레코드를 다시 수집해도 중복 삽입되지 않음).
- `cost_kind`는 `actual | estimated | list_price_estimate` 중 하나이며, 서로 다른 종류·통화를 같은 의미로 합산하지 않는다. 각 레코드는 `period_start`/`period_end`(기간), `as_of`(기준 시각), `source`(수집 출처)를 함께 가진다.
- GCP의 실제(actual) 비용 수집 방식과 필요한 권한은 **아직 미확정**이다. `provider` 값 자체는 지원하되, 구체적인 API·권한은 이번 범위에서 구현하거나 확정하지 않는다.

### audit_events

- 일반 애플리케이션 로그와 별개로, 감사 가능한 보안·파괴적 작업(credential 생성/검증/삭제, 리소스 시작/중지/삭제, 프로비저닝 요청/완료, 회원 탈퇴)을 구조화해 저장한다.
- `metadata_json`에는 **다음을 절대 넣지 않는다**: 비밀번호·JWT, 클라우드 access/secret key나 service account JSON, 복호화된 credential payload, Terraform secret variable·민감 output, 비밀번호 재설정 원본 토큰.
- 이 테이블에 대한 수정·삭제 API는 만들지 않는다. 보존 기간은 미확정이므로 자동 삭제 정책도 만들지 않았다.

### migration 안전성 메모

- `2964dfe0a706`은 기존 데이터가 있다고 가정한다: `provisioning_jobs.provisioning_request_id`는 nullable로 추가 → 각 기존 job에 합성 `provisioning_requests` 부모를 backfill(`request_key = 'legacy-job-<job.id>'`) → NULL이 남아있으면 migration 자체가 실패하도록 검증 → 그제서야 NOT NULL로 전환한다. `resources.resource_type_id`는 위에서 설명한 대로 계속 nullable이다.
- `downgrade()`는 `provisioning_requests`/`resource_types`/`cloud_resource_costs`/`audit_events`와 그 안의 데이터, 그리고 `provisioning_jobs`/`resources`에 채워진 backfill 결과를 되돌릴 수 없이 삭제한다. 실행 시 경고 메시지를 출력하며, 운영 데이터가 있는 환경에서는 백업 없이 실행하지 않는다.


## 이번 단계에서 구현하지 않은 것

- 회원가입·로그인·클라우드 리소스 조회 등 비즈니스 API 라우터
- 실제 AWS/Azure/GCP SDK 호출, Terraform 실행
- 파일 기반 로깅 미들웨어
- 보고서, 예산, 보안 finding
- 프론트엔드 화면(정적 파일은 `frontend/`에 이미 있고 `web` 서비스는 그것을 그대로 서빙만 함)
- 다중 계정 프로비저닝의 조합 규칙(`provisioning_requests` 1개에 `provisioning_jobs`를 여러 개 연결할 수 있도록 DB만 설계했고, 실제로 몇 개를 어떤 규칙으로 생성할지는 아직 정책 미확정)

## 운영 환경 참고

로컬 `docker-compose.yml`의 DB 비밀번호·암호화 키 기본값은 개발 편의용 placeholder다. 운영 환경에서는 `POSTGRES_PASSWORD`, `CREDENTIAL_ENCRYPTION_KEY`를 compose 파일이나 `.env`에 두지 말고 AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault 같은 secret manager에서 런타임에 주입해야 한다.
