# GCP 실측 비용 수집 — BigQuery 청구 Export 기반 설계·구현 기록 (2026-09-26)

구현: `backend/app/cost/gcp_cost.py` · 테스트: `test_cost_ingest_gcp.py`(모의 38) ·
`test_cost_ingest_gcp_sdk.py`(실제 SDK 경로 6). **실제 GCP 호출은 아직 0건이다.**

## 왜 GCP만 모양이 다른가

AWS는 Cost Explorer, Azure는 Cost Management라는 **비용 조회 API**가 있다. GCP에는 그에 해당하는
API가 없고, 결제 계정이 **BigQuery로 내보낸 Export 테이블**이 사실상의 원천이다. 그래서 세 가지가
달라진다.

1. **사전 설정이 필요하다.** 결제 계정에서 Export를 켜야 하고, 켠 시점 **이후** 데이터부터 쌓인다
   (과거 데이터는 소급되지 않는다). 보통 하루 정도 지나야 볼 만해진다.
2. **위험이 요청 수가 아니라 스캔 바이트(=과금)다.** Azure에서 "HTTP 요청 3회 상한"을 건 자리에,
   GCP는 **dry-run으로 예상 스캔량을 먼저 재고 상한을 넘으면 실제 쿼리를 보내지 않는다**.
3. **테이블 위치를 우리가 알아야 한다.** 계정 식별자(프로젝트 ID)만으로는 Export 테이블 경로를 알
   수 없다.

## 확인한 사실 (공식 문서, 2026-09-26)

출처: https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/standard-usage

| 항목 | 값 |
|---|---|
| 테이블 이름 | `gcp_billing_export_v1_<BILLING_ACCOUNT_ID>` |
| 시각 | `usage_start_time`·`usage_end_time`(**시간 단위** Timestamp), `export_time` |
| 금액·통화 | `cost`(Float), `currency`(String), `currency_conversion_rate` |
| 서비스/SKU | `service.id`·`service.description`, `sku.id`·`sku.description` |
| 크레딧 | `credits` **배열**(원소에 `amount`·`type`·`name`) |
| 요금 종류 | `cost_type` = regular / tax / adjustment / rounding_error |
| 지연 도착 | "월말에 늦게 보고된 사용량은 그 달 청구서에 없을 수 있다" — **나중에 늘어난다** |

## 우리 설계 결정

- **집계 단위**: 일(UTC) × 서비스 × `cost_type` × 통화. `DATE(usage_start_time, 'UTC')`로 묶는다
  (원본은 시간 단위라 그대로 두면 행이 24배가 된다).
- **크레딧은 별도 행**(`charge_category="credit"`, 음수). usage에 합치면 예산 소진율이 실제보다
  작아 보인다. AWS 어댑터와 같은 정책이다.
- **`cost_type` 매핑**: regular→usage, tax→tax, adjustment·rounding_error→other, **모르는 값→other**
  (usage로 만들지 않는다 — usage는 예산·급증 판정의 기준이다).
- **`coverage_basis="observed_only"` 고정.** Export는 지연 도착이 문서로 확인된 만큼 "이 날은 다
  왔다"고 말할 근거가 없다. 금액은 그대로 보이고, 전망·기간 비교·예산 소진율·급증만 보류된다.
- **원통화 보존**: 금액이 있는데 통화가 비면 추측하지 않고 형식 오류로 실패한다. 금액도 통화도
  없는 행은 조용히 건너뛴다.
- **저장 출처(`source`)는 `gcp_billing_export`** — `docs/DB_ERD_v1.2.md`가 적어 둔 이름에 맞췄다
  (코드에 예약돼 있던 `gcp_bigquery_billing`을 이번에 정리. 아직 행이 하나도 없어 비용 0).

## 과금 안전장치

| 장치 | 값 | 뜻 |
|---|---|---|
| dry-run 선행 | 항상 | 예상 스캔량을 먼저 재고 로그(`cost.gcp.dry_run`)에 남긴다 |
| `COST_GCP_MAX_SCANNED_BYTES` | 기본 2GiB | 예상치가 넘으면 **실제 쿼리를 보내지 않는다**(요청 1건으로 끝) |
| `maximum_bytes_billed` | 같은 값 | 예상이 빗나가도 서버가 그 이상 과금되기 전에 잘라 준다 |
| `COST_GCP_QUERY_TIMEOUT_SECONDS` | 기본 120 | 결과 대기 상한 |
| `api_calls` | dry-run 1 + 실제 1 = 2 | BigQuery **job 수**를 센다(스캔 바이트는 별도 축) |

## 설정

```
COST_GCP_EXPORT_TABLES=<project_id>:<프로젝트.데이터셋.테이블>[,...]
```

- 키는 **우리 DB의 계정 id가 아니라 GCP 프로젝트 id**다(어댑터가 받는 값이 그것이다).
- 비어 있으면 그 계정은 `COST_SETUP_REQUIRED`로 끝난다 — 임의의 테이블을 추측하지 않는다.
- 테이블 참조는 SQL에 문자열로 들어가는 자리라(파라미터 불가) **세 토막·식별자 문자**만 허용하고
  그 외에는 쿼리를 만들지 않는다.
- 수집 활성화 게이트(`app/cost/gating.py`)는 그대로 적용된다 — 기본값에서 GCP 호출은 0건이고,
  `COST_INGEST_PROVIDERS`에 gcp를 넣고 `COST_INGEST_ACCOUNT_IDS`에 계정을 명시해야 켜진다.

## 남은 불확실성 (실제 테이블이 있어야 확정)

- **파티션 열**: 표준 export가 `_PARTITIONTIME` 기준인지 문서가 명시하지 않는다. 없는 열을
  참조하면 쿼리가 실패하므로 지금은 `usage_start_time`으로만 거른다 → **스캔량이 클 수 있다**.
  실제 테이블에서 파티션을 확인하면 프루닝 조건을 추가한다(그만큼 과금이 준다).
- **상세(detailed) export**에서도 같은 쿼리가 도는지 미확인(표준 열을 포함하므로 될 것으로 보이나
  확인 전에는 단정하지 않는다).
- 한 계정 안에서 **통화가 섞일 수 있는지** 미확인(섞여 와도 행별 통화를 그대로 보존한다).
- `credits[].type`별 구분(프로모션/약정 할인 등)은 하지 않는다 — 합계만 뺀다.

## 실수집 전에 필요한 것 (사용자 작업)

1. 결제 계정 → **BigQuery로 비용 내보내기(표준 사용량)** 활성화, 대상 데이터셋 지정.
2. **하루 정도 대기** — 켠 이후 데이터부터 쌓인다.
3. 서비스 계정(우리가 저장한 키)에 권한 부여: 쿼리를 실행할 프로젝트의 **BigQuery 작업 사용자**,
   Export 데이터셋의 **BigQuery 데이터 뷰어**.
4. 테이블 경로(`프로젝트.데이터셋.gcp_billing_export_v1_...`)를 `COST_GCP_EXPORT_TABLES`에 등록.
5. 그 다음 격리 환경에서 preflight → 수동 1회 → 결과 확인(아래 체크리스트).

### 실데이터가 들어오면 확인할 것

- 열 이름이 문서와 같은지(`service.description`·`cost_type`·`credits.amount`).
- `cost_type`의 실제 값 목록(문서 4종 외의 값이 오는지 — 오면 `other`로 떨어지고 로그에 남는다).
- 통화·금액 규모, 크레딧 부호(음수로 오는지).
- dry-run이 보고한 스캔량(상한 조정 근거) — 로그 `cost.gcp.dry_run`.

## 검증 현황

- 모의 38개: 쿼리/파라미터 모양, dry-run 게이트(초과 시 요청 1건), 서버 상한 전달, 오류 매핑
  (403/401→권한, 404→설정 필요, 기타→API 오류), 크레딧 분리, 통화 보존, NaN/Inf 거부, 열 누락,
  범위 밖 행 제거, 테이블 설정 파싱(다른 계정 설정을 빌려 쓰지 않음·형식 위반 거부).
- 실제 SDK 경로 6개: 진짜 `bigquery.Client`에 **자격증명과 HTTP 세션만** 대체해, 실제로 나가는
  REST 요청(URL·`dryRun`·`parameterMode`·`maximumBytesBilled`·SQL 본문)과 응답 파싱을 확인.
- **실제 GCP 호출 0건.**

## 화면·계약에 미친 영향 (같은 라운드에서 함께 반영)

- **수집 전에 "설정 필요"를 먼저 알려 준다**: Export 테이블이 등록되지 않은 GCP 계정은
  `GET /costs/capabilities`에서 `PENDING`이 아니라 **`SETUP_REQUIRED`**로 나오고 `setup_hint`가
  붙는다. 단 **이미 완주한 수집 run이 있으면 그 결과가 우선이다**(저장된 데이터는 실제로 있는
  것이고, 설정이 빠진 사실은 다음 수집 시도에서 `COST_SETUP_REQUIRED`로 드러난다) — 처음엔 이
  예외 없이 넣었다가 "observed_only 정상 빈 응답"이 `CONNECTED_EMPTY` 대신 `SETUP_REQUIRED`로
  보이는 것을 기존 테스트가 잡아냈다. PENDING으로 두면 사용자가 수집 버튼만 반복해서 누르게 되고, 누르면
  `COST_SETUP_REQUIRED`로 실패한다. 이 판정은 **설정만 보므로 CSP를 호출하지 않는다**(ADR-040
  유지) — 그래서 설정 해석을 `app/cost/gcp_export_config.py`(google import 없음)로 떼어 두고
  `capability.py`와 어댑터가 같은 규칙을 쓴다.
- **`COST_SETUP_REQUIRED`를 에러 카탈로그에 등록**했다(category `cost`). 그동안 "정의만 있고 쓰는
  곳 없음"이었는데 이번에 실제로 쓰기 시작했다. 증상·원인·해결을 사람 말로 적어 뒀다.
- `docs/01_API_Specification_v1.2.md`: capabilities 예시의 GCP 항목을 `UNSUPPORTED` →
  `SETUP_REQUIRED`로 바꾸고, §11-3 상태 표에 "행 없음이어도 Export 미등록이면 SETUP_REQUIRED"를
  적었다. §19의 "GCP 수집 방식" 행은 **해소·구현됨**으로 갱신.
- 상태 어휘는 늘리지 않았다 — `SETUP_REQUIRED`는 팀 확정 5종에 이미 있고 프론트도 문구를 갖고 있다
  (`cost-state.js`: "설정 필요").
