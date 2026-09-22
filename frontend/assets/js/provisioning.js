/* PROV-01 프로비저닝 설정값 실입력 (순수 프론트엔드 — 서버 통신 없음).
 *
 * 이 단계(단위 1)에서는 ③ 리소스 종류=Compute일 때 ④ 공통 설정 스텝의 입력 필드를
 * 선택된 플랫폼에 맞춰 동적으로 렌더링하고, 입력값을 전역 상태(provisioningSpec)에
 * 반영한다. DB/Storage(단위 2), ⑤ 추가 설정(단위 3), 생성하기 활성화 검증(단위 4)은
 * 후속 커밋에서 붙인다.
 *
 * 필드명은 향후 BE 연동 시 01_API_Specification_v1.1.md §10.3의 common_spec/provider_spec 구조로
 * 거의 그대로 전송할 수 있도록 잡았다(commonSpec: 3사 공통, providerSpec: 플랫폼별).
 */
(function () {
  "use strict";

  // ── 상수 ────────────────────────────────────────────────────────────────
  // 리전 제한(서비스 컨텍스트 9절). 플랫폼별로 허용 리전만 노출한다.
  var REGIONS = {
    aws: ["ap-northeast-2", "us-east-1"],
    azure: ["koreacentral", "eastus", "koreasouth", "canadacentral"],
    gcp: ["asia-northeast3", "us-central1"],
  };

  // 국가 하나를 고르면 각 플랫폼의 실제 리전 코드로 매핑한다(공통 설정은 플랫폼 무관).
  var COUNTRY_REGION = {
    "한국": { aws: "ap-northeast-2", azure: "koreacentral", gcp: "asia-northeast3" },
    "미국": { aws: "us-east-1", azure: "eastus", gcp: "us-central1" },
  };

  // 추상 사양 등급 → 플랫폼별 실제 SKU 매핑(코드 상수).
  //
  // 2026-09-18 정정: 예전엔 등급 라벨 자체에 "1 vCPU · 2GB"처럼 3사 공통 사양을 박아 넣었는데,
  // 실제로는 3사가 전혀 다른 사양이었다(예: Azure B1s는 1 vCPU/1GiB인데 AWS t3.micro는 2 vCPU/
  // 1GiB) — 하나의 라벨에 틀린 사양을 표시해 사용자에게 잘못된 정보를 주고 있었다. 그래서 등급
  // 이름(경량/표준/고성능)만 공통이고, 실제 vCPU·메모리는 플랫폼별 메타데이터로 따로 둔다.
  // 화면에는 등급 선택 후 `specDetailHtml()`이 플랫폼별 실제 사양을 별도 영역에 표시한다.
  //
  // 실제 사양(공식 스펙 기준 재검증):
  //   AWS t3.micro=2vCPU/1GiB, t3.medium=2vCPU/4GiB, t3.large=2vCPU/8GiB
  //   Azure B1s=1vCPU/1GiB, B2s=2vCPU/4GiB, B4ms=4vCPU/16GiB
  //   GCP e2-micro=2vCPU/1GiB, e2-medium=2vCPU/4GiB, e2-standard-4=4vCPU/16GiB
  var SPEC_TIERS = [
    {
      key: "light", label: "경량",
      sku: {
        aws: { name: "t3.micro", vcpu: 2, memGiB: 1 },
        azure: { name: "B1s", vcpu: 1, memGiB: 1, free: true },
        gcp: { name: "e2-micro", vcpu: 2, memGiB: 1 },
      },
    },
    {
      key: "standard", label: "표준",
      sku: {
        aws: { name: "t3.medium", vcpu: 2, memGiB: 4 },
        azure: { name: "B2s", vcpu: 2, memGiB: 4 },
        gcp: { name: "e2-medium", vcpu: 2, memGiB: 4 },
      },
    },
    {
      key: "high", label: "고성능",
      sku: {
        aws: { name: "t3.large", vcpu: 2, memGiB: 8 },
        azure: { name: "B4ms", vcpu: 4, memGiB: 16 },
        gcp: { name: "e2-standard-4", vcpu: 4, memGiB: 16 },
      },
    },
  ];

  // Azure 무료 체험 계정 공식 안내 기준 무료 후보(2026-09-18) — "경량" 등급 기본값(B1s)의 x86-64
  // 대안. B2pts_v2(ARM64)도 무료 후보로 안내되지만, ARM64 이미지 호환성을 이번 작업에서 구현하지
  // 않았으므로(기존 Ubuntu/Windows 이미지는 x86-64 전용) 선택지에서 제외한다 — 자동으로 골라주지
  // 않고, 이미지 호환 작업이 끝난 뒤에야 추가할 수 있다.
  var AZURE_LIGHT_FREE_ALT = { name: "B2ats_v2", vcpu: 2, memGiB: 1, free: true };

  // 무료/유료 안내 문구 — "무료 혜택 대상"과 "실제 그 구독·리전에서 생성 가능"은 별개라는 점을
  // 항상 같이 적는다(무료 혜택은 신규 계정의 12개월/월별 한도 조건부일 뿐, SKU 가용성을 보장하지
  // 않는다).
  var AZURE_FREE_TIER_NOTE = "Azure 무료 체험 계정의 12개월 무료 한도 대상 SKU입니다(월 사용 시간 " +
    "한도 있음). 무료 혜택 대상이라는 것과 실제 이 구독·리전에서 생성 가능한지는 별개입니다 — " +
    "생성 시 거부될 수 있습니다.";
  var AZURE_PAID_NOTE = "무료 혜택 대상이 아닙니다 — 비용이 청구되며, 구독의 vCPU 할당량이 " +
    "필요할 수 있습니다.";

  // 인바운드 규칙 프리셋(체크박스). 기본 CIDR은 전체 허용.
  // SSH(22)는 2026-09-15부로 뺐다 — 키 페어를 새로 발급하지 않고 SSM Session Manager(인스턴스에
  // 붙는 IAM 역할, terraform/aws/ec2/main.tf의 aws_iam_instance_profile.ssm)로 접속하는 방향으로
  // 바꿨기 때문에 22번 포트를 열어도 쓸 방법이 없다.
  var INBOUND_PRESETS = [
    { label: "HTTP (80)", port: 80 },
    { label: "HTTPS (443)", port: 443 },
  ];
  var DEFAULT_CIDR = "0.0.0.0/0";

  var PLATFORM_LABEL = { aws: "AWS", azure: "Azure", gcp: "GCP" };

  // 실 API 연동: (리소스 종류, 플랫폼)별로 백엔드 러너가 있는 조합만 실제 job을 만든다(§10).
  // 2026-09-14: azure(storage_account/sql_database)·gcp(cloud_storage/cloud_sql) 러너가 추가돼
  // db/storage_object도 3사 다 실 연동됐다. 2026-09-15: azure(cdn, Front Door Standard)에 이어
  // gcp(cloud_cdn, #51 안권형님 백엔드 러너 — 이 프론트 매핑만 누락돼 있었음)까지 연결해 CDN도
  // 3사 전부 실 연동 완료. CDN은 이제 시뮬레이션 대상이 없다.
  var SERVICE_CODE = {
    compute: { aws: "ec2", azure: "vm", gcp: "compute_engine" },
    storage_object: { aws: "s3", azure: "storage_account", gcp: "cloud_storage" },
    cdn: { aws: "cloudfront", azure: "cdn", gcp: "cloud_cdn" },
    db: { aws: "rds", azure: "sql_database", gcp: "cloud_sql" },
  };
  function hasRealRunner(kind, platform) {
    return !!(SERVICE_CODE[kind] && SERVICE_CODE[kind][platform]);
  }
  // 에러코드→문구 매핑은 백엔드 error_catalog가 단일 소스이며, 표시는 MCErr(error-explain.js)가
  // 담당한다. 진행 모달 실패 행은 job.error / api Error를 그대로 MCErr에 넘긴다.
  function escHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function newIdemKey() {
    return "prov-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);
  }

  // DB 엔진 옵션(플랫폼별로 다름). aws는 실제 백엔드 러너(RDS)가 mysql/postgres만 지원해서
  // 그 두 개만 노출한다. azure/gcp는 세 엔진(MySQL/PostgreSQL/SQL Server) 모두 실제 러너가
  // 지원한다(app/azure_database_provisioning.py·app/gcp_cloudsql_provisioning.py).
  var DB_ENGINES = {
    aws: ["MySQL", "PostgreSQL"],
    azure: ["MySQL", "PostgreSQL", "SQL Server"],
    gcp: ["MySQL", "PostgreSQL", "SQL Server"],
  };
  // aws 실 API용 engine 값 매핑(표시 라벨 → provider_spec.engine).
  var DB_ENGINE_CODE = { MySQL: "mysql", PostgreSQL: "postgres" };
  // azure CDN(Front Door) 실 API용 값 매핑(한글 표시 라벨 → app/azure_cdn_provisioning.py가
  // 받는 코드). "지정 파라미터만"은 특정 파라미터를 입력받는 필드가 화면에 없어 IgnoreSpecifiedQueryStrings
  // (빈 목록 취급)로 정규화한다.
  var AZURE_CDN_QUERY_STRING_CODE = {
    "전체 무시": "IgnoreQueryString",
    "전체 사용": "UseQueryString",
    "지정 파라미터만": "IgnoreSpecifiedQueryStrings",
  };
  var AZURE_CDN_PROTOCOL_CODE = { "HTTPS만": "https_only", "HTTP+HTTPS": "http_and_https" };
  var WARN_STYLE = 'style="color:#b45309"'; // amber-700, 경고 문구용

  // ⑤ 추가 설정 옵션.
  var AMI_CUSTOM = "직접 AMI ID 입력";
  var COMPUTE_IMAGES = {
    aws: ["Amazon Linux 2023", "Ubuntu 22.04", AMI_CUSTOM],
    azure: ["Ubuntu 22.04", "Windows Server 2022"],
    // GCP도 3사 동일 옵션 요구에 맞춰 큐레이티드 이미지 2종을 노출한다(2026-09-21,
    // app/gcp_provisioning.py의 IMAGE_FAMILIES와 값이 같아야 함). 기본값(Debian 12)을 먼저 둔다.
    gcp: ["Debian 12", "Ubuntu 22.04"],
  };
  var STORAGE_CLASSES = ["Standard", "Nearline", "Coldline", "Archive"]; // GCP 전용

  // 맵핑 문서 "제외" 필드 — 폼에 렌더링하지 않고 providerSpec에 서버 기본값만 채운다
  // (사용자 입력 없음). 값은 데모 기본값이며 실제 정책은 BE 연동 시 확정한다.
  var SERVER_DEFAULTS = {
    compute: { rootVolumeGb: 30, iamRole: "default" }, // 스토리지·권한
    db: { instanceClass: "db-standard", storageGb: 20, network: "auto", publicAccess: false, multiAz: false }, // 사양·스토리지·네트워크·접근제어·가용성
    storage_object: { publicAccess: false, redundancy: "LRS", versioning: false }, // 접근제어·중복성·버전관리
  };

  // CDN 입력 옵션(맵핑 문서 4절, 2026-09-11). 3사 필드셋이 완전히 다르고 공통 스텝이 없다.
  // Terraform으로 실제 설정 가능한 값은 전부 입력받고, 결과값(Endpoint)·고정값(Scope=Global)·
  // 조회전용(Raw status)만 제외한다.
  var CDN_OPTS = {
    awsCachePolicy: ["CachingOptimized", "CachingDisabled", "CachingOptimizedForUncompressedObjects"],
    awsPathRouting: ["기본 동작만 사용", "정적 콘텐츠 캐시 우선"],
    awsViewerProtocol: ["Redirect to HTTPS", "HTTPS Only", "Allow All"],
    awsPriceClass: ["전체 리전", "북미·유럽만", "북미·유럽·아시아"],
    // Premium은 월 기본료가 Standard($35)의 약 10배($330, Microsoft Learn 가격 비교)라 이 프로젝트가
    // 쓰지 않는 WAF/Private Link 오리진 때문에 실수로 고르면 순수 손해다 — 2026-09-15 결정으로
    // Standard만 선택 가능하게 뺐다(app/azure_cdn_provisioning.py도 동일하게 서버에서 거부).
    azSku: ["Standard"],
    azQueryString: ["전체 무시", "전체 사용", "지정 파라미터만"],
    azProtocols: ["HTTPS만", "HTTP+HTTPS"],
    gcpCacheMode: ["CACHE_ALL_STATIC", "USE_ORIGIN_HEADERS", "FORCE_CACHE_ALL"],
    gcpCompression: ["AUTOMATIC", "DISABLED"],
  };

  // ── 전역 상태 ────────────────────────────────────────────────────────────
  var state = {
    resourceKind: null, // 'compute' | 'db' | 'storage_object' | 'cdn'
    platforms: [], // ['aws','azure']
    selectedAccounts: {}, // { aws: 'label', ... } — 검토/검증 표시용(플랫폼당 대표 1개)
    selectedCredentials: [], // [{ provider, credentialId, label }] — 실제 job 생성 단위
    commonSpec: {}, // 공통 설정 필드
    providerSpec: { aws: {}, azure: {}, gcp: {} }, // 플랫폼별 추가/구분 필드
  };
  window.provisioningSpec = state;

  // ── DOM 헬퍼 ─────────────────────────────────────────────────────────────
  function el(tag, attrs, html) {
    var node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (html != null) node.innerHTML = html;
    return node;
  }
  var FIELD_INPUT =
    "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary";
  function labelHtml(text, required) {
    return '<label class="mb-1 block text-sm font-medium">' + text +
      (required ? ' <span class="text-primary">*</span>' : "") + "</label>";
  }

  // 선택된 등급의 플랫폼별 실제 사양(SKU·vCPU·메모리)을 나열한다 — 공통 라벨에 사양을 안 넣는
  // 대신(SPEC_TIERS 주석 참고) 여기서 플랫폼마다 다른 실제 값을 각각 보여준다. 여러 플랫폼을
  // 동시에 선택했으면 전부 나열한다. Azure는 등급별 무료/유료 안내 문구도 같이 붙인다(실제
  // 전송될 SKU는 useFreeAltSku 체크 여부에 따라 달라질 수 있음 — computeAuthEl 참고).
  function specDetailHtml(tierKey, platforms) {
    var tier = SPEC_TIERS.filter(function (t) { return t.key === tierKey; })[0];
    if (!tier || !platforms || !platforms.length) return "";
    var rows = platforms.map(function (p) {
      var sku = tier.sku[p];
      if (!sku) return "";
      var freeBadge = sku.free
        ? ' <span class="rounded border border-primary px-1 text-[11px] text-primary">무료 대상</span>' : "";
      return '<div class="flex items-center justify-between gap-2 rounded-lg border border-border bg-background px-3 py-1.5 text-sm">' +
        "<span>" + escHtml(PLATFORM_LABEL[p]) + "</span>" +
        '<span class="text-muted-foreground">' + escHtml(sku.name) + " · " + sku.vcpu + " vCPU · " +
        sku.memGiB + " GiB" + freeBadge + "</span></div>";
    }).join("");
    var azureNote = "";
    if (platforms.indexOf("azure") >= 0) {
      azureNote = '<p class="mt-1.5 text-xs" ' + WARN_STYLE + ">" +
        (tierKey === "light" ? AZURE_FREE_TIER_NOTE : AZURE_PAID_NOTE) + "</p>";
    }
    return '<div class="space-y-1.5">' + rows + "</div>" + azureNote;
  }

  // #prov-common-fields의 [data-spec-detail]을 현재 state 기준으로 다시 그린다. collect()가
  // state.commonSpec.specTier/state.platforms를 갱신할 때마다 호출해 계속 최신으로 맞춘다.
  function refreshSpecDetail() {
    var box = document.querySelector("#prov-common-fields [data-spec-detail]");
    if (!box) return;
    box.innerHTML = specDetailHtml((state.commonSpec || {}).specTier, state.platforms);
  }
  // 이름 필드 정책(리소스 종류 × 플랫폼). 2026-09-15 백엔드 조사 결과, compute(aws/gcp)·db(aws/
  // azure/gcp)·storage_object(aws)는 전부 같은 정규식 `^[a-z][a-z0-9-]{0,38}[a-z0-9]$`를 쓴다
  // (app/aws_provisioning.py, app/aws_rds_provisioning.py, app/aws_s3_provisioning.py,
  // app/aws_cloudfront_provisioning.py, app/gcp_provisioning.py, app/gcp_cloudsql_provisioning.py,
  // app/gcp_cdn_provisioning.py, app/azure_database_provisioning.py). Azure Storage Account
  // (app/azure_storage_provisioning.py, 하이픈 불가·2~21자)와 GCP Cloud Storage
  // (app/gcp_storage_provisioning.py, 점·밑줄 허용·3~63자)만 다르다. Azure VM(app/azure_provisioning.py)은
  // 백엔드에 이름 검증이 아예 없지만, 이름 입력창이 선택된 플랫폼 전체가 공유하는 필드라
  // AWS/GCP를 함께 선택하면 어차피 그쪽 규칙에 맞춰야 하므로 같은 정책을 적용하고, Azure 공식
  // 문서 기준으로도 안전한 문자만 남긴다.
  var NAME_POLICY = {
    compute: {
      aws: { chars: "a-z0-9-", startLetter: true, max: 40,
        note: "소문자로 시작 · 소문자/숫자/하이픈(-)만 · 끝은 소문자나 숫자 · 최대 40자",
        doc: { label: "AWS 리소스 명명 규칙", url: "https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html" } },
      azure: { chars: "a-z0-9-", startLetter: true, max: 40,
        note: "영소문자/숫자/하이픈(-)만 · 하이픈으로 시작·종료 불가 · 최대 64자",
        doc: { label: "Azure 리소스 명명 규칙", url: "https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-name-rules" } },
      gcp: { chars: "a-z0-9-", startLetter: true, max: 40,
        note: "소문자로 시작 · 소문자/숫자/하이픈(-)만 · 끝은 소문자나 숫자 · 최대 63자",
        doc: { label: "GCP 리소스 명명 규칙(RFC1035)", url: "https://docs.cloud.google.com/compute/docs/naming-resources" } },
    },
    db: {
      aws: { chars: "a-z0-9-", startLetter: true, max: 40,
        note: "문자로 시작 · 소문자/숫자/하이픈만 · 하이픈 연속·종료 불가 · 최대 63자",
        doc: { label: "AWS RDS 식별자 규칙", url: "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_Limits.html" } },
      azure: { chars: "a-z0-9-", startLetter: true, max: 40,
        note: "영소문자/숫자/하이픈만 · 하이픈으로 시작·종료 불가 · 최대 63자",
        doc: { label: "Azure Database 서버 명명 규칙", url: "https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-name-rules" } },
      gcp: { chars: "a-z0-9-", startLetter: true, max: 40,
        note: "문자로 시작 · 소문자/숫자/하이픈만 · 프로젝트ID 포함 최대 98자",
        doc: { label: "GCP Cloud SQL 인스턴스 ID 규칙", url: "https://docs.cloud.google.com/sql/docs/mysql/instance-settings" } },
    },
    storage_object: {
      // 2026-09-18: 실사용 중 "영어+숫자만 써도 생성이 안 된다"는 신고로 조사 — AWS/Azure는
      // mcp- 접두사+job_id 접미사를 자동으로 붙여 전역 유일성을 대신 보장해 주는데(백엔드
      // aws_s3_provisioning.py/azure_storage_provisioning.py), GCP만 사용자가 입력한 이름을
      // 그대로 써서 흔한 이름이 이미 다른 프로젝트가 선점했을 확률이 높았다. GCP도 같은
      // 접두사/접미사 패턴으로 통일(gcp_storage_provisioning.py)했고, 여기 max도 원래 GCS
      // 자체 한도(63자)에서 그 여유분(약 15자)을 뺀 40으로 맞췄다(AWS와 동일한 값).
      // startLetter: true — app/aws_s3_provisioning.py의 _NAME_RE(`^[a-z]...`)가 실제로 첫
      // 글자를 소문자로만 강제한다(숫자 시작 거부, 2026-09-18 실제 API 호출로 확인) — 여기를
      // false로 뒀던 게 "숫자로 시작하는 이름은 프론트는 통과하는데 AWS만 422로 막히는" 불일치의
      // 원인이었다.
      aws: { chars: "a-z0-9-", startLetter: true, max: 40,
        note: "소문자로 시작 · 소문자/숫자/하이픈(-)만 · 원래 3~63자 규칙에 접두사(mcp)+job 번호 여유를 둬 최대 40자까지 입력 가능",
        doc: { label: "AWS S3 버킷 명명 규칙", url: "https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html" } },
      azure: { chars: "a-z0-9", startLetter: false, max: 21,
        note: "하이픈 없이 영소문자/숫자만 · 원래 3~24자 규칙에 접두사(mcp) 여유를 둬 최대 21자까지 입력 가능",
        doc: { label: "Azure Storage 계정 명명 규칙", url: "https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-name-rules" } },
      gcp: { chars: "a-z0-9._-", startLetter: false, max: 40,
        note: "소문자/숫자/하이픈(-)/밑줄(_)/점(.)만 · 원래 3~63자 규칙에 접두사(mcp)+job 번호 여유를 둬 최대 40자까지 입력 가능",
        doc: { label: "GCP Cloud Storage 버킷 명명 규칙", url: "https://docs.cloud.google.com/storage/docs/buckets" } },
    },
  };
  // 현재 선택된 플랫폼 각각의 정책(리소스 종류 기준). 아직 플랫폼을 못 골랐으면 3사 전체로 본다.
  function activeNamePolicies(kind) {
    var byPlatform = NAME_POLICY[kind];
    if (!byPlatform) return [];
    var platforms = state.platforms.length ? state.platforms : Object.keys(byPlatform);
    var out = [];
    platforms.forEach(function (p) {
      if (byPlatform[p]) out.push({ platform: p, policy: byPlatform[p] });
    });
    return out;
  }
  // 여러 플랫폼을 동시에 선택하면 이름 입력 하나를 공유하므로, 허용 문자는 선택된 플랫폼
  // 전부가 허용하는 문자만(교집합), 길이·시작문자 제약은 어느 한쪽이라도 요구하면 적용한다
  // (그래야 어느 플랫폼에서도 422/apply 실패가 안 난다).
  function effectiveNameConstraint(kind) {
    var active = activeNamePolicies(kind);
    if (!active.length) return { charsetRe: /[^a-z0-9-]/g, max: 40, startLetter: true };
    var maxLen = Math.min.apply(null, active.map(function (a) { return a.policy.max; }));
    var startLetter = active.some(function (a) { return a.policy.startLetter; });
    // policy.chars(예: "a-z0-9-")는 정규식 문자 클래스 표기이므로 리터럴 substring 검사(indexOf)로
    // 멤버십을 확인하면 안 된다 — 'a'/'z'/'0'/'9'/'-'만 문자열에 그대로 포함돼 있어 나머지
    // a-z/0-9 범위 전체가 "허용 안 됨"으로 오판된다(2026-09-15 실사용 중 발견: b~y, 1~8 입력 불가).
    // 각 정책의 chars를 실제 정규식 문자 클래스로 컴파일해 멤버십을 판정한다.
    var charRes = active.map(function (a) { return new RegExp("^[" + a.policy.chars + "]$"); });
    var ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-_.";
    var allowed = "";
    for (var i = 0; i < ALPHABET.length; i++) {
      var c = ALPHABET[i];
      var okEverywhere = charRes.every(function (re) { return re.test(c); });
      if (okEverywhere) allowed += c;
    }
    var escaped = allowed.replace(/[-\]\\^]/g, "\\$&");
    return { charsetRe: new RegExp("[^" + escaped + "]", "g"), max: maxLen, startLetter: startLetter };
  }
  // 선택된 플랫폼별 규칙 + 공식 문서 링크를 입력창 아래 안내 문구로 렌더링한다.
  function namePolicyHintHtml(kind) {
    var active = activeNamePolicies(kind);
    if (!active.length) return "";
    var items = active.map(function (a) {
      return "<li>" + escHtml(PLATFORM_LABEL[a.platform]) + ": " + escHtml(a.policy.note) + " — " +
        '<a href="' + a.policy.doc.url + '" target="_blank" rel="noopener" class="underline">' +
        escHtml(a.policy.doc.label) + "</a></li>";
    }).join("");
    return '<div class="mt-1 text-xs text-muted-foreground">' +
      "<p>ⓘ 선택한 플랫폼 기준 이름 규칙 — 아래 문자·형식을 지켜 주세요(공식 문서 기준):</p>" +
      '<ul class="ml-4 list-disc space-y-0.5">' + items + "</ul></div>";
  }
  // data-cs="name" 입력값의 유효성 판정. 입력 자체는 막지 않고(키를 눌러도 그대로 보인다),
  // 규칙(effectiveNameConstraint)은 그대로 재사용하되 "검사 시점"만 blur/Enter로 옮긴다
  // (2026-09-16: 실시간 preventDefault 차단은 왜 안 눌리는지 알 수 없어 혼란스럽다는 피드백).
  // 반환값: 유효하면 null, 아니면 사람이 읽을 수 있는 사유 문자열.
  function nameError(kind, value) {
    var v = value == null ? "" : String(value);
    if (v === "") return null; // 빈 값은 '필수 미입력'으로 각 종류의 presence 검사가 처리
    var c = effectiveNameConstraint(kind);
    var bad = v.match(c.charsetRe); // 허용 안 되는 문자들
    if (bad) {
      var uniq = bad.filter(function (ch, i) { return bad.indexOf(ch) === i; });
      var shown = uniq.map(function (ch) {
        if (ch === " ") return "공백";
        if (/[A-Z]/.test(ch)) return "'" + ch + "'(대문자)";
        return "'" + ch + "'";
      });
      return "사용할 수 없는 문자가 있어요: " + shown.join(", ") + ". 아래 이름 규칙에 맞는 문자만 써 주세요.";
    }
    if (c.startLetter && !/^[a-z]/.test(v)) return "이름은 영소문자로 시작해야 해요.";
    if (v.length > c.max) return "이름이 너무 길어요(최대 " + c.max + "자).";
    return null;
  }
  // 이름 필드의 테두리 상자(prefix가 붙는 종류는 감싸는 div, storage는 input 자신)와 안내 <p>를 찾는다.
  function nameFieldParts(input) {
    var box = input.closest("[data-name-box]") || input;
    var errEl = box.parentElement ? box.parentElement.querySelector("[data-name-error]") : null;
    return { box: box, errEl: errEl };
  }
  function showNameError(input) {
    var err = nameError(state.resourceKind, input.value);
    if (!err) { clearNameError(input); return; }
    var parts = nameFieldParts(input);
    parts.box.style.borderColor = "#dc2626"; // 인라인 스타일이 border 클래스를 덮어써 안정적으로 빨간 테두리
    if (parts.errEl) { parts.errEl.textContent = err; parts.errEl.classList.remove("hidden"); }
  }
  function clearNameError(input) {
    var parts = nameFieldParts(input);
    parts.box.style.borderColor = ""; // 인라인 해제 → 원래 클래스(포커스 시 border-primary 등) 복귀
    if (parts.errEl) { parts.errEl.classList.add("hidden"); parts.errEl.textContent = ""; }
  }
  // 현재 이름 입력값이 규칙에 어긋나는지(제출 게이팅용) — 입력 차단을 없앤 대신 여기서 막는다.
  function currentNameInvalid(kind) {
    var input = (document.getElementById("prov-common-fields") || document).querySelector('[data-cs="name"]');
    return !!(input && nameError(kind, input.value));
  }

  // ── 선택 상태 읽기 ────────────────────────────────────────────────────────
  function readPlatforms() {
    var out = [];
    document.querySelectorAll("[data-prov-platform]").forEach(function (cb) {
      if (cb.checked) out.push(cb.getAttribute("data-prov-platform"));
    });
    return out;
  }
  function readSelectedAccounts() {
    var acc = {};
    var creds = [];
    document.querySelectorAll("#prov-account-list [data-prov-account]").forEach(function (cb) {
      if (!cb.checked) return;
      var p = cb.getAttribute("data-prov-account");
      var label = cb.value || cb.getAttribute("data-prov-account-label") || p;
      acc[p] = label; // 플랫폼당 대표 1개(검토/검증 표시용)
      creds.push({ provider: p, credentialId: cb.getAttribute("data-credential-id"), label: label });
    });
    state.selectedCredentials = creds;
    return acc;
  }

  // ── 실 계정/자격 증명 로딩 + ② 계정 칩 렌더링 ─────────────────────────────
  // 마이페이지에 등록된 실제 cloud-account + credential을 불러와 ② 계정 스텝을 채운다.
  var allCredentials = []; // [{ id, provider, label, verified }]

  function loadAccountCredentials() {
    if (!window.MCPApi) return Promise.resolve();
    return MCPApi.request("/cloud-accounts")
      .then(function (data) {
        var accounts = (data && data.items) || [];
        return Promise.all(accounts.map(function (a) {
          return MCPApi.request("/cloud-accounts/" + a.id + "/credentials").then(function (cd) {
            return ((cd && cd.items) || []).map(function (c) {
              return {
                id: String(c.id),
                provider: a.provider,
                label: (a.account_label || a.external_account_id) + " · " + c.name + (c.verified ? "" : " (미검증)"),
                verified: !!c.verified,
              };
            });
          }).catch(function () { return []; });
        }));
      })
      .then(function (groups) {
        allCredentials = [];
        groups.forEach(function (g) { allCredentials = allCredentials.concat(g); });
      })
      .catch(function () { allCredentials = []; });
  }

  function renderAccounts() {
    var list = document.getElementById("prov-account-list");
    if (!list) return;
    var platforms = readPlatforms();
    if (!platforms.length) {
      list.innerHTML = '<span class="text-xs text-muted-foreground">먼저 ① 플랫폼을 선택하세요.</span>';
      return;
    }
    var creds = allCredentials.filter(function (c) { return platforms.indexOf(c.provider) >= 0; });
    if (!creds.length) {
      list.innerHTML = '<span class="text-xs text-muted-foreground">선택한 플랫폼에 등록된 자격 증명이 없습니다 — ' +
        '<a href="mypage.html" class="text-primary underline">마이페이지</a>에서 먼저 등록하세요.</span>';
      return;
    }
    list.innerHTML = creds.map(function (c) {
      return '<label data-select-chip class="flex cursor-pointer items-center gap-2 rounded-lg border border-border bg-muted text-muted-foreground px-3 py-2 text-sm">' +
        '<input type="checkbox" data-prov-account="' + c.provider + '" data-credential-id="' + c.id + '" value="' + escHtml(c.label) + '" /> ' +
        escHtml(PLATFORM_LABEL[c.provider] + " · " + c.label) + "</label>";
    }).join("");
  }
  function readResourceKind() {
    var checked = document.querySelector("[data-prov-kind]:checked");
    return checked ? checked.getAttribute("data-prov-kind") : null;
  }

  // ── Compute 공통 설정 렌더링 ──────────────────────────────────────────────
  function renderComputeCommon(container, platforms) {
    container.innerHTML = "";

    // 1) 이름 (prefix mcp- 고정)
    var nameField = el("div", { class: "sm:col-span-2" });
    nameField.innerHTML = labelHtml("이름", true) +
      '<div data-name-box class="flex items-stretch rounded-lg border border-border bg-background focus-within:border-primary">' +
      '<span class="flex items-center px-3 text-sm text-muted-foreground">mcp-</span>' +
      '<input type="text" data-cs="name" data-prefix="mcp-" placeholder="web-01" class="w-full rounded-r-lg bg-transparent px-2 py-2 text-sm outline-none" />' +
      "</div>" +
      '<p data-name-error class="mt-1 hidden text-xs" style="color:#dc2626"></p>' +
      namePolicyHintHtml("compute");
    container.appendChild(nameField);

    // 3) 사양(추상 등급) — providerSpec의 실제 SKU는 collect 시 매핑. 등급 이름만 공통이고
    // 실제 vCPU·메모리는 3사가 다르므로(SPEC_TIERS 주석 참고) 바로 아래 data-spec-detail에
    // 플랫폼별 실제 사양을 별도로 표시한다(공통 라벨에 사양 수치를 넣지 않음).
    var specField = el("div", { class: "sm:col-span-2" });
    var specOpts = '<option value="">선택하세요</option>' + SPEC_TIERS.map(function (t) {
      return '<option value="' + t.key + '">' + t.label + "</option>";
    }).join("");
    specField.innerHTML = labelHtml("사양", true) +
      '<select data-cs="specTier" class="' + FIELD_INPUT + '">' + specOpts + "</select>" +
      '<div data-spec-detail class="mt-2"></div>';
    container.appendChild(specField);

    // 2) 리전 — 국가 단일 선택(플랫폼 무관, collect에서 각 플랫폼 리전으로 매핑)
    container.appendChild(countryFieldEl());

    // 4) 네트워크 — "새로 생성"(기본) / "기존 리소스 사용" 토글(2026-09-17, 실제로 동작)
    container.appendChild(networkFieldEl("compute", platforms));
    wireAzureRegionRefilter(container, "compute", platforms);

    // 인바운드 규칙·인증은 플랫폼별로 내용이 달라 ⑤ 추가 설정으로 이동(공통은 플랫폼 무관 유지).

    // 태그 — key-value 반복 입력 (선택)
    var tagField = el("div", { class: "sm:col-span-2" });
    tagField.innerHTML = labelHtml("태그") +
      '<div data-tag-rows class="space-y-2"></div>' +
      '<button type="button" data-tag-add class="mt-2 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted">+ 태그 추가</button>';
    container.appendChild(tagField);

    wireDynamicRows(container);
    collect();
  }

  // ── 공용 필드 빌더 ────────────────────────────────────────────────────────
  function nameFieldEl(placeholder, kind) {
    var f = el("div", { class: "sm:col-span-2" });
    f.innerHTML = labelHtml("이름", true) +
      '<div data-name-box class="flex items-stretch rounded-lg border border-border bg-background focus-within:border-primary">' +
      '<span class="flex items-center px-3 text-sm text-muted-foreground">mcp-</span>' +
      '<input type="text" data-cs="name" data-prefix="mcp-" placeholder="' + (placeholder || "") +
      '" class="w-full rounded-r-lg bg-transparent px-2 py-2 text-sm outline-none" /></div>' +
      '<p data-name-error class="mt-1 hidden text-xs" style="color:#dc2626"></p>' + namePolicyHintHtml(kind);
    return f;
  }
  // 국가 단일 select(공통). 선택 국가는 collect()에서 각 플랫폼 리전으로 매핑된다.
  function countryFieldEl() {
    var f = el("div");
    var opts = '<option value="">선택하세요</option>' +
      Object.keys(COUNTRY_REGION).map(function (c) { return "<option>" + c + "</option>"; }).join("");
    f.innerHTML = labelHtml("리전(국가)", true) +
      '<select data-cs="country" class="' + FIELD_INPUT + '">' + opts + "</select>" +
      '<p class="mt-1 text-xs text-muted-foreground">국가를 고르면 각 플랫폼에 맞는 리전이 자동 설정됩니다.</p>';
    return f;
  }
  function appendTagField(container, warning) {
    var f = el("div", { class: "sm:col-span-2" });
    f.innerHTML = labelHtml("태그") +
      (warning ? '<p class="mb-1 text-xs" ' + WARN_STYLE + ">" + warning + "</p>" : "") +
      '<div data-tag-rows class="space-y-2"></div>' +
      '<button type="button" data-tag-add class="mt-2 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted">+ 태그 추가</button>';
    container.appendChild(f);
  }

  // Compute 인바운드 규칙(⑤로 이동) — 프리셋 체크박스 + 커스텀 행 추가/삭제.
  // 기존 리소스(VPC/서브넷/보안그룹 등) 재사용 입력 필드 — provider마다 값 형식이 달라 provider별로
  // 다른 필드셋을 쓴다(2026-09-17 결정). 전부 선택 입력이라 비워두면 서버가 지금까지처럼 새로
  // 만든다. DB는 서브넷/NSG 재사용을 안 받는다(위임·private endpoint 정책 강제 변경 위험 —
  // 서버 쪽 terraform 모듈 주석 참고) — 리소스 그룹/VNet까지만.
  var EXISTING_RESOURCE_FIELDS = {
    compute: {
      aws: [
        ["vpcId", "VPC ID", "vpc-xxxxxxxxxxxxxxxxx"],
        ["subnetId", "서브넷 ID", "subnet-xxxxxxxxxxxxxxxxx"],
        ["securityGroupId", "보안 그룹 ID", "sg-xxxxxxxxxxxxxxxxx"],
      ],
      azure: [
        ["existingResourceGroupName", "리소스 그룹 이름", "my-existing-rg"],
        ["existingSubnetId", "서브넷 리소스 ID", "/subscriptions/.../subnets/..."],
        ["existingNetworkSecurityGroupId", "NSG 리소스 ID", "/subscriptions/.../networkSecurityGroups/..."],
      ],
      gcp: [["network", "VPC 네트워크 이름", "my-existing-vpc"]],
    },
    db: {
      aws: [
        ["vpcId", "VPC ID", "vpc-xxxxxxxxxxxxxxxxx"],
        ["securityGroupId", "보안 그룹 ID", "sg-xxxxxxxxxxxxxxxxx"],
      ],
      azure: [
        ["existingResourceGroupName", "리소스 그룹 이름", "my-existing-rg"],
        ["existingVnetId", "VNet 리소스 ID", "/subscriptions/.../virtualNetworks/..."],
      ],
      gcp: [["network", "VPC 네트워크 이름", "my-existing-vpc"]],
    },
    // Storage는 Azure만 "기존 리소스 그룹" 재사용 개념이 있다(AWS/GCP 버킷은 전역 고유 이름이라
    // 재사용할 상위 리소스가 없음). 2026-09-18까지는 수동 텍스트 입력뿐이었는데, compute/db와
    // 다르게 취급할 이유가 없어 같은 "실제 목록 불러오기" 드롭다운으로 통일한다.
    storage_object: {
      azure: [["existingResourceGroupName", "리소스 그룹 이름", "my-existing-rg"]],
    },
    // GCP CDN "기존 버킷 연결"(2026-09-22) — 자유 텍스트 입력이라 사용자가 버킷 이름을 직접
    // 타이핑해야 했던 것을, compute/db/storage_object와 같은 "실제 목록 불러오기" 드롭다운으로 통일.
    cdn: {
      gcp: [["backendBucketName", "연결할 기존 버킷 이름", "my-existing-bucket"]],
    },
  };

  // provider별로 "불러오기"가 채울 select의 원본 데이터(마지막으로 불러온 값) — 재조회 없이
  // 필터링(예: AWS VPC 선택 시 서브넷/보안그룹 목록 좁히기)에 재사용한다.
  var fetchedNetworkData = {};

  // kind+platform별 필드 -> 실제 목록 데이터 매핑. `source`는 응답의 어느 배열을 쓸지,
  // `parent`는 그 배열을 필터링할 상위 필드 key(선택), `optionOf`는 {value, text} 변환.
  var EXISTING_FIELD_SOURCES = {
    compute: {
      aws: {
        vpcId: { source: "vpcs", optionOf: function (v) {
          return { value: v.id, text: v.id + (v.name ? " - " + v.name : "") + " (" + v.cidr_block + ")" + (v.is_default ? " [기본]" : "") };
        } },
        subnetId: { source: "subnets", parent: "vpcId", parentKey: "vpc_id", optionOf: function (s) {
          return { value: s.id, text: s.id + " (" + s.availability_zone + ", " + s.cidr_block + ")" + (s.name ? " - " + s.name : "") };
        } },
        securityGroupId: { source: "security_groups", parent: "vpcId", parentKey: "vpc_id", optionOf: function (g) {
          return { value: g.id, text: g.id + (g.name ? " - " + g.name : "") };
        } },
      },
      azure: {
        existingResourceGroupName: { source: "resource_groups", optionOf: function (r) {
          return { value: r.name, text: r.name + " (" + r.location + ")" };
        } },
        existingSubnetId: { source: "azure_subnets", regionFilter: true, optionOf: function (s) {
          return { value: s.id, text: s.vnet_name + "/" + s.name + " (" + (s.address_prefix || "") + ") · rg:" + s.resource_group + (s.location ? " · " + s.location : "") };
        } },
        existingNetworkSecurityGroupId: { source: "network_security_groups", regionFilter: true, optionOf: function (n) {
          return { value: n.id, text: n.name + " (rg:" + n.resource_group + ", " + n.location + ")" };
        } },
      },
      gcp: {
        network: { source: "networks", optionOf: function (n) {
          return { value: n.name, text: n.name + (n.auto_create_subnetworks ? " [자동 서브넷]" : "") };
        } },
      },
    },
    db: {
      aws: {
        vpcId: { source: "vpcs", optionOf: function (v) {
          return { value: v.id, text: v.id + (v.name ? " - " + v.name : "") + " (" + v.cidr_block + ")" + (v.is_default ? " [기본]" : "") };
        } },
        securityGroupId: { source: "security_groups", parent: "vpcId", parentKey: "vpc_id", optionOf: function (g) {
          return { value: g.id, text: g.id + (g.name ? " - " + g.name : "") };
        } },
      },
      azure: {
        existingResourceGroupName: { source: "resource_groups", optionOf: function (r) {
          return { value: r.name, text: r.name + " (" + r.location + ")" };
        } },
        existingVnetId: { source: "virtual_networks", regionFilter: true, optionOf: function (v) {
          return { value: v.id, text: v.name + " (rg:" + v.resource_group + ", " + v.location + ")" };
        } },
      },
      gcp: {
        network: { source: "networks", optionOf: function (n) {
          return { value: n.name, text: n.name + (n.auto_create_subnetworks ? " [자동 서브넷]" : "") };
        } },
      },
    },
    storage_object: {
      azure: {
        // 리소스 그룹 자신의 location이 실제 적용 리전이 되므로(azureEffectiveRegion과 동일 원칙)
        // regionFilter는 필요 없다 — 리스트 자체를 거를 대상이 없다.
        existingResourceGroupName: { source: "resource_groups", optionOf: function (r) {
          return { value: r.name, text: r.name + " (" + r.location + ")" };
        } },
      },
    },
    cdn: {
      gcp: {
        backendBucketName: { source: "buckets", optionOf: function (b) {
          return { value: b.name, text: b.name + " (" + b.location + ")" };
        } },
      },
    },
  };

  // Azure만 겪는 문제(2026-09-18): list_network_resources는 구독 전체 리전을 한 번에 섞어
  // 돌려준다(AWS와 달리 리전별 조회가 아니다) — 그래서 "기존 리소스 사용"에서 지금 만들려는
  // 리전과 다른 서브넷/NSG/VNet도 그냥 골라지고, terraform apply 단계에서야
  // `InvalidResourceReference ... same region`으로 실패한다(실사용 중 발견). 이 리전이
  // "실제로 적용될" 리전과 같은지 걸러야 한다 — 그런데 그 리전은 두 갈래로 정해진다:
  // 기존 리소스 그룹을 재사용하면 terraform이 **그 리소스 그룹 자신의 location**을 그대로
  // 쓰고(`terraform/azure/{vm,database/*}/main.tf`의 `data "azurerm_resource_group"`), 새로
  // 만들면 마법사 ④에서 고른 국가의 리전을 쓴다. 그래서 existingResourceGroupName을 먼저
  // 골랐으면 그 리소스 그룹의 location을, 아니면 지금 선택된 국가의 리전을 기준으로 삼는다.
  function azureEffectiveRegion(p, currentValues) {
    var rgName = currentValues.existingResourceGroupName;
    if (rgName) {
      var rgs = (fetchedNetworkData[p] || {}).resource_groups || [];
      var match = rgs.filter(function (r) { return r.name === rgName; })[0];
      if (match) return match.location;
    }
    return (state.providerSpec[p] || {}).region;
  }

  function existingFieldManualHtml(p, key, label, placeholder) {
    return labelHtml(label) +
      '<input type="text" data-ps-platform="' + p + '" data-ps="' + key + '" placeholder="' + placeholder +
      '" class="' + FIELD_INPUT + '" />';
  }

  function existingFieldSelectHtml(label, options) {
    var opts = '<option value="">선택하세요</option>' +
      options.map(function (o) { return '<option value="' + escHtml(o.value) + '">' + escHtml(o.text) + "</option>"; }).join("") +
      '<option value="__manual__">직접 입력…</option>';
    return labelHtml(label) + '<select class="' + FIELD_INPUT + '">' + opts + "</select>";
  }

  // 필드 하나(래퍼 div)를 select 모드로 바꾼다. 원본 <input data-ps-platform/data-ps>는 남겨두고
  // (collect()가 계속 그 값을 읽는다) select는 그 값을 받아쓰기만 하는 보조 컨트롤로 둔다 —
  // "직접 입력…"을 고르면 원본 input이 다시 드러나서 자유 입력으로 돌아간다.
  //
  // 원본 <label>도 select 모드에서는 같이 숨긴다(2026-09-22 수정) — 이전엔 input만 숨기고
  // label은 그대로 둬서, select 쪽이 새로 만드는 "…목록" 라벨과 겹쳐 같은 라벨이 두 번 보였다
  // (GCP CDN "기존 버킷 연결"에서 실사용 중 발견 — 이 함수를 공유하는 AWS VPC/Azure 리소스
  // 그룹 등 다른 필드에도 있던 잠재 버그였다). rebuildExistingFieldSelect가 재호출될 때도
  // 원본 label을 삭제하지 않고 hidden 클래스만 토글하므로, 재호출 시에도 label.textContent로
  // 원래 라벨 문구를 계속 읽어올 수 있다.
  function wireExistingFieldSelect(wrap, key, label, options, onPicked) {
    var input = wrap.querySelector('input[data-ps="' + key + '"]');
    var originalLabel = wrap.querySelector("label");
    var selectWrap = el("div", { class: "mt-1" });
    selectWrap.innerHTML = existingFieldSelectHtml(label + " 목록", options);
    var select = selectWrap.querySelector("select");
    wrap.appendChild(selectWrap);
    input.classList.add("hidden");
    if (originalLabel) originalLabel.classList.add("hidden");
    select.addEventListener("change", function () {
      if (select.value === "__manual__") {
        input.classList.remove("hidden");
        if (originalLabel) originalLabel.classList.remove("hidden");
        selectWrap.remove();
        input.focus();
        collect();
        updateSubmitState();
        return;
      }
      input.value = select.value;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      if (onPicked) onPicked();
    });
    return select;
  }

  // 지금 그려진 select들의 현재 선택값(필드 key -> value). select가 없으면(아직 "직접 입력"
  // 상태거나 렌더 전) 빈 문자열.
  function currentExistingSelectValues(kind, p, wrapsByKey) {
    var fieldDefs = (EXISTING_FIELD_SOURCES[kind] || {})[p] || {};
    var values = {};
    Object.keys(fieldDefs).forEach(function (key) {
      var wrap = wrapsByKey[key];
      var select = wrap && wrap.querySelector("select");
      values[key] = select ? select.value : "";
    });
    return values;
  }

  // 필드 하나의 select를 (다시) 그린다 — 상위 필드가 있으면 currentValues로 좁힌다. 이미 골라둔
  // 값이 새 옵션 목록에도 있으면 그대로 유지한다(상위 필드 자신은 이 함수로 재조회하지 않는다 —
  // 그러면 방금 고른 값이 매번 초기화돼 버린다, 2026-09-17 실사용 시나리오로 직접 확인).
  function rebuildExistingFieldSelect(kind, p, wrapsByKey, key, currentValues) {
    var wrap = wrapsByKey[key];
    if (!wrap) return null;
    var def = ((EXISTING_FIELD_SOURCES[kind] || {})[p] || {})[key];
    if (!def) return null;

    var existingSelect = wrap.querySelector("select");
    var preserveValue = existingSelect ? existingSelect.value : "";
    if (existingSelect) existingSelect.parentElement.remove();

    var data = fetchedNetworkData[p] || {};
    var items = data[def.source] || [];
    if (def.parent) {
      var parentVal = currentValues[def.parent];
      if (parentVal) items = items.filter(function (it) { return it[def.parentKey] === parentVal; });
    }
    if (def.regionFilter && p === "azure") {
      var region = azureEffectiveRegion(p, currentValues);
      if (region) items = items.filter(function (it) { return it.location === region; });
    }
    var options = items.map(def.optionOf);

    var label = wrap.querySelector("label");
    var labelText = label ? label.textContent : key;
    var select = wireExistingFieldSelect(wrap, key, labelText, options, function () {
      onExistingFieldSelectChanged(kind, p, wrapsByKey);
    });
    if (preserveValue && options.some(function (o) { return o.value === preserveValue; })) {
      select.value = preserveValue;
      var input = wrap.querySelector('input[data-ps="' + key + '"]');
      input.value = preserveValue;
    }
    return select;
  }

  // 아무 select나 바뀌면 호출된다 — "부모(parent)가 있는" 필드만 지금 선택값 기준으로 다시
  // 좁혀 그린다. 부모 필드 자신은 다시 그리지 않는다.
  function onExistingFieldSelectChanged(kind, p, wrapsByKey) {
    var fieldDefs = (EXISTING_FIELD_SOURCES[kind] || {})[p] || {};
    var values = currentExistingSelectValues(kind, p, wrapsByKey);
    Object.keys(fieldDefs).forEach(function (key) {
      if (fieldDefs[key].parent || fieldDefs[key].regionFilter) rebuildExistingFieldSelect(kind, p, wrapsByKey, key, values);
    });
    collect();
    updateSubmitState();
  }

  // "불러오기" 직후 — 이 플랫폼의 모든 필드를 처음부터 select로 그린다(선언 순서상 상위 필드가
  // 하위보다 먼저 오므로, 앞서 그린 값을 바로 다음 필드의 부모값으로 쓸 수 있다).
  function applyFetchedResourcesToFields(kind, p, wrapsByKey) {
    var fieldDefs = (EXISTING_FIELD_SOURCES[kind] || {})[p] || {};
    var values = {};
    Object.keys(fieldDefs).forEach(function (key) {
      var select = rebuildExistingFieldSelect(kind, p, wrapsByKey, key, values);
      values[key] = select ? select.value : "";
    });
    collect();
    updateSubmitState();
  }

  function fetchExistingResources(kind, p, container) {
    var btn = container.querySelector('[data-fetch-existing="' + p + '"]');
    var msg = container.querySelector('[data-fetch-existing-msg="' + p + '"]');
    var credEntry = (state.selectedCredentials || []).filter(function (c) { return c.provider === p; })[0];
    if (!credEntry || !credEntry.credentialId) {
      window.alert("먼저 ② 단계에서 " + PLATFORM_LABEL[p] + " 계정(자격 증명)을 선택하세요.");
      return;
    }
    var region = (state.providerSpec[p] || {}).region;
    if (p === "aws" && !region) {
      window.alert("먼저 ④ 공통 설정에서 리전(국가)을 선택하세요 — AWS는 리전별로 조회합니다.");
      return;
    }
    var path = "/credentials/" + credEntry.credentialId + "/network-resources";
    if (p === "aws") path += "?region=" + encodeURIComponent(region);

    if (!window.MCPApi) return;
    btn.disabled = true;
    var original = btn.textContent;
    btn.textContent = "불러오는 중…";
    if (msg) { msg.classList.add("hidden"); msg.textContent = ""; }
    MCPApi.request(path)
      .then(function (data) {
        fetchedNetworkData[p] = data;
        var wrapsByKey = {};
        container.querySelectorAll('[data-field-key]').forEach(function (w) {
          wrapsByKey[w.getAttribute("data-field-key")] = w;
        });
        applyFetchedResourcesToFields(kind, p, wrapsByKey);
        if (msg) {
          msg.classList.remove("hidden");
          msg.textContent = "불러왔습니다 — 아래에서 선택하세요." +
            (p === "azure" && kind !== "storage_object" ? " (지금 만들 리소스와 같은 리전의 서브넷/NSG/VNet만 표시됩니다)" : "");
        }
      })
      .catch(function (err) {
        // err.explanation/specificReason은 MCErr 패널용 구조화 객체(증상/원인/해결책)라 그냥 이어붙이면
        // "[object Object]"가 된다(2026-09-17 실사용 중 발견) — alert에는 항상 문자열인 err.message를 쓴다.
        window.alert("목록을 가져오지 못했습니다: " + (err.message || err.code || "알 수 없는 오류"));
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = original;
      });
  }

  function existingResourceFieldsHtml(kind, platforms) {
    var byPlatform = EXISTING_RESOURCE_FIELDS[kind] || {};
    return platforms.map(function (p) {
      var fields = byPlatform[p] || [];
      if (!fields.length) return "";
      var inputs = fields.map(function (f) {
        return '<div data-field-key="' + f[0] + '">' + existingFieldManualHtml(p, f[0], f[1], f[2]) + "</div>";
      }).join("");
      return '<div class="rounded-lg border border-border bg-background p-3" data-existing-field-wrap="' + p + '">' +
        '<div class="mb-2 flex items-center justify-between gap-2">' +
        '<p class="text-xs font-medium text-muted-foreground">' + PLATFORM_LABEL[p] + "</p>" +
        '<button type="button" data-fetch-existing="' + p + '" class="rounded-lg border border-border px-2 py-1 text-xs font-medium hover:bg-muted">실제 목록 불러오기</button>' +
        "</div>" +
        '<p data-fetch-existing-msg="' + p + '" class="mb-2 hidden text-xs text-muted-foreground"></p>' +
        '<div class="grid gap-2 sm:grid-cols-2">' + inputs + "</div></div>";
    }).join("");
  }

  // 마법사 ④ 리전(국가) 선택이 바뀌면 이미 "실제 목록 불러오기"로 가져와 둔 Azure 기존 리소스
  // select도 새 리전 기준으로 다시 걸러 그린다 — 안 그러면 국가를 나중에 바꿔도 예전 리전 기준
  // 옵션이 그대로 남아 리전이 다른 서브넷/NSG/VNet을 여전히 고를 수 있게 된다.
  function wireAzureRegionRefilter(container, kind, platforms) {
    if (platforms.indexOf("azure") === -1) return;
    var countrySelect = container.querySelector('select[data-cs="country"]');
    if (!countrySelect) return;
    countrySelect.addEventListener("change", function () {
      collect(); // state.providerSpec.azure.region을 최신값으로 먼저 맞춘 뒤 다시 그린다.
      if (!fetchedNetworkData.azure) return;
      var box = container.querySelector('[data-existing-field-wrap="azure"]');
      if (!box) return;
      var wrapsByKey = {};
      box.querySelectorAll('[data-field-key]').forEach(function (w) {
        wrapsByKey[w.getAttribute("data-field-key")] = w;
      });
      applyFetchedResourcesToFields(kind, "azure", wrapsByKey);
    });
  }

  // 네트워크 필드(공통) — "새로 생성"(기본) / "기존 리소스 사용" 토글 + 체크 시 provider별 입력
  // + "실제 목록 불러오기"(2026-09-17 — 전엔 ID를 사용자가 직접 콘솔에서 찾아 타이핑해야 했다).
  function networkFieldEl(kind, platforms) {
    var f = el("div", { class: "sm:col-span-2" });
    var fieldsHtml = existingResourceFieldsHtml(kind, platforms);
    f.innerHTML = labelHtml("네트워크") +
      '<label class="flex cursor-pointer items-center gap-2 text-sm">' +
      '<input type="checkbox" data-network-existing-toggle /> 기존 리소스 사용(선택한 것만 재사용, ' +
      "비워두면 지금처럼 자동 생성)</label>" +
      '<div data-network-existing-fields class="mt-2 hidden space-y-2">' + (fieldsHtml || "") + "</div>";
    var toggle = f.querySelector("[data-network-existing-toggle]");
    var fieldsBox = f.querySelector("[data-network-existing-fields]");
    toggle.addEventListener("change", function () {
      fieldsBox.classList.toggle("hidden", !toggle.checked);
      collect();
      updateSubmitState();
    });
    fieldsBox.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-fetch-existing]");
      if (!btn) return;
      var p = btn.getAttribute("data-fetch-existing");
      var box = btn.closest('[data-existing-field-wrap="' + p + '"]');
      fetchExistingResources(kind, p, box);
    });
    return f;
  }

  function inboundFieldEl() {
    var f = el("div", { class: "sm:col-span-2" });
    var presetHtml = INBOUND_PRESETS.map(function (r) {
      var checked = ""; // 기본 선택 없음 — 사용자가 직접 골라야 한다
      return '<label class="flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-sm">' +
        '<input type="checkbox" data-inbound-preset="' + r.port + '"' + checked + " /> " + r.label + "</label>";
    }).join("");
    f.innerHTML = labelHtml("인바운드 규칙", true) +
      '<div class="flex flex-wrap gap-2">' + presetHtml + "</div>" +
      '<div data-inbound-custom class="mt-2 space-y-2"></div>' +
      '<button type="button" data-inbound-add class="mt-2 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted">+ 규칙 추가</button>' +
      '<p data-inbound-msg class="mt-1 text-xs text-muted-foreground">최소 1개 이상의 규칙이 필요합니다.</p>';
    return f;
  }

  // Compute 인증(⑤로 이동) — 플랫폼별 위젯.
  function computeAuthEl(p) {
    var f = el("div", { class: "sm:col-span-2 rounded-xl bg-muted p-3" });
    var inner = '<p class="mb-2 text-sm font-medium">인증 · ' + PLATFORM_LABEL[p] + "</p>";
    if (p === "aws") {
      // 2026-09-15: SSH 키 페어 발급을 없앴다(정적 비밀키를 새로 만들지 않는 방향). 생성 후
      // 인벤토리 상세에서 "AWS CLI로 접속" 버튼으로 단기 자격증명을 발급받아 SSM Session
      // Manager로 접속한다 — 이 단계에서 별도로 입력받을 값이 없다.
      inner += '<p class="text-xs text-muted-foreground">SSH 키 페어 없이 생성됩니다 — 생성 후 ' +
        "인벤토리에서 <b>AWS CLI로 접속</b> 버튼으로 SSM Session Manager를 통해 접속하세요.</p>";
    } else if (p === "azure") {
      inner += '<div class="grid gap-2 sm:grid-cols-2">' +
        "<div>" + labelHtml("관리자 계정명", true) +
        '<input type="text" data-ps-platform="azure" data-ps="adminUsername" class="' + FIELD_INPUT + '" /></div>' +
        "<div>" + labelHtml("비밀번호", true) +
        '<input type="password" data-ps-platform="azure" data-ps="adminPassword" class="' + FIELD_INPUT + '" /></div></div>';
      // "경량" 등급 기본 SKU(B1s)가 실제 구독·리전에서 SkuNotAvailable로 거부되는 사례가 있어,
      // 무료 대상 x86-64 대안(B2ats_v2)을 사용자가 "명시적으로" 선택할 수 있게 한다 — 자동으로
      // 바꿔치기하지 않는다(collect()의 useFreeAltSku 게이트, "경량" 등급일 때만 적용됨). ARM64
      // 대안(B2pts_v2)은 이번 작업에서 이미지 호환을 구현하지 않아 선택지에서 아예 제외한다.
      var lightSku = SPEC_TIERS.filter(function (t) { return t.key === "light"; })[0].sku.azure;
      inner += '<label class="mt-2 flex items-start gap-2 text-xs">' +
        '<input type="checkbox" data-ps-platform="azure" data-ps="useFreeAltSku" class="mt-0.5" />' +
        '<span>"경량" 등급에서 기본 SKU(' + escHtml(lightSku.name) + ", " + lightSku.vcpu + " vCPU · " + lightSku.memGiB +
        ' GiB) 대신 무료 대상 x86-64 대안인 <b>' + escHtml(AZURE_LIGHT_FREE_ALT.name) + "</b>(" +
        AZURE_LIGHT_FREE_ALT.vcpu + " vCPU · " + AZURE_LIGHT_FREE_ALT.memGiB + " GiB)을 사용합니다. " +
        "다른 등급에서는 이 체크와 무관하게 적용되지 않습니다. ARM64 대안(B2pts_v2)은 이미지 " +
        "호환이 구현되지 않아 제공하지 않습니다.</span></label>";
    } else if (p === "gcp") {
      // 2026-09-16: SSH 공개키 입력을 없앴다 — 이전엔 필수 입력칸이었지만 백엔드/Terraform
      // 어디에도 전달되지 않는 죽은 필드였다(실사용 테스트로 발견). AWS의 SSM 전환(2026-09-15)과
      // 같은 원칙으로, GCP는 IAP + OS Login으로 접속한다 — 키 관리 자체가 필요 없다.
      inner += '<p class="text-xs text-muted-foreground">SSH 키 없이 생성됩니다 — 접속은 GCP ' +
        "콘솔의 <b>SSH</b> 버튼이나 <code>gcloud compute ssh --tunnel-through-iap</code>로 " +
        "가능합니다(접속 권한은 이 GCP 프로젝트 소유자가 IAM에서 부여).</p>";
    }
    f.innerHTML = inner;
    return f;
  }

  // DB 엔진+인증(⑤로 이동) — 플랫폼별 박스.
  function dbBoxEl(p) {
    var box = el("div", { class: "sm:col-span-2 space-y-3 rounded-xl bg-muted p-3" });
    var engineOpts = '<option value="">선택하세요</option>' +
      DB_ENGINES[p].map(function (e) { return "<option>" + e + "</option>"; }).join("");
    var html = '<p class="text-sm font-medium">' + PLATFORM_LABEL[p] + "</p>";
    html += "<div>" + labelHtml("엔진", true) +
      '<select data-ps-platform="' + p + '" data-ps="engine" class="' + FIELD_INPUT + '">' + engineOpts + "</select>";
    if (p === "azure") {
      html += '<p class="mt-1 text-xs" ' + WARN_STYLE + ">MariaDB는 2025년 9월 19일 이후 Azure에서 지원 종료됩니다.</p>";
    }
    html += "</div>";
    if (p === "gcp" || p === "aws") {
      // aws는 실 API가 master_username을 안 받는다(서버가 mcp_admin으로 고정) — gcp와 같은 UI.
      // 2026-09-16: 비밀번호 규칙을 안내 문구에 명시 — 규칙 안내 없이 막연히 "비밀번호만
      // 입력합니다"라고만 써놔서, 실제 생성 시 규칙 위반으로 VALIDATION_ERROR가 나고서야
      // 사용자가 규칙을 알게 되는 문제가 있었음(실사용 중 발견). 규칙은 플랫폼마다 다르다
      // (app/gcp_cloudsql_provisioning.py: 8자 이상만 / app/aws_rds_provisioning.py: 8~41자,
      // '/'·'"'·'@'·공백 금지).
      var pwHint = p === "gcp"
        ? "GCP는 비밀번호만 입력합니다(사용자명은 자동 지정). 최소 8자 이상이어야 합니다."
        : "AWS는 비밀번호만 입력합니다(사용자명은 자동 지정). 8~41자, ' / \" @ ' 문자와 공백은 사용할 수 없습니다.";
      html += "<div>" + labelHtml("루트 비밀번호", true) +
        '<input type="password" data-ps-platform="' + p + '" data-ps="masterPassword" class="' + FIELD_INPUT + '" />' +
        '<p class="mt-1 text-xs text-muted-foreground">' + pwHint + "</p></div>";
    } else {
      html += '<div class="grid gap-2 sm:grid-cols-2"><div>' + labelHtml("마스터 사용자명", true) +
        '<input type="text" data-ps-platform="' + p + '" data-ps="masterUsername" class="' + FIELD_INPUT + '" /></div>' +
        "<div>" + labelHtml("비밀번호", true) +
        '<input type="password" data-ps-platform="' + p + '" data-ps="masterPassword" class="' + FIELD_INPUT + '" /></div></div>';
    }
    box.innerHTML = html;
    return box;
  }

  // ── DB 공통 설정 렌더링 ───────────────────────────────────────────────────
  function renderDbCommon(container, platforms) {
    container.innerHTML = "";
    container.appendChild(nameFieldEl("db-01", "db"));
    container.appendChild(countryFieldEl());

    // 엔진·인증은 플랫폼별로 달라 ⑤ 추가 설정으로 이동(공통은 플랫폼 무관 유지).

    // 네트워크 — "새로 생성"(기본) / "기존 리소스 사용" 토글(2026-09-17). DB는 서브넷/NSG
    // 재사용은 지원하지 않는다(위임·private endpoint 정책 강제 변경 위험) — 리소스 그룹/VNet만.
    container.appendChild(networkFieldEl("db", platforms));
    wireAzureRegionRefilter(container, "db", platforms);

    // 백업 — 읽기 전용 안내(입력 아님)
    var backup = el("div", { class: "sm:col-span-2" });
    backup.innerHTML = labelHtml("백업") +
      '<div class="rounded-lg border border-dashed border-border bg-muted px-3 py-2 text-sm text-muted-foreground">자동 백업 활성화, 보존 기간은 기본값</div>';
    container.appendChild(backup);

    appendTagField(container, null);
    wireDynamicRows(container);
    collect();
  }

  // ── Storage 공통 설정 렌더링 ──────────────────────────────────────────────
  function renderStorageCommon(container, platforms) {
    container.innerHTML = "";

    // 이름(서비스) — 읽기 전용 라벨(선택 종류 확인용)
    var svc = el("div", { class: "sm:col-span-2" });
    svc.innerHTML = labelHtml("이름(서비스)") +
      '<div class="rounded-lg border border-dashed border-border bg-muted px-3 py-2 text-sm text-muted-foreground">Object Storage</div>';
    container.appendChild(svc);

    // 버킷/계정명 — 전역 고유(프리픽스 없음)
    var bucket = el("div", { class: "sm:col-span-2" });
    bucket.innerHTML = labelHtml("버킷/계정명", true) +
      '<input type="text" data-cs="name" data-name-box placeholder="my-unique-bucket" class="' + FIELD_INPUT + '" />' +
      '<p data-name-error class="mt-1 hidden text-xs" style="color:#dc2626"></p>' +
      '<p class="mt-1 text-xs text-muted-foreground">전역에서 고유한 이름이어야 합니다.</p>' +
      namePolicyHintHtml("storage_object");
    container.appendChild(bucket);

    // 리전 — 국가 단일 선택(플랫폼 무관)
    container.appendChild(countryFieldEl());

    // 태그 — Azure 선택 시 경고
    var azureWarn = platforms.indexOf("azure") >= 0
      ? "컨테이너가 아닌 스토리지 계정 태그로 저장되어 태그 검색은 지원하지 않습니다."
      : null;
    appendTagField(container, azureWarn);
    wireDynamicRows(container);
    collect();
  }

  // 커스텀 인바운드 행 / 태그 행 추가·삭제 버튼 배선
  function wireDynamicRows(container) {
    var addInbound = container.querySelector("[data-inbound-add]");
    var inboundWrap = container.querySelector("[data-inbound-custom]");
    if (addInbound) {
      addInbound.addEventListener("click", function () {
        var row = el("div", { class: "flex items-center gap-2" });
        row.innerHTML =
          '<input type="number" data-inbound-port placeholder="포트" class="w-28 rounded-lg border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary" />' +
          '<input type="text" data-inbound-cidr value="' + DEFAULT_CIDR + '" class="flex-1 rounded-lg border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary" />' +
          '<button type="button" data-row-del class="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted" aria-label="삭제">✕</button>';
        inboundWrap.appendChild(row);
        collect();
      });
    }
    var addTag = container.querySelector("[data-tag-add]");
    var tagWrap = container.querySelector("[data-tag-rows]");
    if (addTag) {
      addTag.addEventListener("click", function () {
        var row = el("div", { class: "flex items-center gap-2" });
        row.innerHTML =
          '<input type="text" data-tag-key placeholder="key" class="w-1/3 rounded-lg border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary" />' +
          '<input type="text" data-tag-val placeholder="value" class="flex-1 rounded-lg border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary" />' +
          '<button type="button" data-row-del class="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted" aria-label="삭제">✕</button>';
        tagWrap.appendChild(row);
        collect();
      });
    }
  }

  // ── DOM → 상태 수집 (리소스 종류 범용) ───────────────────────────────────
  function collect() {
    var container = document.getElementById("prov-common-fields");
    state.platforms = readPlatforms();
    state.selectedAccounts = readSelectedAccounts();
    // 선택 안 된 플랫폼의 이전 값이 남지 않도록 providerSpec을 매번 새로 구성
    state.providerSpec = { aws: {}, azure: {}, gcp: {} };

    var kind = state.resourceKind;
    if (!container || !kind) { state.commonSpec = {}; return; }

    // CDN은 공통 설정 스텝이 없으므로 commonSpec은 비우고 플랫폼별(⑤)만 수집한다.
    var cs = {};
    if (kind !== "cdn") {
      // 공통 스칼라 필드(data-cs). data-prefix가 있으면 접두어를 붙인다(예: 이름 mcp-).
      container.querySelectorAll("[data-cs]").forEach(function (inp) {
        var key = inp.getAttribute("data-cs");
        var prefix = inp.getAttribute("data-prefix");
        cs[key] = prefix ? prefix + (inp.value || "").trim() : inp.value;
      });

      // 태그(모든 종류 공통)
      var tags = {};
      container.querySelectorAll("[data-tag-rows] > div").forEach(function (row) {
        var k = row.querySelector("[data-tag-key]");
        var v = row.querySelector("[data-tag-val]");
        if (k && k.value.trim()) tags[k.value.trim()] = (v && v.value.trim()) || "";
      });
      cs.tags = tags;

      // Compute 전용: 인바운드 규칙(⑤로 이동) + 네트워크 고정
      if (kind === "compute") {
        var rules = [];
        var inboundScope = document.getElementById("prov-provider-fields") || container;
        inboundScope.querySelectorAll("[data-inbound-preset]").forEach(function (cb) {
          if (cb.checked) rules.push({ port: Number(cb.getAttribute("data-inbound-preset")), cidr: DEFAULT_CIDR });
        });
        inboundScope.querySelectorAll("[data-inbound-custom] > div").forEach(function (row) {
          var portEl = row.querySelector("[data-inbound-port]");
          var cidrEl = row.querySelector("[data-inbound-cidr]");
          var port = portEl && portEl.value ? Number(portEl.value) : null;
          if (port) rules.push({ port: port, cidr: (cidrEl && cidrEl.value.trim()) || DEFAULT_CIDR });
        });
        cs.inboundRules = rules;
      }
      if (kind === "db") cs.backup = "auto"; // 자동 백업 고정(표시만)
      // 기존 리소스 재사용 토글(compute/db 공통, networkFieldEl) — 체크 안 돼 있으면 provider별로
      // 입력했던 기존 리소스 ID가 있어도 무시한다(buildProviderSpec에서 이 플래그로 게이트).
      if (kind === "compute" || kind === "db") {
        var networkToggle = container.querySelector("[data-network-existing-toggle]");
        cs.useExistingNetwork = !!(networkToggle && networkToggle.checked);
      }
    }
    state.commonSpec = cs;

    // 플랫폼별(data-ps) — 리전/엔진/인증(④) + 이미지/스토리지등급(⑤). 두 컨테이너 모두 스캔.
    var tier = kind === "compute"
      ? SPEC_TIERS.filter(function (t) { return t.key === cs.specTier; })[0]
      : null;
    var providerContainer = document.getElementById("prov-provider-fields");
    var scopes = [container, providerContainer].filter(Boolean);
    state.platforms.forEach(function (p) {
      var ps = state.providerSpec[p];
      scopes.forEach(function (scope) {
        scope.querySelectorAll('[data-ps-platform="' + p + '"][data-ps]').forEach(function (input) {
          ps[input.getAttribute("data-ps")] = input.type === "checkbox" ? input.checked : input.value;
        });
      });
      if (tier && tier.sku[p]) {
        // Azure "경량" 등급 + 무료 대안(B2ats_v2) 체크박스가 켜져 있으면 기본 B1s 대신 그
        // SKU를 보낸다 — 자동으로 바꿔치기하지 않고 사용자가 명시적으로 체크해야만 적용된다
        // (computeAuthEl의 azure 분기 체크박스, data-ps="useFreeAltSku"). 실제 전송되는 SKU는
        // renderReview()에도 그대로 노출한다.
        var useFreeAlt = p === "azure" && cs.specTier === "light" && ps.useFreeAltSku;
        ps.instanceType = useFreeAlt ? AZURE_LIGHT_FREE_ALT.name : tier.sku[p].name;
      }
      // 국가 → 플랫폼 리전 매핑(공통 설정의 국가 하나로 각 플랫폼 리전 결정)
      if (cs.country && COUNTRY_REGION[cs.country]) ps.region = COUNTRY_REGION[cs.country][p];
      // 제외 필드의 서버 기본값 병합(사용자 입력 없음)
      var defs = SERVER_DEFAULTS[kind];
      if (defs) Object.keys(defs).forEach(function (k) { if (ps[k] === undefined) ps[k] = defs[k]; });
    });
    if (kind === "compute") refreshSpecDetail();
  }

  // ── CDN 입력 폼 빌더 ──────────────────────────────────────────────────────
  function cdnText(p, key, label, req, ph, val, hint) {
    return "<div>" + labelHtml(label, req) +
      '<input type="text" data-ps-platform="' + p + '" data-ps="' + key + '" placeholder="' + (ph || "") +
      '" value="' + (val || "") + '" class="' + FIELD_INPUT + '" />' +
      (hint ? '<p class="mt-1 text-xs text-muted-foreground">' + hint + "</p>" : "") + "</div>";
  }
  function cdnNumber(p, key, label, req, val) {
    return "<div>" + labelHtml(label, req) +
      '<input type="number" data-ps-platform="' + p + '" data-ps="' + key + '" value="' + (val == null ? "" : val) +
      '" class="' + FIELD_INPUT + '" /></div>';
  }
  function cdnSelect(p, key, label, options, req, extraAttr) {
    var o = options.map(function (v) { return "<option>" + v + "</option>"; }).join("");
    return "<div>" + labelHtml(label, req) +
      '<select data-ps-platform="' + p + '" data-ps="' + key + '" ' + (extraAttr || "") +
      ' class="' + FIELD_INPUT + '">' + o + "</select></div>";
  }
  function cdnToggle(p, key, label, checked) {
    return '<label class="flex items-center justify-between rounded-lg border border-border bg-background px-3 py-2 text-sm"><span>' +
      label + '</span><input type="checkbox" data-ps-platform="' + p + '" data-ps="' + key + '"' +
      (checked ? " checked" : "") + " /></label>";
  }

  // CDN은 공통 스텝 없이, 선택한 플랫폼(AWS→Azure→GCP 순)의 서로 다른 필드셋만 렌더링.
  function renderCdn(container, platforms) {
    ["aws", "azure", "gcp"].forEach(function (p) {
      if (platforms.indexOf(p) < 0) return;
      var box = el("div", { class: "space-y-3 rounded-xl bg-muted p-3" });
      var html = '<p class="text-sm font-medium">' + PLATFORM_LABEL[p] + " CDN</p>";

      if (p === "aws") {
        html += '<div class="grid gap-3 sm:grid-cols-2">' +
          cdnText("aws", "origin", "Origin", true, "example.s3.ap-northeast-2.amazonaws.com", "",
            "https:// 등 스킴이나 경로 없이 호스트명만 입력하세요.") +
          cdnSelect("aws", "cachePolicy", "캐시 정책", CDN_OPTS.awsCachePolicy) +
          cdnSelect("aws", "pathRouting", "Path routing", CDN_OPTS.awsPathRouting) +
          cdnSelect("aws", "viewerProtocolPolicy", "Viewer Protocol Policy", CDN_OPTS.awsViewerProtocol) +
          cdnSelect("aws", "priceClass", "Price Class", CDN_OPTS.awsPriceClass) +
          "</div>" +
          cdnToggle("aws", "compression", "Compression", true);
      } else if (p === "azure") {
        html += '<div class="grid gap-3 sm:grid-cols-2">' +
          cdnText("azure", "origin", "Origin", true, "example.com", "",
            "https:// 등 스킴이나 경로 없이 호스트명만 입력하세요.") +
          cdnText("azure", "resourceGroup", "Resource Group", true) +
          "</div>" +
          // 2026-09-17: 기존엔 이 이름으로 항상 새 리소스 그룹을 만들려고 해서 기존 이름과
          // 겹치면 apply가 실패했다 — 체크하면 "새로 만들 이름"이 아니라 "조회할 기존 이름"으로
          // 쓴다(app/azure_cdn_provisioning.py의 use_existing_resource_group).
          cdnToggle("azure", "useExistingResourceGroup", "위 Resource Group을 기존 것으로 사용(체크 안 하면 새로 생성)", false) +
          '<div class="grid gap-3 sm:grid-cols-2">' +
          "<div>" + labelHtml("SKU", true) +
          '<select data-ps-platform="azure" data-ps="sku" class="' + FIELD_INPUT + '"><option value="">선택하세요</option>' +
          CDN_OPTS.azSku.map(function (v) { return "<option>" + v + "</option>"; }).join("") + "</select></div>" +
          cdnSelect("azure", "queryStringCaching", "쿼리스트링 캐시 처리", CDN_OPTS.azQueryString) +
          cdnSelect("azure", "supportedProtocols", "지원 프로토콜", CDN_OPTS.azProtocols) +
          cdnText("azure", "healthProbePath", "Health Probe 경로", false, "", "/") +
          cdnNumber("azure", "healthProbeIntervalSec", "Health Probe 간격(초)", false, 240) +
          "</div>" +
          cdnToggle("azure", "compression", "Compression", true) +
          cdnToggle("azure", "httpsRedirect", "HTTPS 리다이렉트", true) +
          '<p class="text-xs" ' + WARN_STYLE + '>Azure Front Door(Standard)는 무료 한도가 없습니다 — ' +
          "월 기본료 약 $35(데이터 전송량 별도)가 생성 즉시 발생합니다. 테스트 후 즉시 삭제하세요.</p>";
      } else if (p === "gcp") {
        html +=
          // 백엔드 버킷을 CDN 전용으로 새로 만들지, 이미 있는 버킷을 그대로 쓸지 선택
          // (app/gcp_cdn_provisioning.py의 provider_spec.create_bucket과 1:1 대응). 설명이 길어서
          // 기본은 한 줄 요약만 보여주고 "상세히 보기"를 눌러야 전체(+ 왜 이렇게 설계했는지)가
          // 펼쳐지게 했다 — 토글은 체크박스 밖에 별도 버튼으로 둬서 클릭해도 체크 상태는 안 바뀐다.
          '<label class="flex items-start gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm">' +
          '<input type="checkbox" data-ps-platform="gcp" data-ps="createBucket" data-gcp-create-bucket class="mt-0.5" checked />' +
          "<span>CDN 전용 버킷을 자동으로 생성합니다(권장). 체크를 해제하면 이미 가지고 있는 버킷을 " +
          "연결할 수도 있습니다.</span></label>" +
          '<button type="button" data-gcp-bucket-detail-toggle class="ml-6 text-xs font-medium text-primary underline">' +
          "상세히 보기</button>" +
          '<div data-gcp-bucket-detail hidden class="ml-6 space-y-2 rounded-lg bg-muted/60 p-3 text-xs text-muted-foreground">' +
          "<p><b>자동 생성(권장)</b> — 이 CDN만을 위한 공개 버킷을 새로 만들어 연결하므로, 이름이 " +
          "겹칠 걱정이나 기존 파일이 함께 공개될 위험이 없습니다.</p>" +
          "<p><b>기존 버킷 연결</b> — 이미 가지고 있는 버킷을 연결할 수 있습니다. 단, 그 버킷의 " +
          "공개 읽기 권한은 저희가 대신 설정해 드리지 않으니 GCP 콘솔에서 미리 직접 설정해 두셔야 " +
          "합니다.</p>" +
          "<p><b>왜 권한을 자동으로 안 바꾸나요</b> — 저희가 만들지 않은(이미 다른 용도로 쓰이고 " +
          "있었을 수도 있는) 버킷의 보안 설정을 자동으로 바꾸면, 버킷 이름을 잘못 입력하는 작은 " +
          "실수만으로 엉뚱한 버킷이 실수로 공개될 수 있습니다. 그래서 같은 실수를 해도 \"정보 " +
          "유출\"이 아니라 \"생성 실패\"로 끝나도록, 기존 버킷의 권한 변경은 항상 사용자가 직접 " +
          "하도록 설계했습니다.</p>" +
          "</div>" +
          // 2026-09-22: 자유 텍스트 대신 "실제 목록 불러오기"로 실제 보유 버킷 중에서 고르게 한다
          // (compute/db/storage_object의 기존 리소스 선택과 같은 프레임워크, EXISTING_RESOURCE_FIELDS.cdn.gcp).
          '<div data-gcp-existing-bucket-wrap hidden class="grid gap-3 sm:grid-cols-2">' +
          existingResourceFieldsHtml("cdn", ["gcp"]) +
          "</div>" +
          '<label data-gcp-existing-bucket-ack-wrap hidden class="flex items-start gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm">' +
          '<input type="checkbox" data-ps-platform="gcp" data-ps="existingBucketPublicAck" class="mt-0.5" />' +
          "<span>이 버킷을 GCP 콘솔에서 이미 공개 읽기(버킷 전체 — 개별 파일 아님, allUsers · " +
          "Storage Object Viewer)로 설정해 두었습니다. 확인합니다. " +
          '<span class="text-primary">*</span></span></label>' +
          '<div class="grid gap-3 sm:grid-cols-2">' +
          cdnSelect("gcp", "cacheMode", "Cache Mode", CDN_OPTS.gcpCacheMode) +
          cdnSelect("gcp", "compression", "Compression", CDN_OPTS.gcpCompression) +
          "</div>" +
          '<label class="flex items-start gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm">' +
          '<input type="checkbox" data-ps-platform="gcp" data-ps="lbStackAck" class="mt-0.5" />' +
          "<span>GCP Cloud CDN은 외부 HTTP(S) 로드밸런서 스택이 필요합니다. 함께 구성에 동의합니다. " +
          '<span class="text-primary">*</span></span></label>' +
          cdnToggle("gcp", "enableCdn", "enableCdn", true) +
          cdnToggle("gcp", "httpsRedirect", "HTTPS 강제 리다이렉트", true);
      }
      box.innerHTML = html;
      container.appendChild(box);
    });
  }

  // CDN 전용 버킷 자동 생성 체크를 해제하면 기존 버킷 이름 입력 + 공개 전환 동의 체크박스를 보여준다
  // (app/gcp_cdn_provisioning.py의 create_bucket=false 경로와 1:1 대응).
  function toggleGcpBucketMode(cb) {
    var boxEl = cb.closest(".rounded-xl");
    if (!boxEl) return;
    var useExisting = !cb.checked;
    var nameWrap = boxEl.querySelector("[data-gcp-existing-bucket-wrap]");
    var ackWrap = boxEl.querySelector("[data-gcp-existing-bucket-ack-wrap]");
    if (nameWrap) nameWrap.hidden = !useExisting;
    if (ackWrap) ackWrap.hidden = !useExisting;
    if (!useExisting) {
      var ackInput = ackWrap && ackWrap.querySelector('[data-ps="existingBucketPublicAck"]');
      if (ackInput) ackInput.checked = false; // 다시 자동 생성으로 바꾸면 동의도 초기화
    }
  }

  // "상세히 보기" 버튼 — 버킷 자동생성/기존연결 설명의 짧은 요약 ↔ 전체 설명을 토글한다.
  function toggleGcpBucketDetail(btn) {
    var detail = btn.nextElementSibling;
    if (!detail || !detail.hasAttribute("data-gcp-bucket-detail")) return;
    detail.hidden = !detail.hidden;
    btn.textContent = detail.hidden ? "상세히 보기" : "간략히 보기";
  }

  // ── ⑤ 플랫폼별 추가 설정 렌더링 ──────────────────────────────────────────
  function renderProviderStep(container, kind, platforms) {
    container.innerHTML = "";

    if (kind === "cdn") {
      renderCdn(container, platforms);
      return;
    }

    if (kind === "compute") {
      platforms.forEach(function (p) {
        if (!COMPUTE_IMAGES[p]) return;
        var box = el("div", { class: "rounded-xl bg-muted p-3" });
        var opts = '<option value="">선택하세요</option>' +
          COMPUTE_IMAGES[p].map(function (o) { return "<option>" + o + "</option>"; }).join("");
        var html = '<p class="mb-2 text-sm font-medium">' + PLATFORM_LABEL[p] + " 이미지</p>" +
          '<select data-ps-platform="' + p + '" data-ps="image"' + (p === "aws" ? " data-image-select" : "") +
          ' class="' + FIELD_INPUT + '">' + opts + "</select>";
        if (p === "aws") {
          html += '<div data-ami-wrap hidden class="mt-2">' + labelHtml("AMI ID", true) +
            '<input type="text" data-ps-platform="aws" data-ps="amiId" placeholder="ami-xxxxxxxx" class="' + FIELD_INPUT + '" /></div>';
        }
        box.innerHTML = html;
        container.appendChild(box);
      });
      // 인바운드 규칙(공통에서 이동) + 인증(플랫폼별)
      container.appendChild(inboundFieldEl());
      platforms.forEach(function (p) { container.appendChild(computeAuthEl(p)); });
      wireDynamicRows(container); // 인바운드 "규칙 추가" 버튼 배선
      return;
    }

    if (kind === "storage_object") {
      var hasExtra = false;
      if (platforms.indexOf("gcp") >= 0) {
        hasExtra = true;
        var opts2 = '<option value="">선택하세요</option>' +
          STORAGE_CLASSES.map(function (o) { return "<option>" + o + "</option>"; }).join("");
        var box2 = el("div", { class: "rounded-xl bg-muted p-3" },
          '<p class="mb-2 text-sm font-medium">GCP 스토리지 등급</p>' +
          '<select data-ps-platform="gcp" data-ps="storageClass" class="' + FIELD_INPUT + '">' + opts2 + "</select>");
        container.appendChild(box2);
      }
      if (platforms.indexOf("azure") >= 0) {
        // Storage Account엔 VNet/NSG 개념이 없어 재사용할 대상이 리소스 그룹뿐이다(2026-09-17).
        // 2026-09-18: compute/db와 똑같이 "실제 목록 불러오기" 드롭다운으로 통일(예전엔 수동
        // 텍스트 입력뿐이었다 — 다르게 취급할 이유가 없었는데 놓친 부분이었다).
        hasExtra = true;
        var box3 = el("div");
        box3.innerHTML =
          '<p class="mb-1 text-xs text-muted-foreground">비워두면 새 리소스 그룹을 만듭니다.</p>' +
          existingResourceFieldsHtml("storage_object", ["azure"]);
        container.appendChild(box3);
      }
      if (!hasExtra) {
        container.appendChild(el("div", { class: "text-xs text-muted-foreground" },
          "선택한 플랫폼에는 추가 입력이 없습니다(서버 기본값 사용)."));
      }
      return;
    }

    if (kind === "db") {
      // 엔진·인증(공통에서 이동) — 플랫폼별 박스
      platforms.forEach(function (p) { container.appendChild(dbBoxEl(p)); });
    }
  }

  // AWS 이미지가 '직접 AMI ID 입력'일 때만 AMI ID 입력 노출
  function toggleAmi(sel) {
    var wrap = sel.parentElement.querySelector("[data-ami-wrap]");
    if (!wrap) return;
    var custom = sel.value === AMI_CUSTOM;
    wrap.hidden = !custom;
    if (!custom) {
      var inp = wrap.querySelector('[data-ps="amiId"]');
      if (inp) inp.value = "";
    }
  }

  function isFilled(v) {
    return typeof v === "string" ? v.trim().length > 0 : v != null;
  }

  // 선택된 각 플랫폼에 대해 리소스 종류의 공통 필수 + 해당 플랫폼 추가 필수 필드가
  // 전부 채워졌는지 검사. CDN은 항상 false.
  function validate() {
    var kind = state.resourceKind;
    if (!kind) return false;
    if (!state.platforms.length) return false;
    var cs = state.commonSpec || {};

    // 이름 문자/형식이 규칙에 어긋나면(대문자·한글·공백 등) 제출 불가 — 실시간 차단을 없앤 대신
    // 여기서 게이팅한다(빈 값은 아래 각 종류의 presence 검사가 따로 처리). CDN은 이름 필드가 없다.
    if (currentNameInvalid(kind)) return false;

    if (kind === "cdn") {
      // 필수: AWS Origin / Azure Origin·Resource Group·SKU /
      // GCP LB stack 동의 + (버킷 자동 생성이면 그걸로 충분, 기존 버킷 사용이면 버킷 이름·공개 동의)
      return state.platforms.every(function (p) {
        var ps = state.providerSpec[p] || {};
        if (p === "aws") return isFilled(ps.origin);
        if (p === "azure") return isFilled(ps.origin) && isFilled(ps.resourceGroup) && isFilled(ps.sku);
        if (p === "gcp") {
          if (ps.lbStackAck !== true) return false;
          if (ps.createBucket === false) {
            return isFilled(ps.backendBucketName) && ps.existingBucketPublicAck === true;
          }
          return true;
        }
        return true;
      });
    }

    if (kind === "compute") {
      if (!cs.name || cs.name === "mcp-") return false; // 이름(프리픽스만이면 미입력)
      if (!cs.specTier) return false; // 사양 등급(기본 선택 없음 — 직접 골라야 함)
      if (!cs.inboundRules || !cs.inboundRules.length) return false; // 인바운드 최소 1개
      return state.platforms.every(function (p) {
        var ps = state.providerSpec[p] || {};
        if (!isFilled(ps.region)) return false;
        if (p === "azure" && (!isFilled(ps.adminUsername) || !isFilled(ps.adminPassword))) return false;
        // GCP는 SSH 키 입력을 안 받는다(IAP+OS Login으로 접속, 2026-09-16) — 필수 검증 없음.
        // ⑤ AWS 이미지가 직접 AMI ID 입력이면 AMI ID 필수
        if (p === "aws" && ps.image === AMI_CUSTOM && !isFilled(ps.amiId)) return false;
        return true;
      });
    }

    if (kind === "db") {
      if (!cs.name || cs.name === "mcp-") return false;
      return state.platforms.every(function (p) {
        var ps = state.providerSpec[p] || {};
        if (!isFilled(ps.region) || !isFilled(ps.engine)) return false;
        if (p === "gcp" || p === "aws") return isFilled(ps.masterPassword);
        return isFilled(ps.masterUsername) && isFilled(ps.masterPassword);
      });
    }

    if (kind === "storage_object") {
      if (!isFilled(cs.name)) return false; // 버킷/계정명(프리픽스 없음)
      return state.platforms.every(function (p) {
        var ps = state.providerSpec[p] || {};
        if (!isFilled(ps.region)) return false;
        // ⑤ 스토리지 등급은 GCP 전용 필수 입력(app/gcp_storage_provisioning.py — 이제 실 API라
        // 값이 없으면 422). 실 연동 전엔 시뮬레이션이라 검사 없이도 문제없었지만 이제는 필요하다.
        if (p === "gcp" && !isFilled(ps.storageClass)) return false;
        return true;
      });
    }
    return false;
  }

  // 생성하기 활성/비활성
  function updateSubmitState() {
    var btn = document.getElementById("prov-submit-btn");
    if (!btn) return;
    var ok = validate();
    btn.disabled = !ok;
    btn.classList.toggle("opacity-50", !ok);
    btn.classList.toggle("cursor-not-allowed", !ok);
  }

  // ── 선택 상태 → 시각 피드백 (①플랫폼/③종류=카드, ②계정=chip) ──────────────
  // 마크업의 고정색을 없애고, 실제 체크/선택 상태에서 강조 클래스를 계산해 반영한다.
  var SELECT_STYLE = {
    card: { on: ["border-primary", "bg-muted"], off: ["border-border"] },
    chip: { on: ["bg-sky", "text-white", "border-primary"], off: ["bg-muted", "text-muted-foreground", "border-border"] },
  };
  function applySelectState(labelEl, on, style) {
    style.on.forEach(function (c) { labelEl.classList.toggle(c, on); });
    style.off.forEach(function (c) { labelEl.classList.toggle(c, !on); });
  }
  function syncSelectionUI() {
    document.querySelectorAll("[data-prov-platform]").forEach(function (cb) {
      var label = cb.closest("[data-select-card]");
      if (label) applySelectState(label, cb.checked, SELECT_STYLE.card);
    });
    document.querySelectorAll("[data-prov-kind]").forEach(function (rb) {
      var label = rb.closest("[data-select-card]");
      if (label) applySelectState(label, rb.checked, SELECT_STYLE.card);
    });
    var count = 0;
    document.querySelectorAll("[data-prov-account]").forEach(function (cb) {
      var label = cb.closest("[data-select-chip]");
      if (label) applySelectState(label, cb.checked, SELECT_STYLE.chip);
      if (cb.checked) count++;
    });
    var countEl = document.getElementById("prov-account-count");
    if (countEl) countEl.textContent = "(" + count + "개 선택)";
  }

  // ── 스텝 렌더 진입점 (④ 공통 + ⑤ 추가) ──────────────────────────────────
  function renderSteps() {
    var commonSection = document.getElementById("prov-step-common");
    var commonC = document.getElementById("prov-common-fields");
    var providerC = document.getElementById("prov-provider-fields");
    if (!commonC) return;
    state.resourceKind = readResourceKind();
    state.platforms = readPlatforms();
    var kind = state.resourceKind;

    // ④ 공통 설정 필드 렌더(섹션 표시/숨김은 위저드가 제어. CDN은 공통 필드 없음).
    if (kind === "compute") {
      renderComputeCommon(commonC, state.platforms);
    } else if (kind === "db") {
      renderDbCommon(commonC, state.platforms);
    } else if (kind === "storage_object") {
      renderStorageCommon(commonC, state.platforms);
    } else {
      commonC.innerHTML = ""; // CDN: ④ 공통 없음(위저드 네비가 스텝 4를 건너뜀)
    }

    // ⑤ 플랫폼별 추가 설정
    if (providerC) renderProviderStep(providerC, kind, state.platforms);

    collect();
    updateSubmitState();
  }

  // ── 스텝(점진적 노출) 흐름 ────────────────────────────────────────────────
  var KIND_LABEL = { compute: "Compute", db: "Database", storage_object: "Storage", cdn: "CDN" };

  // 현재 리소스 종류에서 실제로 노출되는 스텝 순서(CDN은 ④ 공통을 건너뜀).
  function visibleSteps() {
    var order = [1, 2, 3, 4, 5, 6];
    if (state.resourceKind === "cdn") order = order.filter(function (s) { return s !== 4; });
    return order;
  }

  // 스텝별 '다음' 진행 가능 여부(필수값 검증).
  function stepValid(n) {
    if (n === 1) return state.platforms.length > 0;
    if (n === 2) return Object.keys(state.selectedAccounts).length > 0;
    if (n === 3) return !!state.resourceKind;
    if (n === 4) {
      var cs = state.commonSpec || {};
      if (state.resourceKind === "storage_object") return isFilled(cs.name);
      return !!cs.name && cs.name !== "mcp-";
    }
    if (n === 5) return validate(); // 공통+추가 전체 필수 충족
    return validate();
  }

  function renderReview() {
    var box = document.getElementById("prov-review");
    if (!box) return;
    var accounts = Object.keys(state.selectedAccounts)
      .map(function (k) { return state.selectedAccounts[k]; }).join(", ") || "-";
    var cs = state.commonSpec || {};
    // Compute는 실제로 API에 전송되는 SKU를 그대로 보여준다(무료 대안 체크 여부에 따라 등급
    // 표시상의 기본 SKU와 달라질 수 있으므로 — collect()가 채운 state.providerSpec[p].instanceType이
    // "실제 전송값"의 단일 소스다. 화면 표시와 전송값이 어긋나지 않도록 여기서 다시 계산하지
    // 않고 그대로 읽는다).
    var skuLines = "";
    if (state.resourceKind === "compute") {
      skuLines = state.platforms.map(function (p) {
        var ps = (state.providerSpec || {})[p] || {};
        if (!ps.instanceType) return "";
        return "<div>" + escHtml(PLATFORM_LABEL[p]) + " SKU: <b>" + escHtml(ps.instanceType) + "</b></div>";
      }).join("");
    }
    box.innerHTML =
      "<div>플랫폼: <b>" + (state.platforms.join(", ") || "-") + "</b></div>" +
      "<div>계정: <b>" + accounts + "</b></div>" +
      "<div>종류: <b>" + (KIND_LABEL[state.resourceKind] || "-") + "</b></div>" +
      (cs.country ? "<div>리전(국가): <b>" + cs.country + "</b></div>" : "") +
      skuLines;
  }

  // 점진적 노출: 한 단계를 만족하면 바로 아래에 다음 단계가 자동으로 나타난다.
  // 완료된 단계는 위에 그대로 쌓여 보이고, 별도의 다음/이전 버튼은 없다.
  var DOT_BASE = "rounded-full border px-2.5 py-1";
  function revealSteps() {
    var order = visibleSteps();

    // 노출 목록에 없는 스텝(CDN의 ④)은 숨기고 인디케이터도 흐리게
    document.querySelectorAll("[data-step]").forEach(function (sec) {
      if (order.indexOf(Number(sec.getAttribute("data-step"))) < 0) sec.hidden = true;
    });
    document.querySelectorAll("[data-step-dot]").forEach(function (dot) {
      if (order.indexOf(Number(dot.getAttribute("data-step-dot"))) < 0) {
        dot.className = DOT_BASE + " border-border opacity-40";
      }
    });

    var reveal = true;      // 첫 스텝은 항상 노출
    var currentMarked = false;
    order.forEach(function (s) {
      var sec = document.querySelector('[data-step="' + s + '"]');
      if (sec) sec.hidden = !reveal;

      var valid = stepValid(s);
      var dot = document.querySelector('[data-step-dot="' + s + '"]');
      if (dot) {
        if (reveal && valid) dot.className = DOT_BASE + " border-sky bg-sky text-white";       // 완료
        else if (reveal && !currentMarked) { dot.className = DOT_BASE + " border-primary text-primary"; currentMarked = true; } // 현재
        else dot.className = DOT_BASE + " border-border text-muted-foreground";                 // 대기
      }
      if (s === 6 && reveal) renderReview();

      reveal = reveal && valid; // 이 스텝을 만족해야 다음 스텝이 노출된다
    });
  }

  // ── 진행률 표시 공통 ────────────────────────────────────────────────────
  function updateMini(targets) {
    var mini = document.getElementById("prov-mini-body");
    if (!mini) return;
    var done = 0, failed = 0;
    targets.forEach(function (t) { if (t.status === "done") done++; else if (t.status === "failed") failed++; });
    var running = targets.length - done - failed;
    mini.innerHTML =
      '<div class="text-sm font-semibold">생성 ' + (done + failed) + "/" + targets.length + " ▴</div>" +
      '<div class="mt-2 flex gap-3 text-xs text-muted-foreground">' +
      '<span class="flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-primary"></span>완료 ' + done + "</span>" +
      '<span class="flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-sky"></span>진행 ' + running + "</span>" +
      '<span class="flex items-center gap-1"><span class="h-2 w-2 rounded-full" style="background:#c0392b"></span>실패 ' + failed + "</span></div>";
  }

  // ── 프로비저닝 요청 조립(§10.3 common_spec/provider_spec) ─────────────────
  function buildCommonSpec() {
    var cs = state.commonSpec || {};
    var kind = state.resourceKind;
    if (kind === "cdn") {
      // CDN엔 공통 이름 입력 스텝이 없다 — CloudFront의 name은 콘솔 표시용 comment일
      // 뿐이라(app/aws_cloudfront_provisioning.py) 사용자 입력 없이 자동 생성한다.
      return { name: "cdn-" + Math.random().toString(36).slice(2, 8), tags: {} };
    }
    var rawName = (cs.name || "").replace(/^mcp-/, ""); // 백엔드가 mcp- 접두사를 다시 붙인다
    var common = { name: rawName, tags: cs.tags || {} };
    // inbound_rules는 compute(ComputeCommonSpec)에만 있는 필드 — 다른 종류(S3/RDS/CloudFront)의
    // common_spec 모델은 extra="forbid"라 이 키를 보내면 그 자체로 422가 난다.
    if (kind === "compute") {
      common.inbound_rules = (cs.inboundRules || []).map(function (r) { return { port: r.port, cidr: r.cidr }; });
    }
    return common;
  }
  function buildProviderSpec(p) {
    var ps = state.providerSpec[p] || {};
    var kind = state.resourceKind;

    if (kind === "storage_object") {
      if (p === "aws") return { region: ps.region }; // app/aws_s3_provisioning.py
      if (p === "azure") {
        // app/azure_storage_provisioning.py — VNet/NSG가 없어 재사용 가능한 건 리소스 그룹뿐(2026-09-17).
        var azStorage = { region: ps.region };
        if (ps.existingResourceGroupName) azStorage.existing_resource_group_name = ps.existingResourceGroupName;
        return azStorage;
      }
      if (p === "gcp") return { region: ps.region, storage_class: ps.storageClass }; // app/gcp_storage_provisioning.py
      return {};
    }
    if (kind === "cdn") {
      if (p === "aws") return { origin_domain_name: ps.origin }; // app/aws_cloudfront_provisioning.py
      if (p === "azure") {
        // app/azure_cdn_provisioning.py(Front Door Standard) — resourceGroup은 그대로 새 리소스
        // 그룹 이름으로 쓰인다(다른 Azure 러너처럼 서버 자동생성이 아님, 2026-09-15 결정).
        return {
          origin: ps.origin,
          resource_group: ps.resourceGroup,
          use_existing_resource_group: !!ps.useExistingResourceGroup,
          sku: ps.sku,
          query_string_caching_behavior: AZURE_CDN_QUERY_STRING_CODE[ps.queryStringCaching] || "IgnoreQueryString",
          protocol: AZURE_CDN_PROTOCOL_CODE[ps.supportedProtocols] || "http_and_https",
          health_probe_path: ps.healthProbePath || "/",
          health_probe_interval_seconds: ps.healthProbeIntervalSec ? Number(ps.healthProbeIntervalSec) : 240,
          compression: !!ps.compression,
          https_redirect: !!ps.httpsRedirect,
        };
      }

      if (p === "gcp") {
        // app/gcp_cdn_provisioning.py — createBucket 체크(기본 true)면 서버가 CDN 전용 버킷을
        // 자동 생성한다(backend_bucket_name 불필요). 체크 해제 시에만 기존 버킷 이름 +
        // 공개 전환 동의를 함께 보낸다.
        var spec = { lb_stack_ack: ps.lbStackAck === true, create_bucket: ps.createBucket !== false };
        if (!spec.create_bucket) {
          spec.backend_bucket_name = ps.backendBucketName;
          spec.existing_bucket_public_ack = ps.existingBucketPublicAck === true;
        }
        return spec;
      }
      return {};
    }
    // 기존 리소스 재사용 필드(2026-09-17) — "기존 리소스 사용" 토글이 체크돼 있을 때만, 값이
    // 채워진 것만 넣는다(비운 필드는 안 보내 서버 기본 동작=자동생성을 그대로 둔다).
    var useExisting = !!(state.commonSpec && state.commonSpec.useExistingNetwork);
    function addIfFilled(target, targetKey, value) { if (useExisting && value) target[targetKey] = value; }

    if (kind === "db") {
      if (p === "aws") {
        // app/aws_rds_provisioning.py — master_username은 서버가 mcp_admin으로 고정, 안 받는다.
        var awsDb = { region: ps.region, engine: DB_ENGINE_CODE[ps.engine], master_password: ps.masterPassword };
        addIfFilled(awsDb, "vpc_id", ps.vpcId);
        addIfFilled(awsDb, "security_group_id", ps.securityGroupId);
        return awsDb;
      }
      if (p === "azure") {
        // app/azure_database_provisioning.py — Azure만 마스터 사용자명도 사용자 입력으로 받는다
        // (엔진 라벨은 "MySQL"/"PostgreSQL"/"SQL Server" 그대로 보낸다 — 백엔드 Literal과 동일 어휘).
        var azureDb = {
          region: ps.region, engine: ps.engine,
          master_username: ps.masterUsername, master_password: ps.masterPassword,
        };
        addIfFilled(azureDb, "existing_resource_group_name", ps.existingResourceGroupName);
        addIfFilled(azureDb, "existing_vnet_id", ps.existingVnetId);
        return azureDb;
      }
      if (p === "gcp") {
        // app/gcp_cloudsql_provisioning.py — aws처럼 비밀번호만 입력받는다(관리자 계정명은 엔진별
        // 서버 고정값 root/postgres/sqlserver). 엔진 라벨도 그대로 보낸다(_ENGINE_CONFIG 키와 동일).
        var gcpDb = { region: ps.region, engine: ps.engine, master_password: ps.masterPassword };
        addIfFilled(gcpDb, "network", ps.network);
        return gcpDb;
      }
      return {};
    }

    // compute
    if (p === "aws") {
      var aws = { region: ps.region, instance_type: ps.instanceType };
      if (ps.image === AMI_CUSTOM && ps.amiId) {
        aws.ami_id = ps.amiId; // 직접 입력한 AMI만 전송(image는 안 보냄 — 서버가 ami_id를 우선함)
      } else if (ps.image) {
        aws.image = ps.image; // "Amazon Linux 2023"/"Ubuntu 22.04" — app/aws_provisioning.py의 IMAGE_FAMILIES가 실제 AMI로 변환
      }
      addIfFilled(aws, "vpc_id", ps.vpcId);
      addIfFilled(aws, "subnet_id", ps.subnetId);
      addIfFilled(aws, "security_group_id", ps.securityGroupId);
      return aws;
    }
    if (p === "gcp") {
      var gcp = { region: ps.region, instance_type: ps.instanceType };
      if (ps.image) gcp.image = ps.image; // "Debian 12"/"Ubuntu 22.04" — app/gcp_provisioning.py의 IMAGE_FAMILIES가 실제 이미지로 변환
      addIfFilled(gcp, "network", ps.network);
      return gcp;
    }
    if (p === "azure") {
      // Azure provider_spec은 extra="forbid" — 정확히 이 필드만 보낸다. image는 값이 있을 때만
      // (빈 값이면 백엔드 기본값 Ubuntu 22.04).
      var az = {
        region: ps.region,
        instance_type: ps.instanceType,
        admin_username: ps.adminUsername,
        admin_password: ps.adminPassword,
        create_public_ip: true,
      };
      if (ps.image) az.image = ps.image;
      addIfFilled(az, "existing_resource_group_name", ps.existingResourceGroupName);
      addIfFilled(az, "existing_subnet_id", ps.existingSubnetId);
      addIfFilled(az, "existing_network_security_group_id", ps.existingNetworkSecurityGroupId);
      return az;
    }
    return {};
  }

  // ── 프로비저닝 실행(대상별로 실 API 또는 시뮬레이션) ───────────────────────
  // 백엔드 §10: POST /provisioning/{provider}/{service} 로 job을 만들고, 202(queued) 응답의
  // job id를 GET /provisioning/jobs/{id}로 폴링해 진행바를 갱신한다. 러너가 없는 (kind, platform)
  // 조합은 501이 뻔하므로 그 대상만 기존 진행률 애니메이션으로 대신한다(simulateTarget).
  //
  // 실행 엔진(runProvisioning)은 신규 생성('start')과 복원('restore')이 공유한다. 복원은
  // 다른 페이지에 갔다가 프로비저닝 화면으로 돌아왔을 때, localStorage에 살아있는 job의 라이브
  // 진행 UI(행·진행바·작은 창 + 폴링)를 다시 그린다. 새 run이 시작되면 세대(runGeneration)가
  // 바뀌어 이전 복원의 폴링·저장소 갱신이 조용히 무효화된다(스테일 타이머 충돌 방지).
  var runGeneration = 0;

  function runProvisioning(mode, targets, kind, common) {
    var list = document.getElementById("prov-progress-list");
    if (!list) return;

    var myGen = ++runGeneration;
    function isCurrent() { return myGen === runGeneration; }

    function rowHtml(t) {
      return '<div data-real-row="' + t.idx + '">' +
        '<div class="flex justify-between text-sm"><span>' + PLATFORM_LABEL[t.platform] + " · " + escHtml(t.account) +
        '</span><span data-real-status class="text-muted-foreground">대기</span></div>' +
        '<div class="mt-1 h-2 rounded-full bg-muted"><div data-real-bar class="h-2 rounded-full bg-sky" style="width:0%"></div></div>' +
        '<div data-real-msg class="mt-1 text-xs" hidden></div></div>';
    }
    list.innerHTML = targets.map(rowHtml).join("");
    updateMini(targets);

    // 진행 중인 real job(백엔드 job id를 가진 대상)을 localStorage에 기록해, 다른 페이지로
    // 이동해도(풀 리로드) 공통 셸(prov-tracker.js)이 작은 창과 폴링을 이어가게 한다.
    // 시뮬레이션 대상(러너 없는 azure/gcp CDN 등)은 job id가 없어 페이지 이동 시 유지 대상이 아니다.
    function syncTrackerStore() {
      if (!window.MCProvTracker || !isCurrent()) return;
      var realTargets = targets.filter(function (t) { return t.real && t.jobId; });
      if (!realTargets.length) return;
      var pending = realTargets.filter(function (t) { return t.status !== "done" && t.status !== "failed"; });
      if (!pending.length) { MCProvTracker.clear(); return; }
      MCProvTracker.save(realTargets.map(function (t) {
        return {
          id: t.jobId,
          platform: t.platform,
          service: t.service || (SERVICE_CODE[kind] && SERVICE_CODE[kind][t.platform]),
          account: t.account,
          status: t.status === "done" ? "success" : (t.status === "failed" ? "failed" : "running"),
        };
      }));
    }

    function setRow(t, status, progress, msg) {
      t.status = status;
      t.progress = progress;
      var row = list.querySelector('[data-real-row="' + t.idx + '"]');
      if (row) {
        var bar = row.querySelector("[data-real-bar]");
        var st = row.querySelector("[data-real-status]");
        var m = row.querySelector("[data-real-msg]");
        // 진행 중에는 다음 갱신 간격에 맞춰 width를 부드럽게 잇고(폴링 2초 / 시뮬 tickMs),
        // 완료·실패로 종결될 때는 즉시 스냅한다(문제 1).
        bar.style.transition = status === "running"
          ? "width " + (t.real ? "1.95s" : (t.tickMs / 1000 + 0.05).toFixed(2) + "s") + " linear"
          : "width .3s ease-out";
        bar.style.width = progress + "%";
        if (status === "failed") {
          bar.className = "h-2 rounded-full"; bar.style.background = "#c0392b";
          st.textContent = "실패"; st.className = "rounded px-1.5 py-0.5 text-xs text-white"; st.style.background = "#c0392b";
          // msg가 에러 객체면 MCErr 패널(증상·해결책 + 접기 상세), 문자열이면 간단 텍스트
          // (취소·러너 없는 시뮬레이션 등 실제 오류가 아닌 경우).
          if (msg && typeof msg === "object") {
            m.style.color = ""; MCErr.renderInto(m, msg);
          } else {
            m.hidden = false; m.style.color = "#c0392b"; m.innerHTML = ""; m.textContent = msg || "실패";
          }
        } else if (status === "done") {
          bar.className = "h-2 rounded-full bg-primary"; st.textContent = "완료 100%"; st.className = "text-sm text-primary";
        } else {
          // 진행 중: 채워진 바에 "지렁이 효과"(흐르는 하이라이트 띠)를 붙인다. 90%에서 멈춰도
          // 애니메이션이 계속 돌아 작업이 살아있음을 보여준다(완료·실패 분기에서 className이
          // 재설정되며 자동으로 제거됨). 진행률 값·폴링 로직은 그대로다.
          bar.className = "h-2 rounded-full bg-sky prov-bar--active";
          st.textContent = "진행중 " + progress + "%";
        }
      }
      updateMini(targets);
      syncTrackerStore();
    }

    function pollTarget(t, jobId) {
      MCPApi.request("/provisioning/jobs/" + jobId)
        .then(function (job) {
          if (!isCurrent()) return; // 새 run이 시작됐으면 이 스테일 폴링은 조용히 멈춘다.
          if (job.status === "success") { setRow(t, "done", 100, null); return; }
          if (job.status === "failed") { setRow(t, "failed", t.progress, job.error || "생성에 실패했습니다."); return; }
          if (job.status === "cancelled") { setRow(t, "failed", t.progress, "취소되었습니다."); return; }
          // queued/running — job별 증가 속도(step)+소폭 지터로 진행 연출(90% 이하에서 대기).
          setRow(t, "running", Math.min(90, (t.progress || t.startAt) + t.step + Math.floor(Math.random() * 4)));
          setTimeout(function () { pollTarget(t, jobId); }, 2000);
        })
        .catch(function (err) { if (isCurrent()) setRow(t, "failed", t.progress, err); });
    }

    // 러너가 없는 조합(azure/gcp의 CDN)은 진행률만 애니메이션한다 — 실패 시점은 랜덤.
    function simulateTarget(t) {
      var willFail = Math.random() < 0.25;
      var failAt = willFail ? 35 + Math.floor(Math.random() * 45) : null;
      var timer = setInterval(function () {
        if (!isCurrent()) { clearInterval(timer); return; }
        t.progress += t.step + Math.floor(Math.random() * 6);
        if (failAt != null && t.progress >= failAt) {
          clearInterval(timer);
          setRow(t, "failed", failAt, "할당량 초과 — 해당 리전의 한도를 넘었습니다.");
        } else if (t.progress >= 100) {
          clearInterval(timer);
          setRow(t, "done", 100, null);
        } else {
          setRow(t, "running", t.progress);
        }
      }, t.tickMs);
    }

    targets.forEach(function (t) {
      if (mode === "restore") {
        // 저장된 종결 상태는 즉시 그리고, 진행 중이면 저장된 job id로 폴링을 재개한다.
        if (t.status === "done") { setRow(t, "done", 100, null); return; }
        if (t.status === "failed") { setRow(t, "failed", t.progress || 0, t.errorMsg || "생성에 실패했습니다."); return; }
        setRow(t, "running", t.progress || t.startAt);
        if (t.real && t.jobId) pollTarget(t, t.jobId);
        return;
      }
      // mode === "start"
      setRow(t, "running", t.startAt);
      if (!t.real) { simulateTarget(t); return; }
      MCPApi.request("/provisioning/" + t.platform + "/" + SERVICE_CODE[kind][t.platform], {
        method: "POST",
        headers: { "Idempotency-Key": newIdemKey(), "X-Action-Confirmed": "true" },
        body: { credential_id: t.credentialId, common_spec: common, provider_spec: buildProviderSpec(t.platform) },
      })
        .then(function (data) { if (!isCurrent()) return; t.jobId = data.id; syncTrackerStore(); pollTarget(t, data.id); })
        .catch(function (err) { if (isCurrent()) setRow(t, "failed", t.progress, err); });
    });
  }

  function startProvisioning() {
    var list = document.getElementById("prov-progress-list");
    if (!list) return;
    var creds = (state.selectedCredentials || []).filter(function (c) {
      return state.platforms.indexOf(c.provider) >= 0 && c.credentialId;
    });
    if (!creds.length) {
      list.innerHTML = '<p class="text-sm" style="color:#c0392b">선택된 자격 증명이 없습니다 — ② 계정에서 선택하세요.</p>';
      return;
    }

    var kind = state.resourceKind;
    var common = buildCommonSpec();
    var targets = creds.map(function (c, i) {
      return {
        idx: i, platform: c.provider, account: c.label, credentialId: c.credentialId,
        progress: 0, status: "pending", real: hasRealRunner(kind, c.provider),
        // job별로 다른 시작 오프셋·증가 속도·타이밍 — 여러 개를 동시에 돌려도 진행률이
        // 제각각 다르게 올라가도록(문제 2). 실제 완료 응답이 오면 즉시 100%로 스냅한다.
        startAt: 5 + Math.floor(Math.random() * 10),   // 5~14%
        step: 4 + Math.floor(Math.random() * 7),       // 4~10%씩
        tickMs: 380 + Math.floor(Math.random() * 240), // 시뮬레이션 job별 간격
      };
    });
    runProvisioning("start", targets, kind, common);
  }

  // 다른 페이지에 갔다가 프로비저닝 화면으로 돌아왔을 때, localStorage에 살아있는 job이 있으면
  // 라이브 진행 UI(행·진행바 + 작은 창)를 복원하고 폴링을 재개한다. 새 run(생성하기)이 아직
  // 시작되지 않은 초기 로드에서만 동작한다.
  function restoreProvisioning() {
    if (!document.getElementById("prov-progress-list")) return;
    if (!window.MCProvTracker) return;
    var store = MCProvTracker.read();
    if (!store || !store.jobs || !store.jobs.length) return;

    var targets = store.jobs.map(function (j, i) {
      var st = j.status === "success" ? "done"
             : (j.status === "failed" || j.status === "cancelled") ? "failed" : "running";
      return {
        idx: i, platform: j.platform, account: j.account || "", credentialId: null,
        real: true, jobId: j.id, service: j.service,
        status: st,
        progress: st === "done" ? 100 : (st === "failed" ? 0 : 30), // 진행 중이면 중간값에서 이어붙인다.
        errorMsg: j.status === "cancelled" ? "취소되었습니다." : "생성에 실패했습니다.",
        startAt: 30, step: 4 + Math.floor(Math.random() * 7), tickMs: 500,
      };
    });
    runProvisioning("restore", targets, state.resourceKind, null);

    // 돌아온 맥락이므로 큰 모달을 자동으로 열지 않고 축소형 카드(작은 창)로 복원한다.
    var anyRunning = targets.some(function (t) { return t.status === "running"; });
    var mini = document.getElementById("prov-progress-mini");
    if (mini && anyRunning) mini.classList.remove("hidden");
  }

  // ── 이벤트 배선 ──────────────────────────────────────────────────────────
  function init() {
    var container = document.getElementById("prov-common-fields");
    if (!container) return; // provisioning 화면이 아니면 무시
    var providerC = document.getElementById("prov-provider-fields");

    // ④ 공통 필드: 입력 변화 → 상태 수집 + 제출 상태 + 다음 단계 노출 갱신
    function onFieldChange() { collect(); updateSubmitState(); revealSteps(); }
    container.addEventListener("input", onFieldChange);
    container.addEventListener("change", onFieldChange);
    // 이름 필드: 입력은 그대로 두고(막지 않음), 검사는 필드를 벗어날 때(blur=focusout)나 Enter 시점에.
    // 입력하는 동안에는 이전 오류 표시를 지운다(고치는 중에 빨간 줄이 남지 않도록).
    container.addEventListener("input", function (e) {
      if (e.target && e.target.matches && e.target.matches('[data-cs="name"]')) clearNameError(e.target);
    });
    container.addEventListener("focusout", function (e) {
      if (e.target && e.target.matches && e.target.matches('[data-cs="name"]')) showNameError(e.target);
    });
    container.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && e.target && e.target.matches && e.target.matches('[data-cs="name"]')) {
        e.preventDefault(); // 이름 필드에서 Enter로 인한 예기치 않은 제출/줄바꿈 방지
        showNameError(e.target);
      }
    });
    // 동적 행 삭제(위임) — 컨테이너에 1회만 배선(재렌더 시 누적 방지)
    container.addEventListener("click", function (e) {
      var del = e.target.closest("[data-row-del]");
      if (del) { del.parentElement.remove(); onFieldChange(); }
    });

    // ⑤ 추가 필드: 입력 변화 + AWS 이미지 토글 + 인바운드 커스텀 행 삭제
    if (providerC) {
      providerC.addEventListener("input", onFieldChange);
      providerC.addEventListener("change", function (e) {
        var imgSel = e.target.closest("[data-image-select]");
        if (imgSel) toggleAmi(imgSel);
        var bucketCb = e.target.closest("[data-gcp-create-bucket]");
        if (bucketCb) toggleGcpBucketMode(bucketCb);
        onFieldChange();
      });
      providerC.addEventListener("click", function (e) {
        var del = e.target.closest("[data-row-del]");
        if (del) { del.parentElement.remove(); onFieldChange(); }
        var detailToggle = e.target.closest("[data-gcp-bucket-detail-toggle]");
        if (detailToggle) toggleGcpBucketDetail(detailToggle);
        // ⑤에 있는 "실제 목록 불러오기"(현재는 Storage의 Azure 기존 리소스 그룹만 해당) —
        // ④의 networkFieldEl과 같은 패턴이지만 컨테이너가 달라 여기서 따로 배선한다.
        var fetchBtn = e.target.closest("[data-fetch-existing]");
        if (fetchBtn) {
          var p = fetchBtn.getAttribute("data-fetch-existing");
          var box = fetchBtn.closest('[data-existing-field-wrap="' + p + '"]');
          fetchExistingResources(state.resourceKind, p, box);
        }
      });
    }

    // 플랫폼/리소스 종류 변경 → 재렌더 + 선택 시각 피드백 + 다음 단계 노출
    document.querySelectorAll("[data-prov-platform]").forEach(function (cb) {
      // 플랫폼이 바뀌면 ② 계정 칩(해당 플랫폼 자격 증명)도 다시 그린다.
      cb.addEventListener("change", function () { renderAccounts(); renderSteps(); syncSelectionUI(); revealSteps(); });
    });
    document.querySelectorAll("[data-prov-kind]").forEach(function (rb) {
      rb.addEventListener("change", function () { renderSteps(); syncSelectionUI(); revealSteps(); });
    });

    // 생성 확인 모달의 "생성 확인" → 확인 모달 닫고 진행률 모달 열기(+시뮬레이션은 단위 6)
    var confirmBtn = document.getElementById("prov-confirm-create");
    if (confirmBtn) {
      confirmBtn.addEventListener("click", function () {
        if (window.MCPModal) {
          MCPModal.close("#prov-confirm-modal");
          MCPModal.open("#prov-progress-modal");
        }
        startProvisioning();
      });
    }

    // ② 계정 칩은 동적 렌더라 컨테이너에 위임 배선한다(정적 바인딩 불가).
    var accList = document.getElementById("prov-account-list");
    if (accList) {
      accList.addEventListener("change", function () { onFieldChange(); syncSelectionUI(); revealSteps(); });
    }

    renderSteps();
    syncSelectionUI();
    revealSteps();

    // 다른 페이지에 갔다가 돌아온 경우, 진행 중인 job의 라이브 UI(작은 창 포함)를 복원한다.
    restoreProvisioning();

    // 실제 등록된 자격 증명을 불러와 ② 계정 스텝을 채운다(로그인 세션 필요 — auth-guard가 보장).
    loadAccountCredentials().then(function () { renderAccounts(); syncSelectionUI(); revealSteps(); });
  }

  // 후속 단위에서 재사용할 수 있도록 최소 API 노출
  // AZURE_LIGHT_FREE_ALT도 함께 노출한다 — Azure 무료 SKU 매핑 회귀 테스트(frontend/assets/js/tests/
  // spec_tiers.test.js)가 B2pts_v2(ARM64, 이미지 호환 미구현)가 아니라 B2ats_v2(x86-64)만
  // 무료 대안으로 노출되는지 런타임에서 직접 검증할 수 있게 하기 위함이다.
  window.PROV = {
    state: state, collect: collect, render: renderSteps, validate: validate,
    SPEC_TIERS: SPEC_TIERS, REGIONS: REGIONS, AZURE_LIGHT_FREE_ALT: AZURE_LIGHT_FREE_ALT,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
