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

| 파일 | 내용 | 한 줄 = |
|---|---|---|
| `logs/access.log` | 요청 1건 | `ts` `level` `request_id` `method` `path`(템플릿) `status` `duration_ms` `user_id` `client_ip` |
| `logs/app.log` | 비즈니스 이벤트·에러 | `ts` `level` `event`(`<도메인>.<동작>`) + 이벤트별 필드, 에러는 `exc`(스택트레이스) |

`level`은 `access.log`에서 상태코드로 갈린다(5xx=ERROR, 4xx=WARNING). 회전은 10MB × 5개
(두 파일 합쳐 디스크 최대 약 100MB). 로그 파일은 git에 커밋하지 않는다(`logs/.gitkeep`만 추적).

```bash
tail -f logs/app.log | jq -r '"\(.ts) \(.level) \(.event) \(.error_code // "")"'  # 실시간 관제
jq -c 'select(.level=="ERROR")' logs/app.log | tail -50                             # 최근 에러
jq -c 'select(.request_id=="<id>")' logs/*.log                                       # 한 요청 추적
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
