# 멀티클라우드 3사 기능 맵핑 — 프로비저닝 설정값 입력 범위 (2026-09-10)

> 출처: 사용자가 업로드한 `☁️ 멀티클라우드 설계 3사 기능 맵핑.pdf` (AWS/Azure/GCP 기능 맵핑표, 셀 배경색으로 "설정값을 사용자에게 입력받을지"를 구분해둔 문서). 최초 판독본은 PDF를 이미지로 변환해 육안으로 읽었으나 일부 행에서 색상을 잘못 판독한 것을 사용자가 지적해, 픽셀 단위 정밀 분석(각 셀의 실제 RGB 배경색 샘플링)으로 재검증했다. 아래는 재검증 완료본이다.

## 색상 규칙 (사용자 확정)

- **파란 배경(RGB 약 207,226,243)** = 프로비저닝 폼에서 값을 실제로 입력받는 항목.
- **회색 배경(RGB 204,204,204)** = 선택지에서 아예 보이지 않도록 완전히 제외(서버 기본값 처리, 사용자에게 미노출).
- **3사(AWS/Azure/GCP) 모두 파란 배경인 행만 "공통 설정"**(프로비저닝 마법사의 공통 스텝에 노출).
- 3사 중 일부만 파란(나머지는 흰 배경 등 무색)이거나, 애초에 공통 속성표 자체가 없는 서비스군(CDN)은 전부 **"추가 설정"**(플랫폼별 스텝에 노출)으로 처리한다 — 완전 회색(제외)이 아닌 이상 사용자에게는 보여준다.

## 좌측 요약표 — 서비스군별 3사 명칭 대응

| 구분 | 세부 항목 | AWS | Azure | GCP |
|---|---|---|---|---|
| Compute | 인스턴스 | EC2 | Virtual Machines | Compute Engine |
| Compute | 컨테이너서비스 | EKS | AKS | GKE |
| Compute | 서버리스 | Lambda | Azure Functions | Cloud Functions |
| DB | RDBMS | RDS | Azure SQL Database | Cloud SQL |
| DB | NoSQL | DynamoDB | Cosmos DB | Firestore |
| DB | 캐시 | ElastiCache | Azure Cache for Redis | Memorystore |
| Storage | 오브젝트 | S3 | Blob Storage | Cloud Storage |
| Storage | 블록 스토리지 | EBS | Managed Disks | Persistent Disk |
| CDN | 콘텐츠 전송 | CloudFront | Azure CDN | Cloud CDN |
| CDN | DNS | Route 53 | Azure DNS | Cloud DNS |
| Log | 로깅 | CloudWatch Logs | Azure Monitor Logs | Cloud Logging |
| Log | 모니터링 | CloudWatch | Azure Monitor | Cloud Monitoring |

> 실제 설정값 상세표가 있는 것은 Compute→인스턴스, DB→RDBMS, Storage→오브젝트, CDN→콘텐츠전송(단, 공통 속성 없음, 대신 4절의 개념 정리표가 있음) 4개뿐이다. 컨테이너서비스/서버리스/NoSQL/캐시/블록스토리지/DNS/로깅/모니터링은 이 PDF에 상세 입력항목표가 없다(서버리스는 `프로토타입 개발 프롬프트.md`에서 이미 스코프 제외 확정된 상태와도 일치).

## 1. Compute — 인스턴스 (EC2 / Virtual Machines / Compute Engine)

| 필드 | AWS | Azure | GCP | 분류 |
|---|---|---|---|---|
| 이름 | 공통 | 공통 | 공통 | **공통 설정** |
| 리전(부분매핑) | 공통 | 공통 | 공통 | **공통 설정** (AWS ap-northeast-2/서울, Azure Korea Central 서울·Korea South 부산, GCP asia-northeast3/서울. zone은 optional·기본값) |
| 사양 | 공통 | 공통 | 공통 | **공통 설정** |
| 네트워크(매핑) | 공통 | 공통 | 공통 | **공통 설정** |
| 인바운드 규칙(불가) | 공통 | 공통 | 공통 | **공통 설정** |
| 인증(불가) | 공통 | 공통 | 공통 | **공통 설정** |
| 태그(매핑) | 공통 | 공통 | 공통 | **공통 설정** |
| 이미지 | 공통(AMI) | 공통(imageReference) | **무색**(GCP 이미지 개념 자체가 구조적으로 다름 — "VM 전체 복제/백업 템플릿에 가까움") | **추가 설정** — AWS/Azure만 입력받고 GCP는 별도 처리 |
| 스토리지(부분매핑) | 제외 | 제외 | 제외 | **완전 제외**(기본값으로 설정) |
| 권한(애매) | 제외 | 제외 | 제외 | **완전 제외**(IAM Role/Managed Identity/Service Account — 자동/기본 처리) |

### 1-1. "사양" 행 정정 — 등급명은 공통이어도 실제 값은 3사가 다르다 (2026-09-18 추가)

위 표의 "사양" 행은 **공통 설정 스텝에 노출되는 UI 구조**(등급 선택 하나)가 3사 공통이라는
뜻이지, 실제 vCPU·메모리 수치가 같다는 뜻이 아니다. 예전에는 등급 라벨 자체에 "1 vCPU · 2GB"
같은 문구를 박아 넣어 3사 공통 사양처럼 보였는데, 실측 결과 전혀 달랐다(예: Azure B1s는
1 vCPU/1GiB인데 AWS t3.micro는 2 vCPU/1GiB) — 사용자에게 틀린 스펙을 안내하고 있었다.

`frontend/assets/js/provisioning.js`의 `SPEC_TIERS`는 이제 등급 이름(경량/표준/고성능)만 공통이고
실제 SKU·vCPU·메모리는 플랫폼별 메타데이터로 따로 관리한다(재검증한 실제 값):

| 등급 | AWS | Azure | GCP |
|---|---|---|---|
| 경량 | t3.micro (2 vCPU / 1 GiB) | **B1s** (1 vCPU / 1 GiB, 무료 대상) | e2-micro (2 vCPU / 1 GiB) |
| 표준 | t3.medium (2 vCPU / 4 GiB) | B2s (2 vCPU / 4 GiB) | e2-medium (2 vCPU / 4 GiB) |
| 고성능 | t3.large (2 vCPU / 8 GiB) | B4ms (4 vCPU / 16 GiB) | e2-standard-4 (4 vCPU / 16 GiB) |

화면에는 등급 선택 후 플랫폼별 실제 사양을 별도 영역에 나열하고(`specDetailHtml()`), 리뷰
화면에도 실제로 전송될 SKU를 그대로 노출한다(`renderReview()`) — 등급 라벨과 전송값이 어긋나지
않도록 한다.

#### Azure 무료 구독 후보와 "무료 대상"≠"이 구독·리전에서 생성 가능"

Azure 무료 체험 계정 공식 안내 기준 "경량" 등급 후보는 세 가지다:

- **B1s** (x86-64, 1 vCPU/1 GiB) — 기본값.
- **B2ats_v2** (x86-64, 2 vCPU/1 GiB) — 무료 대상 대안. 프론트에서 사용자가 **명시적으로**
  체크해야만 적용되고(`data-ps="useFreeAltSku"`), 자동으로 바꿔치기하지 않는다. 실제 전송되는
  SKU는 리뷰 화면에 그대로 보인다.
- **B2pts_v2** (**ARM64**, 2 vCPU/1 GiB) — 이번 작업 범위에서는 **선택지에서 제외**했다. 지금
  프로비저닝이 쓰는 이미지(Ubuntu 22.04/Windows Server 2022, `azure_provisioning.py`의
  `IMAGE_REFERENCES`)는 x86-64 전용이라 ARM64 이미지 호환성이 구현돼 있지 않다 — 이미지 호환
  작업이 끝나기 전까지는 절대 자동으로 고르면 안 된다.

**"무료 혜택 대상 SKU"라는 것과 "실제 이 구독·리전에서 지금 생성할 수 있음"은 별개다.** 무료
혜택은 신규 계정의 12개월/월별 사용 시간 한도 조건부일 뿐이고, 구독·리전 단위의 실제 SKU
가용성(용량 배정 여부)은 이와 무관하게 결정된다 — 실측 사례(2026-09-18, koreacentral/eastus의
B1s/B2s/D2s_v3)로 실제 거부가 확인됐다. 그래서:

- 프론트 안내 문구(`AZURE_FREE_TIER_NOTE`)가 "무료 혜택 대상"과 "생성 가능 여부는 별개"를 항상
  같이 명시한다. "표준"/"고성능" 등급은 무료 혜택 대상이 아니라는 점도 별도 안내(`AZURE_PAID_NOTE`)한다.
- `app/providers/azure.py`의 `list_vm_sku_availability()` + `GET
  /credentials/{credential_id}/vm-sku-availability`가 실제 생성 전에 그 구독·리전에서 SKU가
  `available`/`restricted`/`not_offered_in_region`인지 읽기 전용으로 미리 확인해준다. 조회
  실패는 "사용 가능"으로 간주하지 않으며, "available"이었다는 결과도 실시간 용량(capacity)까지
  보장하지 않는다 — 스냅샷일 뿐이고, 실제 생성 시 Azure가 다시 검증한다.

#### `SkuNotAvailable` vs 할당량(quota) 초과 — 원인이 다르므로 안내도 다르다

Azure VM 생성 실패의 두 원인을 혼동하면 사용자가 잘못된 조치를 하게 된다(`app/error_patterns.py`
참고):

- **`SkuNotAvailable` / `Capacity Restrictions`**: 그 SKU 자체가 이 구독·리전에 배정돼 있지
  않다는 뜻 — 할당량을 늘려도 해결되지 않는 경우가 흔하다. 안내: 다른 SKU·리전·가용 영역을
  선택하거나 Azure 지원에 SKU 사용을 요청하라.
- **`OperationNotAllowed` / `ResourceQuotaExceeded`(vCPU 할당량 초과)**: 리전 전체 또는 VM
  계열별 vCPU 수 자체가 부족하다는 뜻 — 할당량 증설 요청이 유효한 해결책일 수 있다. 다만 **무료
  체험(free trial) 구독은 보통 할당량 증설 신청 대상이 아니다** — 계속 쓰려면 종량제(pay-as-you-go)
  전환이 필요할 수 있다는 점을 같이 안내한다.

두 원인은 서로 다른 한글 문구로 번역되며(첫 매칭 우선순위 상 두 패턴 모두 기존 범용 quota/capacity
패턴보다 앞에 둔다), 비밀번호 복잡도 오류(`master password` 패턴)나 `text file busy`류 실행 환경
오류와도 섞이지 않도록 키워드를 좁게 잡았다. `SkuNotAvailable`의 다른 원인(예: 리전 자체 미제공)까지
전부 "할당량 문제"로 단정하지 않는다.

## 2. DB — RDBMS (RDS / Azure SQL Database / Cloud SQL)

| 필드 | AWS | Azure | GCP | 분류 |
|---|---|---|---|---|
| 이름 | 공통 | 공통 | 공통 | **공통 설정** |
| 엔진(매핑) | 공통 | 공통 | 공통 | **공통 설정** (MariaDB는 Azure에서 25년 9월 19일 이후 종료 예정) |
| 리전(매핑) | 공통 | 공통 | 공통 | **공통 설정** (Azure만 "Asia Pacific Korea Central"로 명칭 다름, 서울 리전 동일) |
| 인증(매핑) | 공통 | 공통 | 공통 | **공통 설정** (GCP는 비밀번호만 입력받는다는 안내 필요) |
| 백업(매핑) | 공통 | 공통 | 공통 | **공통 설정** (3사 모두 자동 지원) |
| 태그(매핑) | 공통 | 공통 | 공통 | **공통 설정** (GCP만 Labels로 명칭 다름) |
| 사양 | 제외 | 제외 | 제외 | **완전 제외**(서비스 사양 체계가 달라 전부 추가설정 대상이라는 비고가 있으나, 표 색상 자체는 회색 — 사용자 확인 시까지는 회색 기준을 따름) |
| 스토리지 | 제외 | 제외 | 제외 | **완전 제외**(타입 명칭이 달라 매핑 불가) |
| 네트워크 | 제외 | 제외 | 제외 | **완전 제외**(Azure가 URL 형식이라 구조상 매핑 불가) |
| 접근제어 | 제외 | 제외 | 제외 | **완전 제외**(기본값) |
| 가용성(매핑) | 제외 | 제외 | 제외 | **완전 제외**(기본값, 프리티어는 선택 불가) |

## 3. Storage — 오브젝트 스토리지 (S3 / Blob Storage / Cloud Storage → "Object Storage"로 통합 명명)

| 필드 | AWS | Azure | GCP | 분류 |
|---|---|---|---|---|
| 이름(서비스) | 공통 | 공통 | 공통 | **공통 설정** ("Object Storage로 통합") |
| 이름(애매, 버킷/계정명) | 공통 | 공통 | 공통 | **공통 설정** (전역 고유 버킷명 등 — 회사별 제약 상이 메모 있으나 색상은 공통) |
| 리전(매핑) | 공통 | 공통 | 공통 | **공통 설정** (Azure는 Central로 통합 권장) |
| 태그(매핑) | 공통 | 공통 | 공통 | **공통 설정** (Azure는 컨테이너 태그가 아니라 스토리지 계정 태그라 태그 검색 불가) |
| 스토리지 등급(N:M) | 무색 | 무색 | 공통 | **추가 설정** — GCP만 입력받음(GCP 추가 설정 메모와 일치), AWS/Azure는 미노출 |
| 접근 제어(애매) | 제외 | 제외 | 제외 | **완전 제외**(퍼블릭 차단이 실무 기본값) |
| 중복성(불가) | 제외 | 제외 | 제외 | **완전 제외**(3사 개념이 너무 달라 동일 설정으로 보기 어려움) |
| 버전관리(애매) | 제외 | 제외 | 제외 | **완전 제외**(Azure는 선택지 자체가 없어 기본값으로 확정) |

## 4. CDN — 공통/제외 구분 없음, 추가 설정만 (2026-09-10 확정, 2026-09-11 필드 범위 확정)

CDN은 3사 개념 자체가 안 맞아 "공통 설정"도 "완전 제외"도 없다 — **공통 설정 스텝 자체를 만들지 않고, 리소스 종류로 CDN을 고르면 곧바로 플랫폼별 추가 설정만 보여준다.** 1차 상세표에는 배경색 구분이 아예 없고(전부 무색), 팀 메모: "CDN은 3사 간 '1:1 공통'이 아니라 '유사 기능 + Adapter 변환'으로 분류해야 함 / 표는 기능 조사 및 개념 정리표 정도로 생각".

PDF 2번째 개념 정리표(배경색 없음, "분류" 열에 유사/공통/Azure전용/GCP전용/CSP차이 텍스트 레이블만 있음):

| 필드 | AWS(CloudFront) | Azure(Front Door) | GCP(LB+Cloud CDN) | 분류(PDF 원문) |
|---|---|---|---|---|
| Endpoint | Distribution domain | AFD Endpoint | Forwarding Rule/IP | 유사 |
| Origin | Origin | Origin | Backend/Backend Bucket | 유사 |
| Path routing | Cache Behavior | Route | URL Map | 유사 |
| Cache enable | Cache Policy/Behavior | Route cache config | enableCdn | 유사 |
| Cache policy mode | Cache Policy | Route/Rule Set | Cache Mode | 유사 |
| Compression | Cache Behavior/Policy | Route Compression | Backend Compression | 유사 |
| Viewer HTTPS | Viewer Protocol Policy | supported Protocols/httpRedirect | HTTPS Proxy/redirect LB | 유사 |
| Scope | Global | Global | Global | 공통 |
| Resource Group | x | 필수 Scope | x | Azure 전용(필수) |
| Price Class | o | x | x | AWS 전용 |
| Health Probe | Origin에 따라 다름 | Origin Group | Backend Service에 따라 필요 | 유사/조건부 |
| GCP LB stack | x | x | 필수 | GCP 전용(필수) |
| Azure SKU | x | Standard/Premium | x | Azure 전용(각주: Front Door는 Standard/Premium 기준으로 파라미터를 잡는다) |
| Raw status | Distribution status | 다중 구성요소 상태 | 다중 LB 구성요소 상태 | CSP 차이(조회 전용) |

### 구현용 필드 확정 (2026-09-11 갱신 — "Terraform으로 설정 가능한 항목은 전부 입력받는다" 기준)

원칙: **PDF 개념표의 각 행 중, 실제로 Terraform 리소스 인자로 설정 가능한 것은 전부 입력 필드로 넣는다.** 아래 세 개만 제외한다 — Terraform이 값을 "쓰는" 게 아니라 CSP가 생성 후 알려주는 결과값이거나(Endpoint, Raw status), 선택의 여지가 없는 고정값이기 때문(Scope — 3사 모두 Global 고정, 리전별 배포가 아님).

- **AWS(CloudFront, `aws_cloudfront_distribution`)**
  - Origin(text, 필수)
  - 캐시 정책(select: `CachingOptimized`(기본)/`CachingDisabled`/`CachingOptimizedForUncompressedObjects` — AWS 관리형 캐시 정책, PDF의 "Cache enable"+"Cache policy mode" 두 행이 실제로는 같은 Terraform 인자(`cache_policy_id`)라 하나로 합침)
  - Path routing(select: 기본 동작만 사용(기본)/정적 콘텐츠 캐시 우선 — `default_cache_behavior`/`ordered_cache_behavior` 단순화)
  - Compression(toggle, 기본 on — `default_cache_behavior.compress`)
  - Viewer Protocol Policy(select: Redirect to HTTPS(기본)/HTTPS Only/Allow All)
  - Price Class(select: 전체 리전/북미·유럽만/북미·유럽·아시아, AWS 전용)
  - Health Probe: 넣지 않음 — PDF에도 "Origin에 따라 다름"이라 되어 있고, CloudFront 자체에 독립된 헬스 체크 인자가 없다(오리진 서버 쪽 설정이라 이 서비스 범위 밖).
- **Azure(Front Door)**
  - Origin(text, 필수)
  - Resource Group(text, **필수**)
  - SKU(select: Standard/Premium, **필수**)
  - 쿼리스트링 캐시 처리(select: 전체 무시(기본)/전체 사용/지정 파라미터만 — `azurerm_cdn_frontdoor_route.cache.query_string_caching_behavior`, PDF의 "Cache enable"+"Cache policy mode")
  - Compression(toggle, 기본 on — `compression_enabled`)
  - 지원 프로토콜(select: HTTPS만(기본)/HTTP+HTTPS) + HTTPS 리다이렉트(toggle, 기본 on)
  - Health Probe(Origin Group 필수 설정 — 프로브 경로 text 기본 `/`, 프로브 간격 초 단위 number 기본 240)
- **GCP(LB + Cloud CDN)**
  - Backend/Backend Bucket(text, 필수)
  - GCP LB stack(select 또는 "로드밸런서 스택을 구성합니다" 안내+확인 체크박스, **필수**)
  - enableCdn(toggle, 기본 on)
  - Cache Mode(select: `CACHE_ALL_STATIC`(기본)/`USE_ORIGIN_HEADERS`/`FORCE_CACHE_ALL`)
  - Compression(select: `AUTOMATIC`(기본)/`DISABLED` — `compression_mode`)
  - Path routing: 기본 경로 매핑만 자동 생성(입력 없음, `google_compute_url_map` 기본값) — 커스텀 라우팅 규칙 편집기는 이번 범위 아님
  - HTTPS 강제 리다이렉트(toggle, 기본 on)
  - Health Probe(백엔드 서비스 기반 구성일 때만 노출 — 경로 text 기본 `/`, 간격 초 단위 number 기본 10. 백엔드 버킷만 쓰는 경우 이 필드 자체를 숨긴다)

## 5. 이번 정리의 용도

이 표는 "프론트엔드 프로비저닝 폼에 실제 값을 입력받는 기능 추가" 작업의 입력 스펙 근거다.

- **공통 설정 스텝**: Compute 7개(이름·리전·사양·네트워크·인바운드규칙·인증·태그) / DB 6개(이름·엔진·리전·인증·백업·태그) / Storage 4개(이름·버킷명·리전·태그).
- **플랫폼별 추가 설정 스텝**: Compute의 이미지(AWS/Azure만), Storage의 스토리지 등급(GCP만), 그리고 CDN 전체(4절 "구현용 필드 확정" 표 그대로 — 3사 완전히 다른 필드셋, 공통 스텝 없음, Terraform 설정 가능한 항목은 전부 입력받음).
- **완전 제외(미노출)**: Compute의 스토리지·권한, DB의 사양·스토리지·네트워크·접근제어·가용성, Storage의 접근제어·중복성·버전관리, CDN의 Endpoint/Scope/Raw status(계산/고정값).
