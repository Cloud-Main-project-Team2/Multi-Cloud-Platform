/* PROV-01 프로비저닝 설정값 실입력 (순수 프론트엔드 — 서버 통신 없음).
 *
 * 이 단계(단위 1)에서는 ③ 리소스 종류=Compute일 때 ④ 공통 설정 스텝의 입력 필드를
 * 선택된 플랫폼에 맞춰 동적으로 렌더링하고, 입력값을 전역 상태(provisioningSpec)에
 * 반영한다. DB/Storage(단위 2), ⑤ 추가 설정(단위 3), 생성하기 활성화 검증(단위 4)은
 * 후속 커밋에서 붙인다.
 *
 * 필드명은 향후 BE 연동 시 01_API_명세서_v1.1.md §10.3의 common_spec/provider_spec 구조로
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
  var INBOUND_PRESETS = [
    { label: "SSH (22)", port: 22 },
    { label: "HTTP (80)", port: 80 },
    { label: "HTTPS (443)", port: 443 },
  ];
  var DEFAULT_CIDR = "0.0.0.0/0";

  var PLATFORM_LABEL = { aws: "AWS", azure: "Azure", gcp: "GCP" };

  // 실 API 연동: (리소스 종류, 플랫폼)별로 백엔드 러너가 있는 조합만 실제 job을 만든다(§10).
  // aws는 ec2/s3/cloudfront/rds 네 조합 다 구현됐고, azure/gcp는 compute(vm/compute_engine)만
  // 있다 — 러너 없는 조합(azure/gcp의 DB·Storage·CDN)은 501이 뻔하므로 대상별로 기존 진행률
  // 시뮬레이션을 대신 돌린다(startProvisioning()의 simulateTarget 참고).
  var SERVICE_CODE = {
    compute: { aws: "ec2", azure: "vm", gcp: "compute_engine" },
    storage_object: { aws: "s3" },
    cdn: { aws: "cloudfront" },
    db: { aws: "rds" },
  };
  function hasRealRunner(kind, platform) {
    return !!(SERVICE_CODE[kind] && SERVICE_CODE[kind][platform]);
  }
  var PROV_ERROR_MESSAGES = {
    IDEMPOTENCY_KEY_REQUIRED: "요청 식별 키가 없습니다(내부 오류).",
    CONFIRMATION_REQUIRED: "확인이 필요한 작업입니다.",
    VALIDATION_ERROR: "입력값을 다시 확인해 주세요(이름 형식·리전·사양·인증).",
    SECRET_FIELD_NOT_ALLOWED: "입력값에 자격 증명으로 보이는 필드가 있습니다.",
    CREDENTIAL_NOT_FOUND: "자격 증명을 찾을 수 없습니다.",
    CLOUD_PERMISSION_DENIED: "이 자격 증명은 검증되지 않았거나 프로비저닝 권한이 없습니다 — 마이페이지에서 검증하세요.",
    PROVISIONING_NOT_IMPLEMENTED: "아직 지원하지 않는 조합입니다.",
    PROVIDER_AUTHENTICATION_FAILED: "클라우드 인증에 실패했습니다 — 자격 증명을 확인하세요.",
    AUTHENTICATION_REQUIRED: "로그인이 만료되었습니다. 다시 로그인해 주세요.",
    INVALID_TOKEN: "로그인이 만료되었습니다. 다시 로그인해 주세요.",
  };
  function provErrorMessage(err) {
    return (err && (PROV_ERROR_MESSAGES[err.code] || err.message)) || "요청 처리 중 오류가 발생했습니다.";
  }
  function escHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function newIdemKey() {
    return "prov-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);
  }

  // DB 엔진 옵션(플랫폼별로 다름). aws는 실제 백엔드 러너(RDS)가 mysql/postgres만 지원해서
  // 그 두 개만 노출한다(azure/gcp는 아직 시뮬레이션이라 더 넓은 목록을 유지).
  var DB_ENGINES = {
    aws: ["MySQL", "PostgreSQL"],
    azure: ["MySQL", "PostgreSQL", "SQL Server"],
    gcp: ["MySQL", "PostgreSQL", "SQL Server"],
  };
  // aws 실 API용 engine 값 매핑(표시 라벨 → provider_spec.engine).
  var DB_ENGINE_CODE = { MySQL: "mysql", PostgreSQL: "postgres" };
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
    azSku: ["Standard", "Premium"],
    azQueryString: ["전체 무시", "전체 사용", "지정 파라미터만"],
    azProtocols: ["HTTPS만", "HTTP+HTTPS"],
    gcpBackendType: ["백엔드 서비스", "백엔드 버킷"],
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
      '<div class="flex items-stretch rounded-lg border border-border bg-background focus-within:border-primary">' +
      '<span class="flex items-center px-3 text-sm text-muted-foreground">mcp-</span>' +
      '<input type="text" data-cs="name" data-prefix="mcp-" placeholder="web-01" class="w-full rounded-r-lg bg-transparent px-2 py-2 text-sm outline-none" />' +
      "</div>";
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
  function nameFieldEl(placeholder) {
    var f = el("div", { class: "sm:col-span-2" });
    f.innerHTML = labelHtml("이름", true) +
      '<div class="flex items-stretch rounded-lg border border-border bg-background focus-within:border-primary">' +
      '<span class="flex items-center px-3 text-sm text-muted-foreground">mcp-</span>' +
      '<input type="text" data-cs="name" data-prefix="mcp-" placeholder="' + (placeholder || "") +
      '" class="w-full rounded-r-lg bg-transparent px-2 py-2 text-sm outline-none" /></div>';
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
      inner += labelHtml("키 페어 이름", true) +
        '<input type="text" data-ps-platform="aws" data-ps="keyPairName" placeholder="mcp-keypair" class="' + FIELD_INPUT + '" />';
    } else if (p === "azure") {
      inner += '<div class="grid gap-2 sm:grid-cols-2">' +
        "<div>" + labelHtml("관리자 계정명", true) +
        '<input type="text" data-ps-platform="azure" data-ps="adminUsername" class="' + FIELD_INPUT + '" /></div>' +
        "<div>" + labelHtml("비밀번호", true) +
        '<input type="password" data-ps-platform="azure" data-ps="adminPassword" class="' + FIELD_INPUT + '" /></div></div>';
    } else if (p === "gcp") {
      inner += labelHtml("SSH 공개키", true) +
        '<textarea data-ps-platform="gcp" data-ps="sshPublicKey" rows="2" placeholder="ssh-rsa AAAA..." class="' + FIELD_INPUT + '"></textarea>';
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
    container.appendChild(nameFieldEl("db-01"));
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
      '<input type="text" data-cs="name" placeholder="my-unique-bucket" class="' + FIELD_INPUT + '" />' +
      '<p class="mt-1 text-xs text-muted-foreground">전역에서 고유한 이름이어야 합니다.</p>';
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
          cdnToggle("azure", "httpsRedirect", "HTTPS 리다이렉트", true);
      } else if (p === "gcp") {
        html += '<div class="grid gap-3 sm:grid-cols-2">' +
          cdnText("gcp", "backend", "Backend/Backend Bucket", true, "my-backend") +
          cdnSelect("gcp", "backendType", "백엔드 유형", CDN_OPTS.gcpBackendType, false, "data-gcp-backend-type") +
          cdnSelect("gcp", "cacheMode", "Cache Mode", CDN_OPTS.gcpCacheMode) +
          cdnSelect("gcp", "compression", "Compression", CDN_OPTS.gcpCompression) +
          "</div>" +
          '<label class="flex items-start gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm">' +
          '<input type="checkbox" data-ps-platform="gcp" data-ps="lbStackAck" class="mt-0.5" />' +
          "<span>GCP Cloud CDN은 외부 HTTP(S) 로드밸런서 스택이 필요합니다. 함께 구성에 동의합니다. " +
          '<span class="text-primary">*</span></span></label>' +
          cdnToggle("gcp", "enableCdn", "enableCdn", true) +
          cdnToggle("gcp", "httpsRedirect", "HTTPS 강제 리다이렉트", true) +
          // Health Probe는 백엔드 '서비스' 구성일 때만 노출(백엔드 버킷이면 숨김)
          '<div data-gcp-health-wrap class="grid gap-3 sm:grid-cols-2">' +
          cdnText("gcp", "healthProbePath", "Health Probe 경로", false, "", "/") +
          cdnNumber("gcp", "healthProbeIntervalSec", "Health Probe 간격(초)", false, 10) +
          "</div>";
      }
      box.innerHTML = html;
      container.appendChild(box);
    });
  }

  // GCP 백엔드 유형이 '백엔드 버킷'이면 Health Probe 필드를 숨긴다.
  function toggleGcpHealth(sel) {
    var boxEl = sel.closest(".rounded-xl");
    if (!boxEl) return;
    var wrap = boxEl.querySelector("[data-gcp-health-wrap]");
    if (wrap) wrap.hidden = sel.value !== "백엔드 서비스";
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

    if (kind === "cdn") {
      // 필수: AWS Origin / Azure Origin·Resource Group·SKU / GCP Backend·LB stack 동의
      return state.platforms.every(function (p) {
        var ps = state.providerSpec[p] || {};
        if (p === "aws") return isFilled(ps.origin);
        if (p === "azure") return isFilled(ps.origin) && isFilled(ps.resourceGroup) && isFilled(ps.sku);
        if (p === "gcp") return isFilled(ps.backend) && ps.lbStackAck === true;
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
        if (p === "aws" && !isFilled(ps.keyPairName)) return false;
        if (p === "azure" && (!isFilled(ps.adminUsername) || !isFilled(ps.adminPassword))) return false;
        if (p === "gcp" && !isFilled(ps.sshPublicKey)) return false;
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
        return isFilled((state.providerSpec[p] || {}).region);
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
      return {};
    }
    if (kind === "cdn") {
      if (p === "aws") return { origin_domain_name: ps.origin }; // app/aws_cloudfront_provisioning.py
      return {};
    }
    if (kind === "db") {
      if (p === "aws") {
        // app/aws_rds_provisioning.py — master_username은 서버가 mcp_admin으로 고정, 안 받는다.
        return { region: ps.region, engine: DB_ENGINE_CODE[ps.engine], master_password: ps.masterPassword };
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
        '<p data-real-msg class="mt-1 text-xs" hidden></p></div>';
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
          m.hidden = false; m.style.color = "#c0392b"; m.textContent = msg || "실패";
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
          if (job.status === "failed") { setRow(t, "failed", t.progress, (job.error && job.error.message) || "생성에 실패했습니다."); return; }
          if (job.status === "cancelled") { setRow(t, "failed", t.progress, "취소되었습니다."); return; }
          setRow(t, "running", Math.min(90, (t.progress || 8) + 7)); // queued/running — 창가 진행 연출
          setTimeout(function () { pollTarget(t, jobId); }, 2000);
        })
        .catch(function (err) { setRow(t, "failed", t.progress, provErrorMessage(err)); });
    }

    // 러너가 없는 조합(azure/gcp의 DB·Storage·CDN)은 진행률만 애니메이션한다 — 실패 시점은 랜덤.
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
        .catch(function (err) { setRow(t, "failed", t.progress, provErrorMessage(err)); });
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
        var btSel = e.target.closest("[data-gcp-backend-type]");
        if (btSel) toggleGcpHealth(btSel);
        onFieldChange();
      });
      providerC.addEventListener("click", function (e) {
        var del = e.target.closest("[data-row-del]");
        if (del) { del.parentElement.remove(); onFieldChange(); }
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
