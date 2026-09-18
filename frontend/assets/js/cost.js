/* frontend/assets/js/cost.js — 비용 화면 조회·필터·블록 렌더·모달·이동
   (docs/비용_개발문서/07_프론트_구현가이드.md §6, 04_화면명세_cost.md).

   PR 6 — 화면 실 API 연동. 이번 라운드(PR 1~6)에서 구현된 12개 블록만 실제로 값을 채운다.
   PR 7(팀·예산)·PR 8(급증탐지·검토큐·보고서)이 필요한 블록은 "준비 중"으로 고정 표시한다
   (CFL-03·CF-026·CF-025·CF-027·CF-033·CF-041·CF-034·CF-007·CF-030). CF-039(AI 상담)는
   팀·예산 없이도 동작하는 기존 agent.js 모달이라 그대로 살아 있다.

   ES 모듈이 아니다 — 이 프로젝트는 빌드 도구 없이 <script src>로 읽는다. defer를 붙이지 않는다
   (다른 9개 화면과 같은 관례 — inventory.js처럼 DOMContentLoaded를 직접 본다). */
window.MCPCost = (function () {
  "use strict";

  var F = window.MCPCostFormat, S = window.MCPCostState, CH = window.MCPCostChart;

  // ── 이스케이프 ──────────────────────────────────────────────────────────────
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ── 날짜 유틸 — period_end는 제외 경계다(05 §4). 변환은 여기 두 함수에서만 한다 ──────────
  function pad2(n) { return n < 10 ? "0" + n : String(n); }
  function isoOf(d) { return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()); }
  function todayISO() { return isoOf(new Date()); }
  function startOfMonthISO() { var d = new Date(); return isoOf(new Date(d.getFullYear(), d.getMonth(), 1)); }
  function addDaysISO(iso, days) {
    var parts = iso.split("-").map(Number);
    var d = new Date(parts[0], parts[1] - 1, parts[2]);
    d.setDate(d.getDate() + days);
    return isoOf(d);
  }
  /** 화면 표시(양끝 포함) → API 전송(끝 제외) */
  function toApiRange(displayStart, displayEnd) {
    return { period_start: displayStart, period_end: addDaysISO(displayEnd, 1) };
  }
  /** API 응답(끝 제외) → 화면 표시(양끝 포함) */
  function toDisplayRange(apiStart, apiEnd) {
    return { start: apiStart, end: addDaysISO(apiEnd, -1) };
  }

  // ── 필터 상태 — URL 쿼리와 동기화한다 ─────────────────────────────────────────────
  function qs(name) {
    var m = new RegExp("[?&]" + name + "=([^&]*)").exec(window.location.search);
    return m ? decodeURIComponent(m[1].replace(/\+/g, " ")) : null;
  }
  function qsAll(name) {
    var re = new RegExp("[?&]" + name + "=([^&]*)", "g");
    var out = [], m;
    while ((m = re.exec(window.location.search))) out.push(decodeURIComponent(m[1].replace(/\+/g, " ")));
    return out;
  }

  function defaultFilters() {
    return {
      periodStart: startOfMonthISO(), periodEnd: todayISO(),
      providers: [], accountIds: [], currency: "", chargeCategory: "usage",
      granularity: "daily", compare: "previous_period"
    };
  }

  function filtersFromUrl() {
    var f = defaultFilters();
    if (qs("period_start")) f.periodStart = qs("period_start");
    if (qs("period_end")) f.periodEnd = addDaysISO(qs("period_end"), -1); // URL은 API 형식(제외)로 저장한다
    var providers = qsAll("provider"); if (providers.length) f.providers = providers;
    var accs = qsAll("cloud_account_id"); if (accs.length) f.accountIds = accs;
    if (qs("currency")) f.currency = qs("currency");
    if (qs("charge_category")) f.chargeCategory = qs("charge_category");
    if (qs("granularity")) f.granularity = qs("granularity");
    if (qs("compare")) f.compare = qs("compare");
    return f;
  }

  var filters = filtersFromUrl();
  var ctx = {};       // 조회 결과 {key: {ok, value|error}}
  var capAccounts = []; // CFL-01 select를 채우는 재료(costs/capabilities items)

  function syncUrl() {
    var api = toApiRange(filters.periodStart, filters.periodEnd);
    var parts = ["period_start=" + encodeURIComponent(api.period_start), "period_end=" + encodeURIComponent(api.period_end)];
    filters.providers.forEach(function (p) { parts.push("provider=" + encodeURIComponent(p)); });
    filters.accountIds.forEach(function (a) { parts.push("cloud_account_id=" + encodeURIComponent(a)); });
    if (filters.currency) parts.push("currency=" + encodeURIComponent(filters.currency));
    if (filters.chargeCategory) parts.push("charge_category=" + encodeURIComponent(filters.chargeCategory));
    parts.push("granularity=" + encodeURIComponent(filters.granularity));
    parts.push("compare=" + encodeURIComponent(filters.compare));
    var url = window.location.pathname + "?" + parts.join("&");
    window.history.replaceState(null, "", url);
  }

  /** 비용 6종·리소스 조회에 쓰는 공통 query string(끝은 자동으로 제외 경계로 바뀐다) */
  function apiQuery(extra) {
    var api = toApiRange(filters.periodStart, filters.periodEnd);
    var parts = ["period_start=" + encodeURIComponent(api.period_start), "period_end=" + encodeURIComponent(api.period_end)];
    filters.providers.forEach(function (p) { parts.push("provider=" + encodeURIComponent(p)); });
    filters.accountIds.forEach(function (a) { parts.push("cloud_account_id=" + encodeURIComponent(a)); });
    if (filters.currency) parts.push("currency=" + encodeURIComponent(filters.currency));
    if (filters.chargeCategory) parts.push("charge_category=" + encodeURIComponent(filters.chargeCategory));
    if (extra) parts.push(extra);
    return "?" + parts.join("&");
  }

  // ── 상태 9종 렌더 공통 ───────────────────────────────────────────────────────────
  var FAIL_SET = ["NOT_CONNECTED", "SETUP_REQUIRED", "PERMISSION_DENIED", "COLLECT_FAILED", "UNSUPPORTED"];

  var DATA_STATUSES = ["CONNECTED_OK", "CONNECTED_EMPTY", "CONNECTED_PARTIAL"];
  var NO_DATA_PRIORITY = ["PENDING", "SETUP_REQUIRED", "PERMISSION_DENIED", "COLLECT_FAILED", "NOT_CONNECTED", "UNSUPPORTED"];
  function aggregateAccountStatus(accounts) {
    if (!accounts || !accounts.length) return "NOT_CONNECTED";
    var uniq = {};
    accounts.forEach(function (a) { uniq[a.status] = true; });
    var keys = Object.keys(uniq);
    if (keys.length === 1) return keys[0];
    var hasData = accounts.some(function (a) { return DATA_STATUSES.indexOf(a.status) >= 0; });
    if (!hasData) {
      // 어느 계정도 실제 데이터를 낸 적이 없다 — 상태가 섞였다고 "부분 합계"라 부르지 않는다
      // ($0 없음이 "부분"으로 보이면 QA-01과 같은 종류의 거짓말이 된다).
      for (var i = 0; i < NO_DATA_PRIORITY.length; i++) { if (uniq[NO_DATA_PRIORITY[i]]) return NO_DATA_PRIORITY[i]; }
      return "UNSUPPORTED";
    }
    var allOk = accounts.every(function (a) { return a.status === "CONNECTED_OK" || a.status === "CONNECTED_EMPTY"; });
    return allOk ? "CONNECTED_OK" : "CONNECTED_PARTIAL";
  }
  function lastSuccessOf(accounts) {
    var times = (accounts || []).map(function (a) { return a.as_of; }).filter(Boolean).sort();
    return times.length ? times[times.length - 1] : null;
  }
  function stateActionHtml(status) {
    switch (status) {
      case "NOT_CONNECTED": return '<div class="button-row no-print"><a class="btn" href="mypage.html">계정 연결</a></div>';
      case "PENDING": return '<div class="button-row no-print"><button type="button" class="btn" data-action="refresh-cost">비용 새로고침</button></div>';
      case "SETUP_REQUIRED": return '<div class="button-row no-print"><button type="button" class="btn" data-action="open-setup-dialog">설정 안내</button></div>';
      case "PERMISSION_DENIED": return '<div class="button-row no-print"><button type="button" class="btn" data-action="open-permission-dialog">필요 권한 보기</button></div>';
      case "COLLECT_FAILED": return '<div class="button-row no-print"><button type="button" class="btn" data-action="refresh-cost">다시 시도</button></div>';
      default: return "";
    }
  }
  function stateViewHtml(status, blockName, lastSuccessAt) {
    var msg = S.text(status, blockName, lastSuccessAt);
    return '<div class="state-view" role="status"><span class="dash">—</span><p>' + esc(msg) + "</p>" +
           stateActionHtml(status) + "</div>";
  }
  function fetchFailedHtml(settled, blockName) {
    var err = settled && settled.error;
    var headline = err && window.MCErr ? window.MCErr.headline(err) : (err && err.message) || "알 수 없는 오류";
    return '<div class="state-view" role="status"><span class="dash">—</span><p>' +
           esc((blockName || "이 블록") + " 조회에 실패했습니다.") + "</p><p class=\"tiny muted\">" +
           esc(headline) + "</p></div>";
  }
  function pendingBlockHtml(reasonText) {
    return '<div class="state-view" role="status"><span class="badge">준비 중</span><span class="dash">—</span><p>' +
           esc(reasonText) + "</p></div>";
  }

  // ── 필터 3-1 표: 이 화면이 지금 적용 중인 조건 ─────────────────────────────────────
  function currentConditionsSummary() {
    var api = toApiRange(filters.periodStart, filters.periodEnd);
    return {
      period_start: api.period_start, period_end: api.period_end,
      providers: filters.providers.length ? filters.providers : null,
      cloud_account_ids: filters.accountIds.length ? filters.accountIds : null,
      currency: filters.currency || null, charge_category: filters.chargeCategory
    };
  }

  // ── 조회 — settle() 로 실패를 값으로 감싼다(QA-01, 07 §6-2·7-1) ───────────────────
  function settle(promise) {
    return promise.then(
      function (v) { return { ok: true, value: v }; },
      function (e) { return { ok: false, error: e }; }
    );
  }

  var loading = false;
  function setBusy(busy) {
    loading = busy;
    document.querySelectorAll(".block-content").forEach(function (el) { el.setAttribute("aria-busy", busy ? "true" : "false"); });
  }

  function load() {
    if (loading) return Promise.resolve();
    var reqs = {
      capabilities:     "/costs/capabilities",
      summary:          "/costs/summary" + apiQuery(),
      trend:            "/costs/trend" + apiQuery("granularity=" + encodeURIComponent(filters.granularity)),
      breakdownService: "/costs/breakdown" + apiQuery("dimension=service&top_n=6"),
      breakdownAccount: "/costs/breakdown" + apiQuery("dimension=account&top_n=20"),
      changes:          "/costs/changes" + apiQuery("compare=" + encodeURIComponent(filters.compare) + "&top_n=10"),
      collection:       "/costs/collection-status",
      resources:        "/resources" + apiQuery()
    };
    var keys = Object.keys(reqs);
    setBusy(true);
    syncUrl();
    return Promise.all(keys.map(function (k) { return settle(window.MCPApi.request(reqs[k])); }))
      .then(function (results) {
        keys.forEach(function (k, i) { ctx[k] = results[i]; });
        if (ctx.capabilities.ok) capAccounts = ctx.capabilities.value.items || [];
        renderFilters(); // select 선택지가 capabilities 응답에서 나온다
        renderAll();
        setBusy(false);
      });
  }

  // ── 블록 레지스트리 ─────────────────────────────────────────────────────────────
  var BLOCKS = [
    { id: "CF-001",  tab: "common",   render: renderDataBar },
    { id: "CFL-01",  tab: "common",   render: renderFiltersBlockPlaceholder },
    { id: "CF-002",  tab: "overview", render: renderMtd },
    { id: "CF-003",  tab: "overview", render: renderForecast },
    { id: "CF-004",  tab: "overview", render: renderEstimated },
    { id: "CF-007",  tab: "overview", render: function () { return { state: "UNSUPPORTED", html: pendingBlockHtml("태그 기준 비용 배분이 아직 구현되지 않았습니다. 선행 조건: 태그 키 규칙 확정과 배분 계약(설계만).") }; } },
    { id: "CF-009",  tab: "overview", render: renderPlatforms },
    { id: "CF-030",  tab: "overview", render: function () { return { state: "UNSUPPORTED", html: pendingBlockHtml("태그 기준 비용 배분이 아직 구현되지 않았습니다. 선행 조건: 태그 키 규칙 확정과 배분 계약(설계만).") }; } },
    { id: "CF-034",  tab: "overview", render: function () { return { state: "UNSUPPORTED", html: pendingBlockHtml("비용 작업 큐는 급증 탐지·검토 워크플로가 필요합니다. 선행 조건: 팀·예산(PR 7) · 급증탐지·검토큐(PR 8) — 이번 라운드 범위 밖입니다.") }; } },
    { id: "CF-013",  tab: "analysis", render: renderTrend },
    { id: "CF-016",  tab: "analysis", render: renderServiceShare },
    { id: "EXT-F12", tab: "analysis", render: renderTopIncreases },
    { id: "CF-024",  tab: "analysis", render: renderPeriodCompare },
    { id: "CF-022",  tab: "analysis", render: renderTopResources },
    { id: "CF-018",  tab: "analysis", render: renderByAccount },
    { id: "CFL-03",  tab: "budget",   render: function () { return { state: "UNSUPPORTED", html: pendingBlockHtml("팀·예산 기능이 아직 구현되지 않았습니다. 선행 조건: 팀·예산(PR 7) — 이번 라운드 범위 밖입니다.") }; } },
    { id: "CF-026",  tab: "budget",   render: function () { return { state: "UNSUPPORTED", html: pendingBlockHtml("예산 대비 실적은 팀·예산 기능이 필요합니다. 선행 조건: 팀·예산(PR 7).") }; } },
    { id: "CF-025",  tab: "budget",   render: function () { return { state: "UNSUPPORTED", html: pendingBlockHtml("예산 설정은 팀·예산 기능이 필요합니다. 선행 조건: 팀·예산(PR 7).") }; } },
    { id: "CF-027",  tab: "budget",   render: function () { return { state: "UNSUPPORTED", html: pendingBlockHtml("임계치 알림은 팀·예산 기능이 필요합니다. 선행 조건: 팀·예산(PR 7).") }; } },
    { id: "CF-033",  tab: "budget",   render: function () { return { state: "UNSUPPORTED", html: pendingBlockHtml("급증 탐지가 아직 구현되지 않았습니다. 선행 조건: 급증탐지·검토큐(PR 8) — 이번 라운드 범위 밖입니다.") }; } },
    { id: "CF-039",  tab: "budget",   render: renderAiCta },
    { id: "CF-041",  tab: "budget",   render: function () { return { state: "UNSUPPORTED", html: pendingBlockHtml("비용 보고서가 아직 구현되지 않았습니다. 선행 조건: 보고서(PR 8) — 이번 라운드 범위 밖입니다.") }; } }
  ];

  function renderAll() {
    BLOCKS.forEach(function (b) {
      var el = document.getElementById(b.id);
      if (!el) return;
      var content = el.querySelector(".block-content");
      if (!content) return;
      var result;
      try { result = b.render(); }
      catch (e) { result = { state: "COLLECT_FAILED", html: fetchFailedHtml({ ok: false, error: e }, b.id) }; }
      content.setAttribute("data-rendered-state", result.state || "CONNECTED_OK");
      if (result.comparable === false) content.setAttribute("data-comparable", "false");
      else content.removeAttribute("data-comparable");
      content.innerHTML = result.html;
    });
  }

  // ── CF-001 데이터 기준 바 ────────────────────────────────────────────────────────
  function metaItem(label, valueHtml) {
    return "<div><dt>" + esc(label) + "</dt><dd>" + valueHtml + "</dd></div>";
  }

  function renderDataBar() {
    if (!ctx.summary || !ctx.summary.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.summary, "데이터 기준") };
    var d = ctx.summary.value;
    var items = (ctx.collection && ctx.collection.ok && ctx.collection.value.items) || [];
    var groups = { fail: 0, partial: 0, pending: 0 };
    items.forEach(function (it) {
      if (FAIL_SET.indexOf(it.status) >= 0) groups.fail++;
      else if (it.status === "CONNECTED_PARTIAL") groups.partial++;
      else if (it.status === "PENDING") groups.pending++;
    });
    var stale = S.stalenessText(d.as_of, Date.now(), d.staleness_threshold_hours);
    var asOfText = d.as_of ? stale : "수집 이력 없음";

    var nextAllowed = null;
    items.forEach(function (it) {
      if (!it.next_manual_allowed_at) return;
      var t = new Date(it.next_manual_allowed_at).getTime();
      if (nextAllowed == null || t < nextAllowed) nextAllowed = t;
    });
    var refreshDisabled = nextAllowed != null && nextAllowed > Date.now();

    var html = '<dl class="meta-grid">' +
      metaItem("조회 기간", esc(d.period.display)) +
      metaItem("통화", esc(filters.currency || "전체")) +
      metaItem("수집 시각", esc(asOfText)) +
      metaItem("확정 여부", '<span class="badge">MTD · 잠정</span>') +
      metaItem("요금 분류", esc(filters.chargeCategory)) +
      "</dl>";

    if (groups.fail > 0) {
      html += '<p class="warning">' + groups.fail + '개 계정의 비용을 가져오지 못했습니다. ' +
              '<button type="button" class="btn no-print" data-action="nav-scroll" data-tab="overview" data-target="CF-009">연동 상태 보기</button></p>';
    }
    if (groups.partial > 0) {
      html += '<p class="warning">' + groups.partial + '개 계정은 일부 범위가 빠진 부분 합계입니다. ' +
              '<button type="button" class="btn no-print" data-action="nav-scroll" data-tab="overview" data-target="CF-009">연동 상태 보기</button></p>';
    }
    if (groups.pending > 0) {
      html += '<p class="note">' + groups.pending + '개 계정은 첫 수집을 기다리는 중입니다.</p>';
    }

    html += '<div class="contract-row">' +
      "MTD 선택 기간의 실제 사용 비용 · 크레딧/환불 제외(usage 기준) · 청구 확정 아님<br>" +
      "Estimated 현재 구성 × 730시간 정가 · 할인·세금·부속 비용 미반영 · 기간 필터와 무관<br>" +
      "Forecast 이번 달 말 전망 · 어제까지의 실측을 남은 일수로 늘린 값 · 저장하지 않음" +
      "</div>";

    html += '<div class="button-row no-print">' +
      '<button type="button" class="btn" data-action="open-basis-dialog">집계 기준 보기</button>' +
      '<button type="button" class="btn primary" data-action="refresh-cost"' + (refreshDisabled ? " disabled" : "") + ">비용 새로고침" +
      (refreshDisabled ? " · 남은 시간 있음" : "") + "</button>" +
      "</div>";

    return { state: "CONNECTED_OK", html: html };
  }

  // ── CFL-01 공통 필터 — 값은 renderFilters()가 직접 DOM에 반영한다(select 선택지가
  //    응답에서 나오므로, BLOCKS 등록표의 render()는 정적 골격만 1회 그리고 이후 DOM을
  //    직접 갱신한다). ───────────────────────────────────────────────────────────────
  var filtersBuilt = false;
  function renderFiltersBlockPlaceholder() {
    if (!filtersBuilt) return { state: "CONNECTED_OK", html: filterGridHtml() };
    return { state: "CONNECTED_OK", html: document.querySelector('#CFL-01 .block-content').innerHTML };
  }

  function filterGridHtml() {
    return '<div class="filter-grid">' +
      '<label>기간 프리셋' +
        '<span class="button-row" style="margin-top:0">' +
          '<button type="button" class="btn" data-action="preset-period" data-preset="mtd">이번 달</button>' +
          '<button type="button" class="btn" data-action="preset-period" data-preset="last-month">지난 달</button>' +
          '<button type="button" class="btn" data-action="preset-period" data-preset="7d">최근 7일</button>' +
        "</span>" +
      "</label>" +
      '<label>시작일<input type="date" id="filter-period-start"></label>' +
      '<label>종료일<input type="date" id="filter-period-end"></label>' +
      '<label>CSP<select id="filter-provider" multiple size="3">' +
        '<option value="aws">AWS</option><option value="azure">Azure</option><option value="gcp">GCP</option>' +
      "</select></label>" +
      '<label>계정<select id="filter-account" multiple size="3"></select></label>' +
      '<label>통화<select id="filter-currency"><option value="">전체 통화</option></select></label>' +
      '<label>요금 분류<select id="filter-charge-category">' +
        '<option value="usage">사용료 (기본)</option><option value="credit">크레딧</option>' +
        '<option value="refund">환불</option><option value="tax">세금</option><option value="other">기타</option>' +
      "</select></label>" +
      '<span class="button-row" style="margin-top:0">' +
        '<button type="button" class="btn primary" data-action="apply-filters">적용</button>' +
        '<button type="button" class="btn" data-action="reset-filters">초기화</button>' +
      "</span>" +
      "</div>" +
      '<p class="note" id="filter-period-note" hidden></p>' +
      '<div class="chip" id="filter-summary-chip"></div>';
  }

  /** capabilities 응답으로 select 선택지를 채우고, 현재 필터 값을 입력칸에 반영한다.
      계정이 0개면 NOT_CONNECTED — 블록 안을 그 문구로 바꾼다(04 §4-0-1). */
  function renderFilters() {
    var el = document.getElementById("CFL-01");
    if (!el) return;
    var content = el.querySelector(".block-content");
    if (!capAccounts.length && ctx.capabilities && ctx.capabilities.ok) {
      content.setAttribute("data-rendered-state", "NOT_CONNECTED");
      content.innerHTML = stateViewHtml("NOT_CONNECTED", "비용 조회");
      filtersBuilt = false;
      return;
    }
    if (!filtersBuilt) {
      content.setAttribute("data-rendered-state", "CONNECTED_OK");
      content.innerHTML = filterGridHtml();
      filtersBuilt = true;
    }

    var startEl = document.getElementById("filter-period-start");
    var endEl = document.getElementById("filter-period-end");
    if (startEl) startEl.value = filters.periodStart;
    if (endEl) endEl.value = filters.periodEnd;

    var provSel = document.getElementById("filter-provider");
    if (provSel) {
      Array.prototype.forEach.call(provSel.options, function (o) { o.selected = filters.providers.indexOf(o.value) >= 0; });
    }

    var accSel = document.getElementById("filter-account");
    if (accSel) {
      var visible = capAccounts.filter(function (a) {
        return !filters.providers.length || filters.providers.indexOf(a.provider) >= 0;
      });
      accSel.innerHTML = visible.map(function (a) {
        return '<option value="' + esc(a.cloud_account_id) + '">' +
               esc((a.account_label || a.external_account_id) + " (" + a.provider.toUpperCase() + ")") + "</option>";
      }).join("");
      Array.prototype.forEach.call(accSel.options, function (o) { o.selected = filters.accountIds.indexOf(o.value) >= 0; });
    }

    var currSel = document.getElementById("filter-currency");
    if (currSel) {
      var currencies = [];
      capAccounts.forEach(function (a) { if (a.currency && currencies.indexOf(a.currency) < 0) currencies.push(a.currency); });
      currSel.innerHTML = '<option value="">전체 통화</option>' +
        currencies.map(function (c) { return '<option value="' + esc(c) + '">' + esc(c) + "</option>"; }).join("");
      currSel.value = filters.currency;
    }

    var ccSel = document.getElementById("filter-charge-category");
    if (ccSel) ccSel.value = filters.chargeCategory;

    var chip = document.getElementById("filter-summary-chip");
    if (chip) {
      chip.textContent = filters.periodStart + " ~ " + filters.periodEnd + " · " +
        (filters.providers.length ? filters.providers.join("/").toUpperCase() : "전체 CSP") + " · " +
        (filters.accountIds.length ? filters.accountIds.length + "개 계정" : "전체 계정") + " · " +
        (filters.currency || "전체 통화");
    }
  }

  function applyFiltersFromInputs() {
    var startEl = document.getElementById("filter-period-start");
    var endEl = document.getElementById("filter-period-end");
    var note = document.getElementById("filter-period-note");
    var start = startEl && startEl.value ? startEl.value : filters.periodStart;
    var end = endEl && endEl.value ? endEl.value : filters.periodEnd;

    if (end < start) {
      if (note) { note.hidden = false; note.textContent = "종료일이 시작일보다 빠릅니다."; }
      return false;
    }
    var apiEnd = addDaysISO(end, 1);
    var days = Math.round((new Date(apiEnd) - new Date(start)) / 86400000);
    if (days > 366) {
      if (note) { note.hidden = false; note.textContent = "조회 기간은 최대 366일입니다."; }
      return false;
    }
    if (note) note.hidden = true;

    filters.periodStart = start;
    filters.periodEnd = end;
    var provSel = document.getElementById("filter-provider");
    filters.providers = provSel ? Array.prototype.filter.call(provSel.options, function (o) { return o.selected; }).map(function (o) { return o.value; }) : [];
    var accSel = document.getElementById("filter-account");
    filters.accountIds = accSel ? Array.prototype.filter.call(accSel.options, function (o) { return o.selected; }).map(function (o) { return o.value; }) : [];
    var currSel = document.getElementById("filter-currency");
    filters.currency = currSel ? currSel.value : "";
    var ccSel = document.getElementById("filter-charge-category");
    filters.chargeCategory = ccSel ? ccSel.value : "usage";
    return true;
  }

  function resetFilters() {
    var d = defaultFilters();
    filters.periodStart = d.periodStart; filters.periodEnd = d.periodEnd;
    filters.providers = []; filters.accountIds = []; filters.currency = ""; filters.chargeCategory = "usage";
  }

  // ── CF-002 MTD ──────────────────────────────────────────────────────────────────
  function moneyBadgeLines(items, badgeText) {
    return items.map(function (it) {
      return '<div class="value">' + esc(F.money(it.amount, it.currency)) +
             ' <span class="badge">' + esc(badgeText) + "</span></div>";
    }).join("");
  }

  function deltaLineHtml(changesResp) {
    if (!changesResp || !changesResp.ok) return "";
    var d = changesResp.value;
    if (!d.comparable) return "";
    var cls = Number(d.totals.delta) >= 0 ? "money-positive" : "money-negative";
    var sign = Number(d.totals.delta) >= 0 ? "+" : "";
    var pct = d.totals.delta_pct == null ? "—" : (Number(d.totals.delta_pct) >= 0 ? "+" : "") + d.totals.delta_pct + "%";
    return '<p class="small muted"><span class="' + cls + '">' + sign + esc(F.money(d.totals.delta, d.currency)) +
           "</span> (" + esc(pct) + ")</p>";
  }

  function renderMtd() {
    if (!ctx.summary || !ctx.summary.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.summary, "MTD") };
    var d = ctx.summary.value;
    var st = aggregateAccountStatus(d.accounts);
    if (CH.BLANK_STATES.indexOf(st) >= 0) {
      return { state: st, html: stateViewHtml(st, "MTD", lastSuccessOf(d.accounts)) };
    }
    var lines = moneyBadgeLines(d.kpis.mtd_actual || [], "MTD · 잠정");
    if (!lines) {
      var acc0 = (d.accounts || []).filter(function (a) { return a.status === "CONNECTED_EMPTY"; })[0];
      lines = '<div class="value">' + esc(F.money("0.000000", acc0 ? acc0.currency : null)) + "</div>";
    }
    var html = lines + deltaLineHtml(ctx.changes) +
      '<div class="button-row no-print"><button type="button" class="btn" data-action="nav-scroll" data-tab="analysis" data-target="EXT-F12">기간 비교 보기</button></div>';
    return { state: st, html: html };
  }

  // ── CF-003 Forecast ─────────────────────────────────────────────────────────────
  function isCurrentMonthToDate() {
    return filters.periodStart === startOfMonthISO() && filters.periodEnd <= todayISO();
  }
  function renderForecast() {
    if (!ctx.summary || !ctx.summary.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.summary, "Forecast") };
    var d = ctx.summary.value;
    if (!isCurrentMonthToDate()) {
      return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="note">전망은 진행 중인 달에만 냅니다.</p>' +
        '<div class="button-row no-print"><button type="button" class="btn" data-action="open-forecast-dialog">예측 방법 보기</button></div>' };
    }
    var rows = d.kpis.forecast_month_end || [];
    if (!rows.length) return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="note">전망할 실측 데이터가 없습니다.</p>' };
    var html = moneyBadgeLines(rows, "Forecast · 전망") +
      '<p class="small muted">근거: ' + esc(rows[0].based_through || "—") + '까지의 실측 기준</p>' +
      '<div class="button-row no-print"><button type="button" class="btn" data-action="open-forecast-dialog">예측 방법 보기</button></div>';
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-004 Estimated ────────────────────────────────────────────────────────────
  function renderEstimated() {
    if (!ctx.summary || !ctx.summary.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.summary, "Estimated") };
    var d = ctx.summary.value;
    var rows = d.kpis.list_price_monthly || [];
    var totalResources = (ctx.resources && ctx.resources.ok) ? ctx.resources.value.items.length : null;
    if (!rows.length) {
      if (totalResources === 0) return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="note">집계할 리소스가 없습니다.</p>' };
      return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="note">정가를 산출할 수 없습니다.</p>' };
    }
    var missing = rows[0].missing_count || 0;
    var supported = totalResources != null ? Math.max(totalResources - missing, 0) : null;
    var html = moneyBadgeLines(rows, "Estimated · 정가 730h") +
      (supported != null ? '<p class="small muted">지원 ' + supported + "개 / 전체 " + totalResources + "개</p>" : "") +
      (missing > 0 ? '<p class="note">정가표에 없어 제외된 리소스 ' + missing + "개</p>" : "") +
      '<p class="note">기간 필터와 무관 · 현재 구성 기준</p>' +
      '<div class="button-row no-print"><button type="button" class="btn" data-action="nav-scroll" data-tab="analysis" data-target="CF-022">대상 보기</button></div>';
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-009 플랫폼별 비용 및 연동 상태 ─────────────────────────────────────────────
  var PROVIDER_LABEL = { aws: "AWS", azure: "Azure", gcp: "GCP" };
  var PROVIDER_DOT = { aws: "", azure: "azure", gcp: "gcp" };

  function renderPlatforms() {
    if (!ctx.capabilities || !ctx.capabilities.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.capabilities, "플랫폼별 비용") };
    var caps = ctx.capabilities.value.items || [];
    var accountsById = {};
    if (ctx.summary && ctx.summary.ok) {
      ctx.summary.value.accounts.forEach(function (a) { accountsById[a.cloud_account_id] = a; });
    }
    var byProvider = { aws: [], azure: [], gcp: [] };
    caps.forEach(function (c) { if (byProvider[c.provider]) byProvider[c.provider].push(c); });

    var cardsHtml = ["aws", "azure", "gcp"].map(function (p) {
      var accs = byProvider[p];
      var selected = !filters.providers.length || filters.providers.indexOf(p) >= 0;
      var dotClass = PROVIDER_DOT[p] ? " " + PROVIDER_DOT[p] : "";
      if (!accs.length) {
        return '<div class="cloud-card"' + (selected ? "" : ' style="opacity:.5"') + '><h3><span class="dot' + dotClass + '"></span>' +
          PROVIDER_LABEL[p] + '</h3><p class="tiny muted">연결된 계정이 없습니다</p></div>';
      }
      var rowsHtml = accs.map(function (c) {
        var acc = accountsById[c.cloud_account_id];
        var moneyLine = acc && acc.actual != null ? F.money(acc.actual, acc.currency) : "—";
        var resourceLine = acc && acc.resources_synced_at ? "정상 · " + esc(acc.resources_synced_at) : "미동기화";
        var connLine = c.status === "NOT_CONNECTED" ? "연결 안 됨" : "정상";
        var costLine = S.text(c.status, "비용 조회", c.as_of) || "정상";
        return '<div class="cloud-card"' + (selected ? "" : ' style="opacity:.5"') + '>' +
          '<h3><span class="dot' + dotClass + '"></span>' + esc(c.account_label || c.external_account_id) + "</h3>" +
          '<div class="value">' + esc(moneyLine) + "</div>" +
          '<div class="status-list">' +
            "<div><span>연결</span><span>" + esc(connLine) + "</span></div>" +
            "<div><span>리소스 조회</span><span>" + esc(resourceLine) + "</span></div>" +
            "<div><span>비용 조회</span><span>" + esc(c.status) + "</span></div>" +
            "<div><span>보안 점검</span><span>이번 범위 아님</span></div>" +
          "</div>" +
          (c.ingestion_running ? '<p class="tiny muted">수집 중…</p>' : "") +
          '<div class="button-row no-print">' +
            '<button type="button" class="btn" data-action="csp-detail" data-provider="' + esc(p) + '">상세 보기</button>' +
            (c.setup_hint ? '<button type="button" class="btn" data-action="open-setup-dialog" data-hint="' + esc(c.setup_hint) + '">설정 안내</button>' : "") +
          "</div></div>";
      }).join("");
      return rowsHtml;
    }).join("");

    return { state: "CONNECTED_OK", html: '<div class="cloud-grid">' + cardsHtml + "</div>" };
  }

  // ── CF-013 클라우드 비용 추이 ─────────────────────────────────────────────────────
  var trendMode = "daily";
  function renderTrend() {
    if (!ctx.trend || !ctx.trend.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.trend, "비용 추이") };
    var d = ctx.trend.value;
    var accStatus = ctx.summary && ctx.summary.ok ? aggregateAccountStatus(ctx.summary.value.accounts) : "CONNECTED_OK";
    var labels = [];
    var labelSet = {};
    (d.series || []).forEach(function (s) { s.points.forEach(function (p) { if (!labelSet[p.period_start]) { labelSet[p.period_start] = true; labels.push(p.period_start); } }); });
    labels.sort();
    var series = (d.series || []).map(function (s) {
      var points = {};
      s.points.forEach(function (p) { points[p.period_start] = p.amount; });
      return { key: s.key, label: s.label, points: points };
    });

    var chartHtml = trendMode === "daily"
      ? CH.stackedBar({ labels: labels, series: series, currency: d.currency, missingLabels: d.missing_days, state: accStatus, blockName: "비용 추이", title: "일별 비용" })
      : CH.lineChart({ labels: labels, series: series, currency: d.currency, missingLabels: d.missing_days, state: accStatus, blockName: "비용 추이", title: "월별 비용" });

    var excludedNote = d.currency_selection && d.currency_selection.excluded && d.currency_selection.excluded.length
      ? '<p class="note">(제외 통화: ' + d.currency_selection.excluded.map(function (e) { return esc(e); }).join(", ") + ")</p>" : "";

    var html = '<div class="cta-row no-print">' +
      '<span class="small muted">' + esc(d.currency || "통화 없음") + "</span>" +
      '<span class="button-row" style="margin-top:0">' +
        '<button type="button" class="btn' + (trendMode === "monthly" ? " primary" : "") + '" data-action="trend-mode" data-mode="monthly">월별</button>' +
        '<button type="button" class="btn' + (trendMode === "daily" ? " primary" : "") + '" data-action="trend-mode" data-mode="daily">일별</button>' +
      "</span></div>" + chartHtml + excludedNote;

    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-016 서비스별 비용 비중 ─────────────────────────────────────────────────────
  function renderServiceShare() {
    if (!ctx.breakdownService || !ctx.breakdownService.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.breakdownService, "서비스별 비중") };
    var d = ctx.breakdownService.value;
    var accStatus = ctx.summary && ctx.summary.ok ? aggregateAccountStatus(ctx.summary.value.accounts) : "CONNECTED_OK";
    var html = CH.donut({ items: d.items, rest: d.rest, unallocated: d.unallocated, total: d.total, currency: d.currency,
      estimate_unavailable_count: d.estimate_unavailable_count, state: accStatus, blockName: "서비스별 비중", title: "서비스별 비용 비중" });
    html += '<div class="button-row no-print"><button type="button" class="btn" data-action="open-full-breakdown-dialog">전체 명세</button></div>';
    return { state: "CONNECTED_OK", html: html };
  }

  // ── EXT-F12 증가액 Top N ────────────────────────────────────────────────────────
  function pctText(v) { return v == null ? "—" : (Number(v) >= 0 ? "+" : "") + v + "%"; }

  function renderTopIncreases() {
    if (!ctx.changes || !ctx.changes.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.changes, "증가액 Top N") };
    var d = ctx.changes.value;
    if (!d.comparable) return { state: "CONNECTED_OK", html: '<p class="note">비교할 수 있는 이전 기간이 없습니다.</p>' };
    var rows = (d.new_items || []).map(function (it) {
      return "<tr><td>" + esc(it.label) + ' <span class="badge">신규</span></td><td class="num">— → ' + esc(F.money(it.current, d.currency)) + "</td><td class=\"num\">—</td></tr>";
    }).concat((d.increases || []).map(function (it) {
      return "<tr><td>" + esc(it.label) + "</td><td class=\"num\">" + esc(F.money(it.previous, d.currency)) + " → " + esc(F.money(it.current, d.currency)) +
             '</td><td class="num money-positive">+' + esc(F.money(it.delta, d.currency)) + " (" + esc(pctText(it.delta_pct)) + ")</td></tr>";
    }));
    if (!rows.length) return { state: "CONNECTED_OK", html: '<p class="note">증가한 항목이 없습니다.</p>' };
    var html = '<div class="table-wrap"><table><thead><tr><th scope="col">구분</th><th scope="col">이전 → 현재</th><th scope="col">증감</th></tr></thead><tbody>' +
      rows.join("") + "</tbody></table></div>";
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-024 같은 길이 기간 비교 ────────────────────────────────────────────────────
  function renderPeriodCompare() {
    if (!ctx.changes || !ctx.changes.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.changes, "기간 비교") };
    var d = ctx.changes.value;
    if (!d.comparable) {
      return { state: "CONNECTED_OK", comparable: false, html:
        '<div class="state-view" role="status"><span class="badge">비교 불가</span><span class="dash">—</span>' +
        "<p>이전 기간을 같은 길이로 자를 수 없어 비교하지 않습니다. 선택 기간을 줄이거나 비교 기준을 바꾸세요.</p></div>" };
    }
    var prevDisp = toDisplayRange(d.previous.start, d.previous.end);
    var curDisp = toDisplayRange(d.current.start, d.current.end);
    var cls = Number(d.totals.delta) >= 0 ? "money-positive" : "money-negative";
    var sign = Number(d.totals.delta) >= 0 ? "+" : "";
    var html = '<div class="paired">' +
      '<div><span class="small muted">' + esc(prevDisp.start) + " ~ " + esc(prevDisp.end) + " · " + d.previous.days + '일</span>' +
        '<div class="value">' + esc(F.money(d.totals.previous, d.currency)) + "</div></div>" +
      '<div><span class="small muted">' + esc(curDisp.start) + " ~ " + esc(curDisp.end) + " · " + d.current.days + '일</span>' +
        '<div class="value">' + esc(F.money(d.totals.current, d.currency)) + "</div></div>" +
      "</div>" +
      '<p class="note"><span class="' + cls + '">' + sign + esc(F.money(d.totals.delta, d.currency)) + "</span> " +
        esc(pctText(d.totals.delta_pct)) + " · " + esc(d.currency || "") + "</p>" +
      '<p class="note">두 기간의 일수가 같을 때만 비교합니다. 자를 수 없으면 비교를 표시하지 않습니다.</p>';
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-022 비용 상위 리소스 ───────────────────────────────────────────────────────
  function renderTopResources() {
    if (!ctx.resources || !ctx.resources.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.resources, "비용 상위 리소스") };
    var items = ctx.resources.value.items || [];
    var withCost = items.filter(function (r) { return r.cost_summary && r.cost_summary.estimated_monthly_cost != null; });
    var sorted = withCost.slice().sort(function (a, b) { return Number(b.cost_summary.estimated_monthly_cost) - Number(a.cost_summary.estimated_monthly_cost); });
    var top = sorted.slice(0, 5);
    var restCount = Math.max(items.length - top.length, 0);

    var rows = top.map(function (r) {
      var stoppedNote = r.status && r.status.toUpperCase() !== "RUNNING" ? ' <span class="tiny muted">(상시 가동 가정 · 현재 중지됨)</span>' : "";
      var owner = (r.tags && r.tags.Owner) || "—";
      return "<tr><td>" + esc(r.cloud_account.provider.toUpperCase()) + " · " + esc(r.name || r.external_resource_id) + "</td>" +
        '<td class="num">' + esc(F.money(r.cost_summary.estimated_monthly_cost, r.cost_summary.currency)) + stoppedNote + "</td>" +
        "<td>" + esc(r.status || "—") + "</td><td>" + esc(owner) + '</td>' +
        '<td><button type="button" class="btn no-print" data-action="open-resource-detail" data-resource-id="' + esc(r.id) + '">상세 보기</button></td></tr>';
    });
    var noCostCount = items.length - withCost.length;
    var html = '<p class="note">정가 열: 현재 구성 × 730h · 기간 무관 — 실측 데이터는 이번 범위에서 리소스 단위로 집계하지 않습니다.</p>' +
      '<div class="table-wrap"><table><thead><tr><th scope="col">플랫폼/자원</th><th scope="col">정가(월)</th><th scope="col">상태</th><th scope="col">담당</th><th scope="col">행동</th></tr></thead><tbody>' +
      (rows.length ? rows.join("") : '<tr><td colspan="5" class="tiny muted">정가가 있는 리소스가 없습니다</td></tr>') +
      "</tbody></table></div>" +
      (restCount > 0 ? '<p class="note">기타(' + restCount + "개)</p>" : "") +
      (noCostCount > 0 ? '<p class="note">정가표에 없음 ' + noCostCount + "개</p>" : "");
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-018 계정별 비용 ────────────────────────────────────────────────────────────
  function renderByAccount() {
    if (!ctx.breakdownAccount || !ctx.breakdownAccount.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.breakdownAccount, "계정별 비용") };
    var d = ctx.breakdownAccount.value;
    var accountsById = {};
    if (ctx.summary && ctx.summary.ok) ctx.summary.value.accounts.forEach(function (a) { accountsById[a.cloud_account_id] = a; });

    var rows = (d.items || []).map(function (it) {
      var acc = accountsById[it.key];
      var status = acc ? acc.status : "—";
      var team = acc && acc.team_id ? acc.team_id : "미배정";
      return "<tr><td>" + esc(it.label) + "</td>" +
        '<td class="num">' + esc(F.money(it.amount, d.currency)) + "</td>" +
        "<td><span class=\"badge\">" + esc(status) + "</span></td>" +
        "<td>" + esc(team) + "</td>" +
        '<td><button type="button" class="btn no-print" data-action="account-filter-set" data-account-id="' + esc(it.key) + '">계정 선택</button></td></tr>';
    });
    var excluded = d.unallocated && Number(d.unallocated.amount) > 0
      ? '<p class="note">미배분 ' + esc(F.money(d.unallocated.amount, d.currency)) + "</p>" : "";
    var html = '<div class="table-wrap"><table><thead><tr><th scope="col">계정</th><th scope="col">비용</th><th scope="col">상태</th><th scope="col">팀</th><th scope="col">행동</th></tr></thead><tbody>' +
      (rows.length ? rows.join("") : '<tr><td colspan="5" class="tiny muted">계정이 없습니다</td></tr>') + "</tbody></table></div>" + excluded;
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-039 AI 비용 상담 — 정적 CTA. 실제 채팅은 agent.js가 처리한다(07 §10) ────────
  function renderAiCta() {
    var c = currentConditionsSummary();
    var html = '<div class="cta-row">' +
      '<p class="small muted">현재 조건: ' + esc(c.period_start) + " ~ " + esc(c.period_end) + " · " +
        esc(c.providers ? c.providers.join("/").toUpperCase() : "전체 CSP") + "</p>" +
      '<button type="button" class="btn yellow" data-modal-open="#agent-chat-modal">AI 비용 상담</button>' +
      "</div>" +
      '<p class="note">AI가 직접 계산하지 않습니다. 현재 조건을 바탕으로 답합니다.</p>';
    return { state: "CONNECTED_OK", html: html };
  }

  // ── 모달·토스트 ─────────────────────────────────────────────────────────────────
  function openDialog(title, bodyHtml) {
    var titleEl = document.getElementById("dialog-title");
    var bodyEl = document.getElementById("dialog-body");
    if (titleEl) titleEl.textContent = title;
    if (bodyEl) bodyEl.innerHTML = bodyHtml;
    if (window.MCPModal) window.MCPModal.open("#cost-dialog");
  }

  var toastTimer = null;
  function toast(message) {
    var el = document.getElementById("toast");
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, 3500);
  }

  // ── 화면 이동 ────────────────────────────────────────────────────────────────────
  function switchTab(key) {
    var tabs = document.querySelectorAll('.tabs [role="tab"][data-tab]');
    Array.prototype.forEach.call(tabs, function (btn) {
      var active = btn.getAttribute("data-tab") === key;
      btn.setAttribute("aria-selected", active ? "true" : "false");
      btn.tabIndex = active ? 0 : -1;
    });
    ["overview", "analysis", "budget"].forEach(function (k) {
      var panel = document.getElementById("panel-" + k);
      if (panel) panel.hidden = k !== key;
    });
  }
  function navScroll(tabKey, targetId) {
    switchTab(tabKey);
    var el = document.getElementById(targetId);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ── 비용 새로고침 ────────────────────────────────────────────────────────────────
  function refreshCost() {
    var api = toApiRange(filters.periodStart, filters.periodEnd);
    return window.MCPApi.request("/cost-ingestion-runs", {
      method: "POST",
      body: {
        cloud_account_ids: filters.accountIds.length ? filters.accountIds : null,
        period_start: api.period_start, period_end: api.period_end
      }
    }).then(function (data) {
      var skipped = (data && data.skipped) || [];
      if (skipped.length) toast(skipped.length + "개 계정은 이미 진행 중이라 건너뛰었습니다.");
      else toast("비용 수집을 요청했습니다.");
      return load();
    }).catch(function (e) {
      toast(window.MCErr ? window.MCErr.headline(e) : "새로고침 요청에 실패했습니다.");
    });
  }

  // ── 이벤트 위임 ──────────────────────────────────────────────────────────────────
  function initEvents() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-action]");
      if (!btn) return;
      var action = btn.getAttribute("data-action");

      switch (action) {
        case "apply-filters":
          if (applyFiltersFromInputs()) load();
          break;
        case "reset-filters":
          resetFilters(); renderFilters(); load();
          break;
        case "preset-period": {
          var preset = btn.getAttribute("data-preset");
          if (preset === "mtd") { filters.periodStart = startOfMonthISO(); filters.periodEnd = todayISO(); }
          else if (preset === "last-month") {
            var d = new Date(); var lm = new Date(d.getFullYear(), d.getMonth() - 1, 1);
            var lastDay = new Date(d.getFullYear(), d.getMonth(), 0);
            filters.periodStart = isoOf(lm); filters.periodEnd = isoOf(lastDay);
          } else if (preset === "7d") { filters.periodEnd = todayISO(); filters.periodStart = addDaysISO(todayISO(), -6); }
          renderFilters();
          break;
        }
        case "nav-scroll":
          navScroll(btn.getAttribute("data-tab"), btn.getAttribute("data-target"));
          break;
        case "refresh-cost":
          if (!btn.disabled) refreshCost();
          break;
        case "trend-mode":
          trendMode = btn.getAttribute("data-mode");
          renderAll();
          break;
        case "compare-basis":
          filters.compare = btn.getAttribute("data-compare");
          load();
          break;
        case "csp-detail":
          filters.providers = [btn.getAttribute("data-provider")];
          renderFilters();
          switchTab("analysis");
          load();
          break;
        case "account-filter-set":
          filters.accountIds = [btn.getAttribute("data-account-id")];
          renderFilters();
          load();
          break;
        case "open-resource-detail":
          window.location.href = "inventory.html?resource_id=" + encodeURIComponent(btn.getAttribute("data-resource-id"));
          break;
        case "open-basis-dialog":
          openDialog("집계 기준", '<dl class="meta-grid" style="grid-template-columns:1fr">' +
            metaItem("MTD", "선택 기간의 실제 사용 비용 · 크레딧/환불 제외(usage 기준) · 청구 확정 아님") +
            metaItem("Estimated", "현재 구성 × 730시간 정가 · 할인·세금·부속 비용 미반영 · 기간 필터와 무관") +
            metaItem("Forecast", "이번 달 말 전망 · 어제까지의 실측을 남은 일수로 늘린 값 · 저장하지 않음") + "</dl>");
          break;
        case "open-forecast-dialog":
          openDialog("예측 방법", '<p>방법: 이번 달 1일부터 어제까지의 실측 비용을 남은 일수 비율로 늘려 계산합니다(<code>mtd_prorated</code>).</p><p class="note">저장하지 않는 값입니다.</p>');
          break;
        case "open-setup-dialog":
          openDialog("설정 안내", "<p>" + esc(btn.getAttribute("data-hint") || "이 계정의 비용 조회를 위한 설정이 필요합니다. 마이페이지에서 자격 증명을 확인하세요.") + "</p>");
          break;
        case "open-permission-dialog":
          openDialog("필요 권한", "<p>이 계정의 비용 조회 권한이 부족합니다. 마이페이지에서 자격 증명의 권한을 확인하세요.</p>");
          break;
        case "open-full-breakdown-dialog":
          if (ctx.breakdownService && ctx.breakdownService.ok) {
            var d = ctx.breakdownService.value;
            var rows = (d.items || []).map(function (it) {
              return "<tr><td>" + esc(it.label) + '</td><td class="num">' + esc(F.money(it.amount, d.currency)) + "</td><td class=\"num\">" + esc(it.share_pct) + "%</td></tr>";
            }).join("");
            openDialog("서비스별 전체 명세", '<div class="table-wrap"><table><thead><tr><th scope="col">서비스</th><th scope="col">금액</th><th scope="col">비중</th></tr></thead><tbody>' + rows + "</tbody></table></div>");
          }
          break;
        default: break;
      }
    });
  }

  // ── 탭 전환(기존 PR2 동작 유지) ───────────────────────────────────────────────────
  function initTabs() {
    var tabs = Array.prototype.slice.call(document.querySelectorAll('.tabs [role="tab"][data-tab]'));
    if (!tabs.length) return;
    tabs.forEach(function (btn) {
      btn.addEventListener("click", function () { switchTab(btn.getAttribute("data-tab")); });
    });
  }

  function init() {
    initTabs();
    initEvents();
    load();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  return { initTabs: initTabs, load: load };
})();
