# Git 브랜치 사용 가이드 (팀원용)

이 문서는 **git이 처음이어도** 그대로 따라 할 수 있도록 명령어 위주로 정리한 실전 가이드입니다.
우리 팀 규칙 요약은 `CONTRIBUTING.md`, 이 문서는 "그래서 실제로 뭘 치면 되는데?"에 답합니다.

> 핵심 규칙 3가지만 기억하세요.
> 1. **`main`에는 직접 커밋/푸시하지 않는다.** 항상 브랜치를 따로 만들어 작업한다.
> 2. 브랜치 이름은 **`<내이름>/fe-<기능>`** 또는 **`<내이름>/be-<기능>`** (예: `sol/fe-auth`, `kwonhyeong/be-env-setup`).
> 3. 작업이 끝나면 **PR(Pull Request)** 을 올리고, 병합은 **조은솔**이 한다.

---

## 0. 큰 그림 (전체 흐름)

```
main (보호됨, 안정 버전)
 └── 내 브랜치 만들기 (예: sol/fe-auth)
       └── 코드 작업 → 커밋(commit)
            └── 원격에 올리기 (push)
                 └── GitHub에서 PR 생성
                      └── 리뷰 → 조은솔이 병합(merge)
                           └── 브랜치 삭제 → 다시 main 최신화
```

한 사이클은 아래 1~7단계입니다. 새 기능을 시작할 때마다 이 사이클을 반복합니다.

---

## 1. 맨 처음 딱 한 번 — 저장소 내려받기 (clone)

이미 받아둔 사람은 건너뜁니다.

```bash
git clone https://github.com/Cloud-Main-project-Team2/Multi-Cloud-Platform.git
cd Multi-Cloud-Platform
```

`clone`은 원격 저장소 전체를 내 컴퓨터로 복제하는 명령입니다. 한 번만 하면 됩니다.

---

## 2. 새 작업 시작 — 최신 main에서 브랜치 만들기

**항상 최신 `main`에서 시작**해야 남의 작업과 충돌이 적습니다.

```bash
# (1) main 브랜치로 이동
git checkout main

# (2) 원격의 최신 내용을 내려받아 로컬 main을 최신화
git pull origin main

# (3) 새 브랜치를 만들면서 그 브랜치로 이동 (-b = 새로 만들기)
git checkout -b sol/fe-auth
```

- `git checkout -b <브랜치명>` = **로컬에 새 브랜치를 만들고 그 위로 이동**.
- 이 시점엔 아직 내 컴퓨터에만 있는 브랜치입니다(= 로컬 브랜치). 원격(GitHub)엔 아직 없습니다.
- 지금 내가 어느 브랜치에 있는지 확인: `git branch` (별표 `*`가 현재 브랜치)

---

## 3. 작업하고 커밋하기 (commit)

파일을 수정한 뒤:

```bash
# (1) 지금까지 바뀐 내용 확인
git status

# (2) 커밋에 포함할 파일을 무대에 올림(stage). 전부 올리려면:
git add .
#   특정 파일만: git add frontend/login.html

# (3) 커밋 = 변경사항을 한 덩어리로 저장 (메시지는 한국어, 규칙은 아래)
git commit -m "feat: 로그인 화면 마크업 추가"
```

**커밋 메시지 규칙** (`<type>: <요약>`):

| type | 언제 |
|---|---|
| `feat` | 새 기능 |
| `fix` | 버그 수정 |
| `docs` | 문서 |
| `chore` | 설정/잡일 |
| `refactor` | 동작 그대로, 코드 정리 |
| `test` | 테스트 |

> 커밋은 **작게 자주** 하세요. "로그인 화면 + 회원가입 + 유효성검사"를 한 커밋에 몰지 말고 나눕니다.

---

## 4. 원격에 브랜치 올리기 (push) — "원격 브랜치 만들기"

로컬 브랜치를 GitHub에도 올려야 다른 사람이 볼 수 있고 PR을 만들 수 있습니다.
**처음 올릴 때만** `-u origin`을 붙입니다:

```bash
git push -u origin sol/fe-auth
```

- 이 명령이 실행되는 순간 **원격(GitHub)에 같은 이름의 브랜치가 새로 생깁니다.** 즉 "원격 브랜치 만들기" = 로컬 브랜치를 push하는 것.
- `-u`(= `--set-upstream`)는 "이 로컬 브랜치는 앞으로 이 원격 브랜치와 짝"이라고 연결해주는 옵션. **한 번만** 해두면 됩니다.
- 이후 추가 커밋을 올릴 때는 짧게:

```bash
git push
```

---

## 5. PR(Pull Request) 만들기

push하면 GitHub 저장소 페이지 위쪽에 **"Compare & pull request"** 버튼이 뜹니다. 그걸 누르거나, 웹에서:
`Pull requests` 탭 → `New pull request` → base: `main` ← compare: `sol/fe-auth` 선택.

- PR 템플릿의 항목(작업 내용, 관련 이슈, 확인한 것 등)을 채웁니다.
- 화면이 바뀌었으면 **스크린샷**을 붙입니다.
- 만들면 조은솔에게 리뷰를 요청하세요.

> **merge(병합) 버튼은 조은솔만 누릅니다.** 팀원은 PR을 만들고 리뷰 요청까지만.
> 병합은 **Squash and merge** 방식으로 통일합니다(→ 무슨 뜻인지는 [부록 B](#부록-b-squash-merge란-왜-쓰나) 참고).

---

## 6. 병합 후 — 브랜치 정리하고 다시 최신화

조은솔이 PR을 병합하면, 그 브랜치는 역할이 끝났으니 지웁니다.

```bash
# (1) 다시 main으로 이동
git checkout main

# (2) 방금 병합된 최신 내용을 내려받아 로컬 main 갱신
git pull origin main

# (3) 이제 필요 없어진 로컬 브랜치 삭제
git branch -d sol/fe-auth
#   ↑ "not fully merged" 같은 경고로 거부되면(스쿼시 병합 등) 대문자로 강제 삭제:
#   git branch -D sol/fe-auth

# (4) 원격 브랜치도 삭제 (GitHub PR 화면의 "Delete branch" 버튼으로 이미 지웠다면 생략)
git push origin --delete sol/fe-auth

# (5) 원격에서 지워진 브랜치의 흔적을 로컬에서 청소
git fetch --prune
```

정리가 끝나면 다시 **2번**으로 돌아가 다음 기능 브랜치를 만듭니다.

---

## 7. (작업이 길어질 때) 중간중간 main 따라잡기

내 브랜치에서 며칠씩 작업하면 그사이 `main`이 바뀝니다. 나중에 한 번에 합치면 충돌이 커지니, **가끔 main을 내 브랜치로 가져와** 충돌을 미리 발견하세요.

```bash
# 내 브랜치에 있는 상태에서:
git fetch origin          # 원격 최신 정보만 가져옴 (아직 내 브랜치엔 반영 안 함)
git merge origin/main     # 최신 main을 내 브랜치에 합침
```

> 충돌(conflict)이 나면 겁먹지 말고: 표시된 파일을 열어 `<<<<<<<`, `=======`, `>>>>>>>` 사이에서 남길 내용을 고른 뒤 그 표시들을 지우고 저장 → `git add <파일>` → `git commit` 하면 됩니다. 막히면 조은솔에게.

---

## 부록 A. 로컬 브랜치 vs 원격 브랜치 (개념)

| 구분 | 어디에 있나 | 어떻게 만드나 | 누가 보나 |
|---|---|---|---|
| **로컬 브랜치** | 내 컴퓨터 | `git checkout -b <이름>` | 나만 |
| **원격 브랜치** | GitHub 서버 | 로컬 브랜치를 `git push -u origin <이름>` 하면 생성 | 팀 전체 |

핵심: **원격 브랜치는 따로 "만드는" 게 아니라, 로컬 브랜치를 처음 push할 때 자동으로 생깁니다.**
- 로컬에서 만들고 → 커밋하고 → push하면 → 원격에도 생김.
- 남이 만든 원격 브랜치를 내 컴퓨터로 가져오려면:
  ```bash
  git fetch origin
  git checkout kwonhyeong/be-env-setup   # 원격 브랜치 이름 그대로 치면 로컬에 연결되어 생성됨
  ```

---

## 부록 B. Squash merge란? (왜 쓰나)

PR을 병합할 때 GitHub는 3가지 방법을 줍니다. 차이는 **"내 브랜치의 커밋들을 `main`에 어떤 모양으로 남길 것이냐"** 뿐입니다.

내가 `sol/fe-auth`에서 커밋을 4개 만들었다고 합시다. 작업하다 보면 이런 잡은 커밋이 쌓입니다:
```
sol/fe-auth 브랜치:
  ● feat: 로그인 화면 마크업
  ● fix: 버튼 정렬 오타 수정
  ● fix: 오타 또 수정
  ● feat: 유효성 검사 추가
```

### ⭐ Squash and merge (우리 팀 정책)
브랜치의 커밋 4개를 **하나로 짓눌러(squash)** 합칩니다. `main`엔 깔끔한 커밋 1개만 남습니다.
```
main:
  ● feat: 로그인 화면 및 회원가입 구현 (#2)   ← 4개가 1개로
```
- ✅ `main` 히스토리가 **기능 단위로 깔끔**. "오타 또 수정" 같은 잡음이 안 남음.
- ✅ 나중에 `main` 로그만 봐도 어떤 기능이 언제 들어왔는지 한눈에 보임.

### Create a merge commit (GitHub 기본값)
커밋 4개를 **그대로 다 가져오고** "합쳤다"는 표시 커밋을 하나 더 얹습니다.
```
main:
  ● Merge pull request #2 ...
  ● feat: 유효성 검사 추가
  ● fix: 오타 또 수정          ← 잡음까지 다 남음
  ● fix: 버튼 정렬 오타 수정
  ● feat: 로그인 화면 마크업
```
사람이 많아지면 히스토리가 지저분해집니다.

### Rebase and merge
merge 표시 커밋 없이 4개를 일렬로 붙입니다. 초보 팀엔 비권장 — **우린 안 씁니다.**

### 정리
- 병합은 **조은솔이 `Squash and merge` 버튼**만 고르면 됩니다. 팀원은 몰라도 무방.
- 아예 다른 방식을 못 고르게 잠글 수도 있음: `Settings → General → Pull Requests`에서 **Allow squash merging만 체크**, 나머지 둘 해제 → 병합 버튼이 Squash 하나만 남음.

---

## 부록 C. 자주 쓰는 명령어 치트시트

```bash
git status                     # 현재 상태(바뀐 파일, 현재 브랜치) 확인
git branch                     # 로컬 브랜치 목록 (* = 현재)
git branch -a                  # 원격 포함 전체 브랜치 목록
git checkout <브랜치>          # 해당 브랜치로 이동
git checkout -b <브랜치>       # 새 브랜치 만들고 이동
git add .                      # 바뀐 파일 전부 stage
git commit -m "type: 요약"     # 커밋
git push -u origin <브랜치>    # 브랜치 첫 push (원격 브랜치 생성)
git push                       # 이후 push
git pull origin main           # 원격 main 최신 내용 받기
git log --oneline -10          # 최근 커밋 10개 한 줄로 보기
git diff                       # 아직 stage 안 한 변경 내용 보기
```

---

## 한 장 요약 (복붙용 사이클)

```bash
# 1. 새 작업 시작
git checkout main && git pull origin main
git checkout -b sol/fe-<기능>

# 2. 작업 → 커밋 (반복)
git add .
git commit -m "feat: ..."

# 3. 원격에 올리고 PR
git push -u origin sol/fe-<기능>
#   → GitHub에서 PR 생성 → 조은솔이 병합

# 4. 병합 후 정리
git checkout main && git pull origin main
git branch -d sol/fe-<기능>
git fetch --prune
```
