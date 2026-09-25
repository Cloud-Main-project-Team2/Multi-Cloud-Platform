# EC2 배포 가이드

> 이 문서는 Multi-Cloud Platform을 **단일 EC2 인스턴스에 docker compose로 배포**하고 **HTTPS**를 붙이는
> 절차를 다룬다. 위에서부터 차례로 따라 하면 처음부터 다시 배포할 수 있다.
> 작성 2026-09-20, **개정 2026-09-25**(실배포 반영 — HTTPS 구조 전환, 절차 순서화, 실제 트러블슈팅 추가).
> 관련 문서: 역할 위임 IAM 설정은 `AWS_Delegation_Role_Setup_Guide.md`, 위임 개념 설명은
> `AWS_AssumeRole_Delegation_Explainer.md`.

## 0. 전제와 범위

- **단일 인스턴스, 단일 프로세스.** api replica는 1개다. `BackgroundTasks`(프로비저닝·동기화),
  in-memory 취소 플래그(`_CANCEL_REQUESTED`), APScheduler 비용 수집, alembic 마이그레이션이 전부
  "프로세스가 하나"를 전제한다. 늘리면 비용이 이중 수집되고 취소가 동작하지 않는다.
- postgres도 같은 인스턴스에 컨테이너로 올린다(RDS 분리는 §13 참고).
- HTTPS는 **호스트 nginx + Let's Encrypt(certbot)**로 처리한다(§9).

### 최종 구조

```
사용자 ──80 (http)───▶ 호스트 nginx ──301──▶ https로 재접속
사용자 ──443 (https)─▶ 호스트 nginx (TLS 종료, Let's Encrypt 인증서)
                          │
                          └─▶ 127.0.0.1:8080 ─▶ [도커] web (nginx)
                                                   ├─ 정적 파일 (frontend/)
                                                   └─ /api/ ─▶ [도커] api:8000 ─▶ [도커] db:5432
```

| 구성 요소 | 어디서 도나 | 외부 노출 |
|---|---|---|
| 호스트 nginx | EC2 호스트 (apt) | 80, 443 |
| certbot | EC2 호스트 (snap, 자동 갱신 타이머) | — |
| `web` (nginx 컨테이너) | docker compose | `127.0.0.1:8080`만 |
| `api` (FastAPI) | docker compose | `127.0.0.1:8000`만 |
| `db` (postgres:16) | docker compose | `127.0.0.1:5432`만 |
| `mailhog` | **운영에서는 띄우지 않는다** | — |

**왜 TLS를 호스트 nginx에서 처리하나**: certbot이 호스트 nginx 기준으로 인증서를 발급·갱신하므로
`certbot renew`가 추가 설정 없이 그대로 동작한다. 도커 nginx에 인증서를 넣으려면 인증서 마운트 +
webroot 방식 갱신 + 갱신 후 컨테이너 reload를 따로 구성해야 한다. 도커 `web`은 로컬과 똑같은 설정
(`nginx/default.conf`, `listen 80`)을 그대로 쓰고, 운영에서는 호스트 루프백에만 묶는다.

### 배포 주소 (확정)

| 항목 | 값 |
|---|---|
| 도메인 | **`mcp.greatsounds.me`** (A 레코드 → 아래 IP) |
| EC2 퍼블릭 IP | `43.200.50.51` (새 인스턴스면 바뀐다 → §8-1 DNS 갱신) |
| 서비스 URL | **`https://mcp.greatsounds.me`** (http로 들어오면 https로 이동) |
| 리전 | `ap-northeast-2` |
| 레포 위치 | `~/Multi-Cloud-Platform` |

> 이 문서에 나오는 `mcp.greatsounds.me`는 전부 실제 배포 주소다. 다른 환경에 올릴 때 바꿀 곳:
> `.env`의 `FRONTEND_BASE_URL`, 호스트 nginx 설정의 `server_name`·인증서 경로(§9), certbot `-d` 인자.

---

## 1. 인스턴스 사양

| 항목 | 값 |
|---|---|
| 인스턴스 타입 | **t3.medium** (2 vCPU / 4 GiB) |
| 스토리지 | **gp3 40 GB** |
| OS | **Ubuntu 24.04 LTS** (이 문서의 명령은 전부 24.04 기준) |
| 계정 | **플랫폼 AWS 계정과 같은 계정** (§4-4) |
| IAM 인스턴스 프로파일 | `mcp-platform-ec2-role` (§4) |
| 고급 세부 정보 → 메타데이터 | IMDSv2 필수, **응답 홉 제한 = 2** (§4-5) |

### 왜 4 GiB인가 — 사양을 결정하는 건 API가 아니라 terraform이다

| 구성 요소 | 메모리 |
|---|---|
| `postgres:16` (기본 설정) | 200–300 MB |
| uvicorn 1 프로세스 | **400–600 MB** — boto3 + azure-mgmt 4종 + google-cloud 3종을 전부 import한다 |
| `terraform apply` subprocess | **회당 300–500 MB** (provider 플러그인) |
| nginx(호스트 + 컨테이너) + 기타 | ~60 MB |

프로비저닝은 `BackgroundTasks`로 돌아 **동시 요청이면 terraform 프로세스가 같이 뜬다.**
2 GiB(t3.small)면 평상시엔 돌지만 프로비저닝 2건 + `docker compose build`가 겹치면 OOM killer가
postgres나 api를 잡는다.

### 왜 40 GB인가

| 항목 | 크기 |
|---|---|
| api 이미지 (python-slim + terraform CLI + 3사 SDK) | ~1.2–1.5 GB |
| postgres 430 MB + nginx 50 MB | ~0.5 GB |
| `terraform_plugin_cache` 볼륨 (aws ~700MB + azurerm ~350MB + google ~250MB) | **~1.3 GB** |
| OS + 도커 런타임 | ~3 GB |
| 빌드 캐시·dangling 이미지 누적 | 수 GB |
| 로그 (회전 상한 있음) | ~100 MB대 고정 |

기본값 8 GB로 띄우면 며칠 안에 디스크가 찬다. **30 GB가 실질 하한, 40 GB면 여유 있다.**
(2026-09-25 배포 직후 실측: 38 GB 중 6.1 GB 사용.)

### vCPU는 2개면 충분

API 자체가 단일 프로세스 전제라 vCPU를 늘려도 처리량이 늘지 않는다. 2 vCPU는 terraform
init/apply와 이미지 빌드 시간용이다. t3의 버스터블 크레딧이 이 간헐적 부하 패턴에 잘 맞는다.

### 더 싸게 — t4g (Graviton)

`backend/Dockerfile`이 `dpkg --print-architecture`로 아키텍처를 감지하므로 **t4g.medium에서도
그대로 빌드된다**(약 20% 저렴). 다만 `grpcio` 등 일부 휠이 arm64에서 소스 빌드로 떨어지면 빌드가
길어질 수 있다. 시연 일정이 촉박하면 검증된 x86(t3.medium)으로 가고, t4g는 여유 있을 때 시도한다.

---

## 2. 보안 그룹

**인바운드**

| 포트 | 소스 | 용도 |
|---|---|---|
| 22 | **내 IP만** | SSH |
| 80 | 0.0.0.0/0 | http → https 리다이렉트 + **certbot 인증서 발급·갱신(HTTP-01)** |
| 443 | 0.0.0.0/0 | 서비스(https) |

- **8000(api)·5432(db)·8080(web)을 열지 않는다.** 외부 요청은 전부 호스트 nginx(80/443)를 거친다.
  compose도 이 셋을 `127.0.0.1`에만 묶는다(§7) — 보안 그룹 위에 한 겹 더.
- 8025/1025(MailHog)는 운영에서 컨테이너를 띄우지 않으므로 해당 없음.
- **80을 닫지 않는다.** https만 서비스하더라도 certbot 갱신이 80번으로 도메인 소유를 확인한다 —
  닫으면 90일 뒤 인증서가 만료된다.
- 80/443이 막혀 있으면 A 레코드가 맞아도 **타임아웃으로만** 보인다.

**아웃바운드**: 전체 허용(기본값). 최소한 443(CSP API·terraform provider 다운로드·Let's Encrypt)과
587(SMTP)이 필요하다.

---

## 3. ✅ 배포용 코드 수정 — 2026-09-25 레포에 반영 완료

이 문서 최초 작성(2026-09-20) 시점에는 **로컬 전용 주소가 하드코딩**돼 있어 그대로 배포하면
로그인조차 되지 않았다. 아래 수정이 `main`에 들어가 있으므로(#134) **서버에서는 clone 후 따로 고칠 것이
없다.** 서버에서 코드를 직접 고치지 않는다 — 고칠 것이 있으면 로컬에서 PR → 병합 → 서버에서 `git pull`.
재배포 경로를 `git pull` 하나로 유지하기 위해서다. 무엇이 왜 그렇게 돼 있는지만 남겨 둔다.

### 3-1. 원래 무엇이 문제였나

- `frontend/assets/js/api.js` → `var API_BASE = "http://localhost:8000/api/v1";`
- `backend/app/main.py` → `allow_origins=["http://localhost:8080"]`

브라우저가 EC2에서 받은 페이지를 열고 **사용자 자기 PC의 localhost:8000**으로 API를 호출한다.
당연히 전부 실패한다.

### 3-2. 해결 — nginx가 `/api/`를 프록시한다

퍼블릭 IP를 코드에 박는 방법도 있지만, 그러면 IP가 바뀔 때마다 코드를 고쳐야 하고 CORS 설정도
같이 따라다녀야 한다. **같은 오리진으로 합치면 두 문제가 동시에 사라진다.**

- `nginx/default.conf`(도커 `web`)에 `location /api/ { proxy_pass http://api:8000/api/; ... }` 블록이 있다
  (`proxy_read_timeout 120s` — 프로비저닝 요청이 길다. `X-Forwarded-For`는 `main.py`의
  `_client_ip()`가 로그에 쓴다).
- `frontend/assets/js/api.js`의 `API_BASE`는 **상대경로 `"/api/v1"`**. `error-reporter.js`·
  `prov-tracker.js`의 fallback 문자열도 같은 값으로 맞춰 뒀다.
- **이 둘은 반드시 같이 간다.** 하나만 적용하면 로컬(8080)이 깨진다 — nginx 설정은 로컬 compose에도
  똑같이 마운트되므로, 둘 다 있으면 로컬·운영이 동일하게 동작한다.
- HTTPS 앞단(호스트 nginx, §9)은 `/`를 통째로 도커 `web`에 넘기므로 `/api/` 프록시 구조는 그대로다.
- `backend/app/main.py`의 CORS 목록에는 `http://mcp.greatsounds.me`와 `http://localhost`(포트 없음)가
  들어 있다. 프록시를 쓰면 preflight 자체가 발생하지 않아 실제로는 쓰이지 않지만, 컨테이너 밖에서
  uvicorn을 직접 띄우는 개발 방식이 여전히 유효해서 남겨 둔다. `https://mcp.greatsounds.me`는
  아직 없다 — 같은 오리진이라 접속엔 영향이 없고, 후속으로 추가한다(§13).

### 3-3. `FRONTEND_BASE_URL`은 `.env`에서 바꿔야 한다

`backend/app/routers/auth.py`가 비밀번호 재설정 링크를, `backend/app/report_email.py`가 보고서
메일의 링크를 이 값으로 만든다:

```python
reset_link = f"{settings.frontend_base_url}/password-reset.html?token={token}"
```

기본값이 `http://localhost:8080`이라 그대로 두면 **메일은 정상 발송되는데 링크를 누르면 사용자
자기 PC로 간다.**

```bash
FRONTEND_BASE_URL=https://mcp.greatsounds.me
```

**https로, 포트 번호 없이** 쓴다. `http://`로 두면 링크는 동작하지만(301로 이동) 토큰이 붙은 첫 요청이
평문으로 나간다. `:8080`을 남겨두면 링크가 깨진다(8080은 외부에 열려 있지 않다).

---

## 4. IAM — 인스턴스 역할

배포하며 새로 만드는 **① EC2 인스턴스 역할**과, 기존 **② `MultiCloudOpsAccess`**는 완전히 별개다.

| | ① EC2 인스턴스 역할 | ② `MultiCloudOpsAccess` |
|---|---|---|
| 누가 만드나 | 우리(배포하며 새로) | 연결할 계정 소유자가 각자 |
| 개수 | 1개 | 계정마다 1개 |
| 역할 | 플랫폼 신원 = `mcp-platform-caller` 사용자를 **대체** | 기존 그대로, **변경 없음** |
| 문서 | 이 절 | `AWS_Delegation_Role_Setup_Guide.md` §3 |

②는 손댈 게 없다. 아래는 ① 이야기다.

### 4-1. 왜 하는가

`CLAUDE.md`의 역할 위임 결정 기록에 적어둔 대로, **서버를 EC2 역할 위에 올리면 장기 키가 0이 된다.**
기존에는 `mcp-platform-caller` IAM 사용자의 액세스 키 1개가 `.env`에 남아 있었는데, 이걸 없애는 단계다.

### 4-2. 역할 생성

IAM → 역할 → 역할 생성 → **신뢰할 수 있는 엔터티: AWS 서비스 → EC2**

권한은 인라인 정책 하나만. `mcp-platform-caller` 사용자에게 준 것과 **글자 그대로 동일**하다:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::*:role/MultiCloudOps*"
    }
  ]
}
```

- 역할 이름: `mcp-platform-ec2-role`(2026-09-25 배포에서 사용).
- ⚠️ **`MultiCloudOps`로 시작하지 않게 짓는다** — 위임 대상 역할과 이름이 섞이면 반드시 헷갈린다.
- 다른 권한은 하나도 주지 않는다. 이 신원이 유출돼도 "이미 우리를 신뢰하도록 설정해 둔 역할"만
  빌릴 수 있게 하는 것이 설계의 핵심이다.
- 생성 후 EC2 인스턴스에 연결한다(인스턴스 → 작업 → 보안 → IAM 역할 수정. 인스턴스 프로파일은
  콘솔이 자동 생성).

### 4-3. `.env` — 키 두 개만 비우고, 계정 ID는 **남긴다**

```bash
PLATFORM_AWS_ACCOUNT_ID=123456789012    # ← 반드시 채워야 함
PLATFORM_AWS_ACCESS_KEY_ID=             # 비움
PLATFORM_AWS_SECRET_ACCESS_KEY=         # 비움
```

`app/providers/session.py`의 `_platform_sts_client()`가 키 두 개가 비면 boto3 기본 자격증명
체인(= 인스턴스 역할)으로 넘어간다.

> ⚠️ **계정 ID까지 비우면 안 된다.** `app/routers/credentials.py:298`이 이 값이 없으면
> `GET /credentials/aws/delegation-setup`을 **503 `PLATFORM_AWS_NOT_CONFIGURED`**로 막는다.
> 마이페이지에서 신뢰 정책 JSON이 아예 표시되지 않아 **아무도 새 AWS 계정을 연결할 수 없게 된다.**
> 이 값은 "신원"이 아니라 "화면에 보여줄 계정 번호"라 성격이 다르다.

### 4-4. ✅ 기존에 연결된 계정들은 깨지지 않는다 — 단, 같은 계정에 띄울 것

신뢰 정책의 Principal이 IAM 사용자 ARN이 아니라 `arn:aws:iam::<계정>:root`이므로
(`app/routers/credentials.py:316`), 플랫폼 계정 **안의 어떤 신원이든** `sts:AssumeRole` 권한만
있으면 역할을 빌릴 수 있다. IAM 사용자 → 인스턴스 역할로 바꿔도 고객 쪽에서 신뢰 정책을 다시
만들 필요가 없다. "사용자 ARN 대신 root를 Principal로 쓴다"는 결정이 여기서 실제로 이득을 본다.

⚠️ **EC2가 플랫폼 계정과 같은 AWS 계정에 있어야 한다.** 다른 계정에 띄우면 root principal이
맞지 않아 이미 연결된 고객 계정이 전부 깨진다.

### 4-5. ⚠️ 가장 많이 막히는 곳 — IMDS hop limit

컨테이너 안의 boto3가 인스턴스 역할을 가져오려면 `169.254.169.254`(IMDS)에 접근해야 하는데,
**도커 브리지 네트워크를 거치면 네트워크 홉이 하나 더 늘어난다.** EC2 기본값이
`HttpPutResponseHopLimit=1`이라 **컨테이너에서는 IMDSv2 토큰 요청이 조용히 막힌다.**

증상: `NoCredentialsError`, 또는 API에서 `PROVIDER_AUTHENTICATION_FAILED`("관리자에게 문의").
원인이 메시지에 전혀 드러나지 않는다.

```bash
aws ec2 modify-instance-metadata-options \
  --instance-id i-xxxxxxxxxxxx \
  --http-tokens required \
  --http-put-response-hop-limit 2 \
  --http-endpoint enabled
```

인스턴스 생성 시 "고급 세부 정보 → 메타데이터 응답 홉 제한 = 2"로 지정해도 된다. 재시작 불필요.
확인 방법은 §8-5.

### 4-6. 전환이 끝나면 기존 액세스 키를 삭제한다

`mcp-platform-caller` 사용자의 **액세스 키를 비활성화 → 삭제**한다. 안 지우면 "영구 키 0개"가
사실이 아니게 된다. 사용자 계정 자체는 그냥 둬도 무방하다(신뢰가 root 기준이라 영향 없음).

---

## 5. 메일 발송

### 5-1. 코드가 실제로 지원하는 것

`backend/app/mailer.py`는 `smtplib.SMTP()` + 선택적 `starttls()` 구조다. `SMTP_SSL`을 쓰지 않는다.

> ⚠️ **STARTTLS(587)만 된다. 암시적 TLS(465)는 안 된다.** `MAIL_PORT=465`로 설정하면 연결은 되지만
> 핸드셰이크가 맞지 않아 10초 타임아웃으로 실패한다.

> ⚠️ `MAIL_USERNAME`이 비어 있으면 `smtp.login()`을 **건너뛴다**(MailHog용 편의). 운영에서
> username을 빠뜨리면 인증 없이 보내려다 거부당한다.

### 5-2. 권장: AWS SES

```bash
MAIL_HOST=email-smtp.ap-northeast-2.amazonaws.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=AKIA...            # SES SMTP 자격증명 — IAM 액세스 키와 다른 별도 값이다
MAIL_PASSWORD=BM...
MAIL_FROM=no-reply@<검증한 도메인>
```

**함정 3개:**

1. **SES 샌드박스** — 신규 계정은 *검증된 주소로만* 발송된다. 프로덕션 액세스 신청은 승인까지
   보통 24시간 걸리므로 **시연 날짜가 정해져 있으면 미리 신청한다.** 급하면 팀원 이메일을 전부
   "검증된 아이덴티티"로 등록해두면 샌드박스 상태로도 데모는 가능하다.
2. **`MAIL_FROM`은 반드시 검증된 아이덴티티** — 기본값 `no-reply@multicloud.example`은 즉시
   거부당한다. 도메인이 없으면 개인 메일 주소 하나를 아이덴티티로 검증해서 FROM으로 쓴다.
3. **포트 25는 EC2에서 기본 차단**(AWS가 스팸 방지로 throttle)이라 587이 필수다.

### 5-3. 대안: Gmail 앱 비밀번호 (SES 승인을 기다릴 여유가 없으면)

```bash
MAIL_HOST=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=<계정>@gmail.com
MAIL_PASSWORD=<앱 비밀번호 16자>   # 일반 로그인 비밀번호는 거부된다
MAIL_FROM=<같은 주소>
```

2단계 인증을 켜야 "앱 비밀번호" 메뉴가 나타난다. 일 500통 제한이지만 발표·데모엔 넉넉하다.

### 5-4. 발송 실패는 화면에 보이지 않는다 — 로그로 확인한다

메일은 전부 `BackgroundTasks`로 나가므로 응답은 이미 200으로 끝난 뒤다. "인증 메일이 안 와요"를
확인할 유일한 방법:

```bash
tail -f logs/app.log | jq -c 'select(.event | startswith("mail"))'
# mail.sent   → 성공
# mail.failed → error_type과 스택트레이스까지 남는다
```

---

## 6. `.env` 작성

`.env`는 git에 없다. 서버에서 직접 만든다. `CREDENTIAL_ENCRYPTION_KEY`/`JWT_SECRET_KEY` 등은
compose가 `:?` 문법으로 강제하므로 **없으면 컨테이너가 기동조차 하지 않는다.**

```bash
# --- DB ---
POSTGRES_USER=mcp
POSTGRES_PASSWORD=<강한 임의 문자열>
POSTGRES_DB=mcp_db
DATABASE_URL=postgresql+psycopg2://mcp:<위와 동일>@db:5432/mcp_db

# --- 시크릿 ---
CREDENTIAL_ENCRYPTION_KEY=<openssl rand -base64 32>
CREDENTIAL_ENCRYPTION_KEY_VERSION=v1
JWT_SECRET_KEY=<openssl rand -base64 48>

# --- 배포 주소 (§3-3) ---
FRONTEND_BASE_URL=https://mcp.greatsounds.me

# --- 포트 노출 (§7) ---
WEB_PORT=127.0.0.1:8080      # ⚠️ 80이 아니다. 80/443은 호스트 nginx가 받는다 (§0)
API_BIND_HOST=127.0.0.1
DB_BIND_HOST=127.0.0.1

# --- 메일 (§5) ---
MAIL_HOST=email-smtp.ap-northeast-2.amazonaws.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=<SES SMTP username>
MAIL_PASSWORD=<SES SMTP password>
MAIL_FROM=no-reply@<검증한 도메인>

# --- AWS 플랫폼 신원 (§4-3) ---
PLATFORM_AWS_ACCOUNT_ID=123456789012
PLATFORM_AWS_ACCESS_KEY_ID=
PLATFORM_AWS_SECRET_ACCESS_KEY=

# --- 스케줄러 (2026-09-23 보고서 기능 추가분) ---
COST_SCHEDULER_ENABLED=true      # 매일 비용 수집
COST_INGEST_HOUR_UTC=6           # 06 UTC = 15 KST
REPORT_SCHEDULER_ENABLED=true    # 정기 보고서 메일 발송
REPORT_SEND_HOUR_UTC=7           # 07 UTC = 16 KST
COST_STALE_AFTER_HOURS=36

# --- 선택 ---
OPENAI_API_KEY=<AI 비용 상담 + 보고서 "AI 분석 요약"에 쓴다>
OPENAI_MODEL=gpt-4o-mini
TZ=Asia/Seoul
```

```bash
chmod 600 .env
```

| 값 | 틀리면 |
|---|---|
| `WEB_PORT` | `80`으로 두면 호스트 nginx와 80번을 두고 충돌한다(`address already in use`) |
| `FRONTEND_BASE_URL` | 메일은 가는데 링크가 localhost·http로 간다 |
| `PLATFORM_AWS_ACCOUNT_ID` 비움 | 마이페이지 역할 위임 503 → 아무도 AWS 계정을 연결 못 함(§4-3) |
| `MAIL_HOST` 누락 | 기본값 `mailhog`로 가서 메일이 조용히 사라진다(§7) |
| `MAIL_FROM` 미검증 | SES가 거부(§5-2) |

> **`.env`를 바꾼 뒤에는 반드시 `docker compose up -d`로 컨테이너를 다시 만든다.** 포트·환경변수는
> 컨테이너 생성 시점에 고정된다 — `restart`로는 반영되지 않는다(§12 사례 1).

> **`OPENAI_API_KEY`의 영향 범위가 넓어졌다(2026-09-23).** 예전에는 `/agent/chat`만 503이 됐는데,
> 이제 보고서의 "AI 분석 요약" 섹션도 같은 키를 쓴다(`app/report_summary.py`). 비우면 보고서 생성은
> 되지만 요약이 `null`로 남는다 — 목업 문구로 채우지 않는다.

> **스케줄러 둘 다 compose 기본값이 `true`다.** 즉 배포하면 바로 매일 돈다. 보고서 정기 발송은
> 실제 SMTP가 동작해야 하므로 §5를 먼저 끝내 둔다. 단일 프로세스 전제(§0)라 api replica를 늘리면
> 중복 수집·중복 발송이 된다.

> ⚠️ **`CREDENTIAL_ENCRYPTION_KEY`와 기존 데이터.** 이 키는 등록된 클라우드 자격증명을
> AES-256-GCM으로 암호화하는 데 쓴다. **기존 DB를 옮겨올 계획이면 그때 쓰던 키와 같은 값이어야
> 복호화된다.** 새 DB로 시작하면 새 키를 생성하고 각자 클라우드 계정을 다시 등록하면 된다.
> (2026-09-20 키 로테이션 이력이 있으니 값을 확인하고 쓸 것.)

---

## 7. 포트 노출 — `.env`로 제어한다 (코드 수정 불필요)

2026-09-25부터 `docker-compose.yml`의 포트 퍼블리시가 전부 환경변수로 빠져 있다. **운영/로컬이
같은 파일을 쓰고 `.env`만 다르다.** 기본값은 종전과 동일해서 아무것도 넣지 않은 팀원 로컬은
지금까지와 똑같이 뜬다.

| 서비스 | compose | 로컬 기본 | 운영 `.env` |
|---|---|---|---|
| `web` | `"${WEB_PORT:-8080}:80"` | 8080 | `WEB_PORT=127.0.0.1:8080` |
| `api` | `"${API_BIND_HOST:-0.0.0.0}:8000:8000"` | 전체 | `API_BIND_HOST=127.0.0.1` |
| `db` | `"${DB_BIND_HOST:-0.0.0.0}:5432:5432"` | 전체 | `DB_BIND_HOST=127.0.0.1` |
| `mailhog` | `"${MAILHOG_BIND_HOST:-0.0.0.0}:1025\|8025"` | 전체 | 띄우지 않음(아래) |

- **포트는 이미지에 구워지지 않는다** — 바꾼 뒤 `docker compose up -d`로 컨테이너만 재생성하면
  된다. `docker compose build`가 필요한 건 `backend/` 소스 변경뿐이다(프론트·nginx conf는 바인드
  마운트라 그것도 필요 없다).
- `api`/`db`/`web`을 루프백에 묶는 건 보안 그룹(8000·5432·8080 미개방) 위에 한 겹 더 거는 것이다.
  외부 접근은 호스트 nginx(§9) → 도커 `web` → `/api/` 프록시 경로로만 받는다.
- **MailHog는 운영에서 띄우지 않는다**: `docker compose up -d db api web`처럼 서비스를 명시한다.
  ⚠️ **`docker compose up -d --build`처럼 서비스를 빼먹으면 mailhog까지 `0.0.0.0:1025/8025`로 뜬다**
  (2026-09-25 실제로 발생). 떴으면 `docker compose stop mailhog`.
  ⚠️ `MAIL_HOST` 기본값이 `mailhog`라서, 운영 `.env`에 `MAIL_HOST`를 지정하지 않으면 메일이
  조용히 사라진다(컨테이너가 떠 있으면 MailHog로 들어가고, 없으면 발송 실패).

### 7-1. 왜 `WEB_PORT=80`이 아닌가

2026-09-25 이전 판은 도커 `web`을 `WEB_PORT=80`으로 직접 열었다(HTTP만). HTTPS를 붙이면서 80/443은
호스트 nginx가 받고, 도커 `web`은 `127.0.0.1:8080`으로 뒤로 물렸다(§0). 한 포트는 프로세스 하나만
잡을 수 있으므로 둘 다 80으로 두면 나중에 뜨는 쪽이 `bind: address already in use`로 실패한다.
compose의 `"${WEB_PORT:-8080}:80"`에 `127.0.0.1:8080`이 들어가 `127.0.0.1:8080:80`이 되는 구조다.

- **사용자는 여전히 80으로 접속할 수 있다** — 호스트 nginx가 받아서 https로 보낸다.
- 로컬에서 80번으로 쓰고 싶으면 각자 `.env`에 `WEB_PORT=80`만 넣으면 된다. 이때 브라우저가 보내는
  Origin이 `http://localhost`(포트 생략)가 되는데 `main.py`의 CORS 목록에 이미 들어 있고,
  **`FRONTEND_BASE_URL=http://localhost`도 같이 바꿔야** 메일 링크가 맞는다.
- `nginx/default.conf`(도커)의 `server_name _;`는 **그대로 둔다** — 호스트 nginx가 이미
  `server_name mcp.greatsounds.me`로 거른다.

---

## 8. 배포 절차

### 8-1. DNS

도메인 관리 화면에서 A 레코드를 인스턴스 퍼블릭 IP로 둔다.

```
mcp.greatsounds.me.   A   43.200.50.51
```

```bash
dig +short mcp.greatsounds.me     # → 인스턴스 퍼블릭 IP와 같아야 한다
```

- 인스턴스를 중지/시작하면 퍼블릭 IP가 바뀐다. 고정하려면 Elastic IP를 붙인다.
- **인증서 발급(§9) 전에 DNS가 새 IP를 가리키고 있어야 한다** — Let's Encrypt가 이 도메인으로 80번에
  접속해 확인한다.

### 8-2. 서버 기본 설치 — Docker(공식 저장소) + 호스트 nginx + certbot

```bash
ssh -i <키.pem> ubuntu@43.200.50.51
```

Ubuntu 기본 `docker.io` 패키지에는 `docker compose` 플러그인이 없으므로 Docker 공식 저장소를 쓴다.

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git jq
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

sudo usermod -aG docker $USER
newgrp docker          # 또는 SSH 재접속
docker compose version

# 호스트 nginx + certbot (§9에서 사용)
sudo apt-get install -y nginx
sudo snap install --classic certbot
sudo ln -sf /snap/bin/certbot /usr/bin/certbot
```

설치 직후 호스트 nginx가 80번을 잡는다. 도커 `web`은 `127.0.0.1:8080`으로만 뜨므로(§7) 충돌하지 않는다.

### 8-3. 코드 배치 + `.env`

```bash
git clone https://github.com/Cloud-Main-project-Team2/Multi-Cloud-Platform.git ~/Multi-Cloud-Platform
cd ~/Multi-Cloud-Platform
vi .env && chmod 600 .env        # §6
```

### 8-4. 빌드 & 기동

```bash
# 로그 디렉터리 — 컨테이너가 uid 1000(appuser)으로 돈다 (Ubuntu AMI의 ubuntu 사용자도 1000)
mkdir -p logs/nginx && sudo chown -R 1000:1000 logs

# 빌드가 가장 오래 걸린다(3사 SDK + terraform CLI). 막히는 게 있으면 여기서 먼저 드러난다.
# 빌드하는 동안 IMDS hop limit(§4-5)·DNS(§8-1)를 정리하면 된다.
docker compose build api

# mailhog는 운영에서 띄우지 않는다 — 서비스를 명시한다(§7)
docker compose up -d db api web
docker compose ps
```

`docker compose ps`에서 이렇게 보여야 한다:

```
api   127.0.0.1:8000->8000/tcp
db    127.0.0.1:5432->5432/tcp  (healthy)
web   127.0.0.1:8080->80/tcp
```

마이그레이션은 `docker-entrypoint.sh`가 자동 실행한다: `docker compose logs -f api`로 확인.

> `logs` 권한을 안 맞춰도 기동 자체는 막히지 않는다 — `app/logging_config.py`가 쓰기 실패 시
> stderr로 폴백한다. 대신 호스트에서 `tail -f logs/app.log`를 못 하게 되므로 맞춰두는 게 좋다.

### 8-5. 도커 쪽 단독 확인 (HTTPS 붙이기 전)

```bash
curl -s  http://127.0.0.1:8000/health
curl -s  http://127.0.0.1:8080/ | grep -o '<title>[^<]*'      # MultiCloud Ops — 멀티클라우드 통합 운영
curl -si http://127.0.0.1:8080/api/v1/auth/me | head -1       # 401 = /api/ 프록시 정상(인증 필요)

# 인스턴스 역할이 컨테이너 안에서 잡히는지 (§4-5)
docker compose exec api python -c "import boto3; print(boto3.client('sts').get_caller_identity()['Arn'])"
# → arn:aws:sts::<계정>:assumed-role/mcp-platform-ec2-role/i-xxxx
```

여기까지 통과해야 §9로 간다. 여기서 실패하면 문제는 도커/앱 쪽이다(호스트 nginx·DNS·SSL과 무관).

---

## 9. HTTPS — 인증서 발급 + 호스트 nginx 설정

### 9-1. 인증서 발급 (`certonly` — 설정 파일은 건드리지 않게)

```bash
sudo certbot certonly --nginx -d mcp.greatsounds.me
# 이메일 입력, 약관 동의
sudo ls /etc/letsencrypt/live/mcp.greatsounds.me/    # fullchain.pem, privkey.pem
```

> **`certbot --nginx`가 아니라 `certbot certonly --nginx`를 쓴다.** `--nginx`만 쓰면 certbot이
> `/etc/nginx/sites-available/default`에 443 블록과 `return 301 https://…`를 **자동으로 끼워 넣는데**,
> 그 블록은 우리 앱이 아니라 `/var/www/html`(nginx 기본 페이지)을 서빙한다. 2026-09-25 배포에서 이
> 상태로 한참 헤맸다(§12 사례 2·3). `certonly`는 인증서만 받고, 설정은 9-2에서 직접 쓴다.
> (이미 `--nginx`로 받았다면 그대로 두고 9-2에서 파일을 통째로 교체하면 된다.)

### 9-2. 호스트 nginx 설정 교체

**여러 줄 heredoc(`sudo tee … <<'EOF'`)을 터미널에 붙여 넣지 않는다** — 2026-09-25에 붙여 넣기가
조용히 실패해 설정이 안 바뀐 채로 진행됐다(§12 사례 3). 에디터로 파일을 만든 뒤 복사한다.

```bash
nano ~/mcp-nginx.conf
```

내용:

```nginx
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name mcp.greatsounds.me;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl ipv6only=on;
    server_name mcp.greatsounds.me;

    ssl_certificate /etc/letsencrypt/live/mcp.greatsounds.me/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcp.greatsounds.me/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;      # 프로비저닝 요청이 길다 (도커 nginx /api/와 같은 값)
    }
}
```

> `options-ssl-nginx.conf`·`ssl-dhparams.pem`은 certbot의 nginx 플러그인이 만들어 둔다.
> 없다는 오류가 나면 두 줄(`include`, `ssl_dhparam`)을 지워도 동작한다(nginx 기본 TLS 설정 사용).

적용:

```bash
sudo cp /etc/nginx/sites-available/default /etc/nginx/sites-available/default.bak
sudo cp ~/mcp-nginx.conf /etc/nginx/sites-available/default

# 정말 바뀌었는지 확인 — nginx -t 통과만으로는 모른다
grep -n proxy_pass /etc/nginx/sites-available/default     # proxy_pass http://127.0.0.1:8080; 가 보여야 한다

sudo nginx -t
sudo systemctl reload nginx     # 이미 켜져 있으면 start는 아무것도 안 한다 — reload
sudo systemctl enable nginx
```

되돌리기:

```bash
sudo cp /etc/nginx/sites-available/default.bak /etc/nginx/sites-available/default
sudo systemctl reload nginx
```

### 9-3. 자동 갱신 확인

```bash
systemctl list-timers | grep certbot     # snap.certbot.renew.timer
sudo certbot renew --dry-run
```

갱신은 80번 HTTP-01 챌린지를 쓴다 — 보안 그룹 80을 닫지 않는다(§2).

---

## 10. 배포 후 검증 체크리스트

서버에서:

```bash
curl -sI http://mcp.greatsounds.me/  | head -1                        # 301
curl -s  https://mcp.greatsounds.me/ | grep -o '<title>[^<]*'         # MultiCloud Ops — 멀티클라우드 통합 운영
curl -si https://mcp.greatsounds.me/api/v1/auth/me | head -1          # 401 (인증 필요 = 프록시 정상)
docker compose exec api python -c "import boto3; print(boto3.client('sts').get_caller_identity()['Arn'])"
tail -n 5 logs/app.log | jq -c '{ts,level,event}'
```

로컬 PC에서:

```bash
dig +short mcp.greatsounds.me      # → 43.200.50.51
curl -sI https://mcp.greatsounds.me | head -1
```

브라우저에서 확인할 것(**시크릿 창** — 일반 창은 예전 301/페이지를 캐시하고 있을 수 있다):

- [ ] `http://mcp.greatsounds.me` → `https://`로 이동하고 메인 화면이 뜬다
- [ ] 회원가입 → **인증 코드 메일이 실제로 도착**한다 (§5)
- [ ] 로그인 성공 → 대시보드 진입 (개발자 도구 Network에서 `/api/v1/...` 호출이 200)
- [ ] 비밀번호 재설정 메일의 **링크가 `https://mcp.greatsounds.me/...`** 를 가리킨다 (§3-3)
- [ ] 마이페이지 → AWS → 역할 위임에서 **신뢰 정책 JSON이 표시**된다 (§4-3, 503이면 계정 ID 누락)
- [ ] 클라우드 계정 등록 → 검증 통과 (§4-5, 실패하면 IMDS hop limit 의심)

---

## 11. 재배포 · 운영

### 11-1. 코드 변경 반영

```bash
cd ~/Multi-Cloud-Platform
git pull
docker compose build api            # backend/ 가 바뀐 경우에만 필요
docker compose up -d db api web
```

- 프론트(`frontend/`)와 `nginx/default.conf`는 바인드 마운트라 **빌드 없이** 반영된다
  (nginx conf를 바꿨으면 `docker compose restart web`).
- `.env`를 바꿨으면 `docker compose up -d`로 **재생성**한다(`restart`로는 반영되지 않는다).
- 호스트 nginx 설정을 바꿨으면 `sudo nginx -t && sudo systemctl reload nginx`.

### 11-2. 자주 쓰는 명령

```bash
docker compose ps
docker compose logs -f api
tail -f logs/app.log | jq -c 'select(.level=="ERROR")'
tail -f logs/access.log
sudo tail -f /var/log/nginx/error.log           # 호스트 nginx
sudo ss -ltnp | grep -E ':(80|443|8080|8000)\b' # 누가 어느 포트를 잡고 있나
```

### 11-3. 디스크 정리

```bash
docker system prune -a --volumes=false     # ⚠️ --volumes 를 붙이면 DB(db_data)가 날아간다
```

---

## 12. 트러블슈팅

**문제를 나눠서 좁힌다.** 안쪽에서 바깥쪽으로 한 층씩 `curl`로 확인한다:

```
① 127.0.0.1:8080 (도커 web)  →  ② localhost:80/443 (호스트 nginx)  →  ③ 도메인 (DNS·보안 그룹)
```

①이 안 되면 도커/앱, ①은 되는데 ②가 안 되면 호스트 nginx·SSL, ②까지 되는데 ③이 안 되면
DNS·보안 그룹이다.

### 12-1. 실제 사례 (2026-09-25 배포)

#### 사례 1. 컨테이너는 다 Up인데 접속이 안 된다 — `.env`를 바꾸고 컨테이너를 재생성하지 않음

**확인**

```bash
docker compose ps                  # web: 0.0.0.0:8080->80  (80이 아님)
ss -ltn | grep -E ':(80|8080)\b'   # 80을 받는 프로세스 없음
curl -I http://localhost:8080/     # 200 — 앱 자체는 정상
```

| 시각 | 일 |
|---|---|
| 14:32 | 컨테이너 생성 — `.env`에 `WEB_PORT` 없음 → 기본값 8080 |
| 14:41 | `.env`에 `WEB_PORT`, `API_BIND_HOST`, `DB_BIND_HOST` 추가 |

**원인**: 포트 매핑은 **컨테이너를 만들 때** 정해진다. `docker compose config`는 이미 새 값을
보여주지만 떠 있는 컨테이너는 옛 값 그대로다. 브라우저가 가는 80번엔 아무도 없었고, 8080은 보안
그룹에서 막혀 있었다.

**해결**: `docker compose up -d`(필요하면 `--build`)로 재생성.

#### 사례 2. SSL 인증서를 받은 뒤 https가 연결 거부 — 인증서를 가진 nginx와 앱을 서빙하는 nginx가 다름

**증상**: 서버에서 `curl http://mcp.greatsounds.me/`는 200인데 브라우저에서는 안 열린다.

**확인**

```bash
curl -I https://mcp.greatsounds.me/    # 443 연결 거부
systemctl status nginx                 # 호스트 nginx inactive
ls /etc/letsencrypt/                   # options-ssl-nginx.conf 있음 → certbot --nginx 로 발급
```

| | 호스트 nginx | 도커 nginx(`web`) |
|---|---|---|
| 인증서 | 있음(certbot이 설정) | 없음 |
| 443 | 설정됨 | `listen 80`뿐 |
| 서빙 | `/var/www/html` 기본 페이지 | 우리 앱 |
| 상태 | 도커에 80을 주려고 **중지** | 실행 중 |

**원인**

1. `certbot --nginx`가 **호스트 nginx** 설정에 443 블록과 http→https **301**을 끼워 넣었다.
2. 호스트 nginx가 켜져 있던 짧은 시간(14:48~14:53)에 접속한 브라우저가 그 **301을 캐시**했다.
3. 이후 `http://`를 쳐도 브라우저가 서버에 묻지 않고 바로 `https://`로 갔고, 443을 받는 프로세스가 없었다.

**해결**: §0 구조로 전환 — 호스트 nginx가 80/443을 받고, 도커 `web`은 `WEB_PORT=127.0.0.1:8080`으로
뒤로 물린다(§7-1, §9). 보안 그룹에 443 추가(§2).

#### 사례 3. https는 열리는데 "Welcome to nginx!"가 나온다 — 설정 파일이 실제로 안 바뀜

**확인**

```bash
stat -c '%y %n' /etc/nginx/sites-available/default*
#   default      14:48   ← certbot 시각 그대로
#   default.bak  15:11   ← 백업만 새로 생김
curl -s https://mcp.greatsounds.me/ | grep -o '<title>[^<]*'    # Welcome to nginx!
curl -s http://127.0.0.1:8080/      | grep -o '<title>[^<]*'    # MultiCloud Ops (도커는 정상)
```

**원인**

- 설정을 쓰는 `sudo tee … <<'EOF'`(여러 줄 heredoc) 붙여 넣기가 조용히 실패했다. 호스트 nginx는
  certbot이 만든 옛 설정으로 켜졌다.
- 옛 설정의 `server_name mcp.greatsounds.me` 줄에 **세미콜론이 빠져** 있었다. 다음 줄 `root`까지
  server_name으로 읽혔지만 문법상 유효해서 `nginx -t`는 통과했다.

**해결**: 에디터로 만든 파일을 `sudo cp`로 덮어쓰고, `grep proxy_pass`로 반영을 확인한 뒤 `reload`(§9-2).

#### 교훈

1. `.env`·compose를 바꾸면 **`docker compose up -d`로 재생성**한다. `config`에 보이는 값 ≠ 떠 있는 컨테이너의 값.
2. `certbot --nginx`는 **호스트 nginx 설정을 직접 고친다.** 인증서를 받기 전에 TLS를 어디서 처리할지
   먼저 정하고, 설정을 직접 쓸 거면 `certonly`.
3. **301은 브라우저가 캐시한다.** 서버를 고친 뒤엔 시크릿 창이나 `curl`로 본다.
4. **켜져 있는 nginx에 `systemctl start`는 아무것도 안 한다.** 설정 변경 후엔 `reload`.
5. **`nginx -t` 통과 ≠ 새 설정 적용.** `stat`(수정 시각)·`grep`으로 파일이 실제로 바뀌었는지 본다.
6. 안쪽(①)부터 바깥(③)으로 한 층씩 `curl`로 좁힌다.

### 12-2. 증상 → 원인 표

| 증상 | 원인 | 조치 |
|---|---|---|
| 컨테이너 전부 Up인데 접속 불가 | `.env` 변경 후 재생성 안 함 | §12-1 사례 1 |
| https 연결 거부 | 443 받는 곳 없음 / 보안 그룹 443 미개방 | §9, §2 |
| "Welcome to nginx!" | 호스트 nginx가 기본 설정으로 서빙 | §12-1 사례 3 |
| `502 Bad Gateway` (https) | 도커 `web`이 안 떠 있거나 8080이 아님 | `docker compose ps`, `curl 127.0.0.1:8080` |
| `bind: address already in use` (web) | `WEB_PORT=80`인데 호스트 nginx가 80 점유 | §7-1, `WEB_PORT=127.0.0.1:8080` |
| 도메인 접속이 타임아웃 | 보안 그룹 80/443 미개방, 또는 A 레코드 미전파 | §2, `dig +short mcp.greatsounds.me` |
| `certbot` 발급 실패 | DNS가 이 서버를 안 가리킴 / 80 막힘 | §8-1, §2 |
| 로그인 버튼 무반응, Network에 `localhost:8000` 실패 | 옛 `api.js`(#134 이전 코드) | `git pull` (§3-2) |
| `PROVIDER_AUTHENTICATION_FAILED` / `NoCredentialsError` | 컨테이너가 IMDS에 접근 못 함 | §4-5 (hop limit 2) |
| 마이페이지 역할 위임에서 `503 PLATFORM_AWS_NOT_CONFIGURED` | `PLATFORM_AWS_ACCOUNT_ID` 누락 | §4-3 |
| 기존 연결 AWS 계정이 전부 `CLOUD_PERMISSION_DENIED` | EC2를 플랫폼 계정이 아닌 계정에 띄움 | §4-4 |
| `CLOUD_PERMISSION_DENIED` (특정 계정만) | 역할 이름 / 신뢰 정책 계정 ID / ExternalId 중 하나 불일치 — STS가 구분해주지 않는다 | `AWS_Delegation_Role_Setup_Guide.md` §3 |
| 인증 메일이 안 옴 | SES 샌드박스 / FROM 미검증 / 465 포트 / `MAIL_HOST` 누락 | §5, `logs/app.log`의 `mail.failed` |
| 비밀번호 재설정 링크가 localhost·http | `FRONTEND_BASE_URL` 미변경 | §3-3 후 `docker compose up -d api` |
| mailhog가 운영에서 떠 있음 | 서비스 미지정 `docker compose up` | `docker compose stop mailhog` (§7) |
| 컨테이너가 계속 재시작 | `.env` 필수 시크릿 누락 | `docker compose logs api` |
| 디스크 풀 | 빌드 캐시·dangling 이미지 누적 | §11-3 |
| OOM으로 컨테이너가 죽음 | 프로비저닝 동시 실행 | t3.medium 확인, 필요 시 swap 2GB 추가 |

---

## 13. 후속 과제 (이 문서 범위 밖)

- **CORS에 `https://mcp.greatsounds.me` 추가**: `backend/app/main.py`. 같은 오리진(`/api`)이라 현재
  접속엔 영향 없음(§3-2).
- **Elastic IP 고정**: 인스턴스 재시작 시 퍼블릭 IP가 바뀌어 DNS를 다시 잡아야 한다.
- **RDS 분리**: postgres를 RDS로 빼면 인스턴스는 t3.small(2 GiB)로 낮출 수 있다. `DATABASE_URL`만
  바꾸면 되고 코드 변경은 없다.
- **백업**: 현재 `db_data`는 도커 named volume이다. EBS 스냅샷 또는 `pg_dump` 크론을 붙인다.
- **replica 확장**: §0의 단일 프로세스 전제를 먼저 풀어야 한다(별도 워커 분리).
