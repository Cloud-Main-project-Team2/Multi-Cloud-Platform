# Azure 자격증명 검증 — 실패 단계 진단 기록(2026-09-26)

> **담당자 영역 알림.** `app/providers/azure.py`(자격증명 검증)와 `app/routers/credentials.py`는
> 키 관리 담당(조은솔) 영역이다. 이 변경은 **비용 라운드(이승현)에서 원인 파악이 막혀** 최소 범위로
> 넣은 것이고, 담당자와의 합의는 **아직 이뤄지지 않았다**(공유·확인 필요). 이 문서는 무엇을 왜
> 바꿨는지와 남은 결정을 넘기기 위한 기록이다.

## 왜 넣었나

2026-09-25 검증 환경에서 실제 Azure 자격증명 등록이 실패했는데(3회 시도, 모두
`verified=false`), 남은 기록으로는 **인증 실패인지 구독 접근 문제인지 구분할 수 없었다.**

원인은 코드 구조였다. `verify()`가 자격증명 객체 생성 + 구독 조회를 **하나의 try**로 묶고
`ClientAuthenticationError·HttpResponseError·AzureError·KeyError`를 전부 같은
`PROVIDER_AUTHENTICATION_FAILED`로 바꾸면서, 예외 종류·HTTP 상태를 어디에도 남기지 않았다.
`azure.identity`/`azure.core` 로거도 설정돼 있지 않아 컨테이너 출력에도 흔적이 없었다.

덧붙여 **`ClientAuthenticationError`는 `HttpResponseError`의 하위 클래스**라(mro 확인),
예외 타입만으로는 단계를 가를 수 없다 — 갈리는 것은 **호출 지점**이다.

## 무엇을 바꿨나 (범위 최소)

`app/providers/azure.py::verify()`를 세 단계로 나누고, 실패한 단계에서만 한 줄을 남긴다.

| 단계 | 하는 일 | 실패 시 기록되는 `stage` |
|---|---|---|
| payload | 입력값으로 `ClientSecretCredential` 생성 | `payload` |
| token | `credential.get_token("https://management.azure.com/.default")` | `token` |
| subscription | `SubscriptionClient(...).subscriptions.get(<구독ID>)` | `subscription` |

azure-identity는 **지연 인증**이라 객체 생성만으로는 토큰을 받지 않는다. 그래서 토큰 단계를
구분하려면 명시적으로 한 번 받아야 한다. 받은 토큰은 자격증명 객체가 캐시해 다음 호출이
재사용하지만, **만료·갱신·SDK 재시도가 있으므로 이 호출로 외부 요청 횟수가 고정되지는 않는다.**

### 기록하는 것 / 하지 않는 것

기록: `stage`, `exception`(클래스 이름), `http_status`(정수일 때만), `azure_error_code`.

- `azure_error_code`는 **허용 목록**에 있는 ARM 코드만 그대로 남긴다 —
  `AuthorizationFailed` · `SubscriptionNotFound` · `InvalidSubscriptionId` ·
  `InvalidAuthenticationToken` · `InvalidAuthenticationTokenTenant` · `ExpiredAuthenticationToken`.
  목록 밖 값은 원문을 남기지 않고 `"other"`로만 적는다.
- AAD 쪽은 예외 문자열에서 **정규식에 맞는 토막만**(`AADSTS[0-9]{4,10}`, 20자 상한) 떼어 온다.
  자릿수를 5~6자리로 단정하지 않았다 — 공식 표에 **5·6·7자리가 모두** 있다(`AADSTS16000` …
  `AADSTS9002341`, learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes,
  2026-09-26 확인).

기록하지 않음: 원문 예외 메시지 · 응답 본문 · 헤더 · 요청 URL · 시크릿 · 토큰. Azure SDK의 전역
디버그/HTTP 본문 로깅(`logging_enable`)은 **켜지 않았다**(그쪽은 본문을 통째로 찍는다).

### 해석하지 않는다

로그는 **관측 사실만** 적는다. 예컨대 `SubscriptionNotFound`를 "테넌트 불일치"로 해석해 적지
않는다 — 구독이 실제로 없을 수도, 그 앱 등록에 보이지 않을 수도 있고 이 기록만으로는 구분되지
않는다. 해석은 사람이 포털에서 확인할 몫이다.

## 바뀌지 않은 것 (계약 유지)

- 반환값: 어느 단계에서 실패해도 `verified=False`, `error_code="PROVIDER_AUTHENTICATION_FAILED"`.
- `permission_scope`: 실패 시 기본값(4개 false), 성공 시 `inventory_read` 프로빙 동작 그대로.
- API 응답 필드(`verification_error_code`/`_message`), 에러 카탈로그, 프론트(`mypage.js`) 모두 변경 없음.
- DB 스키마·마이그레이션 없음.

## 함께 고친 것 — CSP별 검증 실패 문구 (2026-09-26, 사용자 요청으로 추가 수정)

처음에는 "별도 결함으로 기록만" 하기로 했다가, Azure뿐 아니라 **GCP에서도 같은 "AWS" 문구가 뜬다**는
확인이 와서 같은 라운드에서 고쳤다(사용자 요청).

- 전: `_VERIFICATION_MESSAGES` 표가 **하나**뿐이라 어느 CSP가 실패해도 "자격 증명으로 AWS 인증에
  실패했습니다."가 나갔다. 역할 위임(AssumeRole) 안내도 AWS 전용 개념인데 다른 CSP에 그대로 쓰였다.
- 후: `_verification_message(error_code, provider)`가 provider별 표를 고른다.
  AWS 문구는 **그대로 유지**(역할 위임 안내 포함), Azure·GCP는 각자 확인할 것을 적는다.
  provider를 모르면 CSP 이름이 없는 일반 문구로 답한다.
- 호출부 3곳(`create_credential` · `patch_credential` · `verify_credential_endpoint`)에 provider를
  넘기도록 했다. 목록 조회는 원래 검증 사유를 싣지 않으므로 영향 없다.
- **오류 코드(`verification_error_code`)는 바꾸지 않았다** — 여전히 CSP와 무관하게 같은 값이다
  (B안은 여전히 보류).
- 안내 문구는 "무엇을 확인하면 되는지"까지만 적는다. 응답만으로는 어느 단계에서 실패했는지 구분되지
  않으므로 단정하지 않는다(단계 구분은 위의 서버 로그 몫).

고정 테스트: `backend/tests/test_credential_verification_messages.py` 12개 — CSP별 이름이 맞게 나오고
다른 CSP 이름이 섞이지 않는지, 역할 위임 안내가 AWS 밖으로 새지 않는지, 재검증 응답도 같은지,
목록 조회에는 사유가 없는지. 표를 하나로 되돌리면 실패하는 것까지 확인했다.

## 보류된 후속 (승인되지 않음)

- **오류 코드 분리(B안)**: 403 → `CLOUD_PERMISSION_DENIED`, 404/`SubscriptionNotFound` → 신규 코드.
  `app/error_catalog.py` · `docs/Error_Catalog_Draft_2026-09-16.md` ·
  `tests/test_error_catalog.py::IN_SCOPE_CODES` · `_VERIFICATION_MESSAGES`가 함께 움직여야 하므로
  담당자 확인 뒤에 판단한다.
- **실제 재검증**: 새 등록이 아니라, 사용자가 고른 **기존 자격증명 1건**에
  `POST /api/v1/credentials/{credential_id}/verify`를 **1회**만 보낸다. 선행 조건은 사용자가 본인
  구독·테넌트·앱 등록의 일치를 확인하는 것. 그 1회에 AAD 토큰 발급 → `subscriptions.get` →
  (성공 시에만) `resources.list` 첫 페이지가 따라오며, **HTTP 요청 수가 정확히 몇 번이라고 보장하지
  않는다.**
- 검증 환경의 계정 3·4와 자격증명 1·2·3은 **그대로 보존**한다(삭제·교체·역할 변경 금지).

## 검증 (네트워크 0건)

`backend/tests/test_azure_credential_verify_diagnostics.py` 10개 — 단계 구분 5종(payload /
token+AADSTS / subscription 403·404·401 / 허용 목록 밖 코드 / 상태·코드 없는 일반 오류),
누출 방지 2종, 성공 경로 회귀 1종, 추출 형식·길이 제한 1종.

누출 테스트는 **실제 로거(`mcp.app`)에 핸들러를 달아** 파일로 나갈 그 줄을 검사한다. 일부러
`detail=str(exc)`를 넣어 보면 이 테스트가 실패하는 것까지 확인했다(반례 확인).

관련 회귀 145개 통과(`test_credentials_api` · `test_credential_auth_types` ·
`test_secret_redaction` · `test_logging_observability` · `test_azure_discover_resources` ·
`test_azure_resource_actions` · `test_error_catalog`).
