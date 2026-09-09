# 기여 가이드 (CONTRIBUTING)

팀원(사람)을 위한 협업 규칙 문서입니다. (AI 세션용 컨텍스트는 `CLAUDE.md` 참고 — 내용은 같고 형식만 다릅니다.)

---

## 1. 브랜치 전략

- **`main` 하나만 보호 브랜치**입니다. 별도 `develop` 레이어는 두지 않습니다.
- `main`에는 **직접 push하지 않습니다.** 모든 변경은 `브랜치 → PR → 병합` 순서를 따릅니다.
- 브랜치 네이밍: **`<이름>/fe-<기능>` / `<이름>/be-<기능>`** (WBS 작업 단위와 1:1)
  - 담당자(영문 로마자)를 네임스페이스로 앞에 붙이고, 뒤에 `fe-`/`be-` 접두로 프론트/백엔드를 구분한 기능명을 붙입니다.
  - 담당자 영문 표기(팀 확정): 조은솔 → `sol`, 김종국 → `jongkook`, 이승현 → `seunghyun`, 안권형 → `kwonhyeong`
    *(로마자 스펠링은 팀원 본인 확인 후 확정 — 다르면 이 표를 고쳐 통일)*
  - 예) 프론트: `sol/fe-auth`, `sol/fe-provisioning`, `sol/fe-inventory`, `sol/fe-dashboard`
  - 예) 백엔드: `kwonhyeong/be-env-setup` 등 담당자에 맞춰 네임스페이스만 바꿔 동일 규칙 적용
- **이름은 "누구 것인지" 보여주는 라벨일 뿐**, 병합·리뷰 단위는 항상 기능입니다. 같은 사람이 두 기능을 동시에 작업해도 `<이름>/`은 같고 뒤 기능명이 달라 브랜치·PR은 기능 단위로 분리됩니다.
- 브랜치는 항상 **최신 `main`에서 분기**합니다. 작업이 길어지면 주기적으로 `main`을 rebase 또는 merge해 충돌을 일찍 발견하세요.
- **PR이 병합되면 브랜치는 삭제**합니다(원격/로컬 모두).

```bash
git checkout main
git pull origin main
git checkout -b <이름>/fe-<기능>   # 예) sol/fe-auth
```

---

## 2. 커밋 컨벤션

[Conventional Commits](https://www.conventionalcommits.org/) 형식을 쓰되 **설명은 한국어**로 씁니다.

```
<type>: <한 줄 요약>
```

`type` 종류: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`

예시:
```
feat: 로그인 화면 마크업 및 유효성 검사 추가
fix: 회원가입 소속 드롭다운 선택 안 되는 문제 수정
chore: frontend 폴더 골격 초기화
docs: CLAUDE.md 진행상황 갱신
```

> 커밋 하나는 **리뷰 가능한 단위로 작게** 쪼갭니다.

---

## 3. PR 규칙

- **모든 변경은 PR을 통해서만 `main`에 들어갑니다. 예외 없음.**
- **병합(merge) 권한은 조은솔에게만** 있습니다. 다른 팀원은 PR을 열고 리뷰를 요청할 수 있지만 merge 버튼은 누르지 않습니다.
- 병합 방식은 **Squash and merge**로 통일합니다(브랜치 내 잔커밋이 `main` 히스토리를 어지럽히지 않도록).
- PR을 열 때 템플릿(`.github/PULL_REQUEST_TEMPLATE.md`)의 항목을 채웁니다:
  - 관련 WBS 항목 / 기능명세서 항목 번호
  - 변경 요약
  - 화면 변경 시 스크린샷 또는 GIF
  - 확인한 것(수동 테스트 체크리스트)
  - 리뷰어에게 특별히 봐달라고 할 부분

### `main` 브랜치 보호 설정 (레포 관리자: 조은솔)

GitHub `Settings → Branches → Branch protection rule for main`:
- ✅ Require a pull request before merging
- ✅ Require approval (최소 1명 — 사실상 조은솔 승인)
- (가능하면) ✅ Restrict who can push to matching branches — 조은솔만 등록
