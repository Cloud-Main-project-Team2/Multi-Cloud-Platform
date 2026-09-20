/* frontend/assets/js/cost.js — 비용 화면 조회·필터·블록 렌더·모달·이동
   (docs/비용_개발문서/07_프론트_구현가이드.md §6, 04_화면명세_cost.md).

   PR 6 — 화면 실 API 연동(14블록). 2026-09-20 프론트 라운드 — PR 7(팀·예산)·PR 8(급증·검토 큐·
   가격 비교·AI 문맥)을 ③ 탭에 연결(CFL-03·CF-026·CF-025·CF-027·CF-033·CF-034·CF-035·CF-039).
   "준비 중"은 CF-041(비용 보고서)뿐이다 — 보고서 부품 3종은 보고서 담당(안권형님)과 필요 계약을
   맞춘 뒤 만든다. ③ 탭의 팀 선택(CFL-03)은 ③ 탭에만 적용되고 ①② 탭 블록에 영향을 주지 않는다.
   CF-007·CF-030은 처음엔 "준비 중"으로 뒀었는데 확정 7을 잘못 읽은 것이었다(2026-09-19 정정,
   12_원본문서_추적표 §2-1-1 #1) — 설계만인 것은 태그 기반 *실측* 배분이고, GET /resources의
   tags.Owner로 *정가 기준* 그룹핑은 지금 된다. 둘 다 기존 API만으로 그린다.

   ES 모듈이 아니다 — 이 프로젝트는 빌드 도구 없이 <script src>로 읽는다. defer를 붙이지 않는다
   (다른 9개 화면과 같은 관례 — inventory.js처럼 DOMContentLoaded를 직접 본다). */
/* CF-039 AI 비용 상담의 추천 질문 6개(확정 17). agent.js:14의 SUGGESTED_PROMPTS가 이 전역을 먼저
   보고 없으면 자기 기본값 3개를 쓴다 — dashboard.html은 이 값을 주지 않으므로 그 화면은 그대로다.
   cost.html에서 cost.js가 agent.js보다 먼저 로드돼야 한다(07 §3-2 순서). 문구는 미결(07 §10-1) —
   바꿀 곳은 여기 한 곳이다. */
window.MCPAgentPrompts = [
  "이번 달 비용이 왜 늘었어?",
  "어느 계정이 가장 많이 쓰고 있어?",
  "예산을 넘길 것 같은 팀이 있어?",
  "정가 추정과 실측이 왜 달라?",
  "수집되지 않은 구간이 있어?",
  "지금 확인이 필요한 항목은 뭐야?"
];

window.MCPCost = (function () {
  "use strict";

  /* CF-007·CF-030이 "담당자 미지정"을 판정하는 태그 키. 확정 7은 "AWS 리소스 태그"라고만 했고
     키 이름은 미결이라(04 §4-4) 임시 기본값 Owner를 여기 한 곳에만 둔다. 대소문자 구분 — 다른
     키(owner·OWNER)를 임의로 인정하지 않는다. */
  var OWNER_TAG_KEY = "Owner";

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

  /** "기간 프리셋" 드롭다운 — 시작일/종료일 입력칸을 채우기만 한다(조회는 '적용'을 눌러야). */
  function applyPeriodPreset(preset) {
    if (preset === "mtd") { filters.periodStart = startOfMonthISO(); filters.periodEnd = todayISO(); }
    else if (preset === "last-month") {
      var d = new Date(); var lm = new Date(d.getFullYear(), d.getMonth() - 1, 1);
      var lastDay = new Date(d.getFullYear(), d.getMonth(), 0);
      filters.periodStart = isoOf(lm); filters.periodEnd = isoOf(lastDay);
    } else if (preset === "7d") { filters.periodEnd = todayISO(); filters.periodStart = addDaysISO(todayISO(), -6); }
    else return;
    var startEl = document.getElementById("filter-period-start");
    var endEl = document.getElementById("filter-period-end");
    if (startEl) startEl.value = filters.periodStart;
    if (endEl) endEl.value = filters.periodEnd;
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

  var selectedTeamId = qs("team") || null; // ③ 탭 전용 — ①② 탭 블록에 영향 없음(04 §6-1)

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
    if (selectedTeamId) parts.push("team=" + encodeURIComponent(selectedTeamId));
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
      resources:        "/resources" + apiQuery(),
      // PR 7·8(③ 탭). 팀 목록은 필터와 무관, 급증·검토 큐는 CSP·계정만 따른다(04 §4-7·§6-6).
      teams:            "/teams",
      anomalies:        "/cost-anomalies" + apiQuery("status=open"),
      reviewItems:      "/cost-review-items" + scopeQuery("status=open"),
      // CF-034가 큐 항목에 금액을 붙일 때 쓰는 짝 — 큐는 기간 필터가 없으므로 최근 90일을 넓게 본다
      anomaliesAll:     "/cost-anomalies" + scopeQuery("status=all&period_start=" + addDaysISO(todayISO(), -90) + "&period_end=" + addDaysISO(todayISO(), 1)),
      notifications:    "/notifications?limit=50"
    };
    var keys = Object.keys(reqs);
    setBusy(true);
    syncUrl();
    return Promise.all(keys.map(function (k) { return settle(window.MCPApi.request(reqs[k])); }))
      .then(function (results) {
        keys.forEach(function (k, i) { ctx[k] = results[i]; });
        if (ctx.capabilities.ok) capAccounts = ctx.capabilities.value.items || [];
        return loadTeamScoped();
      })
      .then(function () {
        renderFilters(); // select 선택지가 capabilities 응답에서 나온다
        renderAll();
        setBusy(false);
      });
  }

  /** CSP·계정 필터만(기간 없음) — 검토 큐·CF-034 짝 맞추기용 */
  function scopeQuery(extra) {
    var parts = [];
    filters.providers.forEach(function (p) { parts.push("provider=" + encodeURIComponent(p)); });
    filters.accountIds.forEach(function (a) { parts.push("cloud_account_id=" + encodeURIComponent(a)); });
    if (extra) parts.push(extra);
    return parts.length ? "?" + parts.join("&") : "";
  }

  /** 선택 팀이 정해진 뒤에만 부를 수 있는 3종(budget-status·budgets). 팀이 없으면 비운다. */
  function loadTeamScoped() {
    var teams = (ctx.teams && ctx.teams.ok && ctx.teams.value.items) || [];
    if (selectedTeamId && !teams.some(function (t) { return t.id === selectedTeamId; })) selectedTeamId = null;
    if (!selectedTeamId && teams.length) selectedTeamId = teams[0].id;
    if (!selectedTeamId) { ctx.budgetStatus = null; ctx.budgets = null; return Promise.resolve(); }
    return Promise.all([
      settle(window.MCPApi.request("/teams/" + encodeURIComponent(selectedTeamId) + "/budget-status")),
      settle(window.MCPApi.request("/teams/" + encodeURIComponent(selectedTeamId) + "/budgets"))
    ]).then(function (r) { ctx.budgetStatus = r[0]; ctx.budgets = r[1]; });
  }

  /** ③ 탭만 다시 그린다(팀 바꿈·예산 저장 뒤). ①② 탭은 건드리지 않는다. */
  function reloadBudgetTab() {
    return settle(window.MCPApi.request("/teams")).then(function (r) {
      ctx.teams = r;
      return loadTeamScoped();
    }).then(function () { renderAll(); });
  }

  // ── 블록 레지스트리 ─────────────────────────────────────────────────────────────
  var BLOCKS = [
    { id: "CF-001",  tab: "common",   render: renderDataBar },
    { id: "CFL-01",  tab: "common",   render: renderFiltersBlockPlaceholder },
    { id: "CF-002",  tab: "overview", render: renderMtd },
    { id: "CF-003",  tab: "overview", render: renderForecast },
    { id: "CF-004",  tab: "overview", render: renderEstimated },
    { id: "CF-007",  tab: "overview", render: renderUnallocated },
    { id: "CF-009",  tab: "overview", render: renderPlatforms },
    { id: "CF-030",  tab: "overview", render: renderNeedsReview },
    { id: "CF-034",  tab: "overview", render: renderReviewQueue },
    { id: "CF-013",  tab: "analysis", render: renderTrend },
    { id: "CF-016",  tab: "analysis", render: renderServiceShare },
    { id: "EXT-F12", tab: "analysis", render: renderTopIncreases },
    { id: "CF-024",  tab: "analysis", render: renderPeriodCompare },
    { id: "CF-022",  tab: "analysis", render: renderTopResources },
    { id: "CF-018",  tab: "analysis", render: renderByAccount },
    { id: "CFL-03",  tab: "budget",   render: renderTeamSelect },
    { id: "CF-026",  tab: "budget",   render: renderBudgetStatus },
    { id: "CF-025",  tab: "budget",   render: renderBudgetForm },
    { id: "CF-027",  tab: "budget",   render: renderThresholds },
    { id: "CF-033",  tab: "budget",   render: renderAnomalies },
    { id: "CF-039",  tab: "budget",   render: renderAiCta },
    { id: "CF-041",  tab: "budget",   render: function () { return { state: "UNSUPPORTED", html: pendingBlockHtml("비용 보고서 부품(미리보기·CSV)은 보고서 담당과 필요한 데이터·집계 단위·응답 형태를 맞춘 뒤 만듭니다(2026-09-20 결정). 선행 조건: 보고서 비용 섹션 계약 확정.") }; } }
  ];

  /** 나중에 만들어진 <select>에 dropdown.js 룩을 입힌다(CFL-01 필터와 동일 클래스). */
  function enhanceNativeSelects(root) {
    if (!root) return;
    Array.prototype.forEach.call(root.querySelectorAll("select"), function (sel) {
      if (sel.dataset.mcEnhanced) return;
      sel.className = DD_SELECT_CLASS;
      if (window.MCDropdown) window.MCDropdown.enhance(sel);
    });
  }

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
      if (b.tab === "budget") enhanceNativeSelects(content);
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

  // dropdown.js(MCDropdown)의 단일 select 버튼과 같은 룩(inventory.html 필터와 동일 클래스,
  // 07 §7-2 "기존 화면에서 따를 패턴")을 쓴다 — 이 클래스가 없으면 enhance()가 빈 버튼을 만든다.
  var DD_SELECT_CLASS = "rounded-lg border border-border bg-surface px-2 py-1.5 text-xs";
  function ddChevron() {
    return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="opacity:.55;flex:0 0 auto"><path d="M6 9l6 6 6-6"/></svg>';
  }
  /** CSP·계정 드롭다운 행 앞의 작은 체크 표시 — 실제 <input type=checkbox>가 아니라 행 클릭
      하나로만 토글되는 순수 표시용 아이콘이다(체크박스를 진짜로 넣으면 행 클릭과 체크박스
      클릭이 각각 토글을 쏴서 두 번 뒤집히는 문제가 생긴다). */
  function ddCheckbox(checked) {
    return '<svg width="14" height="14" viewBox="0 0 16 16" style="flex:0 0 auto" aria-hidden="true">' +
      '<rect x="1" y="1" width="14" height="14" rx="3" fill="' + (checked ? "var(--primary)" : "none") + '" stroke="' + (checked ? "var(--primary)" : "var(--border)") + '" stroke-width="1.5"/>' +
      (checked ? '<path d="M4 8.2l2.4 2.4L12 5" fill="none" stroke="white" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>' : "") +
      "</svg>";
  }
  /** CSP·계정처럼 다중 선택이 필요한 필터용 드롭다운. dropdown.js가 다루는 건 단일 <select>
      뿐이라(multiple 미지원) 같은 .mc-dd/.mc-dd__menu/.mc-dd__item 모양을 재사용해 직접
      그린다 — inventory.html의 단일 선택 드롭다운(상태·리전 등)과 같은 룩으로 맞추되(체크박스
      없이 행 자체를 클릭·선택 시 배경만 강조), 여러 행을 동시에 선택 상태로 둘 수 있게 한다.
      클래스가 같아서 바깥 클릭·Esc로 닫는 동작은 dropdown.js의 전역 리스너가 그대로 처리한다.
      선택 상태는 각 행의 aria-selected에 있다(숨은 select 없음). */
  function multiSelectDropdownHtml(id, options, selectedValues, allLabel) {
    var selectedSet = {};
    selectedValues.forEach(function (v) { selectedSet[v] = true; });
    var label = !selectedValues.length ? allLabel
      : (selectedValues.length === options.length ? allLabel : selectedValues.length + "개 선택");
    var allRow = '<div class="mc-dd__item flex items-center gap-2 cursor-pointer whitespace-nowrap px-3 py-2 text-sm hover:bg-muted' +
      (!selectedValues.length ? " bg-muted font-medium" : "") + '" data-dd-multi-all>' + esc(allLabel) + "</div>";
    var items = options.map(function (o) {
      var isSel = !!selectedSet[o.value];
      return '<div class="mc-dd__item flex items-center gap-2 cursor-pointer whitespace-nowrap px-3 py-2 text-sm hover:bg-muted' +
        (isSel ? " bg-muted font-medium" : "") + '" data-dd-multi-item data-value="' + esc(o.value) +
        '" aria-selected="' + (isSel ? "true" : "false") + '">' + ddCheckbox(isSel) + esc(o.label) + "</div>";
    }).join("");
    return '<div class="mc-dd relative" id="' + id + '">' +
      '<button type="button" class="' + DD_SELECT_CLASS + ' flex w-full items-center justify-between gap-2 text-left" data-dd-toggle>' +
        '<span class="mc-dd__label truncate min-w-0">' + esc(label) + "</span>" + ddChevron() +
      "</button>" +
      '<div class="mc-dd__menu hidden absolute left-0 top-full z-50 mt-1 min-w-full max-h-64 overflow-auto rounded-xl border border-border bg-surface py-1 shadow-lg">' +
        allRow + (items || '<div class="tiny muted px-3 py-2">선택지가 없습니다</div>') +
      "</div></div>";
  }
  function selectedDropdownValues(wrapId) {
    var wrap = document.getElementById(wrapId);
    if (!wrap) return [];
    return Array.prototype.map.call(wrap.querySelectorAll('[data-dd-multi-item][aria-selected="true"]'), function (el) { return el.getAttribute("data-value"); });
  }
  /** 행의 선택 상태(강조 배경 + 체크 아이콘)를 한 곳에서 맞춘다. 아이콘은 인라인 SVG라
      checked를 다시 그려야 한다 — textContent로 라벨 글자만 뽑아 체크 아이콘과 다시 합친다. */
  function setDdItemChecked(el, checked) {
    var labelText = el.textContent;
    el.setAttribute("aria-selected", checked ? "true" : "false");
    el.classList.toggle("bg-muted", checked);
    el.classList.toggle("font-medium", checked);
    el.innerHTML = ddCheckbox(checked) + esc(labelText);
  }
  /** 행을 클릭할 때마다 버튼 라벨(선택 개수)·강조 표시만 즉시 갱신한다 — 전체 재조회는
      '적용'을 눌러야 일어난다(03-2). */
  function refreshDropdownLabel(wrapId, allLabel, totalCount) {
    var wrap = document.getElementById(wrapId);
    if (!wrap) return;
    var n = selectedDropdownValues(wrapId).length;
    var labelEl = wrap.querySelector(".mc-dd__label");
    if (labelEl) labelEl.textContent = (!n || n === totalCount) ? allLabel : n + "개 선택";
  }

  function filterGridHtml() {
    return '<div class="filter-grid">' +
      '<div class="filter-field">기간 프리셋' +
        '<span class="button-row" style="margin-top:0">' +
          '<button type="button" class="btn" data-action="preset-period" data-preset="mtd">이번 달</button>' +
          '<button type="button" class="btn" data-action="preset-period" data-preset="last-month">지난 달</button>' +
          '<button type="button" class="btn" data-action="preset-period" data-preset="7d">최근 7일</button>' +
        "</span>" +
      "</div>" +
      '<label class="filter-box">시작일<input type="date" id="filter-period-start" class="' + DD_SELECT_CLASS + '"></label>' +
      '<label class="filter-box">종료일<input type="date" id="filter-period-end" class="' + DD_SELECT_CLASS + '"></label>' +
      '<div class="filter-field filter-box">CSP<div id="filter-provider-field"></div></div>' +
      '<div class="filter-field filter-box">계정<div id="filter-account-field"></div></div>' +
      '<label class="filter-box">통화<select id="filter-currency"><option value="">전체 통화</option></select></label>' +
      '<label class="filter-box">요금 분류<select id="filter-charge-category">' +
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

    var provField = document.getElementById("filter-provider-field");
    if (provField) {
      provField.innerHTML = multiSelectDropdownHtml(
        "filter-provider-dd",
        [{ value: "aws", label: "AWS" }, { value: "azure", label: "Azure" }, { value: "gcp", label: "GCP" }],
        filters.providers, "전체 CSP"
      );
    }

    var accField = document.getElementById("filter-account-field");
    if (accField) {
      var visible = capAccounts.filter(function (a) {
        return !filters.providers.length || filters.providers.indexOf(a.provider) >= 0;
      });
      accField.innerHTML = multiSelectDropdownHtml(
        "filter-account-dd",
        visible.map(function (a) { return { value: a.cloud_account_id, label: (a.account_label || a.external_account_id) + " (" + a.provider.toUpperCase() + ")" }; }),
        filters.accountIds, "전체 계정"
      );
    }

    var currSel = document.getElementById("filter-currency");
    if (currSel) {
      var currencies = [];
      capAccounts.forEach(function (a) { if (a.currency && currencies.indexOf(a.currency) < 0) currencies.push(a.currency); });
      currSel.innerHTML = '<option value="">전체 통화</option>' +
        currencies.map(function (c) { return '<option value="' + esc(c) + '">' + esc(c) + "</option>"; }).join("");
      // className을 매번 다시 쓰면 enhance()가 붙인 sr-only가 지워져 네이티브 select가 다시
      // 드러난다 — 처음 한 번만 클래스를 입히고, 그 뒤로는 enhance()의 결과물(버튼+메뉴)을
      // 건드리지 않는다.
      if (!currSel.dataset.mcEnhanced) currSel.className = DD_SELECT_CLASS;
      currSel.value = filters.currency;
      currSel.dispatchEvent(new Event("change"));
      if (window.MCDropdown) window.MCDropdown.enhance(currSel);
    }

    var ccSel = document.getElementById("filter-charge-category");
    if (ccSel) {
      if (!ccSel.dataset.mcEnhanced) ccSel.className = DD_SELECT_CLASS;
      ccSel.value = filters.chargeCategory;
      ccSel.dispatchEvent(new Event("change"));
      if (window.MCDropdown) window.MCDropdown.enhance(ccSel);
    }

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
    filters.providers = selectedDropdownValues("filter-provider-dd");
    filters.accountIds = selectedDropdownValues("filter-account-dd");
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

  // ── CF-007 · CF-030 공통: Owner 미지정 리소스 고르기 ─────────────────────────────
  // tags에 OWNER_TAG_KEY가 없거나 값이 비어 있으면 미지정으로 본다 — 키만 있고 값이 빈 태그는
  // 담당자를 정한 것이 아니다. 실측 기준 배분(확정 7, 설계만)이 아니라 정가 추정치를 고르는 것뿐이다.
  function isOwnerUnassigned(r) {
    var tags = r && r.tags;
    if (!tags || typeof tags !== "object") return true;
    if (!Object.prototype.hasOwnProperty.call(tags, OWNER_TAG_KEY)) return true;
    var v = tags[OWNER_TAG_KEY];
    return v == null || String(v).trim() === "";
  }
  function unassignedResources() {
    var items = (ctx.resources && ctx.resources.ok && ctx.resources.value.items) || [];
    return items.filter(isOwnerUnassigned);
  }

  // ── CF-007 미할당 비용 · Owner 미지정 (04 §4-4) ────────────────────────────────────
  // CF-004와 같은 정가 기준(현재 구성 × 730h)이라 기간 필터와 무관하다 — /resources가 period_*를
  // 받지 않으므로 구조적으로도 안 변한다. 금액은 CF-004의 부분집합이지 CF-004에서 뺀 값이 아니다.
  function renderUnallocated() {
    if (!ctx.resources || !ctx.resources.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.resources, "미할당 비용") };
    var unassigned = unassignedResources();
    if (!unassigned.length) {
      // $0.00이 아니다 — 0원이 아니라 셀 대상이 없는 것이다.
      return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="note">' + esc(OWNER_TAG_KEY) + "가 비어 있는 리소스가 없습니다.</p>" };
    }
    var priced = [], noPrice = 0;
    unassigned.forEach(function (r) {
      var cs = r.cost_summary;
      if (!cs || cs.estimated_monthly_cost == null) { noPrice++; return; } // 정가 없음은 개수로만 센다
      priced.push({ amount: cs.estimated_monthly_cost, cost_kind: "list_price_estimate", currency: cs.currency, period_start: "current", period_end: "current" });
    });
    var guarded = F.sumWithGuard(priced); // 통화가 다르면 합치지 않고 줄을 늘린다
    var lines = guarded.groups.map(function (g) { return { amount: g.total, currency: g.currency }; });
    var html = lines.length ? moneyBadgeLines(lines, "Estimated 중 미할당") : '<div class="value">—</div>';
    html += '<p class="small muted">' + esc(OWNER_TAG_KEY) + " 미지정 " + unassigned.length + "개 · 전체와 중복되는 부분집합</p>" +
      (noPrice > 0 ? '<p class="note">정가표에 없어 금액에서 제외된 리소스 ' + noPrice + "개(개수에는 포함)</p>" : "") +
      '<p class="note">정가 기준(현재 구성 × 730h) · 기간 필터와 무관 · 태그 기준 실측 배분이 아닙니다</p>' +
      '<div class="button-row no-print"><button type="button" class="btn" data-action="nav-scroll" data-tab="overview" data-target="CF-034">미지정 대상 보기</button></div>';
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-030 원인 확인이 필요한 비용 (04 §4-6) ───────────────────────────────────────
  // 경고 2장. ②는 계정별로 세 가지를 가른다 — ㉮ 실행 중 + 실측 0원 / ㉯ 리소스 0개 + 실측 양수 /
  // ㉰ CONNECTED_EMPTY(정상 0, 경고 아님). 셋을 한 경고로 합치면 정상 0을 문제로 보고하게 된다.
  // 문구는 "누수"가 아니라 "원인 확인 필요"(확정 13) — 조직 소유권을 단정하지 않는다.
  function isZeroAmount(amount) { return amount != null && !/[1-9]/.test(String(amount)); }
  function isPositiveAmount(amount) { return amount != null && /[1-9]/.test(String(amount)) && String(amount).trim().charAt(0) !== "-"; }
  function accountLabelOf(a) { return a.account_label || a.external_account_id || a.cloud_account_id; }

  function renderNeedsReview() {
    var warnings = [], notes = [];

    // ① Owner 미지정 개수 — /resources
    if (ctx.resources && ctx.resources.ok) {
      var n = unassignedResources().length;
      if (n > 0) {
        warnings.push(esc(OWNER_TAG_KEY) + " 태그가 없는 리소스 " + n + "개 — 태그 기준 검토 후보입니다. 담당자를 정하면 정가 추정치를 나눠 볼 수 있습니다." +
          ' <button type="button" class="btn no-print" data-action="nav-scroll" data-tab="overview" data-target="CF-007">미할당 비용 보기</button>');
      }
    } else {
      notes.push("리소스 목록을 가져오지 못해 " + esc(OWNER_TAG_KEY) + " 미지정 개수를 판정하지 못했습니다.");
    }

    // ② 정상 0 · 미수집 · 과거 비용 — /costs/summary accounts[] (기간 필터는 여기만 탄다)
    if (ctx.summary && ctx.summary.ok) {
      (ctx.summary.value.accounts || []).forEach(function (a) {
        var label = esc(accountLabelOf(a));
        var pv = esc((PROVIDER_LABEL[a.provider] || a.provider || "").toString());
        var hasData = a.status === "CONNECTED_OK" || a.status === "CONNECTED_PARTIAL";
        if (a.status === "CONNECTED_EMPTY") {
          // ㉰ 정상 0 — 경고가 아니다
          notes.push(pv + " · " + label + ": 조회 기간에 실측 비용이 0입니다(정상 0 · 수집은 정상).");
        } else if (hasData && a.resource_count > 0 && isZeroAmount(a.actual)) {
          // ㉮ 실행 중 + 실측 0원
          warnings.push(pv + " · " + label + ": 리소스 " + a.resource_count + "개가 있는데 조회 기간 실측이 " +
            esc(F.money(a.actual, a.currency)) + "입니다 — 무료 티어·크레딧 적용 가능성이 있습니다. 청구서와 대조해 원인 확인이 필요합니다.");
        } else if (hasData && a.resource_count === 0 && isPositiveAmount(a.actual)) {
          // ㉯ 리소스 0개 + 실측 양수
          warnings.push(pv + " · " + label + ": 현재 리소스가 0개인데 조회 기간 실측이 " +
            esc(F.money(a.actual, a.currency)) + "입니다 — 삭제 전 사용료·미동기화 리소스 등 원인 확인이 필요합니다.");
        }
      });
    } else {
      notes.push("실측 요약을 가져오지 못해 정상 0 · 미수집 · 과거 비용 판정을 하지 못했습니다.");
    }

    if (!warnings.length && !notes.length) {
      return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="note">확인이 필요한 항목이 없습니다.</p>' };
    }
    var html = '<div class="paired">' +
      (warnings.length ? warnings.map(function (w) { return '<p class="warning">' + w + "</p>"; }).join("")
                       : '<p class="note">경고 없음 — 확인이 필요한 항목이 없습니다.</p>') +
      "</div>" +
      notes.map(function (t) { return '<p class="note">' + t + "</p>"; }).join("") +
      '<div class="button-row no-print"><button type="button" class="btn" data-action="nav-scroll" data-tab="overview" data-target="CF-034">검토 목록 보기</button></div>';
    return { state: "CONNECTED_OK", html: html };
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
    var disp = toDisplayRange(c.period_start, c.period_end);
    var html = '<div class="cta-row">' +
      '<p class="small muted">현재 조건: ' + esc(disp.start) + " ~ " + esc(disp.end) + " · " +
        esc(c.providers ? c.providers.join("/").toUpperCase() : "전체 CSP") + " · " +
        esc(c.cloud_account_ids ? "계정 " + c.cloud_account_ids.length + "개" : "전체 계정") +
        (selectedTeamId ? " · 팀 " + esc(teamNameOf(selectedTeamId)) : "") + "</p>" +
      '<button type="button" class="btn yellow" data-modal-open="#agent-chat-modal">AI 비용 상담</button>' +
      "</div>" +
      '<p class="note">AI가 직접 계산하지 않습니다. 위 조건(기간·CSP·계정·팀)을 서버에 그대로 보내고, 서버가 계산한 값으로만 답합니다. 금액은 화면에서 보내지 않습니다.</p>';
    return { state: "CONNECTED_OK", html: html };
  }

  // agent.js가 요청 body의 conditions로 넣는다(PR 8 선택 필드) — 필터만, 금액 없음.
  window.MCPAgentConditions = function () {
    var api = toApiRange(filters.periodStart, filters.periodEnd);
    return {
      period_start: api.period_start, period_end: api.period_end,
      provider: filters.providers.slice(), cloud_account_id: filters.accountIds.slice(),
      team_id: selectedTeamId ? [selectedTeamId] : []
    };
  };

  // ═══════════════════════════════════════════════════════════════════════════════
  // ③ 예산·검토 탭 — PR 7(팀·예산) · PR 8(급증·검토 큐·가격 비교) 연동 (2026-09-20)
  // 화면은 소진률·증가율을 다시 계산하지 않는다 — 서버 값을 그대로 찍는다(04 §6-2).
  // ═══════════════════════════════════════════════════════════════════════════════

  function teamsList() { return (ctx.teams && ctx.teams.ok && ctx.teams.value.items) || []; }
  function teamById(id) { return teamsList().filter(function (t) { return t.id === id; })[0] || null; }
  function teamNameOf(id) { var t = teamById(id); return t ? t.name : "—"; }
  function fmtDateTime(iso) { return iso ? new Date(iso).toLocaleString("ko-KR") : "—"; }
  function accountLabelOf(id) {
    var a = capAccounts.filter(function (x) { return x.cloud_account_id === String(id); })[0];
    return a ? (a.account_label || a.external_account_id) : "계정 " + id;
  }
  /** 서버 오류를 사람 문구로 — details.reason이 있으면 그쪽을 우선 해석한다(04 §6-1·§6-3 표). */
  function serverErrorText(e) {
    var reason = e && e.details && e.details[0] && e.details[0].reason;
    var map = {
      team_has_collected_costs: "이미 수집된 비용이 있어 통화를 바꿀 수 없습니다. 새 팀을 만들어 주세요.",
      account_already_in_team: "이미 다른 팀(" + (e && e.details && e.details[0] && teamNameOf(e.details[0].team_id)) + ")에 속한 계정입니다. 옮기려면 그 팀에서 먼저 빼 주세요.",
      active_recurring_exists: "이미 활성 반복 예산이 있습니다. 더 늦은 적용 시작일로 새 예산을 만들어 주세요(한도 변경은 새 행으로 남습니다).",
      custom_period_overlap: "기간이 겹치는 사용자 지정 예산이 이미 있습니다.",
      range_too_long: "사용자 지정 예산 기간은 최대 1년입니다.",
      budget_already_started: "이미 시작된 예산은 수정할 수 없습니다. 한도를 바꾸려면 새 예산을 만들어 주세요.",
      create_new_budget: "적용 시작일·주기·통화는 바꿀 수 없습니다. 새 예산을 만들어 주세요.",
      already_resolved: "종결된 항목은 되돌릴 수 없습니다. 재발은 새 항목으로 등록됩니다.",
      not_current_anomaly: "현재 규칙으로 탐지된 급증이 아니라 큐에 넣지 않습니다.",
      not_yet_eligible: "아직 판정 대상이 아닌 날짜입니다(최근 3일 제외).",
      duplicate: "같은 이름의 팀이 이미 있습니다."
    };
    if (reason && map[reason]) return map[reason];
    return window.MCErr ? window.MCErr.headline(e) : (e && e.message) || "요청에 실패했습니다.";
  }

  // ── CFL-03 팀 선택 · 계정 묶음 ─────────────────────────────────────────────────────
  function renderTeamSelect() {
    if (!ctx.teams || !ctx.teams.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.teams, "팀 목록") };
    var d = ctx.teams.value;
    var teams = d.items || [];
    var unassigned = d.unassigned || { account_count: 0, accounts: [] };
    var html = "";
    if (!teams.length) {
      html += '<div class="state-view" role="status"><span class="dash">—</span><p>팀이 아직 없습니다. 팀을 만들고 계정을 묶으면 예산·임계 알림을 쓸 수 있습니다.</p>' +
        '<button type="button" class="btn primary no-print" data-action="open-team-dialog">팀 만들기</button></div>';
    } else {
      html += '<div class="filter-grid">' +
        '<label class="filter-box" style="flex-basis:260px">팀 선택<select id="team-select">' +
        teams.map(function (t) {
          return '<option value="' + esc(t.id) + '"' + (t.id === selectedTeamId ? " selected" : "") + ">" +
            esc(t.name) + " · " + t.account_count + "개 계정 · " + esc(t.currency) + "</option>";
        }).join("") + "</select></label>" +
        '<span class="button-row" style="margin-top:0"><button type="button" class="btn no-print" data-action="open-team-dialog">팀 관리</button></span>' +
        "</div>";
      var t = teamById(selectedTeamId);
      if (t) {
        var mismatched = (t.accounts || []).filter(function (a) { return a.currency_matches_team === false; });
        html += '<div class="status-list" style="margin-top:14px">' +
          "<div><span>계정</span><span>" + (t.accounts.length ? t.accounts.map(function (a) {
            return esc(PROVIDER_LABEL[a.provider] || a.provider) + " " + esc(a.account_label || a.external_account_id) +
              (a.currency ? " (" + esc(a.currency) + ")" : " (통화 미확인)") +
              (a.currency_matches_team === false ? ' <span class="badge" style="color:var(--cost-warn)">팀 통화와 다름</span>' : "");
          }).join(" · ") : "배정된 계정 없음") + "</span></div>" +
          "<div><span>팀 통화</span><span>" + esc(t.currency) + "</span></div>" +
          "<div><span>현재 예산</span><span>" + (t.active_budget
            ? esc(F.money(t.active_budget.limit_amount, t.active_budget.currency)) + " · " + esc(PERIOD_LABEL[t.active_budget.period_type] || t.active_budget.period_type)
            : "예산 미설정") + "</span></div>" +
          "</div>";
        if (mismatched.length) html += '<p class="warning">팀 통화(' + esc(t.currency) + ")와 다른 통화의 계정 " + mismatched.length + "개는 예산 계산에서 제외됩니다. 팀에서 자동으로 빠지지는 않습니다.</p>";
      }
    }
    html += '<p class="note">미배정 계정 ' + unassigned.account_count + "개" +
      (unassigned.accounts && unassigned.accounts.length ? ": " + unassigned.accounts.map(function (a) { return esc(PROVIDER_LABEL[a.provider] || a.provider) + " " + esc(a.account_label || a.external_account_id); }).join(", ") : "") +
      " · 팀 선택은 이 탭(예산·검토)에만 적용됩니다.</p>";
    return { state: "CONNECTED_OK", html: html };
  }

  var PERIOD_LABEL = { monthly: "월간", quarterly: "분기", annual: "연간", custom: "사용자 지정" };

  function teamDialogHtml() {
    var teams = teamsList();
    var t = teamById(selectedTeamId);
    var allAccounts = capAccounts.slice();
    var html = '<h3 class="small" style="font-weight:700">새 팀</h3>' +
      '<div class="filter-grid" style="margin-top:6px">' +
      '<label class="filter-box" style="flex-basis:200px">이름<input id="team-new-name" type="text" maxlength="200" placeholder="예: 운영팀"></label>' +
      '<label class="filter-box">통화<select id="team-new-currency"><option value="USD">USD</option><option value="KRW">KRW</option></select></label>' +
      '<span class="button-row" style="margin-top:0"><button type="button" class="btn primary" data-action="team-create">만들기</button></span></div>' +
      '<p id="team-feedback" class="note" role="status"></p>';
    if (t) {
      var memberIds = (t.accounts || []).map(function (a) { return a.cloud_account_id; });
      html += '<hr style="margin:14px 0;border:0;border-top:1px solid var(--border)">' +
        '<h3 class="small" style="font-weight:700">선택한 팀: ' + esc(t.name) + "</h3>" +
        '<div class="filter-grid" style="margin-top:6px">' +
        '<label class="filter-box" style="flex-basis:200px">이름<input id="team-edit-name" type="text" maxlength="200" value="' + esc(t.name) + '"></label>' +
        '<label class="filter-box">통화<select id="team-edit-currency"><option value="USD"' + (t.currency === "USD" ? " selected" : "") + '>USD</option><option value="KRW"' + (t.currency === "KRW" ? " selected" : "") + ">KRW</option></select></label>" +
        '<span class="button-row" style="margin-top:0"><button type="button" class="btn" data-action="team-patch" data-team-id="' + esc(t.id) + '">이름·통화 저장</button></span></div>' +
        '<p class="note">수집된 비용이 있는 팀은 통화를 바꿀 수 없습니다(서버가 거절).</p>' +
        '<h3 class="small" style="font-weight:700;margin-top:12px">계정 배정 (1계정 1팀)</h3>' +
        '<div class="status-list" style="margin-top:6px">' +
        (allAccounts.length ? allAccounts.map(function (a) {
          var inOther = a.team_id && a.team_id !== t.id;
          return '<div><label style="display:flex;gap:8px;align-items:center"><input type="checkbox" class="team-account-cb" value="' + esc(a.cloud_account_id) + '"' +
            (memberIds.indexOf(a.cloud_account_id) >= 0 ? " checked" : "") + (inOther ? " disabled" : "") + "> " +
            esc(PROVIDER_LABEL[a.provider] || a.provider) + " " + esc(a.account_label || a.external_account_id) +
            (inOther ? ' <span class="tiny muted">(' + esc(teamNameOf(a.team_id)) + " 소속 — 옮기려면 그 팀에서 먼저 빼세요)</span>" : "") +
            "</label></div>";
        }).join("") : '<div><span class="tiny muted">연결된 계정이 없습니다</span></div>') +
        "</div>" +
        '<div class="button-row"><button type="button" class="btn primary" data-action="team-put-accounts" data-team-id="' + esc(t.id) + '">배정 저장</button>' +
        '<button type="button" class="btn" data-action="team-delete" data-team-id="' + esc(t.id) + '" data-team-name="' + esc(t.name) + '">팀 삭제</button></div>' +
        '<p id="team-edit-feedback" class="note" role="status"></p>';
    }
    return html;
  }

  function setFeedback(id, text, isError) {
    var el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.style.color = isError ? "var(--cost-up)" : "";
  }

  // ── CF-026 이번 달 예산 대비 실적 ─────────────────────────────────────────────────────
  var REASON_TEXT = {
    NO_BUDGET: "예산이 설정되지 않았습니다.",
    NO_ACCOUNTS: "이 팀에 배정된 계정이 없습니다.",
    MISSING_DAYS: "기간 안에 수집되지 않은 날이 있어 소진률을 판정하지 않습니다.",
    CURRENCY_MISMATCH: "팀 통화와 다른 통화의 계정이 있어 합산하지 않습니다.",
    UNSUPPORTED: "이 팀의 계정은 아직 비용 수집을 지원하지 않습니다.",
    NO_COMPLETED_DAYS: "이 예산 구간에 완료된 날이 아직 없습니다(월초). 이전 구간의 예산을 대신 보여주지 않습니다."
  };
  var REASON_ACTION = {
    NO_BUDGET: '<button type="button" class="btn no-print" data-action="nav-scroll" data-tab="budget" data-target="CF-025">예산 설정</button>',
    NO_ACCOUNTS: '<button type="button" class="btn no-print" data-action="open-team-dialog">계정 배정</button>',
    MISSING_DAYS: '<button type="button" class="btn no-print" data-action="nav-scroll" data-tab="overview" data-target="CF-001">수집 상태 보기</button>'
  };

  function budgetScopeNote(b) {
    return '<p class="note">예산 기간 ' + esc(b.period_start) + " ~ " + esc(addDaysISO(b.period_end, -1)) + " · 기준일 " + esc(b.basis_date) +
      " · 팀 전체 계정 · 현재 소속 기준 · <strong>상단 필터 미적용</strong></p>";
  }

  function renderBudgetStatus() {
    if (!selectedTeamId) return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="note">팀을 먼저 선택하세요.</p>' };
    if (!ctx.budgetStatus || !ctx.budgetStatus.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.budgetStatus, "예산 대비 실적") };
    var d = ctx.budgetStatus.value;
    var b = d.budget;
    var html = "";
    if (!d.computable) {
      html += '<div class="state-view" role="status"><span class="dash">—</span><p>' + esc(REASON_TEXT[d.reason_code] || ("판정하지 않습니다 (" + d.reason_code + ")")) + "</p>" +
        (d.reason_code === "CURRENCY_MISMATCH" && d.excluded_accounts.length ? "<p>제외 계정: " + d.excluded_accounts.map(function (a) { return esc(PROVIDER_LABEL[a.provider] || a.provider) + " " + esc(a.account_label || a.cloud_account_id) + " (" + esc(a.currency || "통화 미확인") + ")"; }).join(", ") + "</p>" : "") +
        (d.usage && d.reason_code === "MISSING_DAYS" ? '<p class="tiny muted">지금까지 합산된 사용액 ' + esc(F.money(d.usage.amount, d.usage.currency)) + " (판정 보류 — 결측일이 채워지면 소진률이 나옵니다)</p>" : "") +
        (REASON_ACTION[d.reason_code] || "") + "</div>";
      if (b) html += budgetScopeNote(b);
      return { state: "CONNECTED_OK", html: html };
    }
    var ratio = Number(d.ratio_pct);
    var remaining = F.subDecimalString(b.limit_amount, d.usage.amount); // 음수 보존(초과분)
    var over = ratio > 100;
    html += '<div class="paired">' +
      '<div><div class="value">' + esc(F.money(d.usage.amount, d.usage.currency)) + ' <span class="tiny muted">/ ' + esc(F.money(b.limit_amount, b.currency)) + "</span></div>" +
        '<p class="small">소진률 <strong>' + esc(d.ratio_pct) + "%</strong> · 잔여 " + (over ? '<span class="money-positive">' : "") + esc(F.money(remaining, b.currency)) + (over ? "</span>" : "") +
        (d.forecast ? " · 전망 " + esc(d.forecast.ratio_pct) + "% (" + esc(F.money(d.forecast.amount, b.currency)) + ", " + esc(d.forecast.based_through) + "까지 실측 기준)" : "") + "</p>" +
        '<div class="bar-track" aria-hidden="true"><div class="bar-fill" style="width:' + Math.min(ratio, 100) + '%"></div></div>' +
        (over ? '<div class="bar-track" style="margin-top:3px"><div class="bar-fill" style="width:' + Math.min(ratio - 100, 100) + '%;background:var(--cost-up)"></div></div><p class="tiny muted">두 번째 막대 = 100% 초과분</p>' : "") +
      "</div>" +
      '<div>' + (over ? '<p class="warning">한도를 ' + esc(F.money(F.subDecimalString(d.usage.amount, b.limit_amount), b.currency)) + " 초과했습니다. 생성 차단은 이번 범위가 아닙니다 — 알림까지입니다.</p>" : '<p class="note">한도 이내입니다.</p>') +
        '<div class="status-list">' + d.thresholds.map(function (t) {
          return "<div><span>" + t.percent + "%</span><span>" + (t.crossed ? "도달" : "미도달") + (t.notified ? " · 알림 발송" : (t.crossed ? " · 알림 대기" : "")) + "</span></div>";
        }).join("") + "</div>" +
        '<p class="tiny muted">사용액 기준: ' + esc(d.usage.basis) + " (크레딧·환불 제외) · 순액 " + esc(F.money(d.usage.net_amount, d.usage.currency)) + (d.usage.is_estimated ? " · 잠정치 포함" : "") + "</p>" +
      "</div></div>" +
      budgetScopeNote(b) +
      '<div class="button-row no-print"><button type="button" class="btn" data-action="nav-scroll" data-tab="budget" data-target="CF-025">예산 편집</button></div>';
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-025 예산 설정 ───────────────────────────────────────────────────────────────
  var STATE_LABEL = { upcoming: "예정", in_progress: "진행 중", ended: "종료" };

  function renderBudgetForm() {
    if (!selectedTeamId) return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="note">팀을 먼저 선택하세요.</p>' };
    var t = teamById(selectedTeamId);
    var items = (ctx.budgets && ctx.budgets.ok && ctx.budgets.value.items) || [];
    var html = '<div class="filter-grid">' +
      '<div class="filter-field">주기<span class="button-row" style="margin-top:0;gap:10px">' +
        ["monthly", "quarterly", "annual", "custom"].map(function (k, i) {
          return '<label style="display:inline-flex;gap:4px;align-items:center;font-size:12px"><input type="radio" name="budget-period-type" value="' + k + '"' + (i === 0 ? " checked" : "") + "> " + PERIOD_LABEL[k] + "</label>";
        }).join("") + "</span></div>" +
      '<label class="filter-box">시작일<input id="budget-start" type="date" value="' + esc(startOfMonthISO()) + '"></label>' +
      '<label class="filter-box" id="budget-end-wrap" hidden>종료일(포함)<input id="budget-end" type="date"></label>' +
      '<label class="filter-box">한도<input id="budget-value" type="number" min="0" step="0.01" placeholder="300"></label>' +
      '<label class="filter-box" style="flex-basis:80px">통화<input type="text" value="' + esc(t ? t.currency : "USD") + '" disabled title="예산 통화는 팀 통화와 같아야 합니다"></label>' +
      '<span class="button-row" style="margin-top:0"><button type="button" class="btn primary no-print" data-action="budget-create">저장</button>' +
      '<button type="button" class="btn no-print" data-action="budget-reset">취소</button></span>' +
      "</div>" +
      '<p id="budget-feedback" class="note" role="status">한도를 바꾸면 기존 행을 고치지 않고 새 행이 생깁니다(이력 보존). 예정 예산만 오타 수정할 수 있습니다.</p>' +
      '<div class="table-wrap"><table><thead><tr><th scope="col">주기</th><th scope="col">적용 시작</th><th scope="col">종료</th><th scope="col">한도</th><th scope="col">상태</th><th scope="col">행동</th></tr></thead><tbody>' +
      (items.length ? items.map(function (b) {
        return "<tr><td>" + esc(PERIOD_LABEL[b.period_type] || b.period_type) + "</td><td>" + esc(b.start_date) + "</td><td>" + esc(b.end_date ? addDaysISO(b.end_date, -1) : "—") + "</td>" +
          '<td class="num">' + esc(F.money(b.limit_amount, b.currency)) + '</td><td><span class="badge">' + esc(STATE_LABEL[b.state] || b.state) + "</span></td><td>" +
          (b.state === "upcoming" ? '<button type="button" class="btn no-print" data-action="budget-patch" data-budget-id="' + esc(b.id) + '" data-period-type="' + esc(b.period_type) + '">한도 수정</button> ' +
            '<button type="button" class="btn no-print" data-action="budget-delete" data-budget-id="' + esc(b.id) + '">삭제</button>' : '<span class="tiny muted">수정 불가 — 새 행으로</span>') +
          "</td></tr>";
      }).join("") : '<tr><td colspan="6" class="tiny muted">예산이 없습니다 — 예산 미설정은 $0이 아닙니다</td></tr>') +
      "</tbody></table></div>";
    return { state: "CONNECTED_OK", html: html };
  }

  function budgetFormValues() {
    var pt = (document.querySelector('input[name="budget-period-type"]:checked') || {}).value || "monthly";
    var start = (document.getElementById("budget-start") || {}).value || "";
    var endDisp = (document.getElementById("budget-end") || {}).value || "";
    var limit = (document.getElementById("budget-value") || {}).value || "";
    var body = { period_type: pt, start_date: start, limit_amount: limit };
    if (pt === "custom") body.end_date = endDisp ? addDaysISO(endDisp, 1) : null; // 화면은 포함, API는 제외 경계
    return body;
  }

  // ── CF-027 임계치 알림 ─────────────────────────────────────────────────────────────
  function renderThresholds() {
    if (!selectedTeamId) return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="note">팀을 먼저 선택하세요.</p>' };
    if (!ctx.budgetStatus || !ctx.budgetStatus.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.budgetStatus, "임계치 알림") };
    var d = ctx.budgetStatus.value;
    var grey = !d.computable;
    var html = '<div class="status-list"' + (grey ? ' style="opacity:.55"' : "") + ">" + d.thresholds.map(function (t) {
      var text = grey ? "판정하지 않습니다" : (t.crossed ? "도달" + (t.crossed_at ? " · " + fmtDateTime(t.crossed_at) : "") : "미도달");
      if (!grey && t.crossed && !t.notified) text += ' · <span style="color:var(--cost-warn)">알림 미발송 — 다음 수집 평가에서 발송</span>';
      if (!grey && t.notified) text += " · 알림 발송됨";
      return "<div><span>" + t.percent + "%</span><span>" + text + "</span></div>";
    }).join("") + "</div>";
    if (grey) html += '<p class="note">' + esc(REASON_TEXT[d.reason_code] || d.reason_code || "") + "</p>";
    var notes = ((ctx.notifications && ctx.notifications.ok && ctx.notifications.value.items) || []).filter(function (n) {
      return n.type === "budget_threshold" && n.message_params && n.message_params.team_id === selectedTeamId;
    }).slice(0, 5);
    html += '<p class="small" style="margin-top:12px;font-weight:600">최근 알림</p>' +
      (notes.length ? '<div class="status-list">' + notes.map(function (n) {
        var p = n.message_params;
        return "<div><span>" + esc(fmtDateTime(n.created_at)) + "</span><span>" + esc(p.team_name) + " 예산 " + p.percent + "% 도달 (" + esc(p.period_start) + " 시작 구간, 한도 " + esc(F.money(p.limit_amount, p.currency)) + ")</span></div>";
      }).join("") + "</div>" : '<p class="tiny muted">이 팀의 임계 알림이 아직 없습니다.</p>') +
      '<p class="note">같은 (예산·구간·임계치)에는 알림이 한 번만 갑니다. 예산 초과 시 생성 차단은 이번 범위가 아닙니다.</p>' +
      '<div class="button-row no-print"><button type="button" class="btn" data-action="open-threshold-dialog">알림 조건 보기</button></div>';
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-033 급증 탐지 ───────────────────────────────────────────────────────────────
  function ruleText(rule) {
    return "규칙: 계정×서비스×완료된 날 · 직전 " + rule.baseline_days + "일 평균 대비 증가액 ≥ " + esc(F.money(rule.min_delta_amount, rule.currency)) +
      " AND 증가율 ≥ " + rule.min_increase_pct + "% · 이력 " + rule.min_history_days + "일 이상 · 최근 " + rule.exclude_recent_days + "일 제외 · " +
      esc(rule.charge_category.join("/")) + " 기준 · 임계 통화 " + esc(rule.currency);
  }
  function anomalyPctText(it) {
    return it.delta_pct == null ? "신규 비용 발생(기준선 0 — 증가율 없음)" : "+" + esc(it.delta_pct) + "%";
  }

  function renderAnomalies() {
    if (!ctx.anomalies || !ctx.anomalies.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.anomalies, "급증 탐지") };
    var d = ctx.anomalies.value;
    var rows = (d.items || []).map(function (it) {
      return "<tr><td>" + esc(it.date) + "<br><span class=\"tiny muted\">" + esc(PROVIDER_LABEL[it.provider] || it.provider) + " " + esc(accountLabelOf(it.cloud_account_id)) + " · " + esc(it.service) + "</span>" +
        (it.is_sample_data ? ' <span class="badge">예시 데이터</span>' : "") + "</td>" +
        '<td class="num">' + esc(F.money(it.baseline_amount, it.currency)) + " → " + esc(F.money(it.amount, it.currency)) + "</td>" +
        '<td class="num"><span class="money-positive">+' + esc(F.money(it.delta, it.currency)) + "</span><br><span class=\"tiny muted\">" + anomalyPctText(it) + " · 그 날의 증가액</span></td>" +
        "<td>" + esc(it.label) + (it.review ? '<br><span class="badge">검토 ' + esc(it.review.status) + "</span>" : "") + "</td>" +
        '<td><button type="button" class="btn no-print" data-action="open-anomaly-dialog" data-source-key="' + esc(it.source_key) + '">근거 보기</button></td></tr>';
    });
    var html = '<p class="note">' + ruleText(d.rule) + "</p>" +
      '<div class="table-wrap"><table><thead><tr><th scope="col">날짜 / 대상</th><th scope="col">기준 → 실제</th><th scope="col">증가액</th><th scope="col">상태</th><th scope="col">행동</th></tr></thead><tbody>' +
      (rows.length ? rows.join("") : '<tr><td colspan="5" class="tiny muted">확인이 필요한 항목이 없습니다</td></tr>') + "</tbody></table></div>";
    (d.insufficient_history || []).forEach(function (h) {
      html += '<p class="note">' + esc(accountLabelOf(h.cloud_account_id)) + ": 이력 " + h.days_available + "일 / 필요 " + h.days_required + "일 — 아직 판정하지 않습니다</p>";
    });
    (d.held || []).forEach(function (h) {
      html += '<p class="note">' + esc(accountLabelOf(h.cloud_account_id)) + ": 기준선 7일 중 미수집일이 있어 " + h.days.length + "일 판정 보류 (0으로 넣지 않습니다)</p>";
    });
    (d.unsupported_currency || []).forEach(function (u) {
      html += '<p class="note">' + esc(accountLabelOf(u.cloud_account_id)) + ": " + esc(u.currency || "통화 미확인") + " 최소 차액 정책이 아직 없어 판정을 보류합니다</p>";
    });
    return { state: "CONNECTED_OK", html: html };
  }

  function anomalyByKey(key) {
    var pools = [ctx.anomalies, ctx.anomaliesAll];
    for (var i = 0; i < pools.length; i++) {
      var items = (pools[i] && pools[i].ok && pools[i].value.items) || [];
      for (var j = 0; j < items.length; j++) if (items[j].source_key === key) return items[j];
    }
    return null;
  }

  function anomalyDialogHtml(it, withActions) {
    if (withActions === undefined) withActions = true;
    return '<dl class="meta-grid" style="grid-template-columns:1fr 1fr">' +
      metaItem("대상", esc(PROVIDER_LABEL[it.provider] || it.provider) + " " + esc(accountLabelOf(it.cloud_account_id)) + " · " + esc(it.service)) +
      metaItem("날짜", esc(it.date)) +
      metaItem("기준값(직전 7일 평균)", esc(F.money(it.baseline_amount, it.currency))) +
      metaItem("실제값", esc(F.money(it.amount, it.currency))) +
      metaItem("증가액(그 날)", "+" + esc(F.money(it.delta, it.currency))) +
      metaItem("증가율", anomalyPctText(it)) +
      "</dl>" +
      '<p class="small" style="margin-top:12px;font-weight:600">관련 변경 후보</p>' +
      (it.related_changes && it.related_changes.length
        ? '<div class="status-list">' + it.related_changes.map(function (c) { return "<div><span>프로비저닝 " + esc(c.service_code) + " #" + esc(c.id) + "</span><span>" + esc(fmtDateTime(c.finished_at)) + "</span></div>"; }).join("") + "</div>"
        : '<p class="tiny muted">같은 날 프로비저닝 기록 없음</p>') +
      '<p class="note">원인을 단정하지 않습니다 — "원인 확인 필요"입니다. 태그 기준 검토 후보이며 조직 소유권을 뜻하지 않습니다.</p>' +
      (withActions ? '<div class="button-row">' +
        (it.review ? '<span class="badge">이미 큐에 있음 · ' + esc(it.review.status) + "</span>" : '<button type="button" class="btn primary" data-action="anomaly-add-queue" data-source-key="' + esc(it.source_key) + '">검토 큐에 추가</button>') +
        '<a class="btn" href="inventory.html?cloud_account_id=' + encodeURIComponent(it.cloud_account_id) + '">인벤토리 보기(계정)</a></div>' +
        '<p id="anomaly-feedback" class="note" role="status"></p>' : "");
  }

  // ── CF-034 비용 작업 큐 ────────────────────────────────────────────────────────────
  function renderReviewQueue() {
    if (!ctx.reviewItems || !ctx.reviewItems.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.reviewItems, "비용 작업 큐") };
    var items = ctx.reviewItems.value.items || [];
    var rows = items.map(function (r) {
      var an = anomalyByKey(r.source_key);
      var parts = r.source_key.split(":");
      var accId = parts[0], day = parts[parts.length - 1], svc = parts.slice(1, -1).join(":");
      return "<tr><td>" + (an ? esc(an.label) : "검토 항목") + "<br><span class=\"tiny muted\">" + esc(accountLabelOf(accId)) + " · " + esc(svc) + " · " + esc(day) + "</span></td>" +
        '<td class="num">' + (an ? '<span class="money-positive">+' + esc(F.money(an.delta, an.currency)) + "</span><br><span class=\"tiny muted\">" + anomalyPctText(an) + " · 그 날의 증가액 (정가 노출액 / 절감액이 아님)</span>" : '— <br><span class="tiny muted">현재 규칙으로 급증이 아니거나 기간 밖 — 금액 없음</span>') + "</td>" +
        "<td>" + esc(r.note || "—") + "</td>" +
        '<td><span class="badge">' + esc(r.status) + "</span>" + (r.resolution ? "<br><span class=\"tiny muted\">" + esc(r.resolution) + "</span>" : "") + "</td>" +
        '<td><button type="button" class="btn no-print" data-action="open-review-dialog" data-item-id="' + esc(r.id) + '">검토</button> ' +
        '<button type="button" class="btn no-print" data-action="open-price-compare">비교하기</button></td></tr>';
    });
    var html = '<div class="table-wrap"><table><thead><tr><th scope="col">문제 / 근거</th><th scope="col">증가액</th><th scope="col">메모</th><th scope="col">검토 상태</th><th scope="col">행동</th></tr></thead><tbody>' +
      (rows.length ? rows.join("") : '<tr><td colspan="5" class="tiny muted">확인이 필요한 항목이 없습니다</td></tr>') + "</tbody></table></div>" +
      '<p class="note">미처리 항목 전부(기간 필터 없음) · 이 화면에서 리소스를 중지·삭제하지 않습니다.</p>';
    return { state: "CONNECTED_OK", html: html };
  }

  function reviewItemById(id) {
    var items = (ctx.reviewItems && ctx.reviewItems.ok && ctx.reviewItems.value.items) || [];
    return items.filter(function (r) { return r.id === id; })[0] || null;
  }

  function reviewDialogHtml(r) {
    var an = anomalyByKey(r.source_key);
    return (an ? anomalyDialogHtml(an, false) : '<p class="tiny muted">현재 급증 목록에 짝이 없어 금액을 표시하지 않습니다(0원이 아니라 미확인).</p>') +
      '<div class="filter-grid" style="margin-top:12px">' +
      '<label class="filter-box">상태<select id="review-status">' + ["open", "investigating", "resolved"].map(function (k) { return '<option value="' + k + '"' + (r.status === k ? " selected" : "") + ">" + k + "</option>"; }).join("") + "</select></label>" +
      '<label class="filter-box">사유(resolved)<select id="review-resolution"><option value="">—</option>' + ["too_small", "expected", "unexpected"].map(function (k) { return '<option value="' + k + '"' + (r.resolution === k ? " selected" : "") + ">" + k + "</option>"; }).join("") + "</select></label>" +
      '<label class="filter-box" style="flex-basis:260px">메모<input id="review-note" type="text" maxlength="2000" value="' + esc(r.note || "") + '"></label>' +
      '<span class="button-row" style="margin-top:0"><button type="button" class="btn primary" data-action="review-patch" data-item-id="' + esc(r.id) + '">저장</button></span></div>' +
      '<p class="note">resolved로 닫힌 항목은 되돌릴 수 없습니다 — 재발은 새 항목입니다. 검토 상태는 금액·합계에 영향을 주지 않습니다.</p>' +
      '<div class="button-row"><a class="btn" href="inventory.html?cloud_account_id=' + encodeURIComponent(r.source_key.split(":")[0]) + '">인벤토리 보기(계정)</a></div>' +
      '<p id="review-feedback" class="note" role="status"></p>';
  }

  // ── CF-035 변경 영향 검토(가격 비교) ────────────────────────────────────────────────────
  function priceCompareDialogHtml() {
    return '<p class="small">유사 사양 3사 월 정가 비교 — 당월 영향은 산출하지 않습니다.</p>' +
      '<div class="filter-grid" style="margin-top:8px">' +
      '<label class="filter-box">vCPU<input id="pc-vcpu" type="number" min="1" max="256" value="2"></label>' +
      '<label class="filter-box">메모리(GiB)<input id="pc-mem" type="number" min="0.5" step="0.5" value="4"></label>' +
      '<label class="filter-box">리전 그룹<select id="pc-region"><option value="northeast_asia">한국(동북아)</option><option value="north_america">미국</option></select></label>' +
      '<label class="filter-box">월 가동 시간<input id="pc-hours" type="number" min="1" max="744" value="730"></label>' +
      '<span class="button-row" style="margin-top:0"><button type="button" class="btn primary" data-action="price-compare-run">비교</button></span></div>' +
      '<p id="pc-feedback" class="note" role="status"></p><div id="pc-result"></div>';
  }

  function priceCompareResultHtml(d) {
    var priced = d.items.filter(function (it) { return it.estimated_monthly_cost != null; });
    var min = priced.length ? priced.reduce(function (m, it) { return Number(it.estimated_monthly_cost) < Number(m.estimated_monthly_cost) ? it : m; }) : null;
    var rows = d.items.map(function (it) {
      var diff = (min && it.estimated_monthly_cost != null) ? F.subDecimalString(it.estimated_monthly_cost, min.estimated_monthly_cost) : null;
      return "<tr><td>" + esc(PROVIDER_LABEL[it.provider] || it.provider) + "<br><span class=\"tiny muted\">" + esc(it.sku || "—") + "</span></td>" +
        '<td class="num">' + (it.estimated_monthly_cost == null ? "이 사양의 정가를 알 수 없습니다" : esc(F.money(it.estimated_monthly_cost, d.currency))) + "</td>" +
        '<td class="num">' + (diff == null ? "—" : (Number(diff) === 0 ? "최저" : "+" + esc(F.money(diff, d.currency)))) + "</td>" +
        '<td class="tiny muted">' + it.assumptions.map(esc).join("<br>") + "</td></tr>";
    });
    return '<div class="table-wrap"><table><thead><tr><th scope="col">플랫폼 / SKU</th><th scope="col">월 정가</th><th scope="col">최저 대비 차이</th><th scope="col">가정</th></tr></thead><tbody>' + rows.join("") + "</tbody></table></div>" +
      '<p class="note">정가(list price) 기준 추정치 · 730h 상시 가동 가정 · 부속 디스크·IP·백업 과금 미확인 · 당월 영향 미산출 · 기준 ' + esc(fmtDateTime(d.as_of)) + "</p>" +
      '<div class="button-row"><a class="btn" href="provisioning.html">프로비저닝에서 검토</a></div>';
  }

  function thresholdDialogHtml() {
    return '<dl class="meta-grid" style="grid-template-columns:1fr">' +
      metaItem("조건", "팀 예산 구간의 사용액(usage · 크레딧/환불 제외)이 한도의 80% · 100%에 도달") +
      metaItem("평가 시점", "자동·수동 수집 성공 직후, 예산 생성·계정 배정·통화 변경·예산 삭제 뒤 (화면 조회는 평가하지 않음)") +
      metaItem("중복 억제", "같은 (예산 · 구간 시작일 · 임계치)에 한 번만 — 서버 UNIQUE로 보장") +
      metaItem("수신 경로", "사이드바 알림(🔔) — 기존 알림 목록") +
      metaItem("판정 불가", "미수집일·통화 혼재·예산 미설정이면 임계 판정을 하지 않음(0%가 아님)") +
      metaItem("생성 차단", "예산 초과 시 프로비저닝 생성 차단은 이번 범위가 아닙니다 — 알림까지입니다") +
      "</dl>";
  }

  // ── ③ 탭 동작(이벤트 위임 initEvents에서 호출) ───────────────────────────────────────
  function handleBudgetAction(action, btn) {
    var teamPath = "/teams/" + encodeURIComponent(selectedTeamId || "");
    switch (action) {
      case "open-team-dialog":
        openDialog("팀 관리", teamDialogHtml());
        return true;
      case "team-create": {
        var name = (document.getElementById("team-new-name") || {}).value || "";
        var currency = (document.getElementById("team-new-currency") || {}).value || "USD";
        window.MCPApi.request("/teams", { method: "POST", body: { name: name.trim(), currency: currency } })
          .then(function (t) { selectedTeamId = t.id; toast("팀을 만들었습니다."); return reloadBudgetTab(); })
          .then(function () { openDialog("팀 관리", teamDialogHtml()); })
          .catch(function (e) { setFeedback("team-feedback", serverErrorText(e), true); });
        return true;
      }
      case "team-patch": {
        var body = {};
        var nameEl = document.getElementById("team-edit-name"), curEl = document.getElementById("team-edit-currency");
        if (nameEl) body.name = nameEl.value.trim();
        if (curEl) body.currency = curEl.value;
        window.MCPApi.request("/teams/" + encodeURIComponent(btn.getAttribute("data-team-id")), { method: "PATCH", body: body })
          .then(function () { toast("저장했습니다."); return reloadBudgetTab(); })
          .then(function () { openDialog("팀 관리", teamDialogHtml()); })
          .catch(function (e) { setFeedback("team-edit-feedback", serverErrorText(e), true); });
        return true;
      }
      case "team-put-accounts": {
        var ids = Array.prototype.map.call(document.querySelectorAll(".team-account-cb:checked"), function (cb) { return cb.value; });
        window.MCPApi.request("/teams/" + encodeURIComponent(btn.getAttribute("data-team-id")) + "/accounts", { method: "PUT", body: { cloud_account_ids: ids } })
          .then(function () { toast("계정 배정을 저장했습니다."); return load(); })
          .then(function () { openDialog("팀 관리", teamDialogHtml()); })
          .catch(function (e) { setFeedback("team-edit-feedback", serverErrorText(e), true); });
        return true;
      }
      case "team-delete": {
        var tname = btn.getAttribute("data-team-name");
        if (!window.confirm("팀 '" + tname + "'을(를) 삭제할까요?\n계정은 미배정으로 돌아가고 비용 기록은 남습니다. 예산·알림 기록은 함께 삭제됩니다.")) return true;
        window.MCPApi.request("/teams/" + encodeURIComponent(btn.getAttribute("data-team-id")), { method: "DELETE", headers: { "X-Action-Confirmed": "true" } })
          .then(function () { selectedTeamId = null; toast("팀을 삭제했습니다."); if (window.MCPModal) window.MCPModal.close("#cost-dialog"); return load(); })
          .catch(function (e) { setFeedback("team-edit-feedback", serverErrorText(e), true); });
        return true;
      }
      case "budget-create": {
        var b = budgetFormValues();
        if (!b.limit_amount) { setFeedback("budget-feedback", "한도를 입력하세요.", true); return true; }
        window.MCPApi.request(teamPath + "/budgets", { method: "POST", body: b })
          .then(function () { toast("예산을 저장했습니다(새 행)."); return reloadBudgetTab(); })
          .catch(function (e) { setFeedback("budget-feedback", serverErrorText(e), true); });
        return true;
      }
      case "budget-reset":
        renderAll();
        return true;
      case "budget-patch": {
        var v = window.prompt("예정 예산의 한도(오타 수정). 시작일·주기·통화는 바꿀 수 없습니다.");
        if (v == null || !v.trim()) return true;
        window.MCPApi.request("/team-budgets/" + encodeURIComponent(btn.getAttribute("data-budget-id")), { method: "PATCH", body: { limit_amount: v.trim() } })
          .then(function () { toast("한도를 수정했습니다."); return reloadBudgetTab(); })
          .catch(function (e) { setFeedback("budget-feedback", serverErrorText(e), true); });
        return true;
      }
      case "budget-delete": {
        if (!window.confirm("이 예산 행을 삭제할까요? 이력에서 사라집니다.")) return true;
        window.MCPApi.request("/team-budgets/" + encodeURIComponent(btn.getAttribute("data-budget-id")), { method: "DELETE", headers: { "X-Action-Confirmed": "true" } })
          .then(function () { toast("예산을 삭제했습니다."); return reloadBudgetTab(); })
          .catch(function (e) { setFeedback("budget-feedback", serverErrorText(e), true); });
        return true;
      }
      case "open-threshold-dialog":
        openDialog("알림 조건", thresholdDialogHtml());
        return true;
      case "open-anomaly-dialog": {
        var it = anomalyByKey(btn.getAttribute("data-source-key"));
        if (it) openDialog("급증 근거 · " + it.date, anomalyDialogHtml(it));
        return true;
      }
      case "anomaly-add-queue": {
        window.MCPApi.request("/cost-review-items", { method: "POST", body: { source_type: "cost_anomaly", source_key: btn.getAttribute("data-source-key"), note: null } })
          .then(function () { toast("검토 큐에 추가했습니다."); if (window.MCPModal) window.MCPModal.close("#cost-dialog"); return load(); })
          .catch(function (e) { setFeedback("anomaly-feedback", serverErrorText(e), true); });
        return true;
      }
      case "open-review-dialog": {
        var r = reviewItemById(btn.getAttribute("data-item-id"));
        if (r) openDialog("검토 · " + r.source_key, reviewDialogHtml(r));
        return true;
      }
      case "review-patch": {
        var st = (document.getElementById("review-status") || {}).value;
        var res = (document.getElementById("review-resolution") || {}).value || null;
        var note = (document.getElementById("review-note") || {}).value;
        var body2 = { status: st, note: note };
        if (res) body2.resolution = res;
        window.MCPApi.request("/cost-review-items/" + encodeURIComponent(btn.getAttribute("data-item-id")), { method: "PATCH", body: body2 })
          .then(function () { toast("검토 상태를 저장했습니다."); if (window.MCPModal) window.MCPModal.close("#cost-dialog"); return load(); })
          .catch(function (e) { setFeedback("review-feedback", serverErrorText(e), true); });
        return true;
      }
      case "open-price-compare":
        openDialog("변경 영향 검토 · 유사 사양 정가 비교", priceCompareDialogHtml());
        return true;
      case "price-compare-run": {
        // §10.2는 service_catalog_id를 요구한다 — 인벤토리 응답의 service.id(compute)를 쓰고, compute 리소스가
        // 하나도 없으면 시드 순서상 첫 compute(aws/ec2 = 1)로 둔다. 비교는 category(compute)만 보므로 결과는 같다.
        var catalog = (ctx.resources && ctx.resources.ok && (ctx.resources.value.items || []).map(function (r) { return r.service; }).filter(function (sv) { return sv && sv.category === "compute"; })[0]) || null;
        var body3 = {
          service_catalog_id: catalog ? String(catalog.id) : "1",
          common_spec: {
            region_group: (document.getElementById("pc-region") || {}).value || "northeast_asia",
            vcpu: Number((document.getElementById("pc-vcpu") || {}).value || 2),
            memory_gib: Number((document.getElementById("pc-mem") || {}).value || 4),
            usage_hours_per_month: Number((document.getElementById("pc-hours") || {}).value || 730)
          },
          providers: ["aws", "azure", "gcp"]
        };
        setFeedback("pc-feedback", "비교 중…", false);
        window.MCPApi.request("/provisioning/price-comparisons", { method: "POST", body: body3 })
          .then(function (d) { setFeedback("pc-feedback", "", false); var el = document.getElementById("pc-result"); if (el) el.innerHTML = priceCompareResultHtml(d); })
          .catch(function (e) { setFeedback("pc-feedback", serverErrorText(e), true); });
        return true;
      }
      default:
        return false;
    }
  }

  // ── 모달·토스트 ─────────────────────────────────────────────────────────────────
  function openDialog(title, bodyHtml) {
    var titleEl = document.getElementById("dialog-title");
    var bodyEl = document.getElementById("dialog-body");
    if (titleEl) titleEl.textContent = title;
    if (bodyEl) { bodyEl.innerHTML = bodyHtml; enhanceNativeSelects(bodyEl); }
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
      if (handleBudgetAction(action, btn)) return; // ③ 탭(팀·예산·급증·검토·가격 비교)

      switch (action) {
        case "apply-filters":
          if (applyFiltersFromInputs()) load();
          break;
        case "reset-filters":
          resetFilters(); renderFilters(); load();
          break;
        case "preset-period":
          applyPeriodPreset(btn.getAttribute("data-preset"));
          break;
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

    // ③ 탭 팀 선택 — 이 탭에만 적용(①② 탭 재조회 없음)
    document.addEventListener("change", function (e) {
      if (e.target && e.target.id === "team-select") {
        selectedTeamId = e.target.value || null;
        syncUrl();
        loadTeamScoped().then(renderAll);
      } else if (e.target && e.target.name === "budget-period-type") {
        var wrap = document.getElementById("budget-end-wrap");
        if (wrap) wrap.hidden = e.target.value !== "custom";
      }
    });

    // CSP·계정 드롭다운 — 열기/닫기는 여기서, 바깥 클릭·Esc로 닫기는 dropdown.js의 전역
    // 리스너가 같은 .mc-dd/.mc-dd__menu 클래스를 보고 대신 처리한다(중복 구현 안 함).
    document.addEventListener("click", function (e) {
      var toggle = e.target.closest("[data-dd-toggle]");
      if (toggle) {
        e.stopPropagation();
        var menu = toggle.parentNode.querySelector(".mc-dd__menu");
        if (!menu) return;
        var willOpen = menu.classList.contains("hidden");
        if (window.MCDropdown) window.MCDropdown.closeAll();
        if (willOpen) menu.classList.remove("hidden");
        return;
      }

      var allRow = e.target.closest("[data-dd-multi-all]");
      if (allRow) {
        var allWrap = allRow.closest(".mc-dd");
        if (!allWrap) return;
        Array.prototype.forEach.call(allWrap.querySelectorAll("[data-dd-multi-item]"), function (el) {
          setDdItemChecked(el, false);
        });
        allRow.classList.add("bg-muted", "font-medium");
        var allLabelText = allWrap.id === "filter-provider-dd" ? "전체 CSP" : "전체 계정";
        refreshDropdownLabel(allWrap.id, allLabelText, allWrap.querySelectorAll("[data-dd-multi-item]").length);
        if (window.MCDropdown) window.MCDropdown.closeAll();
        return;
      }

      var item = e.target.closest("[data-dd-multi-item]");
      if (item) {
        var wrap = item.closest(".mc-dd");
        if (!wrap) return;
        var nowSelected = item.getAttribute("aria-selected") !== "true";
        setDdItemChecked(item, nowSelected);
        var total = wrap.querySelectorAll("[data-dd-multi-item]").length;
        var allLabel = wrap.id === "filter-provider-dd" ? "전체 CSP" : "전체 계정";
        var allRowEl = wrap.querySelector("[data-dd-multi-all]");
        var anySelected = selectedDropdownValues(wrap.id).length > 0;
        if (allRowEl) allRowEl.classList.toggle("bg-muted", !anySelected);
        if (allRowEl) allRowEl.classList.toggle("font-medium", !anySelected);
        refreshDropdownLabel(wrap.id, allLabel, total);
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

  /** 알림(🔔)이 cost.html#CF-026 같은 해시로 들어오면 그 블록의 탭을 열고 스크롤한다. */
  function openHashBlock() {
    var id = (window.location.hash || "").replace(/^#/, "");
    if (!id) return;
    var b = BLOCKS.filter(function (x) { return x.id === id; })[0];
    if (!b || b.tab === "common") return;
    navScroll(b.tab, id);
  }

  function init() {
    initTabs();
    initEvents();
    load().then(openHashBlock);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  return { initTabs: initTabs, load: load };
})();
