# Multi-Cloud Platform — 멀티클라우드 통합 운영 대시보드

여러 클라우드(Provider)의 자원을 한 화면에서 프로비저닝·인벤토리·모니터링할 수 있게 하는 멀티클라우드 통합 운영 대시보드.

## 기술 스택

| 구분 | 스택 |
|---|---|
| Frontend | 빌드 없는 HTML/CSS(**Tailwind CSS**) + **Vanilla JS** |
| Backend | **FastAPI** (Python) |
| Database | **PostgreSQL** |
| Infra / IaC | **Terraform** |
| VCS / 협업 | GitHub (branch → PR → squash merge) |

### 로컬 실행 (Docker)

```bash
cp backend/.env.example backend/.env   # 필요 값(시크릿 등) 채우기
docker compose up -d --build           # db · api · web · mailhog 기동
```

- 프론트: http://localhost:8080 · API: http://localhost:8000 · 메일 확인(MailHog): http://localhost:8025
- `api` 컨테이너는 기동 시 Alembic 마이그레이션 + 목업 시딩을 자동 실행한다.
- 환경변수 목록·기본값은 [`backend/.env.example`](./backend/.env.example) 참고.

## 로그 (관제)

`api` 컨테이너는 로그를 레포 루트 `logs/`에 **JSON Lines**로 남긴다(`docker-compose.yml`이
`./logs`를 바인드 마운트하므로 `docker exec` 없이 서버에서 바로 읽을 수 있다).
모든 `ts`는 **한국 시간(+09:00)**이다 — 사람이 읽는 물건이라 암산이 필요 없게 했고, 오프셋을
남기므로 다른 시간대와 비교해도 모호하지 않다. **API 응답·DB 시각은 UTC 그대로다**(기계가
읽는 계약이라 건드리지 않는다). 다른 시간대로 운영하려면 `LOG_TZ_OFFSET_HOURS`(api)와
`TZ`(web) 환경변수를 바꾼다.

| 파일 | 내용 | 한 줄 = |
|---|---|---|
| `logs/access.log` | API 요청 1건 | `ts` `level` `request_id` `method` `path`(템플릿) `status` `duration_ms` `user_id` `client_ip` |
| `logs/app.log` | 비즈니스 이벤트·에러 | `ts` `level` `event`(`<도메인>.<동작>`) + 이벤트별 필드, 에러는 `exc`(스택트레이스) |
| `logs/nginx/access.log` | 정적 파일 요청 1건 | `ts` `level` `method` `path` `status` `duration_s` `client_ip` `user_agent` |

`app.log`에 남는 주요 이벤트:

| 이벤트 | 언제 |
|---|---|
| `service.started` / `service.stopping` | API 기동·종료(재시작 시각 기준선) |
| `http.api_error` / `http.validation_error` / `http.unhandled_exception` | 오류 응답. 마지막 것은 `exc` 포함 |
| `auth.login_succeeded` / `auth.login_failed` / `auth.refresh_rotated` / `auth.signed_up` / `auth.password_reset_completed` | 인증. 실패는 시도한 이메일과 사유(`no_such_user`/`bad_password`/`withdrawn`) |
| `provisioning.job.queued` / `.succeeded` / `.failed` / `.cancelled` | 프로비저닝 job 생명주기 |
| `terraform.command` / `.timeout` | terraform init·plan·apply·destroy 각각의 종료코드·소요시간 |
| `sync.job.started` / `sync.item.finished` / `sync.job.finished` | 동기화. 항목별 발견·생성·갱신·stale 개수 포함 |
| `client.error` | 브라우저에서 올라온 오류(아래) |
| `mail.sent` / `mail.failed` | 메일 발송(백그라운드라 실패해도 화면에 안 보인다) |

### 프론트엔드 오류 수집

`frontend/assets/js/error-reporter.js`가 JS 예외·unhandled rejection·실패한 API 호출을
`POST /api/v1/client-logs`로 보내면 서버가 `client.error`로 남긴다. **에러만** 보내고 사용자
행동은 수집하지 않는다. API 실패는 5xx·네트워크 단절·400/422만 보낸다(나머지 4xx는 정상적인
사용자 오류라 백엔드 `http.api_error`와 중복된다). 실패한 응답의 `X-Request-Id`를 함께 실어
보내므로 **브라우저에서 본 오류와 백엔드 로그를 같은 `request_id`로 이어볼 수 있다.**

`level`은 `access.log`에서 상태코드로 갈린다(5xx=ERROR, 4xx=WARNING). 회전은 10MB × 5개
(두 파일 합쳐 디스크 최대 약 100MB). 로그 파일은 git에 커밋하지 않는다(`logs/.gitkeep`만 추적).

```bash
tail -f logs/app.log | jq -r '"\(.ts) \(.level) \(.event) \(.error_code // "")"'  # 실시간 관제
jq -c 'select(.level=="ERROR")' logs/app.log | tail -50                             # 최근 에러
jq -c 'select(.request_id=="<id>" or .server_request_id=="<id>")' logs/*.log         # 한 요청 추적(프론트↔백엔드)
jq -c 'select(.event=="client.error")' logs/app.log | tail -20                       # 브라우저 오류
jq -c 'select(.event|startswith("provisioning.job"))' logs/app.log                    # 프로비저닝 이력
jq -c 'select(.status>=500)' logs/access.log                                         # 5xx만
jq -s 'map(.duration_ms) | add/length' logs/access.log                               # 평균 응답시간
```

> 리눅스 서버에 배포할 때는 `chown 1000:1000 logs`가 필요하다(컨테이너가 uid 1000으로 실행).
> 쓰기 권한이 없으면 서비스가 죽지 않고 로그만 stderr(`docker compose logs api`)로 폴백한다.

## 디렉토리 구조

```
repo-root/
  frontend/     # HTML/CSS(Tailwind)+Vanilla JS. 화면은 feature/fe-* 브랜치에서 추가
  backend/      # FastAPI + PostgreSQL + Terraform. feature/be-* 브랜치에서 채워짐
  docs/         # 확정 기획 문서 보관용
  .github/      # PR / Issue 템플릿
  .gitignore
  README.md
  CLAUDE.md     # AI 세션용 프로젝트 컨텍스트
  CONTRIBUTING.md  # 팀원용 협업 규칙
```

> `frontend/`, `backend/` 내부 상세 폴더(`assets/js`, `app/routers` 등)는 실제 코드를 작성하는 브랜치에서 그때그때 추가한다.

## 브랜치 · PR 규칙 (요약)

- `main`은 유일한 보호 브랜치. 모든 변경은 **브랜치 → PR → 병합** 순서를 따른다.
- 브랜치 네이밍: `<이름>/fe-<기능>`(프론트), `<이름>/be-<기능>`(백엔드). 담당자 영문명을 네임스페이스로. WBS 작업 단위와 1:1. 예) `solcho/fe-pages`
- 커밋: Conventional Commits(`feat`/`fix`/`chore`/`docs`/`refactor`/`test`), 설명은 한국어.
- 병합: **Squash and merge**로 통일. `main` 병합 권한은 **조은솔**에게만.

자세한 내용은 [CONTRIBUTING.md](./CONTRIBUTING.md) 참고.

## 현재 상태

1주차(2026-09-14 기준) 완료: 인증(로그인·회원가입·이메일 검증·비밀번호 재설정·JWT/refresh),
키 관리·인벤토리(조회/동기화/액션), 프로비저닝(AWS·GCP·Azure 3사 + 서비스 확장) 백엔드/프론트
실 연동까지 `main`에 병합. 상세 진행 상황은 [CLAUDE.md](./CLAUDE.md)의 진행 트래커 참고.
