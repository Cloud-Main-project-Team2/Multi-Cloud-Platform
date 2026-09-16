# 대시보드 멀티 콘솔 런처 — 구현 설계 (2026-09-16)

> `solcho/fe-*` 계열 프론트 작업. 대시보드(`frontend/dashboard.html`)의 콘솔 바로가기
> 버튼을 **탭형 Cloud Shell 런처 카드**로 교체하는 작업의 구현 설계·주의사항·정보 출처·단계
> 정리. **구현 시작 전 합의용 문서** — 코드 작성 전에 이 문서로 방향을 고정한다.

## 1. 배경 & 목표

- 현재 대시보드 헤더에는 AWS/Azure/GCP **콘솔 홈으로 가는 정적 링크 버튼 3개**가 있다
  (`dashboard.html:70-74`). 클릭하면 각 CSP 콘솔 **루트(홈페이지)** 로만 이동한다
  (딥링크가 막히는 게 아니라 `href`가 홈 URL 그 자체 — 가로채는 JS 핸들러 없음).
- 사용자 요구: 3사 콘솔 셸을 **회사별 창 3개로 오가지 않고, 한 자리에서 탭으로 전환**하며
  띄우고 싶다.
- **원칙(사용자 확정)**: 로그인·인증은 **100% 사용자 몫**. 편의보다 **안정성/보안 우선**.
  우리 플랫폼은 자격증명을 URL·federation으로 끼워넣지 **않는다**.

## 2. 확정된 방향

- **형태**: 대시보드 **안**에 들어가는 **작은 탭형 카드**(별도 페이지 아님).
  - 탭 바 `[AWS] [Azure] [GCP]` + 내용 영역(대상 정보 한 줄 · 열기 버튼 · 복사 버튼 · 안내문).
  - 탭을 누르면 내용 영역이 해당 provider 값으로 **교체**된다(한 번에 한 클라우드).
- **동작**: 열기 버튼 → 실제 셸은 **새 브라우저 탭**으로 열림
  (`window.open(url, '_blank', 'noopener,noreferrer')`). 결과적으로 브라우저 한 창 안에
  3사 셸이 **브라우저 네이티브 탭**으로 나란히 놓인다.
- 기존 헤더 버튼 3개는 이 카드로 **대체**한다.

## 3. 기술적 제약 & 설계 원칙 (반드시 지킬 것)

1. **iframe 임베드 불가 — 우회 금지.** AWS/Azure/GCP 콘솔·CloudShell은 모두
   `X-Frame-Options: DENY` / CSP `frame-ancestors 'none'`으로 임베드를 막는다. 우리 페이지
   *내부에* 터미널을 렌더하는 형태(side-by-side든 탭이든)는 **만들 수 없다**. 이는 각 CSP가
   보안상 의도적으로 막은 것이므로 우회하지 않는다(우회 = "안정성 우선" 원칙과 정반대).
   → 그래서 "런처"다: 우리는 **여는 조작만** 한 화면에 모으고, 터미널은 브라우저 탭에 맡긴다.
2. **로그인은 전적으로 사용자 브라우저 세션.** 우리가 만드는 값 중 어떤 것도 인증에 관여하지
   않는다. 셸은 사용자가 이미 로그인해 둔 계정으로 열린다.
3. **URL에 토큰·비밀키·자격증명 일절 미포함.** 설계상 넣을 일도 없다.
4. **백엔드/모델/스키마/마이그레이션 변경 없음.** 순수 프론트 작업
   (`dashboard.html` + `dashboard.js`, 필요 시 작은 신규 JS).
5. **값을 못 구해도 셸은 열려야 한다.** 계정/리전을 못 구하면 **맨 URL로 폴백**한다(§4·§5 참고).

## 4. 사용되는 정보 (출처 · 역할)

세 URL 모두 **계정 정보 없이 맨 URL만으로도 열린다.** 아래 값들은 "여는 데 필수"가 아니라
**어디로 열지 사용자가 고르고, 로그인 계정을 확인**하기 위한 값이다. **로그인과는 무관**하다.
정보를 표시만 하지 않고 **드롭다운으로 사용자가 선택**할 수 있게 한다(§6).

| 값 | 출처 | URL에 실제 사용? | 역할·선택 |
|---|---|---|---|
| AWS **region** | 허용목록 드롭다운, 기본=`/resources` 최빈 region → 없으면 `ap-northeast-2` | ✅ `?region=<region>` | **선택** → 그 리전으로 진입 |
| GCP **project id** | 계정 드롭다운(`external_account_id`, gcp) | ✅ `?project=<id>` | **선택** → 그 프로젝트로 진입 |
| AWS **계정 ID** | 계정 드롭다운(`external_account_id`, aws) | ❌ | **선택**(표시) — 로그인 계정 확인용 |
| Azure **구독 ID** | 계정 드롭다운(`external_account_id`, azure) | ❌ (공식 파라미터 없음) | **선택** → `az account set` 복사에 반영 |
| GCP **region** | 허용목록 드롭다운, 기본=`/resources` 최빈 region | ❌ | **선택** → `gcloud config set` 복사에 반영 |

- **리전 허용목록**(프로비저닝 폼과 동일): AWS `ap-northeast-2`/`us-east-1`,
  GCP `asia-northeast3`/`us-central1`. 유추 리전이 목록 밖이면 그 값을 옵션으로 추가한다.
  Azure는 Cloud Shell URL에 리전 파라미터가 없어 리전 선택 없음.

- **Azure 테넌트 ID는 사용하지 않는다.** 계정 컬럼에 없고 암호화된 `secret_payload` 안에만
  있어 목록 API로는 못 받는다. → Azure는 테넌트 없이 `https://portal.azure.com/#cloudshell/`.
- **데이터 소스 API (둘 다 이미 존재, 인증 필요)**:
  - `GET /cloud-accounts` → `{ items: [{ id, provider, external_account_id, account_label,
    … }], total }` (`id` 오름차순). `MCPApi.request()`가 envelope의 `data`를 벗겨 주므로
    프론트는 `resp.items`로 접근. **provider별 계정 전부를 드롭다운 옵션으로** 쓰고 기본 선택은
    첫 계정(최소 id). (dashboard.js에 이 호출을 **신규 추가**한다 — 현재는 미호출.)
  - `GET /resources` → 각 항목 `region`, `cloud_account.provider`. AWS/GCP 리전 유추에 사용
    (이미 대시보드가 호출 중).

## 5. Provider별 URL 규칙

| provider | 값 있을 때 | 값 없을 때(폴백) |
|---|---|---|
| AWS | `https://console.aws.amazon.com/cloudshell/home?region=<region>` | region은 항상 `ap-northeast-2` 폴백이 있으므로 최소 이 형태 |
| Azure | `https://portal.azure.com/#cloudshell/` | 동일(테넌트 미사용) |
| GCP | `https://console.cloud.google.com/home/dashboard?project=<projectId>&cloudshell=true` | 프로젝트 없으면 `https://shell.cloud.google.com/` |

## 6. 화면 / UX 구성

```
┌ 클라우드 콘솔 ──────────────────────────────────┐
│ [ AWS ] [ Azure ] [ GCP ]          ← 탭 바       │
│ ─────────────────────────────────────────────── │
│  대상 계정 [1111-2222-3333 ▼] · region [서울 ▼] │  ← 드롭다운 선택, 탭 따라 교체
│  ⓘ 로그인은 브라우저에서 직접 하세요              │
│             [ AWS Cloud Shell 새 창으로 열기 ↗ ] │
└──────────────────────────────────────────────────┘
```

- **탭 내용(교체되는 부분) — 표시가 아니라 선택**
  - AWS: `대상 계정 [select] · region [select]` + 열기 버튼. (region select → URL 반영)
  - Azure: `대상 구독 [select]` + `[az account set 복사]` + 열기 버튼. (region 없음)
  - GCP: `대상 프로젝트 [select] · region [select]` + `[gcloud config set 복사]` + 열기 버튼.
  - 계정이 여러 개면 드롭다운으로 **선택**, 하나면 그 값이 기본. 없으면 "미연결" + 맨 URL 폴백.
  - select 변경 시 내용을 재렌더해 **열기 URL·복사 명령이 즉시 갱신**된다.
- **버튼 라벨**: "◯◯ Cloud Shell 새 창으로 열기 ↗" (새 창 이동임을 ↗로 명시).
- **복사 버튼**: `navigator.clipboard.writeText(...)`. 문구 예:
  - Azure `az account set --subscription "<subscription_id>"`
  - GCP `gcloud config set compute/region <region>`
- **안내문**: 각 탭 "로그인은 브라우저에서 직접 하세요". GCP 탭에는
  "`cloudshell=true`로 안 열리면 우측 상단 Cloud Shell 아이콘을 눌러주세요" 폴백 안내.
- **스타일**: 기존 카드 톤(`rounded-2xl border border-border bg-surface`, 배지 스타일) 재사용.

## 7. 주의사항 (구현 시 반드시 인지)

1. **다중 계정 — 드롭다운으로 선택.** 한 provider의 계정 전부를 드롭다운 옵션으로 제공하고
   기본 선택은 첫 계정(최소 id). 시드는 provider당 1개라 데모에선 옵션이 하나로 보인다.
2. **계정 미연결 provider.** `/cloud-accounts`에 해당 provider가 없으면 값 표시 없이
   "계정 미연결" 안내 + 맨 셸 URL로 열기(§5 폴백).
3. **GCP `cloudshell=true` 자동 오픈은 공식 문서에 없는 동작.** 실제 브라우저에서 Cloud Shell
   패널이 자동으로 열리는지 **실 계정 테스트로 확인 필요**. 안 열리면 폴백 안내(§6)로 충분히
   대응되도록 문구를 미리 넣어 둔다.
4. **표시 계정 ≠ 로그인 계정일 수 있음.** 셸은 브라우저의 현재 로그인 계정으로 열린다. 표시된
   "대상 계정"은 사용자가 불일치를 **눈치채게 하는 안전장치**이지 강제가 아니다.
5. **`window.open`은 항상 `noopener,noreferrer`.** 새 탭이 우리 페이지 `window.opener`에
   접근하지 못하게 한다(탭내빙 방지).
6. **팝업 차단 가능성.** 사용자 클릭 핸들러 안에서 직접 `window.open`을 호출한다(비동기 뒤로
   미루지 않음) — 그래야 브라우저가 사용자 제스처로 인정해 팝업 차단을 피한다.
7. **5상태 실 계정 테스트는 이 환경에서 불가.** 로그인됨/로그아웃/타계정/권한없음/처음 상태 및
   GCP 자동 오픈은 실 계정 브라우저 필요 → §9 체크리스트를 실 계정 보유자가 수행.

## 8. 개발 단계 & 커밋 분할

1. **커밋 1 — 마크업**: `dashboard.html:70-74`의 `<a>` 3개를 탭형 카드로 교체
   (탭 바 + 내용 영역 골격 + id 부여, 기존 카드 톤 재사용).
2. **커밋 2 — 데이터 + 탭·선택 로직**: `dashboard.js`에
   - `/cloud-accounts` 호출 → provider별 계정 목록(드롭다운 옵션) 구성.
   - `/resources`에서 AWS/GCP 최빈 region 유추(+`ap-northeast-2` 폴백)로 리전 select 기본값.
   - 탭 전환(active 토글 + 내용 교체), 계정/리전 **select**(change 시 재렌더), URL 생성,
     열기 버튼(`window.open`).
3. **커밋 3 — 복사 버튼 · 안내 문구**: Azure/GCP `navigator.clipboard` 복사 버튼(선택값 반영),
   탭별 로그인/폴백 안내문, 계정 미연결·팝업 차단 등 엣지 처리.

- 커밋 컨벤션: `feat: <요약>`(한국어). PR로 `main` 병합(merge owner 조은솔).

## 9. 검증 계획

- **개발자 검증(내가 수행)**: JS 문법(`node --check`), 로컬에서 탭 전환·버튼 클릭 시
  **새 탭 URL이 의도대로** 생성/오픈되는지, 값 없을 때 폴백 URL이 나오는지.
- **실 계정 검증(실 계정 보유자 수행)** — AWS/Azure/GCP 각각 아래 5상태 확인:

  | 상태 | 기대 동작 |
  |---|---|
  | 이미 로그인됨 | 새 탭에서 해당 계정 Cloud Shell로 진입 |
  | 로그아웃 상태 | CSP 로그인 화면 표시(문제 아님 — 로그인 후 셸) |
  | 다른 계정으로 로그인됨 | 셸은 열리되 그 계정으로 열림 → 표시 "대상 계정"으로 불일치 인지 |
  | 권한 없음 | CSP가 자체 권한 안내 표시(우리 책임 밖) |
  | 그 클라우드 브라우저 처음 | 로그인 플로우부터 정상 진입 |

  - GCP 추가: `cloudshell=true`가 실제로 패널을 자동으로 여는지 / 폴백 안내가 맞는지.

## 10. 범위 밖 / 후속 과제

- **무로그인 Federation URL(AWS AssumeRole 임시자격 → 콘솔 진입)**: 이번 스코프 제외
  (별도 요청 시 추가).
- **AWS IAM Identity Center 딥링크**: **적용하지 않음.** 우리 인증은 "서버가 플랫폼 IAM 키로
  고객 역할에 `sts:AssumeRole`"(`be-assume-role`) 구조이고, 브라우저의 사람은 우리를 거치지
  않고 자기 CSP 세션으로 로그인한다. IdC 딥링크는 최종 사용자가 IdC(SSO 포털)에 로그인하는
  별도 신원 체계를 전제 — 우리에겐 IdC 인스턴스/permission set이 없어 **아키텍처가 맞지 않는다.**
- **계정별 region/tenant 스키마 승격**: 지금은 리전 유추+기본값·테넌트 생략으로 충분. 조회·필터
  요구가 생기면 `CloudAccount`에 컬럼 추가를 검토(이 프로젝트는 Alembic 없이
  `Base.metadata.create_all`이라 스키마 변경은 신중히 — 별도 과제).
