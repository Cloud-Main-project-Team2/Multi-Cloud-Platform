# CLAUDE.md

Project context for AI (Claude Code) sessions working on this repository.
Human contributors: see `CONTRIBUTING.md` (same rules, human-readable form).

## Project overview

**Multi-Cloud Platform** — a unified operations dashboard that lets a team
provision, inventory, and monitor resources across multiple cloud providers
from a single UI.

## Tech stack

| Layer | Stack |
|---|---|
| Frontend | Build-less HTML/CSS (**Tailwind CSS**) + **Vanilla JS** |
| Backend | **FastAPI** (Python) |
| Database | **PostgreSQL** |
| Infra / IaC | **Terraform** |

## Repository layout

```
frontend/   # HTML/CSS(Tailwind)+Vanilla JS; screens added on feature/fe-* branches
backend/    # FastAPI + PostgreSQL + Terraform; filled on feature/be-* branches
docs/       # confirmed planning documents (spec, screen design, WBS)
.github/    # PR / issue templates
```

Sub-folders inside `frontend/` and `backend/` (e.g. `assets/js`, `app/routers`)
are **not** pre-created — add them on the branch that actually writes the code,
so skeleton commits don't clutter branch diffs.

## Team workflow

- **Branch naming**: `<name>/fe-<feature>` (frontend), `<name>/be-<feature>`
  (backend). The owner's romanized name is a namespace prefix; the `fe-`/`be-`
  prefix marks frontend/backend. One branch per WBS work item; branch off the
  latest `main`. Confirmed romanizations: 조은솔 → `solcho`, 김종국 → `jongkook`,
  이승현 → `seunghyun`, 안권형 → `kwonhyeong` (confirm spellings with each member).
  The name is just a "whose is this" label — merge/review unit is always the
  feature (e.g. `solcho/fe-pages`, `kwonhyeong/be-env-setup`).
- **Commit convention**: Conventional Commits with Korean descriptions —
  `<type>: <요약>`, where `type` ∈ `feat | fix | chore | docs | refactor | test`.
  Keep each commit small and reviewable.
- **PR / merge rule**: every change reaches `main` through a PR. **Merge owner:
  조은솔 — only 조은솔 merges into `main`.** Others open PRs and request review
  but do not press merge.
- **Merge method**: **Squash and merge** (uniform, keeps `main` history clean).
- Delete a branch (remote + local) once its PR is merged.

## Current phase & progress tracker

Phase 0 (repo skeleton + collaboration rules) complete. Feature work in progress.

| WBS 항목 | 브랜치 | 담당 | 상태 |
|---|---|---|---|
| 페이지 UI 구현 — 확정 화면 12개 정적 UI | `solcho/fe-pages` | 조은솔 | in progress |
| 개발 환경 구축 — DB 구축 | `kwonhyeong/be-env-setup` | 안권형/김종국/이승현 | not started |

> Keep this table updated as branches open, progress, and merge.

## Pointer — where the planning docs live

The functional spec (기능명세서), screen design (화면설계서), WBS, and the
existing confirmed design documents (프로토타입 개발 프롬프트.md, Tier2 확장
프롬프트 등) are **NOT in this repo** — they live only in the Claude project
context. Don't look for them under `docs/` yet; the team adds copies there as
needed. Flag this to new members during onboarding so they aren't confused.
