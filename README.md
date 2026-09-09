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

> 상세 실행 방법(로컬 구동, 환경변수, DB 마이그레이션 등)은 백엔드 개발 환경 구축(`feature/be-env-setup`) 완료 후 이 문서에 갱신된다.

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

레포 골격 셋업 완료, 기능 구현 진행 중.
