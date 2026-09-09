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

## 이번 단계에서 구현하지 않은 것

- 회원가입·로그인·클라우드 리소스 조회 등 비즈니스 API 라우터
- 실제 AWS/Azure/GCP SDK 호출, Terraform 실행
- 파일 기반 로깅 미들웨어
- 비용 이력, 보고서, 예산, 보안 finding
- 프론트엔드 화면(정적 파일은 `frontend/`에 이미 있고 `web` 서비스는 그것을 그대로 서빙만 함)
- 다중 계정 프로비저닝의 조합 규칙(`provisioning_jobs`는 현재 "credential 1개 × 서비스 1개 = 실행 1건" 단위로만 설계됨)

## 운영 환경 참고

로컬 `docker-compose.yml`의 DB 비밀번호·암호화 키 기본값은 개발 편의용 placeholder다. 운영 환경에서는 `POSTGRES_PASSWORD`, `CREDENTIAL_ENCRYPTION_KEY`를 compose 파일이나 `.env`에 두지 말고 AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault 같은 secret manager에서 런타임에 주입해야 한다.
