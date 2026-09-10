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

  // DB 엔진 옵션(플랫폼별로 다름).
  var DB_ENGINES = {
    aws: ["MySQL", "PostgreSQL", "MariaDB", "SQL Server", "Oracle", "DB2", "Aurora"],
    azure: ["MySQL", "PostgreSQL", "SQL Server"],
    gcp: ["MySQL", "PostgreSQL", "SQL Server"],
  };
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

  // ── 전역 상태 ────────────────────────────────────────────────────────────
  var state = {
    resourceKind: null, // 'compute' | 'db' | 'storage_object' | 'cdn'
    platforms: [], // ['aws','azure']
    selectedAccounts: {}, // { aws: 'prod-aws-01', ... }
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
    document.querySelectorAll("[data-prov-account]").forEach(function (cb) {
      if (cb.checked) acc[cb.getAttribute("data-prov-account")] = cb.value || cb.getAttribute("data-prov-account-label");
    });
    return acc;
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
    var specOpts = SPEC_TIERS.map(function (t) {
      return '<option value="' + t.key + '">' + t.label + "</option>";
    }).join("");
    specField.innerHTML = labelHtml("사양", true) +
      '<select data-cs="specTier" class="' + FIELD_INPUT + '">' + specOpts + "</select>";
    container.appendChild(specField);

    // 2) 리전 — 선택된 플랫폼마다 별도 select
    platforms.forEach(function (p) {
      var regionField = el("div");
      var opts = REGIONS[p].map(function (r) { return '<option value="' + r + '">' + r + "</option>"; }).join("");
      regionField.innerHTML = labelHtml("리전 · " + PLATFORM_LABEL[p], true) +
        '<select data-ps-platform="' + p + '" data-ps="region" class="' + FIELD_INPUT + '">' + opts + "</select>";
      container.appendChild(regionField);
    });

    // 4) 네트워크 — 읽기 전용 안내 + 비활성 토글 자리
    var netField = el("div", { class: "sm:col-span-2" });
    netField.innerHTML = labelHtml("네트워크") +
      '<div class="flex items-center justify-between rounded-lg border border-dashed border-border bg-muted px-3 py-2 text-sm text-muted-foreground">' +
      "<span>새 VPC/Subnet 자동 생성</span>" +
      '<label class="flex cursor-not-allowed items-center gap-2 opacity-50"><input type="checkbox" disabled /> 기존 리소스 사용</label>' +
      "</div>";
    container.appendChild(netField);

    // 5) 인바운드 규칙 — 프리셋 체크박스 + 커스텀 행 추가/삭제
    var inboundField = el("div", { class: "sm:col-span-2" });
    var presetHtml = INBOUND_PRESETS.map(function (r) {
      var checked = r.port === 22 ? " checked" : "";
      return '<label class="flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-sm">' +
        '<input type="checkbox" data-inbound-preset="' + r.port + '"' + checked + " /> " + r.label + "</label>";
    }).join("");
    inboundField.innerHTML = labelHtml("인바운드 규칙", true) +
      '<div class="flex flex-wrap gap-2">' + presetHtml + "</div>" +
      '<div data-inbound-custom class="mt-2 space-y-2"></div>' +
      '<button type="button" data-inbound-add class="mt-2 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted">+ 규칙 추가</button>' +
      '<p data-inbound-msg class="mt-1 text-xs text-muted-foreground">최소 1개 이상의 규칙이 필요합니다.</p>';
    container.appendChild(inboundField);

    // 6) 인증 — 플랫폼별 위젯
    platforms.forEach(function (p) {
      var authField = el("div", { class: "sm:col-span-2 rounded-xl bg-muted p-3" });
      var inner = '<p class="mb-2 text-sm font-medium">인증 · ' + PLATFORM_LABEL[p] + "</p>";
      if (p === "aws") {
        inner += labelHtml("키 페어 이름", true) +
          '<input type="text" data-ps-platform="aws" data-ps="keyPairName" placeholder="mcp-keypair" class="' + FIELD_INPUT + '" />';
      } else if (p === "azure") {
        inner += '<div class="grid gap-2 sm:grid-cols-2">' +
          "<div>" + labelHtml("관리자 계정명", true) +
          '<input type="text" data-ps-platform="azure" data-ps="adminUsername" class="' + FIELD_INPUT + '" /></div>' +
          "<div>" + labelHtml("비밀번호", true) +
          '<input type="password" data-ps-platform="azure" data-ps="adminPassword" class="' + FIELD_INPUT + '" /></div>' +
          "</div>";
      } else if (p === "gcp") {
        inner += labelHtml("SSH 공개키", true) +
          '<textarea data-ps-platform="gcp" data-ps="sshPublicKey" rows="2" placeholder="ssh-rsa AAAA..." class="' + FIELD_INPUT + '"></textarea>';
      }
      authField.innerHTML = inner;
      container.appendChild(authField);
    });

    // 7) 태그 — key-value 반복 입력 (선택)
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
  function regionFieldEl(p, note) {
    var f = el("div");
    var opts = REGIONS[p].map(function (r) { return '<option value="' + r + '">' + r + "</option>"; }).join("");
    f.innerHTML = labelHtml("리전 · " + PLATFORM_LABEL[p], true) +
      '<select data-ps-platform="' + p + '" data-ps="region" class="' + FIELD_INPUT + '">' + opts + "</select>" +
      (note ? '<p class="mt-1 text-xs text-muted-foreground">' + note + "</p>" : "");
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

  // ── DB 공통 설정 렌더링 ───────────────────────────────────────────────────
  function renderDbCommon(container, platforms) {
    container.innerHTML = "";
    container.appendChild(nameFieldEl("db-01"));

    platforms.forEach(function (p) {
      var box = el("div", { class: "sm:col-span-2 space-y-3 rounded-xl bg-muted p-3" });
      var engineOpts = DB_ENGINES[p].map(function (e) { return "<option>" + e + "</option>"; }).join("");
      var html = '<p class="text-sm font-medium">' + PLATFORM_LABEL[p] + "</p>";
      // 엔진
      html += "<div>" + labelHtml("엔진", true) +
        '<select data-ps-platform="' + p + '" data-ps="engine" class="' + FIELD_INPUT + '">' + engineOpts + "</select>";
      if (p === "azure") {
        html += '<p class="mt-1 text-xs" ' + WARN_STYLE + ">MariaDB는 2025년 9월 19일 이후 Azure에서 지원 종료됩니다.</p>";
      }
      html += "</div>";
      // 리전
      var regionOpts = REGIONS[p].map(function (r) { return '<option value="' + r + '">' + r + "</option>"; }).join("");
      html += "<div>" + labelHtml("리전", true) +
        '<select data-ps-platform="' + p + '" data-ps="region" class="' + FIELD_INPUT + '">' + regionOpts + "</select></div>";
      // 인증
      if (p === "gcp") {
        html += "<div>" + labelHtml("루트 비밀번호", true) +
          '<input type="password" data-ps-platform="gcp" data-ps="masterPassword" class="' + FIELD_INPUT + '" />' +
          '<p class="mt-1 text-xs text-muted-foreground">GCP는 비밀번호만 입력합니다.</p></div>';
      } else {
        html += '<div class="grid gap-2 sm:grid-cols-2"><div>' + labelHtml("마스터 사용자명", true) +
          '<input type="text" data-ps-platform="' + p + '" data-ps="masterUsername" class="' + FIELD_INPUT + '" /></div>' +
          "<div>" + labelHtml("비밀번호", true) +
          '<input type="password" data-ps-platform="' + p + '" data-ps="masterPassword" class="' + FIELD_INPUT + '" /></div></div>';
      }
      box.innerHTML = html;
      container.appendChild(box);
    });

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

    // 리전 — 플랫폼별(Azure는 Central 고정 안내)
    platforms.forEach(function (p) {
      var note = p === "azure" ? "Azure는 'Central' 리전으로 고정됩니다." : null;
      container.appendChild(regionFieldEl(p, note));
    });

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
    if (!container || !kind || kind === "cdn") { state.commonSpec = {}; return; }

    var cs = {};
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

    // Compute 전용: 인바운드 규칙 + 네트워크 고정
    if (kind === "compute") {
      var rules = [];
      container.querySelectorAll("[data-inbound-preset]").forEach(function (cb) {
        if (cb.checked) rules.push({ port: Number(cb.getAttribute("data-inbound-preset")), cidr: DEFAULT_CIDR });
      });
      container.querySelectorAll("[data-inbound-custom] > div").forEach(function (row) {
        var portEl = row.querySelector("[data-inbound-port]");
        var cidrEl = row.querySelector("[data-inbound-cidr]");
        var port = portEl && portEl.value ? Number(portEl.value) : null;
        if (port) rules.push({ port: port, cidr: (cidrEl && cidrEl.value.trim()) || DEFAULT_CIDR });
      });
      cs.inboundRules = rules;
      cs.network = "auto"; // 항상 자동 생성 고정
    }
    if (kind === "db") cs.backup = "auto"; // 자동 백업 고정(표시만)

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
          ps[input.getAttribute("data-ps")] = input.value;
        });
      });
      if (tier) ps.instanceType = tier.sku[p];
      // 제외 필드의 서버 기본값 병합(사용자 입력 없음)
      var defs = SERVER_DEFAULTS[kind];
      if (defs) Object.keys(defs).forEach(function (k) { if (ps[k] === undefined) ps[k] = defs[k]; });
    });
  }

  // ── ⑤ 플랫폼별 추가 설정 렌더링 ──────────────────────────────────────────
  function renderProviderStep(container, kind, platforms) {
    container.innerHTML = "";

    if (kind === "cdn") {
      container.appendChild(el(
        "div",
        { class: "rounded-xl border border-dashed border-border bg-muted p-4 text-sm text-muted-foreground" },
        "CDN은 클라우드별 설정 체계 차이가 커서 상세 입력 폼은 준비 중입니다."
      ));
      return;
    }

    if (kind === "compute") {
      platforms.forEach(function (p) {
        if (!COMPUTE_IMAGES[p]) return; // GCP 등 이미지 필드 미노출
        var box = el("div", { class: "rounded-xl bg-muted p-3" });
        var opts = COMPUTE_IMAGES[p].map(function (o) { return "<option>" + o + "</option>"; }).join("");
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
      return;
    }

    if (kind === "storage_object") {
      if (platforms.indexOf("gcp") >= 0) {
        var opts2 = STORAGE_CLASSES.map(function (o) { return "<option>" + o + "</option>"; }).join("");
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
      container.appendChild(el("div", { class: "text-xs text-muted-foreground" },
        "추가 입력 없이 서버 기본값으로 생성됩니다."));
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
    if (!kind || kind === "cdn") return false;
    if (!state.platforms.length) return false;
    var cs = state.commonSpec || {};

    if (kind === "compute") {
      if (!cs.name || cs.name === "mcp-") return false; // 이름(프리픽스만이면 미입력)
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
        if (p === "gcp") return isFilled(ps.masterPassword);
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

  // ── 스텝 렌더 진입점 (④ 공통 + ⑤ 추가) ──────────────────────────────────
  function renderSteps() {
    var commonSection = document.getElementById("prov-step-common");
    var commonC = document.getElementById("prov-common-fields");
    var providerC = document.getElementById("prov-provider-fields");
    if (!commonC) return;
    state.resourceKind = readResourceKind();
    state.platforms = readPlatforms();
    var kind = state.resourceKind;

    // ④ 공통 설정 (CDN은 공통 필드가 없어 스텝 자체를 건너뛰어 숨긴다)
    if (kind === "compute") {
      if (commonSection) commonSection.hidden = false;
      renderComputeCommon(commonC, state.platforms);
    } else if (kind === "db") {
      if (commonSection) commonSection.hidden = false;
      renderDbCommon(commonC, state.platforms);
    } else if (kind === "storage_object") {
      if (commonSection) commonSection.hidden = false;
      renderStorageCommon(commonC, state.platforms);
    } else {
      if (commonSection) commonSection.hidden = true; // CDN: ④ 건너뛰기
      commonC.innerHTML = "";
    }

    // ⑤ 플랫폼별 추가 설정
    if (providerC) renderProviderStep(providerC, kind, state.platforms);

    collect();
    updateSubmitState();
  }

  // ── 이벤트 배선 ──────────────────────────────────────────────────────────
  function init() {
    var container = document.getElementById("prov-common-fields");
    if (!container) return; // provisioning 화면이 아니면 무시
    var providerC = document.getElementById("prov-provider-fields");

    // ④ 공통 필드: 입력 변화 → 상태 수집 + 제출 상태 갱신
    function onFieldChange() { collect(); updateSubmitState(); }
    container.addEventListener("input", onFieldChange);
    container.addEventListener("change", onFieldChange);
    // 동적 행 삭제(위임) — 컨테이너에 1회만 배선(재렌더 시 누적 방지)
    container.addEventListener("click", function (e) {
      var del = e.target.closest("[data-row-del]");
      if (del) { del.parentElement.remove(); onFieldChange(); }
    });

    // ⑤ 추가 필드: 입력 변화 + AWS 이미지 토글
    if (providerC) {
      providerC.addEventListener("input", onFieldChange);
      providerC.addEventListener("change", function (e) {
        var sel = e.target.closest("[data-image-select]");
        if (sel) toggleAmi(sel);
        onFieldChange();
      });
    }

    // 플랫폼/리소스 종류 변경 → 재렌더(관련 필드가 바뀌므로)
    document.querySelectorAll("[data-prov-platform]").forEach(function (cb) {
      cb.addEventListener("change", renderSteps);
    });
    document.querySelectorAll("[data-prov-kind]").forEach(function (rb) {
      rb.addEventListener("change", renderSteps);
    });
    document.querySelectorAll("[data-prov-account]").forEach(function (cb) {
      cb.addEventListener("change", onFieldChange);
    });

    renderSteps();
  }

  // 후속 단위에서 재사용할 수 있도록 최소 API 노출
  window.PROV = { state: state, collect: collect, render: renderSteps, validate: validate, SPEC_TIERS: SPEC_TIERS, REGIONS: REGIONS };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
