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
| 목업 데이터 시딩 — 13테이블 최신 스키마 + 화면 예시 데이터 | `solcho/be-mock-data` | 조은솔 | in progress |

> Keep this table updated as branches open, progress, and merge.

## Key architectural decisions

- **API 형태(2026-09-10)**: 신규 통합 API 명세(`docs/01_API_명세서_v1.1.md`)가 아니라
  기존에 구현돼 있던 **provider별 개별 엔드포인트**(`/credentials/{provider}`,
  `/provisioning/{provider}/{service}`, `/resources/action` 형태)를 유지하기로 결정.
  이에 따라 그 통합 API 전용으로만 추가됐던 `provisioning_requests`·`resource_types`
  테이블과 참조 컬럼(`provisioning_jobs.provisioning_request_id`,
  `resources.resource_type_id`)을 제거(Alembic `0caab346f140`, 테이블 15→13).
  credentials 암호화·Account/Credential 분리·인벤토리 캐시·비용/감사 구조 등 나머지
  개선은 유지. 현재 스키마는 `docs/DB_ERD_v1.1.md` 참고.
- **목업/데모 데이터(2026-09-10)**: 화면 하드코딩 예시값(MY-01/INV-01/DASH-01)을
  `backend/app/seed_mock_data.py`로 시딩 — 데모 유저 1, 클라우드 계정 3, 자격증명 3
  (dev-gcp만 `verified=false` 검증실패 예시), 리소스 5, 프로비저닝 잡 2(성공/실패),
  동기화 상태 AWS·Azure·GCP 각각 상이, 비용 카드용 최소 행. **재실행 idempotent**.
  credentials는 `app/security/credential_crypto.py`(AES-256-GCM)로 실제 암호화 저장하며,
  이후 BE API(키 관리/리소스 조회/대시보드)는 이 데이터 위에서 개발·테스트한다.

## Assumptions — frontend static UI (`solcho/fe-pages`, 화면설계서 V1.1)

이번 정적 UI 작업의 확정/가정 항목. **화면설계서에 "[확인 필요]"로 남았지만 프로토타입 개발 프롬프트에서 이미 확정된 것**이라 다시 묻지 않음:
- **소셜 로그인(Google)**: 버튼만 배치, 클릭 시 "준비 중" 안내만(실제 OAuth 없음).
- **PROV-01 계정 선택**: 다중 선택 허용 — 같은 설정으로 계정 수만큼 생성된다는 안내 문구 표시.
- **마이페이지 "보고서 작성" 섹션**: 최신 화면설계서 기준으로 **넣지 않음**(옛 기능명세서엔 있었음).
- **인벤토리 "서비스 종류" 열**: 삭제하고 **"CSP 원본 리소스 유형"** 열로 대체(09/06 확정).

이 문서(프론트 프롬프트)에서 "가정"으로 처리한 것:
- **범위**: 화면 12개의 마크업+스타일만. 폼 검증/API/세션/데이터 저장 로직 없음. 순수 UI 상태 전환(사이드바 active, 테마 토글, 모달/드롭다운 open·close, 인트로 탭)만 포함.
- **다중 상태 화면**(로그인 실패 배너, PROV-02 성공/진행중/실패, 마이페이지 검증 실패 행 등): 조건 분기 없이 화면설계서대로 **정적 예시**로 동시 노출.
- **브랜드명 미확정**: `"MultiCloud Ops"` 자리표시자 — `frontend/assets/js/ui.js`의 `SERVICE_NAME` 한 곳에서 `data-service-name`로 주입.
- **인트로(INTRO-01~05) 소개 문구·이미지, 푸터 정책 문서 미확정** → 자리표시자 텍스트/링크.
- **디자인 스크린샷**(`docs/design/*.png`)은 색·타이포·컴포넌트 룩만 참고 — 화면 구성은 화면설계서 V1.1을 따름.
- **MAIN-01 헤더 테마 토글**: 화면설계서엔 명시 없으나 라이트/다크 검토가 가능하도록 헤더에 추가.

### Assumptions — 기본 기능 JS (`solcho/fe-basic-js`)

순수 클라이언트 검증/필터만 구현(서버 API 호출 없음 — Network 탭에 요청 0건). 데모성 동작:
- **로그인**: 형식 유효 시 실제 인증 없이 `dashboard.html`로 데모 리다이렉트. 실제 인증은 API 연동 단계에서 붙음.
- **회원가입 중복 확인**: 서버가 없어 이메일 형식이 유효하면 항상 "사용 가능한 이메일입니다"(데모). 이메일 변경 시 중복 확인 재요구.
- **비밀번호 찾기**: STEP1 발송 후 안내 노출·폼 비활성(실제 메일 발송 없음), STEP2 통과 시 `login.html` 데모 이동.
- **검증 실패 메시지**: 대부분 기존 요소 재사용. 없던 곳(중복확인 결과 `#dup-msg`, 비밀번호 불일치 `#pw-match-msg`)만 최소 추가.
- start/stop/delete·검색·정렬 등 테이블 액션은 하드코딩 예시 행 위에서만 동작(실데이터/서버 없음).

### Assumptions — 모달·팝업 공통 기능 (`solcho/fe-modals`)

- **모달 유틸을 별도 브랜치로 분리한 이유**: 정적 UI 단계(`fe-pages`)에서 "모달 열기/닫기"를 화면의 일부로 포함하라 했으나, 실제 결과물 일부(진행률·소셜 모달 등)가 열고 닫는 토글 없이 평면 정적 블록으로 얹혀 있었음. 이 브랜치에서 `assets/js/modal.js`(`MCPModal`) 공통 유틸(배경클릭/ESC/스크롤 잠금/전역 API)로 표준화하고 빠진 개폐 동작을 채움.
- **비밀번호 변경 성공 팝업 보완**: 기능명세서엔 있으나 정적 UI 문서에 누락됐던 팝업을 이 브랜치에서 가입완료 팝업과 동일한 결과 팝업 스타일로 `password-reset.html`에 추가(2절 기본값 "포함").
- **소셜 가입 추가정보 모달**: 마크업+id만 준비(`#signup-social-modal`), 실제 트리거 연결은 OAuth 연동 브랜치로 보류.
- **PROV-02 진행률**: 기본형↔축소형은 실제 진행 상태 반영이 아니라 정적 두 형태의 UI 토글일 뿐(실 폴링은 백엔드 연동 이후).
- 기존 `MCUI.open/close`(ui.js)로 열리던 모달들도 이 브랜치에서 `MCPModal`/data 속성으로 통일.

### Assumptions — 프로비저닝 설정 폼 (`solcho/fe-provisioning-form`)

PROV-01 마법사 ④공통·⑤추가 설정 스텝을 리소스 종류/플랫폼에 따라 동적 렌더링하고 입력값을
전역 상태(`window.provisioningSpec`)에 반영하는 작업. 서버 통신은 없음(Network 탭 요청 0건).
필드명은 이후 BE 연동을 위해 `01_API_명세서_v1.1.md` §10.3의 `common_spec`/`provider_spec`
구조에 맞춰 잡음(`assets/js/provisioning.js`).

- **사양 등급 → 실제 SKU 매핑**(추상 3등급, `provisioning.js`의 `SPEC_TIERS` 상수):

  | 등급 | AWS | Azure | GCP |
  |---|---|---|---|
  | 경량 (1 vCPU · 2GB) | `t3.micro` | `B1s` | `e2-micro` |
  | 표준 (2 vCPU · 4GB) | `t3.medium` | `B2s` | `e2-medium` |
  | 고성능 (4 vCPU · 8GB) | `t3.large` | `B4ms` | `e2-standard-4` |

  표준 등급은 가격 비교 모달 예시값과 일치. 사용자는 등급만 고르고 실제 SKU는 코드에서 변환한다.
- **리전 제한**(서비스 컨텍스트 9절): AWS `ap-northeast-2`/`us-east-1`, Azure
  `koreacentral`/`eastus`/`koreasouth`/`canadacentral`, GCP `asia-northeast3`/`us-central1`.
  선택한 플랫폼마다 별도 리전 select.
- **네트워크**: 오늘은 "새 VPC/Subnet 자동 생성" 고정, "기존 리소스 사용" 토글은 자리만 두고 비활성
  (실제 VPC/Subnet lookup은 BE 연동 이후).
- **맵핑 문서 "제외" 필드**(Compute 스토리지·권한, DB 사양·스토리지·네트워크·접근제어·가용성,
  Storage 접근제어·중복성·버전관리)는 폼에 렌더링하지 않고 `providerSpec`에 서버 기본값 상수로만
  채운다(사용자 입력 없음).
- **DB 공통 설정**: 엔진 옵션은 플랫폼별로 다름(AWS 7종, Azure/GCP 3종). Azure는 "MariaDB 25/9/19
  지원 종료" 경고 상시 노출. 인증은 마스터 사용자명+비밀번호, 단 **GCP는 루트 비밀번호만**. 백업은
  읽기 전용 안내(자동 백업 고정). `providerSpec[p]`에 `engine`/`region`/`masterUsername`/`masterPassword` 저장.
- **Storage 공통 설정**: 버킷/계정명(전역 고유, mcp- 프리픽스 없음), 리전(Azure는 'Central' 고정 안내).
  Azure 선택 시 태그 검색 미지원 경고. "이름(서비스)"는 읽기 전용 라벨.
- **리소스 종류 선택 가능화**: 정적 UI의 "권한 없음" 데모로 비활성이던 Database/Storage 라디오를 이
  브랜치에서 활성화(동적 폼이 실제로 동작해야 하므로). CDN만 비활성 유지.
- **⑤ 플랫폼별 추가 설정**: Compute 이미지는 **AWS/Azure만**(AWS는 "직접 AMI ID 입력" 선택 시
  AMI ID 입력칸 노출, GCP는 미노출), Storage 스토리지 등급은 **GCP만**(Standard/Nearline/Coldline/
  Archive), DB는 추가 입력 없음(서버 기본값). "제외" 필드는 `SERVER_DEFAULTS` 상수로 `providerSpec`에만
  채운다(사용자 입력 없음).
- **CDN**: 3사 공통 입력 스펙 미확정 → 선택은 가능하되 ④(공통)를 **건너뛰어 숨기고** ⑤에 "준비 중"
  안내만 표시, "생성하기"는 **비활성**. (CDN 라디오는 이 브랜치에서 선택 가능하게 활성화함.)
- **생성하기 활성화**: 선택된 각 플랫폼에 대해 리소스 종류의 공통 필수 + 해당 플랫폼 추가 필수
  필드가 전부 채워졌을 때만 버튼 활성화(`provisioning.js`의 `validate()`, 상태 기반). 미충족/CDN/
  플랫폼 미선택 시 비활성. 초기 로드 시에도 필수값 비어 있으면 비활성으로 시작한다.
- **진행 단위**: 이 브랜치는 (1)Compute 공통 폼 → (2)DB/Storage 공통 폼 → (3)⑤추가 설정+CDN
  → (4)생성하기 활성화 검증 순으로 커밋을 쪼갠다.

### Assumptions — 프로비저닝 페이지 완성 (`solcho/fe-provisioning-complete`)

`fe-provisioning-form` 후속. CDN 실입력 + 선택 시각 피드백 + 마법사 전체 흐름을 붙여 프로비저닝
페이지를 완성한다. 실제 API로 리소스가 생성되는 것 외의 모든 기능(버튼·모달·진행바 애니메이션
포함)을 구현하는 것이 목표. 서버 통신은 여전히 없음(Network 요청 0건).

- **리전 = 국가 단일 선택**: 공통 설정은 플랫폼 무관해야 하므로, 플랫폼별 리전 select를 없애고 국가
  하나(한국/미국)만 고르면 `COUNTRY_REGION` 상수로 각 플랫폼 리전(AWS `ap-northeast-2`/`us-east-1`,
  Azure `koreacentral`/`eastus`, GCP `asia-northeast3`/`us-central1`)이 `providerSpec[p].region`에
  자동 매핑된다. 일본 등 추가 국가는 허용 리전 확정 후.

- **CDN 실입력 폼**: 공통 설정 스텝 없이 ③에서 CDN 선택 시 곧바로 플랫폼별(⑤) 폼만 렌더
  (AWS→Azure→GCP 순). 필드 확정 근거는 `docs/멀티클라우드 3사 기능 맵핑 — 설정값 입력 범위
  (2026-09-10).md` **4절 "구현용 필드 확정"(2026-09-11)** — 원칙은 "Terraform으로 설정 가능한
  항목은 전부 입력받는다". 제외는 **Endpoint(결과값)·Scope(Global 고정)·Raw status(조회전용)**
  세 가지뿐. 3사 필드셋이 완전히 다름:
  - AWS(CloudFront): Origin(필수)·캐시 정책·Path routing·Compression·Viewer Protocol Policy·
    Price Class. **Health Probe 없음**(CloudFront에 독립 헬스체크 인자 없음).
  - Azure(Front Door): Origin·Resource Group·SKU(**셋 다 필수**)·쿼리스트링 캐시 처리·Compression·
    지원 프로토콜·HTTPS 리다이렉트·Health Probe(경로/간격 기본 240).
  - GCP(LB+Cloud CDN): Backend(필수)·LB stack 확인 체크박스(**필수**)·백엔드 유형(서비스/버킷)·
    enableCdn·Cache Mode·Compression·HTTPS 강제 리다이렉트·Health Probe(**백엔드 서비스일 때만**
    노출, 기본 10). Path routing은 기본 매핑 자동 생성이라 입력 없음.
  - 필수 충족(AWS Origin / Azure Origin·RG·SKU / GCP Backend·LB stack 동의) 시 생성하기 활성화.
- **진행률 모달 값**: PROV-02 진행률 모달의 성공/진행중/실패 3-상태는 **정적 예시 그대로** 재사용하며,
  실제 job 상태 반영은 BE 연동 이후로 미룬다(이번 범위 아님).

## Pointer — where the planning docs live

The functional spec (기능명세서), screen design (화면설계서), WBS, and the
existing confirmed design documents (프로토타입 개발 프롬프트.md, Tier2 확장
프롬프트 등) are **NOT in this repo** — they live only in the Claude project
context. Don't look for them under `docs/` yet; the team adds copies there as
needed. Flag this to new members during onboarding so they aren't confused.
