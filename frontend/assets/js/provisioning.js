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
      '<input type="text" data-cs="name" placeholder="web-01" class="w-full rounded-r-lg bg-transparent px-2 py-2 text-sm outline-none" />' +
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

  // ── DOM → 상태 수집 ──────────────────────────────────────────────────────
  function collect() {
    var container = document.getElementById("prov-common-fields");
    state.platforms = readPlatforms();
    state.selectedAccounts = readSelectedAccounts();
    // 선택 안 된 플랫폼의 이전 값이 남지 않도록 providerSpec을 매번 새로 구성
    state.providerSpec = { aws: {}, azure: {}, gcp: {} };

    if (state.resourceKind !== "compute" || !container) return;

    var cs = {};
    var nameInput = container.querySelector('[data-cs="name"]');
    if (nameInput) cs.name = "mcp-" + (nameInput.value || "").trim();
    var specSel = container.querySelector('[data-cs="specTier"]');
    var tierKey = specSel ? specSel.value : null;
    cs.specTier = tierKey;

    // 인바운드: 프리셋 체크 + 커스텀 행
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

    // 태그
    var tags = {};
    container.querySelectorAll("[data-tag-rows] > div").forEach(function (row) {
      var k = row.querySelector("[data-tag-key]");
      var v = row.querySelector("[data-tag-val]");
      if (k && k.value.trim()) tags[k.value.trim()] = (v && v.value.trim()) || "";
    });
    cs.tags = tags;

    cs.network = "auto"; // 오늘은 항상 자동 생성 고정
    state.commonSpec = cs;

    // 플랫폼별: 리전 + 사양 SKU + 인증
    var tier = SPEC_TIERS.filter(function (t) { return t.key === tierKey; })[0];
    state.platforms.forEach(function (p) {
      var ps = state.providerSpec[p];
      var regionSel = container.querySelector('[data-ps-platform="' + p + '"][data-ps="region"]');
      if (regionSel) ps.region = regionSel.value;
      if (tier) ps.instanceType = tier.sku[p];
      container.querySelectorAll('[data-ps-platform="' + p + '"][data-ps]').forEach(function (input) {
        var key = input.getAttribute("data-ps");
        if (key === "region") return;
        ps[key] = input.value;
      });
    });
  }

  // ── 스텝 렌더 진입점 ──────────────────────────────────────────────────────
  function renderCommonStep() {
    var section = document.getElementById("prov-step-common");
    var container = document.getElementById("prov-common-fields");
    if (!container) return;
    state.resourceKind = readResourceKind();
    state.platforms = readPlatforms();

    if (state.resourceKind === "compute") {
      if (section) section.hidden = false;
      renderComputeCommon(container, state.platforms);
    } else {
      // DB/Storage(단위 2), CDN 스텝 건너뛰기(단위 3/4)는 후속 커밋에서 처리.
      container.innerHTML = "";
      collect();
    }
  }

  // ── 이벤트 배선 ──────────────────────────────────────────────────────────
  function init() {
    var container = document.getElementById("prov-common-fields");
    if (!container) return; // provisioning 화면이 아니면 무시

    // 입력 변화 → 상태 수집(공통 필드 컨테이너 내부)
    container.addEventListener("input", collect);
    container.addEventListener("change", collect);
    // 동적 행 삭제(위임) — 컨테이너에 1회만 배선(재렌더 시 누적 방지)
    container.addEventListener("click", function (e) {
      var del = e.target.closest("[data-row-del]");
      if (del) { del.parentElement.remove(); collect(); }
    });

    // 플랫폼/리소스 종류 변경 → 재렌더(관련 필드가 바뀌므로)
    document.querySelectorAll("[data-prov-platform]").forEach(function (cb) {
      cb.addEventListener("change", renderCommonStep);
    });
    document.querySelectorAll("[data-prov-kind]").forEach(function (rb) {
      rb.addEventListener("change", renderCommonStep);
    });
    document.querySelectorAll("[data-prov-account]").forEach(function (cb) {
      cb.addEventListener("change", collect);
    });

    renderCommonStep();
  }

  // 후속 단위에서 재사용할 수 있도록 최소 API 노출
  window.PROV = { state: state, collect: collect, render: renderCommonStep, SPEC_TIERS: SPEC_TIERS, REGIONS: REGIONS };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
