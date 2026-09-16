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

  // 추상 사양 등급 → 플랫폼별 실제 SKU 매핑(코드 상수). 표준 등급은 가격 비교 모달과 일치.
  var SPEC_TIERS = [
    { key: "light", label: "경량 (1 vCPU · 2GB)", sku: { aws: "t3.micro", azure: "B1s", gcp: "e2-micro" } },
    { key: "standard", label: "표준 (2 vCPU · 4GB)", sku: { aws: "t3.medium", azure: "B2s", gcp: "e2-medium" } },
    { key: "high", label: "고성능 (4 vCPU · 8GB)", sku: { aws: "t3.large", azure: "B4ms", gcp: "e2-standard-4" } },
  ];

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
    // gcp: 이미지 필드는 구조가 달라 노출하지 않음(맵핑 문서 "제외").
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
      aws: { chars: "a-z0-9-", startLetter: false, max: 40,
        note: "소문자/숫자/하이픈(-)만 · 3~63자 · 전역에서 고유해야 함",
        doc: { label: "AWS S3 버킷 명명 규칙", url: "https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html" } },
      azure: { chars: "a-z0-9", startLetter: false, max: 21,
        note: "하이픈 없이 영소문자/숫자만 · 원래 3~24자 규칙에 접두사(mcp) 여유를 둬 최대 21자까지 입력 가능",
        doc: { label: "Azure Storage 계정 명명 규칙", url: "https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-name-rules" } },
      gcp: { chars: "a-z0-9._-", startLetter: false, max: 63,
        note: "소문자/숫자/하이픈(-)/밑줄(_)/점(.)만 · 3~63자 · 전역에서 고유해야 함",
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

    // 3) 사양(추상 등급) — providerSpec의 실제 SKU는 collect 시 매핑
    var specField = el("div");
    var specOpts = '<option value="">선택하세요</option>' + SPEC_TIERS.map(function (t) {
      return '<option value="' + t.key + '">' + t.label + "</option>";
    }).join("");
    specField.innerHTML = labelHtml("사양", true) +
      '<select data-cs="specTier" class="' + FIELD_INPUT + '">' + specOpts + "</select>";
    container.appendChild(specField);

    // 2) 리전 — 국가 단일 선택(플랫폼 무관, collect에서 각 플랫폼 리전으로 매핑)
    container.appendChild(countryFieldEl());

    // 4) 네트워크 — 읽기 전용 안내 + 비활성 토글 자리
    var netField = el("div", { class: "sm:col-span-2" });
    netField.innerHTML = labelHtml("네트워크") +
      '<div class="flex items-center justify-between rounded-lg border border-dashed border-border bg-muted px-3 py-2 text-sm text-muted-foreground">' +
      "<span>새 VPC/Subnet 자동 생성</span>" +
      '<label class="flex cursor-not-allowed items-center gap-2 opacity-50"><input type="checkbox" disabled /> 기존 리소스 사용</label>' +
      "</div>";
    container.appendChild(netField);

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
      html += "<div>" + labelHtml("루트 비밀번호", true) +
        '<input type="password" data-ps-platform="' + p + '" data-ps="masterPassword" class="' + FIELD_INPUT + '" />' +
        '<p class="mt-1 text-xs text-muted-foreground">' + PLATFORM_LABEL[p] + '는 비밀번호만 입력합니다(사용자명은 자동 지정).</p></div>';
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
        cs.network = "auto"; // 항상 자동 생성 고정
      }
      if (kind === "db") cs.backup = "auto"; // 자동 백업 고정(표시만)
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
      if (tier) ps.instanceType = tier.sku[p];
      // 국가 → 플랫폼 리전 매핑(공통 설정의 국가 하나로 각 플랫폼 리전 결정)
      if (cs.country && COUNTRY_REGION[cs.country]) ps.region = COUNTRY_REGION[cs.country][p];
      // 제외 필드의 서버 기본값 병합(사용자 입력 없음)
      var defs = SERVER_DEFAULTS[kind];
      if (defs) Object.keys(defs).forEach(function (k) { if (ps[k] === undefined) ps[k] = defs[k]; });
    });
  }

  // ── CDN 입력 폼 빌더 ──────────────────────────────────────────────────────
  function cdnText(p, key, label, req, ph, val) {
    return "<div>" + labelHtml(label, req) +
      '<input type="text" data-ps-platform="' + p + '" data-ps="' + key + '" placeholder="' + (ph || "") +
      '" value="' + (val || "") + '" class="' + FIELD_INPUT + '" /></div>';
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
          cdnText("aws", "origin", "Origin", true, "example.s3.ap-northeast-2.amazonaws.com") +
          cdnSelect("aws", "cachePolicy", "캐시 정책", CDN_OPTS.awsCachePolicy) +
          cdnSelect("aws", "pathRouting", "Path routing", CDN_OPTS.awsPathRouting) +
          cdnSelect("aws", "viewerProtocolPolicy", "Viewer Protocol Policy", CDN_OPTS.awsViewerProtocol) +
          cdnSelect("aws", "priceClass", "Price Class", CDN_OPTS.awsPriceClass) +
          "</div>" +
          cdnToggle("aws", "compression", "Compression", true);
      } else if (p === "azure") {
        html += '<div class="grid gap-3 sm:grid-cols-2">' +
          cdnText("azure", "origin", "Origin", true) +
          cdnText("azure", "resourceGroup", "Resource Group", true) +
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
          '<div data-gcp-existing-bucket-wrap hidden class="grid gap-3 sm:grid-cols-2">' +
          cdnText("gcp", "backendBucketName", "연결할 기존 버킷 이름", true, "my-existing-bucket") +
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
        if (!COMPUTE_IMAGES[p]) return; // GCP 등 이미지 필드 미노출
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
      if (platforms.indexOf("gcp") >= 0) {
        container.appendChild(el("div", { class: "text-xs text-muted-foreground" },
          "GCP는 이미지 설정이 별도 입력 없이 기본값으로 처리됩니다."));
      }
      // 인바운드 규칙(공통에서 이동) + 인증(플랫폼별)
      container.appendChild(inboundFieldEl());
      platforms.forEach(function (p) { container.appendChild(computeAuthEl(p)); });
      wireDynamicRows(container); // 인바운드 "규칙 추가" 버튼 배선
      return;
    }

    if (kind === "storage_object") {
      if (platforms.indexOf("gcp") >= 0) {
        var opts2 = '<option value="">선택하세요</option>' +
          STORAGE_CLASSES.map(function (o) { return "<option>" + o + "</option>"; }).join("");
        var box2 = el("div", { class: "rounded-xl bg-muted p-3" },
          '<p class="mb-2 text-sm font-medium">GCP 스토리지 등급</p>' +
          '<select data-ps-platform="gcp" data-ps="storageClass" class="' + FIELD_INPUT + '">' + opts2 + "</select>");
        container.appendChild(box2);
      } else {
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
    box.innerHTML =
      "<div>플랫폼: <b>" + (state.platforms.join(", ") || "-") + "</b></div>" +
      "<div>계정: <b>" + accounts + "</b></div>" +
      "<div>종류: <b>" + (KIND_LABEL[state.resourceKind] || "-") + "</b></div>" +
      (cs.country ? "<div>리전(국가): <b>" + cs.country + "</b></div>" : "");
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
      if (p === "azure") return { region: ps.region }; // app/azure_storage_provisioning.py
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
    if (kind === "db") {
      if (p === "aws") {
        // app/aws_rds_provisioning.py — master_username은 서버가 mcp_admin으로 고정, 안 받는다.
        return { region: ps.region, engine: DB_ENGINE_CODE[ps.engine], master_password: ps.masterPassword };
      }
      if (p === "azure") {
        // app/azure_database_provisioning.py — Azure만 마스터 사용자명도 사용자 입력으로 받는다
        // (엔진 라벨은 "MySQL"/"PostgreSQL"/"SQL Server" 그대로 보낸다 — 백엔드 Literal과 동일 어휘).
        return {
          region: ps.region, engine: ps.engine,
          master_username: ps.masterUsername, master_password: ps.masterPassword,
        };
      }
      if (p === "gcp") {
        // app/gcp_cloudsql_provisioning.py — aws처럼 비밀번호만 입력받는다(관리자 계정명은 엔진별
        // 서버 고정값 root/postgres/sqlserver). 엔진 라벨도 그대로 보낸다(_ENGINE_CONFIG 키와 동일).
        return { region: ps.region, engine: ps.engine, master_password: ps.masterPassword };
      }
      return {};
    }

    // compute
    if (p === "aws") {
      var aws = { region: ps.region, instance_type: ps.instanceType };
      if (ps.image === AMI_CUSTOM && ps.amiId) aws.ami_id = ps.amiId; // 직접 입력한 AMI만 전송
      return aws;
    }
    if (p === "gcp") return { region: ps.region, instance_type: ps.instanceType };
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
      return az;
    }
    return {};
  }

  // ── 프로비저닝 실행(대상별로 실 API 또는 시뮬레이션) ───────────────────────
  // 백엔드 §10: POST /provisioning/{provider}/{service} 로 job을 만들고, 202(queued) 응답의
  // job id를 GET /provisioning/jobs/{id}로 폴링해 진행바를 갱신한다. 러너가 없는 (kind, platform)
  // 조합은 501이 뻔하므로 그 대상만 기존 진행률 애니메이션으로 대신한다(simulateTarget).
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
      };
    });

    function rowHtml(t) {
      return '<div data-real-row="' + t.idx + '">' +
        '<div class="flex justify-between text-sm"><span>' + PLATFORM_LABEL[t.platform] + " · " + escHtml(t.account) +
        '</span><span data-real-status class="text-muted-foreground">대기</span></div>' +
        '<div class="mt-1 h-2 rounded-full bg-muted"><div data-real-bar class="h-2 rounded-full bg-sky" style="width:0%"></div></div>' +
        '<div data-real-msg class="mt-1 text-xs" hidden></div></div>';
    }
    list.innerHTML = targets.map(rowHtml).join("");
    updateMini(targets);

    function setRow(t, status, progress, msg) {
      t.status = status;
      t.progress = progress;
      var row = list.querySelector('[data-real-row="' + t.idx + '"]');
      if (row) {
        var bar = row.querySelector("[data-real-bar]");
        var st = row.querySelector("[data-real-status]");
        var m = row.querySelector("[data-real-msg]");
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
          st.textContent = "진행중 " + progress + "%";
        }
      }
      updateMini(targets);
    }

    function pollTarget(t, jobId) {
      MCPApi.request("/provisioning/jobs/" + jobId)
        .then(function (job) {
          if (job.status === "success") { setRow(t, "done", 100, null); return; }
          if (job.status === "failed") { setRow(t, "failed", t.progress, job.error || "생성에 실패했습니다."); return; }
          if (job.status === "cancelled") { setRow(t, "failed", t.progress, "취소되었습니다."); return; }
          setRow(t, "running", Math.min(90, (t.progress || 8) + 7)); // queued/running — 창가 진행 연출
          setTimeout(function () { pollTarget(t, jobId); }, 2000);
        })
        .catch(function (err) { setRow(t, "failed", t.progress, err); });
    }

    // 러너가 없는 조합(azure/gcp의 CDN)은 진행률만 애니메이션한다 — 실패 시점은 랜덤.
    function simulateTarget(t) {
      var willFail = Math.random() < 0.25;
      var failAt = willFail ? 35 + Math.floor(Math.random() * 45) : null;
      var timer = setInterval(function () {
        t.progress += 4 + Math.floor(Math.random() * 9);
        if (failAt != null && t.progress >= failAt) {
          clearInterval(timer);
          setRow(t, "failed", failAt, "할당량 초과 — 해당 리전의 한도를 넘었습니다.");
        } else if (t.progress >= 100) {
          clearInterval(timer);
          setRow(t, "done", 100, null);
        } else {
          setRow(t, "running", t.progress);
        }
      }, 450);
    }

    targets.forEach(function (t) {
      setRow(t, "running", 8);
      if (!t.real) { simulateTarget(t); return; }
      MCPApi.request("/provisioning/" + t.platform + "/" + SERVICE_CODE[kind][t.platform], {
        method: "POST",
        headers: { "Idempotency-Key": newIdemKey(), "X-Action-Confirmed": "true" },
        body: { credential_id: t.credentialId, common_spec: common, provider_spec: buildProviderSpec(t.platform) },
      })
        .then(function (data) { pollTarget(t, data.id); })
        .catch(function (err) { setRow(t, "failed", t.progress, err); });
    });
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

    // 실제 등록된 자격 증명을 불러와 ② 계정 스텝을 채운다(로그인 세션 필요 — auth-guard가 보장).
    loadAccountCredentials().then(function () { renderAccounts(); syncSelectionUI(); revealSteps(); });
  }

  // 후속 단위에서 재사용할 수 있도록 최소 API 노출
  window.PROV = { state: state, collect: collect, render: renderSteps, validate: validate, SPEC_TIERS: SPEC_TIERS, REGIONS: REGIONS };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
