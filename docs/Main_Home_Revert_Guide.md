# 메인홈(main.html) 원래 형식으로 되돌리기 가이드

메인홈을 **기능 투어·사용 흐름·마무리 CTA가 있는 리디자인 버전**과 **원래(카드 그리드) 버전**
사이에서 언제든 되돌릴 수 있도록 정리한 문서. 필요할 때 아래 명령을 그대로 실행하면 된다.

## 무엇이 바뀌었나

- **날짜**: 2026-09-17
- **PR**: #82 (`feat: 메인홈에 기능 투어·사용 흐름·마무리 CTA 섹션 추가 + 히어로 그라데이션`)
- **병합 커밋**: `77b7393` (main에 squash merge)
- **직전(원래) 버전**: `77b7393^` = `77c103a` (#81)
- **바뀐 파일**: `frontend/main.html` **단 한 개** (298줄 추가, 1줄 삭제)

리디자인 내용 요약:
1. **히어로**: 배경을 이미지 → **그라데이션**으로 교체, 페이지 배경을 흰색으로 통일
2. **Explore the workspace**: 대시보드/인벤토리/프로비저닝/비용 관리 **탭 투어**(자동재생·키보드·ARIA)
3. **사용 흐름**: 스토리 2종(Connect & Explore / Create & Review)
4. **마무리 CTA**: 로그인 시 대시보드, 게스트는 회원가입으로 분기
5. 외부 시안 색상을 `theme.css` 토큰으로 치환(라이트/다크 대응), 모든 CSS를 `.ws-explore`로 네임스페이스

> 커밋 해시가 기억나지 않으면 `git log --oneline --grep "메인홈에 기능 투어"` 로 찾을 수 있다.

---

## 되돌리기 — 원래(카드 그리드) 버전으로

`frontend/main.html` 한 파일만 바뀌었으므로 되돌리기도 이 파일만 복원하면 된다.
**main에 직접 커밋하지 말 것** — 팀 규칙상 모든 변경은 브랜치 + PR로만 main에 들어간다(merge는 조은솔).

### 방법 A — 파일만 원래 버전으로 복원 (권장, 가장 단순)

```bash
git checkout main && git pull
git checkout -b solcho/fe-main-home-revert

# 원래(리디자인 직전) main.html로 복원
git checkout 77b7393^ -- frontend/main.html

git add frontend/main.html
git commit -m "revert: 메인홈을 리디자인 이전(카드 그리드) 버전으로 되돌림"
git push -u origin solcho/fe-main-home-revert
# → GitHub에서 PR 생성 후 병합(조은솔)
```

### 방법 B — git revert (되돌림 이력을 남기고 싶을 때)

```bash
git checkout main && git pull
git checkout -b solcho/fe-main-home-revert
git revert --no-edit 77b7393   # 이 커밋은 main.html만 바꿔서 충돌 없이 깔끔하게 되돌려진다
git push -u origin solcho/fe-main-home-revert
# → PR 생성 후 병합
```

---

## 다시 리디자인 버전으로 되돌아오기

원래 버전으로 되돌린 뒤 다시 리디자인을 적용하고 싶으면, 리디자인 커밋의 `main.html`을 그대로 가져온다.

```bash
git checkout -b solcho/fe-main-home-redesign-restore
git checkout 77b7393 -- frontend/main.html
git add frontend/main.html
git commit -m "feat: 메인홈 리디자인 버전 재적용"
git push -u origin solcho/fe-main-home-redesign-restore
# → PR 생성 후 병합
```

---

## 빠른 미리보기 (커밋 없이 확인만)

브랜치를 만들기 전에 두 버전을 눈으로만 비교하고 싶을 때:

```bash
# 원래 버전을 임시 파일로 뽑아 브라우저로 열어보기
git show 77b7393^:frontend/main.html > /tmp/main-original.html
git show 77b7393:frontend/main.html  > /tmp/main-redesign.html
# 두 파일을 브라우저에서 열어 비교 (assets 경로가 상대경로라 스타일이 안 보일 수 있으니 구조 확인용)

# 두 버전의 차이만 보기
git diff 77b7393^ 77b7393 -- frontend/main.html
```

작업 트리에서 잠깐만 원래 버전으로 바꿔 확인하고 되돌리려면:

```bash
git checkout 77b7393^ -- frontend/main.html   # 원래 버전으로
# ... 브라우저에서 확인 ...
git checkout 77b7393  -- frontend/main.html   # 다시 리디자인 버전으로
# (또는 git restore --source=HEAD -- frontend/main.html 로 현재 main 버전 복구)
```

---

## git 이력을 쓸 수 없을 때 — 수동 되돌리기

`git` 접근이 불가능한 상황을 대비한 fallback. **git 방법이 훨씬 안전하므로 가급적 위 방법을 쓴다.**
리디자인은 순수 추가 위주라, `frontend/main.html`에서 아래 4가지를 제거/원복하면 원래 형식이 된다.

1. **`<head>`의 `<style>` 블록에서** 아래 규칙들을 제거
   - `body{ background:var(--surface); }` (페이지 배경 흰색 통일)
   - `.hero{ ... 그라데이션 ... }` 와 `:root[data-theme="dark"] .hero{ ... }`
   - `.ws-explore ...`로 시작하는 모든 규칙(탭 투어·스토리·CTA 스타일)과 `@keyframes ws-enter`/`ws-grow`
2. **히어로 섹션 원복**: `<section class="hero">` → 원래 `<section class="mx-auto max-w-content px-6 py-20 md:py-28">`로
   바꾸고, 리디자인에서 안쪽에 추가로 감싼 `<div class="mx-auto max-w-content ...">` 한 겹을 제거
3. **본문 섹션 3개 제거**: `<section id="features" class="ws-explore ...">`(Explore the workspace),
   `<section id="workflow" ...>`(사용 흐름), 그리고 그 아래 마무리 CTA `<section class="ws-explore ... ending>`
4. **하단 스크립트 제거**: 탭 투어용 `<script> (function () { ... var entries = [ ... ] ... })(); </script>`
   블록(주석 `Explore the workspace 탭 투어:`로 시작)

> "하나의 대시보드, 모든 클라우드" 카드 섹션과 푸터, 기존 헤더/히어로 텍스트는 원래 그대로 두면 된다.

---

## 참고

- 리디자인 원본 시안: `main-home-animated.html`(외부 제공, repo 미포함). 색상만 `theme.css` 토큰으로 치환해 이식.
- 미사용 자산: `frontend/assets/imgs/multicloud-hero-background-v2.png`(로컬 존재, 미커밋).
  히어로가 그라데이션이라 현재 v1/v2 이미지 모두 참조하지 않는다.
