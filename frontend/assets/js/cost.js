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
  // 비용 집계·예산 판정의 날짜는 모두 UTC다(백엔드 coverage.utc_today — 비용 1단계, 예산은 2026-09-22 결정).
  // KST 00~09시엔 로컬 "오늘"이 UTC보다 하루 앞서 기본 기간이 UTC 미래를 포함하거나 "오늘 시작" 예산이
  // "시작 전"으로 보였다 — 화면의 날짜 기본값·프리셋·입력 상한은 전부 UTC 유틸을 쓴다(로컬 날짜 유틸은 두지 않음).
  // 시각(마지막 수집 시각 등) 표시는 fmtWhen 등 로컬 그대로 — 날짜 기준과 시각 표시는 다른 문제다.
  var clock = { now: function () { return new Date(); } };   // 테스트가 시계를 바꿔 끼울 수 있게 한 곳에 둔다
  function utcIsoOf(d) { return d.getUTCFullYear() + "-" + pad2(d.getUTCMonth() + 1) + "-" + pad2(d.getUTCDate()); }
  function utcTodayISO() { return utcIsoOf(clock.now()); }
  function utcStartOfMonthISO() { var d = clock.now(); return utcIsoOf(new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1))); }
  function utcLastMonthRange() {
    var d = clock.now();
    var lm = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() - 1, 1));
    var lastDay = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 0));
    return { start: utcIsoOf(lm), end: utcIsoOf(lastDay) };
  }
  function addDaysISO(iso, days) {
    var parts = iso.split("-").map(Number);
    var d = new Date(parts[0], parts[1] - 1, parts[2]);
    d.setDate(d.getDate() + days);
    return isoOf(d);
  }
  function addMonthISO(isoFirst) {
    var y = Number(isoFirst.slice(0, 4)), m = Number(isoFirst.slice(5, 7));
    m += 1; if (m > 12) { m = 1; y += 1; }
    return y + "-" + (m < 10 ? "0" : "") + m + "-01";
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
    if (preset === "mtd") { filters.periodStart = utcStartOfMonthISO(); filters.periodEnd = utcTodayISO(); }
    else if (preset === "last-month") {
      var lmr = utcLastMonthRange();
      filters.periodStart = lmr.start; filters.periodEnd = lmr.end;
    } else if (preset === "7d") { filters.periodEnd = utcTodayISO(); filters.periodStart = addDaysISO(utcTodayISO(), -6); }
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
      periodStart: utcStartOfMonthISO(), periodEnd: utcTodayISO(),
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
      case "PENDING": return '<div class="button-row no-print"><button type="button" class="btn" data-action="refresh-cost">CSP 재수집 요청</button></div>';
      case "SETUP_REQUIRED": return '<div class="button-row no-print"><button type="button" class="btn" data-action="open-setup-dialog">설정 안내</button></div>';
      case "PERMISSION_DENIED": return '<div class="button-row no-print"><button type="button" class="btn" data-action="open-permission-dialog">필요 권한 보기</button></div>';
      case "COLLECT_FAILED": return '<div class="button-row no-print"><button type="button" class="btn" data-action="refresh-cost">CSP 재수집 요청</button></div>';
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
    // 준비 중은 KPI와 같은 덩치로 반복하지 않는다 — 한 줄 안내(2026-09-20). 상태값은 UNSUPPORTED 그대로.
    return '<p class="pending-note" role="status"><span class="badge">준비 중</span><span>' + esc(reasonText) + "</span></p>";
  }

  /** 블록 제목(h2)을 조회 조건에 맞춰 바꾼다 — 예: 이번 달이 아니면 "이번 달 누적"이라 부르지 않는다. */
  function setBlockTitle(blockId, text) {
    var h = document.querySelector("#" + blockId + " .block-head h2");
    if (h && h.textContent !== text) h.textContent = text;
  }

  /** ISO 시각 → "2026-09-19 17:51 (1시간 전 · 지연)" + title에 원본. 표시 시간대는 브라우저 로컬이고,
      비용 집계의 날짜 경계(UTC)는 API 쪽이라 이 표시가 집계를 바꾸지 않는다. */
  function fmtWhen(iso, thresholdHours) {
    if (!iso) return { text: "—", title: "" };
    var d = new Date(iso);
    var abs = d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    var rel = S.stalenessText(iso, Date.now(), thresholdHours);
    return { text: abs + (rel ? " (" + rel + ")" : ""), title: iso };
  }
  function whenHtml(iso, thresholdHours) {
    var w = fmtWhen(iso, thresholdHours);
    return '<time datetime="' + esc(w.title) + '" title="' + esc(w.title) + '">' + esc(w.text) + "</time>";
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
  var loadSeq = 0; // 빠르게 필터를 바꿨을 때 늦게 도착한 옛 응답이 최신 화면을 덮지 않게 한다
  function setBusy(busy) {
    loading = busy;
    document.querySelectorAll(".block-content").forEach(function (el) {
      el.setAttribute("aria-busy", busy ? "true" : "false");
      // 첫 조회처럼 아직 아무것도 없는 블록엔 "조회 중…"을 보여준다(빈 카드가 "값 없음"으로 읽히지 않게)
      if (busy && !el.textContent.trim()) el.innerHTML = '<p class="note" role="status">조회 중…</p>';
    });
    var b = document.getElementById("summary-busy");
    if (b) b.hidden = !busy;
  }

  function load() {
    var seq = ++loadSeq;
    var reqs = {
      capabilities:     "/costs/capabilities",
      summary:          "/costs/summary" + apiQuery(),
      trend:            "/costs/trend" + apiQuery("granularity=" + encodeURIComponent(filters.granularity)),
      breakdownService: "/costs/breakdown" + apiQuery("dimension=service&top_n=6"),
      changes:          "/costs/changes" + apiQuery("compare=" + encodeURIComponent(filters.compare) + "&top_n=10"),
      collection:       "/costs/collection-status" + apiQuery(),   // 1단계: 기간·CSP·계정·통화 기준 계정별 coverage
      resources:        "/resources" + apiQuery(),
      // PR 7·8(③ 탭). 팀 목록은 필터와 무관, 급증·검토 큐는 CSP·계정만 따른다(04 §4-7·§6-6).
      teams:            "/teams",
      anomalies:        "/cost-anomalies" + apiQuery("status=open"),
      reviewItems:      "/cost-review-items" + scopeQuery("status=open"),
      // CF-034가 큐 항목에 금액을 붙일 때 쓰는 짝 — 큐는 기간 필터가 없으므로 최근 90일을 넓게 본다
      anomaliesAll:     "/cost-anomalies" + scopeQuery("status=all&period_start=" + addDaysISO(utcTodayISO(), -90) + "&period_end=" + addDaysISO(utcTodayISO(), 1))
      // notifications는 팀 종속 조회(loadTeamScoped)에서 예산 상태와 함께 읽는다 — 쓰기 뒤 재조회와 같은 경로
    };
    var keys = Object.keys(reqs);
    setBusy(true);
    syncUrl();
    renderFilters(); // 적용된 조건 칩을 먼저 갱신(응답을 기다리지 않아도 "무엇을 조회 중인지"는 보인다)
    return Promise.all(keys.map(function (k) { return settle(window.MCPApi.request(reqs[k])); }))
      .then(function (results) {
        if (seq !== loadSeq) return null; // 그 사이 새 조회가 시작됐다 — 이 결과는 버린다
        keys.forEach(function (k, i) { ctx[k] = results[i]; });
        if (ctx.capabilities.ok) capAccounts = ctx.capabilities.value.items || [];
        return loadTeamScoped().then(function () { return seq; });
      })
      .then(function (ok) {
        if (ok !== seq) return;
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
  // 팀별 후속 조회(budget-status·budgets·notifications). A팀 조회 중 B팀을 고르면 늦게 온 A팀 응답이
  // B팀 화면을 덮을 수 있다 — 요청마다 세대 번호와 "그때 선택한 팀"을 기억하고, 응답을 반영하기 직전에
  // 둘 다 여전히 유효한지 확인한다. 공통 load()의 순번과는 별개다(공통 조회는 팀 조회를 포함하므로
  // 여기 세대도 함께 올린다).
  var teamLoadSeq = 0;
  var teamLoading = false;
  function loadTeamScoped() {
    var teams = (ctx.teams && ctx.teams.ok && ctx.teams.value.items) || [];
    if (selectedTeamId && !teams.some(function (t) { return t.id === selectedTeamId; })) selectedTeamId = null;
    if (!selectedTeamId && teams.length) selectedTeamId = teams[0].id;
    var seq = ++teamLoadSeq;
    if (!selectedTeamId) { ctx.budgetStatus = null; ctx.budgets = null; teamLoading = false; return Promise.resolve(); }
    var teamId = selectedTeamId;
    teamLoading = true;
    return Promise.all([
      settle(window.MCPApi.request("/teams/" + encodeURIComponent(teamId) + "/budget-status")),
      settle(window.MCPApi.request("/teams/" + encodeURIComponent(teamId) + "/budgets")),
      settle(window.MCPApi.request("/notifications?limit=50"))
    ]).then(function (r) {
      if (seq !== teamLoadSeq || teamId !== selectedTeamId) return; // 그 사이 다른 팀을 골랐다 — 버린다
      ctx.budgetStatus = r[0]; ctx.budgets = r[1]; ctx.notifications = r[2];
      teamLoading = false;
    });
  }

  /** ③ 탭만 다시 가져온다(팀 바꿈·예산 저장 뒤). 서버가 쓰기 뒤 알림을 평가하므로 예산 상태와 최근 알림을
      함께 새로 읽는다(loadTeamScoped가 세 요청을 다 한다). ①② 탭은 건드리지 않는다. */
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
    { id: "CF-041",  tab: "budget",   render: function () { return { state: "UNSUPPORTED", html: pendingBlockHtml("비용 보고서(미리보기·CSV 내보내기)는 준비 중입니다. 보고서 기능과 연동 방식이 정해지면 이 자리에서 제공됩니다.") }; } }
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

  /** 블록 하나만 다시 그린다(리사이즈 시 차트 등). */
  function renderBlock(id) {
    var b = BLOCKS.filter(function (x) { return x.id === id; })[0];
    var el = b && document.getElementById(b.id);
    var content = el && el.querySelector(".block-content");
    if (!b || !content) return;
    var result;
    try { result = b.render(); }
    catch (e) { if (window.console) console.error("[cost] " + b.id + " 렌더 실패", e); result = { state: "COLLECT_FAILED", html: fetchFailedHtml({ ok: false, error: e }, b.id) }; }
    content.setAttribute("data-rendered-state", result.state || "CONNECTED_OK");
    content.innerHTML = result.html;
  }
  var resizeTimer = null;
  window.addEventListener("resize", function () {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { if (ctx.trend) renderBlock("CF-013"); }, 200); // 차트 좌표를 새 너비로
  });

  function renderAll() {
    BLOCKS.forEach(function (b) {
      var el = document.getElementById(b.id);
      if (!el) return;
      var content = el.querySelector(".block-content");
      if (!content) return;
      if (b.id === "CFL-01" && filtersBuilt) return; // 필터는 renderFilters()가 값만 갱신한다 — DOM 재생성 금지
      var result;
      try { result = b.render(); }
      catch (e) { if (window.console) console.error("[cost] " + b.id + " 렌더 실패", e); result = { state: "COLLECT_FAILED", html: fetchFailedHtml({ ok: false, error: e }, b.id) }; }
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

  /** capabilities 응답으로 계정 상태를 종류별로 센다 — 경고 줄과 KPI 근거 줄이 같은 숫자를 쓴다. */
  function accountStatusCounts() {
    var counts = {};
    capAccounts.forEach(function (a) { counts[a.status] = (counts[a.status] || 0) + 1; });
    return counts;
  }

  /** 경고 줄 하나: 같은 원인끼리만 묶는다. 서로 다른 원인을 한 문장으로 합치지 않는다(04 §4-0). */
  function warningLines() {
    var out = [];
    var acct = '<button type="button" class="btn no-print" data-action="nav-scroll" data-tab="overview" data-target="CF-009">계정 표 보기</button>';
    var d = ctx.summary && ctx.summary.ok ? ctx.summary.value : null;
    var inScope = (d && d.accounts) || [];      // 현재 조회 조건 안 계정 — "현재 합계"에 영향을 주는 것만 여기서 센다
    var scoped = {};
    inScope.forEach(function (a) { if (!a.filter_excluded) scoped[a.status] = (scoped[a.status] || 0) + 1; });
    var curExcluded = inScope.filter(function (a) { return a.filter_excluded === "currency"; }).length;

    // ① 조회 기간 결측 — 1단계 백엔드: 대상 계정 중 하나라도 미확인인 날. 개수는 missing_count, 계정은 accounts[].
    ((d && d.warnings) || []).forEach(function (w) {
      if (w.code !== "PARTIAL_PERIOD") return;
      var days = w.missing_days || [];
      var count = w.missing_count != null ? w.missing_count : days.length;
      var accts = w.accounts || [];
      out.push({ level: "warn", html: "조회 기간 중 " + count + "일이 수집되지 않았습니다(" + esc(days.slice(0, 3).join(", ")) + (count > 3 ? " 외 " + (count - 3) + "일" : "") + ")" +
        (accts.length ? " — 계정 " + accts.length + "개: " + esc(accts.slice(0, 2).map(function (a) { return accountLabelOf(a.cloud_account_id) + " " + a.missing_count + "일"; }).join(", ")) + (accts.length > 2 ? " 외" : "") : "") +
        ". 그 날들은 합계에 빠져 있고 0원이 아닙니다. " + acct });
    });
    // ② 조회 범위 안 계정의 상태 — 합계에 영향
    if (scoped.COLLECT_FAILED) out.push({ level: "warn", html: scoped.COLLECT_FAILED + "개 계정은 마지막 수집이 실패했습니다 — 금액을 0으로 대체하지 않습니다(과거 수집분은 합계에 포함). 재수집은 계정 표의 행 버튼으로. " + acct });
    if (scoped.CONNECTED_PARTIAL) out.push({ level: "warn", html: scoped.CONNECTED_PARTIAL + "개 계정은 마지막 수집이 부분 성공이라 그 구간은 확인되지 않았습니다. " + acct });
    if (scoped.PERMISSION_DENIED) out.push({ level: "warn", html: scoped.PERMISSION_DENIED + "개 계정은 비용 조회 권한이 부족합니다. " + acct });
    if (scoped.SETUP_REQUIRED) out.push({ level: "warn", html: scoped.SETUP_REQUIRED + "개 계정은 비용 조회 설정이 필요합니다. " + acct });
    if (scoped.NOT_CONNECTED) out.push({ level: "warn", html: scoped.NOT_CONNECTED + '개 계정은 연결되지 않았습니다. <a class="btn no-print" href="mypage.html">계정 연결</a>' });
    if (scoped.PENDING) out.push({ level: "info", html: scoped.PENDING + "개 계정은 첫 수집을 기다리는 중입니다 — 아직 금액이 없습니다(0원이 아닙니다)." });
    if (scoped.UNSUPPORTED) out.push({ level: "info", html: scoped.UNSUPPORTED + "개 계정은 이 CSP의 비용 수집을 아직 지원하지 않습니다(설정으로 해결되지 않습니다). 합계에 들어 있지 않습니다." });
    if (curExcluded) out.push({ level: "info", html: curExcluded + "개 계정은 현재 통화 조건(" + esc(filters.currency) + ")에서 제외됐습니다 — 수집 문제가 아닙니다." });
    // ③ 조회 범위 밖 연결 계정의 문제는 현재 합계와 무관 — 한 줄로만
    var outside = capAccounts.filter(function (c) { return !inScope.some(function (a) { return a.cloud_account_id === c.cloud_account_id; }); });
    var outsideIssues = outside.filter(function (c) { return S.kind(c.status) === "attention"; }).length;
    if (outsideIssues) out.push({ level: "info", html: "조회 조건 밖 계정 " + outsideIssues + "개에 수집 문제가 있습니다(현재 합계와 무관). " + acct });
    // 중요한 것(누락·실패·권한)이 먼저, 안내(미지원·대기)는 뒤 — 2줄만 보일 때 누락이 숨지 않게
    return out.filter(function (w) { return w.level === "warn"; }).concat(out.filter(function (w) { return w.level !== "warn"; }));
  }

  function warningsHtml(list, max) {
    if (!list.length) return "";
    var shown = list.slice(0, max);
    var html = '<div class="warning-list">' + shown.map(function (w) { return '<p class="warning' + (w.level === "info" ? " info" : "") + '">' + w.html + "</p>"; }).join("") + "</div>";
    if (list.length > max) {
      html += '<p class="note">외 ' + (list.length - max) + '건 · <button type="button" class="btn no-print" data-action="open-warnings-dialog">전체 사유 보기</button></p>';
    }
    return html;
  }

  function renderDataBar() {
    if (!ctx.summary || !ctx.summary.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.summary, "조회 기준") };
    var d = ctx.summary.value;
    var items = (ctx.collection && ctx.collection.ok && ctx.collection.value.items) || [];
    var disp = toDisplayRange(d.period.start, d.period.end);
    var asOf = d.as_of ? whenHtml(d.as_of, d.staleness_threshold_hours) : "수집 이력 없음";
    var anyEstimated = (d.kpis.mtd_actual || []).some(function (r) { return r.is_estimated; });
    var running = items.some(function (it) { return it.ingestion_running; });

    var nextAllowed = null;
    items.forEach(function (it) {
      if (!it.next_manual_allowed_at) return;
      var t = new Date(it.next_manual_allowed_at).getTime();
      if (nextAllowed == null || t < nextAllowed) nextAllowed = t;
    });
    // 3-C: 버튼 문구·실행 전 안내·실제 body가 같은 대상을 가리킨다. 이 버튼은 CSP 재수집(과금 가능)이고,
    // 화면 값만 다시 읽는 것은 조건 '적용'이다.
    var targets = refreshTargets();
    var refreshDisabled = (nextAllowed != null && nextAllowed > Date.now()) || refreshPoll.active || running || !targets.length;
    var refreshLabel = refreshPoll.active ? "수집 확인 중…" : (running ? "수집 진행 중…" : "CSP 재수집 (" + targets.length + "개 계정)");
    var refreshHint = refreshPoll.active ? refreshPoll.text
      : (nextAllowed != null && nextAllowed > Date.now() ? "계정당 1시간 1회 · " + fmtWhen(new Date(nextAllowed).toISOString()).text.replace(/ \(.*\)$/, "") + " 이후 가능 · 대상 " + refreshScopeText(targets)
         : "재수집 대상: " + refreshScopeText(targets) + " · 조회 기간 " + esc(disp.start) + " ~ " + esc(disp.end) + " · CSP API 호출(과금 가능) · 화면 값만 다시 읽으려면 조건 '적용'");

    var html = '<div class="summary-bar">' +
      '<span class="sb-item"><span class="sb-label">조회 기간</span><span class="sb-value">' + esc(disp.start) + " ~ " + esc(disp.end) + '</span><span class="tiny muted" title="비용은 CSP가 UTC 하루 단위로 확정합니다. 화면 시각은 로컬, 집계 날짜는 UTC입니다."> UTC 일 기준</span></span>' +
      '<span class="sb-item"><span class="sb-label">통화</span><span class="sb-value">' + esc(filters.currency || "전체(통화별 표시)") + "</span></span>" +
      '<span class="sb-item"><span class="sb-label">요금 분류</span><span class="sb-value">' + esc(chargeCategoryLabel(filters.chargeCategory)) + "</span></span>" +
      '<span class="sb-item"><span class="sb-label">마지막 수집</span><span class="sb-value">' + asOf + "</span></span>" +
      '<span class="sb-item"><span class="badge' + (anyEstimated ? " est" : "") + '">' + (anyEstimated ? "잠정치 포함 · 청구 확정 아님" : "청구 확정 아님") + "</span></span>" +
      '<span class="sb-item busy" id="summary-busy" hidden>조회 중…</span>' +
      '<span class="button-row no-print">' +
        '<button type="button" class="btn" data-action="open-basis-dialog">집계 기준 보기</button>' +
        '<button type="button" class="btn primary" data-action="refresh-cost"' + (refreshDisabled ? " disabled" : "") + ">" + esc(refreshLabel) + "</button>" +
      "</span></div>" +
      (refreshHint ? '<p class="note" role="status">' + esc(refreshHint) + "</p>" : "") +
      warningsHtml(warningLines(), 2);
    return { state: "CONNECTED_OK", html: html };
  }

  var CHARGE_CATEGORY_LABEL = { usage: "사용료", credit: "크레딧", refund: "환불", tax: "세금", other: "기타" };
  function chargeCategoryLabel(k) { return CHARGE_CATEGORY_LABEL[k] || k; }

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
    return '<details class="filter-panel" id="filter-panel">' +
      '<summary aria-label="공통 조회 조건 펼치기/접기">' +
        '<span class="chip" id="filter-summary-chip"></span>' +
        '<span class="small filter-dirty" id="filter-dirty" hidden>수정 중 — 적용 전</span>' +
        '<span class="small filter-error" id="filter-error-out" hidden></span>' +
        '<span class="btn toggle">조건 변경</span>' +
      "</summary>" +
      '<div class="filter-grid">' +
      '<div class="filter-field">기간 프리셋' +
        '<span class="button-row" style="margin-top:0">' +
          '<button type="button" class="btn" data-action="preset-period" data-preset="mtd">이번 달</button>' +
          '<button type="button" class="btn" data-action="preset-period" data-preset="last-month">지난 달</button>' +
          '<button type="button" class="btn" data-action="preset-period" data-preset="7d">최근 7일</button>' +
        "</span>" +
      "</div>" +
      '<label class="filter-box">시작일<input type="date" id="filter-period-start" class="' + DD_SELECT_CLASS + '" aria-describedby="filter-period-note"></label>' +
      '<label class="filter-box">종료일(포함)<input type="date" id="filter-period-end" class="' + DD_SELECT_CLASS + '" aria-describedby="filter-period-note"></label>' +
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
      '<p class="note filter-error" id="filter-period-note" role="alert" hidden></p>' +
      '<p class="note">공통 조회 조건입니다. 예외 — 정가 추정(현재 구성 예상 월 비용·상위 리소스)은 기간·통화·요금 분류와 무관하고, 비용 작업 큐는 기간과 무관하며, 예산·검토 탭의 팀 예산은 팀 선택만 따릅니다.</p>' +
      "</details>";
  }

  /** 입력칸 값(수정 중)과 적용된 필터가 다른지 — 접힌 상태에서도 "적용 전"을 보여준다. */
  function updateFilterDirty() {
    var startEl = document.getElementById("filter-period-start");
    var endEl = document.getElementById("filter-period-end");
    var currSel = document.getElementById("filter-currency");
    var ccSel = document.getElementById("filter-charge-category");
    var dirty = false;
    if (startEl && startEl.value && startEl.value !== filters.periodStart) dirty = true;
    if (endEl && endEl.value && endEl.value !== filters.periodEnd) dirty = true;
    if (currSel && currSel.value !== (filters.currency || "")) dirty = true;
    if (ccSel && ccSel.value !== filters.chargeCategory) dirty = true;
    if (document.getElementById("filter-provider-dd") && selectedDropdownValues("filter-provider-dd").join(",") !== filters.providers.join(",")) dirty = true;
    if (document.getElementById("filter-account-dd") && selectedDropdownValues("filter-account-dd").join(",") !== filters.accountIds.join(",")) dirty = true;
    var el = document.getElementById("filter-dirty");
    if (el) el.hidden = !dirty;
  }

  function showFilterError(text) {
    var note = document.getElementById("filter-period-note");
    var out = document.getElementById("filter-error-out");
    var startEl = document.getElementById("filter-period-start");
    var endEl = document.getElementById("filter-period-end");
    if (note) { note.hidden = !text; note.textContent = text || ""; }
    if (out) { out.hidden = !text; out.textContent = text ? "입력 오류: " + text : ""; } // 접혀 있어도 보인다
    [startEl, endEl].forEach(function (el) { if (el) el.classList.toggle("input-error", !!text); });
    if (text) { var panel = document.getElementById("filter-panel"); if (panel) panel.open = true; }
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
    if (startEl) { startEl.value = filters.periodStart; startEl.max = utcTodayISO(); }
    if (endEl) { endEl.value = filters.periodEnd; endEl.max = utcTodayISO(); }

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
      var parts = [
        "적용 중: " + filters.periodStart + " ~ " + filters.periodEnd,
        filters.providers.length ? filters.providers.join("/").toUpperCase() : "전체 CSP",
        filters.accountIds.length ? filters.accountIds.length + "개 계정" : "전체 계정",
        filters.currency || "전체 통화"
      ];
      if (filters.chargeCategory !== "usage") parts.push("요금 분류: " + chargeCategoryLabel(filters.chargeCategory)); // 기본값이 아닐 때만
      if (selectedTeamId) parts.push("팀: " + teamNameOf(selectedTeamId) + "(예산·검토 탭만)");
      chip.textContent = parts.join(" · ");
      // "이 계정만 보기"로 좁힌 상태는 계정 이름과 해제 버튼을 붙인다 — 다른 조건은 건드리지 않고 계정만 푼다
      var clearBtn = document.getElementById("account-filter-clear-btn");
      if (filters.accountIds.length) {
        var names = filters.accountIds.map(function (id) { return accountLabelOf(id); }).join(", ");
        if (!clearBtn) { clearBtn = document.createElement("button"); clearBtn.type = "button"; clearBtn.id = "account-filter-clear-btn"; clearBtn.className = "btn no-print"; clearBtn.setAttribute("data-action", "account-filter-clear"); clearBtn.style.cssText = "padding:2px 8px;font-size:11px;margin-left:6px"; chip.parentNode.insertBefore(clearBtn, chip.nextSibling); }
        clearBtn.textContent = "계정 조건 해제 (" + names + ")";
        clearBtn.hidden = false;
      } else if (clearBtn) { clearBtn.hidden = true; }
    }
    updateFilterDirty();
  }

  function applyFiltersFromInputs() {
    var startEl = document.getElementById("filter-period-start");
    var endEl = document.getElementById("filter-period-end");
    var start = startEl && startEl.value ? startEl.value : filters.periodStart;
    var end = endEl && endEl.value ? endEl.value : filters.periodEnd;

    if (end < start) {
      showFilterError("종료일이 시작일보다 빠릅니다. 종료일은 포함 날짜입니다(같은 날이면 하루).");
      return false;
    }
    var apiEnd = addDaysISO(end, 1); // 화면은 종료일 포함, API는 제외 경계 — 여기서만 변환한다
    var days = Math.round((new Date(apiEnd) - new Date(start)) / 86400000);
    if (days > 366) {
      showFilterError("조회 기간은 최대 366일입니다(현재 " + days + "일).");
      return false;
    }
    showFilterError("");

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
      var badge = badgeText;
      if (it.is_estimated) badge += " · 잠정치 포함"; // CE가 Estimated로 준 행이 섞여 있을 때만
      return '<div class="value">' + esc(F.money(it.amount, it.currency)) +
             ' <span class="badge' + (it.is_estimated ? " est" : "") + '">' + esc(badge) + "</span></div>";
    }).join("");
  }

  /** 합계에 실제로 들어간 계정 수 — summary.accounts[].actual이 null이 아닌 계정. 합계가 그 값들의
      합이라 추정이 아니다. 상태별 개수는 capabilities의 status를 그대로 센다. */
  function coverageLineHtml(d) {
    var accounts = d.accounts || [];
    var included = accounts.filter(function (a) { return a.actual != null; });
    var zeroConfirmed = accounts.filter(function (a) { return a.actual == null && a.coverage && a.coverage.days > 0 && a.coverage.missing_count === 0; });
    var partial = included.filter(function (a) { return a.coverage && a.coverage.missing_count > 0; });
    var rc = (d.excluded && d.excluded.reason_counts) || {};
    var reasons = Object.keys(rc).map(function (k) { return S.exclusionLabel(k) + " " + rc[k]; });
    var text = "합계 반영 " + (included.length + zeroConfirmed.length) + " / 조회 범위 " + accounts.length + " 계정" +
      (zeroConfirmed.length ? " (확인된 0원 " + zeroConfirmed.length + ")" : "") + (reasons.length ? " · 미반영: " + reasons.join(", ") : "");
    var partialNote = partial.length ? '<p class="kpi-note warning">' + partial.length + "개 계정은 조회 기간에 미수집일이 있어 합계가 부족합니다(0원으로 채우지 않음).</p>" : "";
    return '<p class="kpi-basis">' + esc(text) + "</p>" + partialNote;
  }

  function deltaLineHtml(changesResp) {
    if (!changesResp || !changesResp.ok) return '<p class="kpi-note muted">이전 기간 비교: 조회 실패</p>';
    var d = changesResp.value;
    if (!d.comparable) {
      // 사유는 comparability.reasons(1단계) — 주 사유만 한 줄, 상세는 CF-024.
      return '<p class="kpi-note muted">이전 기간 비교 안 함 — ' + compareReasonHtml(d, { compact: true }) + "</p>";
    }
    if (d.totals.previous == null || d.totals.delta == null) return '<p class="kpi-note muted">이전 기간 비교: 증감 없음(계산되지 않음)</p>';
    if (Number(d.totals.previous) === 0) {
      // comparable=true인데 이전 합계 0 = 두 기간 모두 수집 확인된 상태의 0원. 증가율은 만들 수 없다(QA-06).
      return '<p class="kpi-note">이전 기간 확인된 0원 → 현재 ' + esc(F.money(d.totals.current, d.currency)) + " (증가율 없음)</p>";
    }
    var cls = Number(d.totals.delta) >= 0 ? "money-positive" : "money-negative";
    var sign = Number(d.totals.delta) >= 0 ? "+" : "";
    var pct = d.totals.delta_pct == null ? "" : " (" + (Number(d.totals.delta_pct) >= 0 ? "+" : "") + d.totals.delta_pct + "%)";
    return '<p class="kpi-note">이전 기간 대비 <span class="' + cls + '">' + sign + esc(F.money(d.totals.delta, d.currency)) + "</span>" + esc(pct) + "</p>";
  }

  function renderMtd() {
    var api = toApiRange(filters.periodStart, filters.periodEnd);
    setBlockTitle("CF-002", isCurrentMonthToDate() ? "이번 달 누적 비용" : "조회 기간 누적 비용");
    if (!ctx.summary || !ctx.summary.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.summary, "누적 비용") };
    var d = ctx.summary.value;
    if (!(d.accounts || []).length && capAccounts.length) {
      return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="kpi-basis">조회 조건에 맞는 계정이 없습니다(필터 결과 없음). CSP·계정 조건을 넓혀 보세요.</p>' };
    }
    var st = aggregateAccountStatus(d.accounts);
    if (CH.BLANK_STATES.indexOf(st) >= 0) {
      return { state: st, html: stateViewHtml(st, "누적 비용", lastSuccessOf(d.accounts)) };
    }
    var rows = d.kpis.mtd_actual || [];
    var lines = moneyBadgeLines(rows, "실측");
    if (!lines) {
      // 행이 하나도 없다. 조회 기간에 결측일이 없고(수집 확인) 데이터 상태 계정만 있으면 "수집된 0원",
      // 결측일이 있으면 0원이라 말하지 않는다.
      var gaps = (d.warnings || []).some(function (w) { return w.code === "PARTIAL_PERIOD"; });
      var acc0 = (d.accounts || []).filter(function (a) { return a.status === "CONNECTED_EMPTY" || a.status === "CONNECTED_OK"; })[0];
      lines = gaps
        ? '<div class="value">—</div><p class="kpi-note">이 기간에 수집된 행이 없고 미수집일이 있어 0원이라 표시하지 않습니다.</p>'
        : '<div class="value">' + esc(F.money("0.000000", acc0 ? acc0.currency : null)) + ' <span class="badge">실측 · 수집 확인된 0원</span></div>';
    }
    var html = lines +
      '<p class="kpi-basis">' + esc(chargeCategoryLabel(filters.chargeCategory)) + " 기준 · 청구 확정 아님</p>" +
      coverageLineHtml(d) +
      deltaLineHtml(ctx.changes) +
      '<div class="button-row no-print"><button type="button" class="btn" data-action="nav-scroll" data-tab="analysis" data-target="CF-024">기간 비교 보기</button></div>';
    return { state: st, html: html };
  }

  // ── CF-003 Forecast ─────────────────────────────────────────────────────────────
  function isCurrentMonthToDate() {
    return filters.periodStart === utcStartOfMonthISO() && filters.periodEnd <= utcTodayISO();
  }
  function renderForecast() {
    if (!ctx.summary || !ctx.summary.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.summary, "월말 예상") };
    var d = ctx.summary.value;
    var fs = d.kpis.forecast_status || null;   // 1단계 백엔드 — 응답 전체 상태(통화별 아님)
    var btn = '<div class="button-row no-print"><button type="button" class="btn" data-action="open-forecast-dialog">계산 방법</button></div>';
    var rows = d.kpis.forecast_month_end || [];
    if (!fs) {
      // 구 응답(forecast_status 없음): 상태를 지어내지 않고 값 유무만 말한다.
      if (!isCurrentMonthToDate()) return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="kpi-basis">' + esc(S.forecastText("not_current_month")) + "</p>" + btn };
      if (!rows.length) return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="kpi-basis">전망 값이 없습니다(사유 미제공).</p>' + btn };
    } else if (fs.state !== "computed") {
      var why = S.forecastText(fs.state);
      var detail = "";
      if (fs.state === "insufficient_coverage") {
        var inc = fs.incomplete_accounts || [];
        var days = inc.reduce(function (n, a) { return n + (a.missing_count || 0); }, 0);
        detail = '<p class="kpi-note">대상 ' + fs.required_accounts + "개 계정 중 " + inc.length + "개에서 이달 1일~" + esc(fs.based_through || "어제") + " 사이 " + days + "일이 수집되지 않았습니다" +
          (inc.length ? " — " + esc(inc.slice(0, 3).map(function (a) { return accountLabelOf(a.cloud_account_id) + " " + a.missing_count + "일"; }).join(", ")) + (inc.length > 3 ? " 외" : "") : "") + "</p>" +
          '<div class="button-row no-print"><button type="button" class="btn" data-action="nav-scroll" data-tab="overview" data-target="CF-009">수집 상태 보기</button></div>';
      }
      return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="kpi-basis">' + esc(why) + "</p>" + detail + btn };
    }
    if (!rows.length) return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="kpi-basis">전망 값이 없습니다.</p>' + btn };
    // computed = 대상 계정 전부가 이달 1일~어제(UTC)를 빠짐없이 수집 확인한 상태. 근거 날짜는 based_through.
    var lastAsOf = d.as_of ? fmtWhen(d.as_of).text.replace(/ \(.*\)$/, "") : "없음";
    var html = moneyBadgeLines(rows, "전망") +
      '<p class="kpi-basis">이달 1일 ~ ' + esc(rows[0].based_through || (fs && fs.based_through) || "어제") + "까지 실측(수집 확인됨)을 남은 일수로 늘린 값 · 마지막 수집 " + esc(lastAsOf) + "</p>" +
      (fs ? '<p class="tiny muted">대상 계정 ' + fs.required_accounts + "개 전부 수집 확인 · 실측·정가 추정과 합치지 않음</p>" : "") + btn;
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-004 Estimated ────────────────────────────────────────────────────────────
  function renderEstimated() {
    if (!ctx.summary || !ctx.summary.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.summary, "현재 구성 예상") };
    var d = ctx.summary.value;
    var rows = d.kpis.list_price_monthly || [];
    var totalResources = (ctx.resources && ctx.resources.ok) ? ctx.resources.value.items.length : null;
    var basis = '<p class="kpi-basis">현재 구성 × 730h 정가(할인·부속 요금 미반영) · <strong>조회 기간과 무관</strong></p>';
    if (!rows.length) {
      if (totalResources === 0) return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="kpi-basis">집계할 리소스가 없습니다.</p>' + basis };
      return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="kpi-basis">정가표에 있는 리소스가 없어 산출하지 않습니다.</p>' + basis };
    }
    var missing = rows[0].missing_count || 0;
    var supported = totalResources != null ? Math.max(totalResources - missing, 0) : null;
    var html = moneyBadgeLines(rows, "정가 추정") +
      (supported != null ? '<p class="kpi-basis">리소스 ' + supported + " / " + totalResources + "개 반영" + (missing > 0 ? " · " + missing + "개는 정가표에 없어 제외" : "") + "</p>" : "") +
      basis +
      '<div class="button-row no-print"><button type="button" class="btn" data-action="nav-scroll" data-tab="analysis" data-target="CF-022">대상 보기</button></div>';
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-009 계정별 비용 및 수집 상태 ─────────────────────────────────────────────────
  var PROVIDER_LABEL = { aws: "AWS", azure: "Azure", gcp: "GCP" };
  var PROVIDER_DOT = { aws: "", azure: "azure", gcp: "gcp" };
  // dashboard.js·inventory.js의 PROVIDER_ICON과 같은 에셋 — 표의 플랫폼 이름 앞에 로고를 붙인다(차트 범례는 색 점 유지).
  var PROVIDER_ICON = { aws: "assets/imgs/aws.png", azure: "assets/imgs/azure.png", gcp: "assets/imgs/gcp.png" };

  function providerCellHtml(p) {
    var mark = PROVIDER_ICON[p]
      ? '<img src="' + PROVIDER_ICON[p] + '" alt="" class="provider-logo" />'
      : '<span class="dot' + (PROVIDER_DOT[p] ? " " + PROVIDER_DOT[p] : "") + '"></span>';
    return '<span class="provider-cell">' + mark + esc(PROVIDER_LABEL[p] || p) + "</span>";
  }
  function statusTagHtml(status) {
    return '<span class="status-tag ' + esc(S.kind(status) || "") + '">' + esc(S.label(status) || status) + "</span>";
  }
  /** 금액 칸이 —일 때 그 이유 한 마디 — 상태 라벨과 겹치지 않는 범위에서 */
  /** 계정 행의 금액 칸 — 1단계 백엔드의 coverage로 "확인된 0원 / 부분 / 수집 기록 없음 / 통화 조건 제외"를 가른다.
      개수는 반드시 missing_count(목록은 31개로 잘릴 수 있다). pending_days(오늘·미래)는 결측이 아니다. */
  function amountCellHtml(acc, cap) {
    if (acc && acc.filter_excluded === "currency") return '—<br><span class="tiny muted">현재 통화 조건에서 제외</span>';
    var cov = acc && acc.coverage;
    if (acc && acc.actual != null) {
      return esc(F.money(acc.actual, acc.currency)) + (cov && cov.missing_count ? '<br><span class="tiny" style="color:var(--cost-warn)">부분 · ' + cov.missing_count + "일 미수집</span>" : "");
    }
    if (cov) {
      if (cov.days > 0 && cov.missing_count === 0) return esc(F.money("0.000000", acc.currency)) + '<br><span class="tiny muted">수집 확인된 0원</span>';
      if (cov.covered > 0) return '—<br><span class="tiny muted">' + cov.missing_count + "일 미수집 · 확인된 날은 0원</span>";
      if (cov.days === 0) return '—<br><span class="tiny muted">완료된 날 없음</span>';
      return '—<br><span class="tiny muted">이 기간 수집 기록 없음</span>';
    }
    // coverage 없음 = 미지원 등(상태 라벨이 이유를 말한다) 또는 구 응답
    var st = cap ? cap.status : (acc && acc.status);
    var why = S.kind(st) === "data" ? "이 기간 행 없음" : (st === "COLLECT_FAILED" ? "0 아님" : "");
    return "—" + (why ? '<br><span class="tiny muted">' + esc(why) + "</span>" : "");
  }
  /** "조회 기간 수집" 칸 — coverage=null(미지원·범위 밖)과 결측 0건을 구분한다. */
  function coverageCellHtml(acc) {
    if (!acc) return '<span class="tiny muted">조회 조건 밖</span>';
    if (acc.filter_excluded === "currency") return '<span class="tiny muted">통화 조건 제외</span>';
    var cov = acc.coverage;
    if (!cov) return '<span class="tiny muted">해당 없음</span>';
    if (cov.days === 0) return '<span class="tiny muted">완료된 날 없음</span>';
    if (cov.missing_count === 0) return '<span class="status-tag data">' + cov.covered + "/" + cov.days + "일 확인</span>";
    var listed = cov.missing_days || [];
    return '<span class="status-tag waiting" title="' + esc(listed.slice(0, 10).join(", ")) + (cov.truncated || listed.length < cov.missing_count ? " …" : "") + '">' +
      cov.missing_count + "일 미수집 / " + cov.days + "일</span>";
  }
  /** 원인에 맞는 행동만 — 미지원엔 설정 안내를 주지 않는다(설정으로 해결되지 않는다). */
  function accountActionsHtml(cap) {
    var out = ['<button type="button" class="btn no-print" data-action="open-account-detail" data-account-id="' + esc(cap.cloud_account_id) + '">상세</button>'];
    if (cap.status === "SETUP_REQUIRED" || cap.status === "PERMISSION_DENIED") out.push('<button type="button" class="btn no-print" data-action="open-setup-dialog" data-hint="' + esc(cap.setup_hint || "") + '" data-status="' + esc(cap.status) + '">설정 안내</button>');
    if (cap.status === "COLLECT_FAILED") out.push('<button type="button" class="btn no-print" data-action="refresh-account" data-account-id="' + esc(cap.cloud_account_id) + '" title="이 계정 하나만 CSP에 다시 수집 요청(과금 가능)">이 계정 다시 수집</button>');
    if (cap.status === "NOT_CONNECTED") out.push('<a class="btn no-print" href="mypage.html">계정 연결</a>');
    return out.join(" ");
  }

  function renderPlatforms() {
    if (!ctx.capabilities || !ctx.capabilities.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.capabilities, "계정별 비용") };
    var caps = ctx.capabilities.value.items || [];
    var threshold = ctx.capabilities.value.staleness_threshold_hours;
    var accountsById = {};
    if (ctx.summary && ctx.summary.ok) ctx.summary.value.accounts.forEach(function (a) { accountsById[a.cloud_account_id] = a; });
    if (!caps.length) return { state: "NOT_CONNECTED", html: stateViewHtml("NOT_CONNECTED", "계정별 비용") };

    var order = { aws: 0, azure: 1, gcp: 2 };
    var scopeApi = toApiRange(filters.periodStart, filters.periodEnd);
    var scopeDisp = toDisplayRange(scopeApi.period_start, scopeApi.period_end);
    var rows = caps.slice().sort(function (a, b) { return (order[a.provider] - order[b.provider]) || String(a.account_label || "").localeCompare(String(b.account_label || "")); })
      .map(function (c) {
        var acc = accountsById[c.cloud_account_id];
        var selected = !!acc; // summary.accounts에 없으면 조회 조건(CSP·계정) 밖 — 결측으로 세지 않는다
        var detail = S.text(c.status, "", c.as_of); // 9종 문구 — cost-state.js 한 곳
        return '<tr data-account-id="' + esc(c.cloud_account_id) + '"' + (selected ? "" : ' style="opacity:.5"') + ">" +
          '<td data-label="CSP">' + providerCellHtml(c.provider) + "</td>" +
          '<td data-label="계정">' + esc(c.account_label || c.external_account_id) + (c.account_label ? '<br><span class="tiny muted">' + esc(c.external_account_id) + "</span>" : "") + "</td>" +
          '<td class="num" data-label="기간 비용">' + amountCellHtml(acc, c) + "</td>" +
          '<td data-label="조회 기간 수집">' + coverageCellHtml(acc) + "</td>" +
          '<td data-label="수집 상태"><span class="status-cell">' + statusTagHtml(c.status) +
            (detail ? '<span class="tiny muted">' + esc(detail.replace(/^\s*/, "")) + "</span>" : "") +
            (c.ingestion_running ? '<span class="tiny muted">수집 진행 중…</span>' : "") + "</span></td>" +
          '<td data-label="마지막 수집">' + (c.as_of ? whenHtml(c.as_of, threshold) : '<span class="tiny muted">없음</span>') + "</td>" +
          '<td class="actions" data-label="">' + accountActionsHtml(c) + "</td></tr>";
      });

    var html = '<div class="table-wrap"><table class="account-table"><thead><tr>' +
      '<th scope="col">CSP</th><th scope="col">계정</th><th scope="col">기간 비용</th><th scope="col">조회 기간 수집</th><th scope="col">수집 상태</th><th scope="col">마지막 수집</th><th scope="col"><span class="sr-only">행동</span></th>' +
      "</tr></thead><tbody>" + rows.join("") + "</tbody></table></div>" +
      '<p class="note">전체 연결 계정의 운영 상태 · 기간 비용 = 위 조회 조건의 ' + esc(chargeCategoryLabel(filters.chargeCategory)) + " 합(계정별) · \"조회 기간 수집\"은 " + esc(scopeDisp.start) + " ~ " + esc(scopeDisp.end) + " 기준(오늘·미래 제외) · \"수집 상태\"는 마지막 수집 실행 기준 · 통화가 다른 계정은 합치지 않습니다" +
      (filters.providers.length || filters.accountIds.length ? " · 흐린 행은 조회 조건에서 제외된 계정(현재 합계와 무관)" : "") + "</p>";
    return { state: "CONNECTED_OK", html: html };
  }

  function accountDetailHtml(cap) {
    var acc = ((ctx.summary && ctx.summary.ok && ctx.summary.value.accounts) || []).filter(function (a) { return a.cloud_account_id === cap.cloud_account_id; })[0];
    var col = ((ctx.collection && ctx.collection.ok && ctx.collection.value.items) || []).filter(function (a) { return a.cloud_account_id === cap.cloud_account_id; })[0];
    var threshold = ctx.capabilities.value.staleness_threshold_hours;
    var text = S.text(cap.status, "", cap.as_of);
    return '<dl class="meta-grid" style="grid-template-columns:1fr 1fr">' +
      metaItem("CSP / 계정", providerCellHtml(cap.provider) + " " + esc(cap.account_label || "") + '<br><span class="tiny muted">' + esc(cap.external_account_id) + "</span>") +
      metaItem("수집 상태", statusTagHtml(cap.status) + (text ? '<br><span class="tiny">' + esc(text.replace(/^\s*/, "")) + "</span>" : "")) +
      metaItem("기간 비용(" + esc(chargeCategoryLabel(filters.chargeCategory)) + ")", amountCellHtml(acc, cap)) +
      metaItem("조회 기간 수집 확인", coverageCellHtml(acc)) +
      metaItem("정가 추정(현재 구성)", acc && acc.list_price_estimate != null ? esc(F.money(acc.list_price_estimate, acc.currency || "USD")) + " /월" : "—") +
      metaItem("마지막 비용 수집", cap.as_of ? whenHtml(cap.as_of, threshold) : "없음") +
      metaItem("리소스 목록 동기화", acc && acc.resources_synced_at ? whenHtml(acc.resources_synced_at) : "없음") +
      metaItem("리소스 수", acc ? String(acc.resource_count) : "—") +
      metaItem("연결", cap.status === "NOT_CONNECTED" ? "연결 안 됨" : "연결됨") +
      metaItem("수집 범위 끝", col && col.covered_through ? esc(col.covered_through) : "—") +
      metaItem("마지막 오류 코드", cap.last_error_code ? esc(cap.last_error_code) : "없음") +
      "</dl>" +
      (cap.setup_hint ? '<p class="note">' + esc(cap.setup_hint) + "</p>" : "") +
      '<p class="note">보안 점검 상태는 이 플랫폼에 데이터 출처가 없어 표시하지 않습니다.</p>' +
      '<div class="button-row">' + accountActionsHtml(cap).replace(/<button[^>]*open-account-detail[^>]*>상세<\/button>\s*/, "") +
      '<button type="button" class="btn" data-action="csp-detail" data-provider="' + esc(cap.provider) + '">이 CSP만 분석 탭에서 보기</button></div>';
  }

  function warningsDialogHtml() {
    var list = warningLines();
    return list.length ? warningsHtml(list, list.length) : '<p class="note">경고가 없습니다.</p>';
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
    var totalEst = (ctx.summary && ctx.summary.ok && (ctx.summary.value.kpis.list_price_monthly || [])[0]) || null;
    html += '<p class="kpi-basis">전체 예상 월 비용' + (totalEst ? "(" + esc(F.money(totalEst.amount, totalEst.currency)) + ")" : "") + " 중 담당자(" + esc(OWNER_TAG_KEY) + " 태그) 미지정 리소스 " + unassigned.length + "개의 금액 · 조회 기간과 무관</p>" +
      (noPrice > 0 ? '<p class="kpi-note">' + noPrice + "개는 정가표에 없어 금액 제외(개수엔 포함)</p>" : "") +
      '<div class="button-row no-print"><button type="button" class="btn" data-action="open-unassigned-dialog">미지정 대상 보기</button></div>';
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-030 원인 확인이 필요한 비용 (04 §4-6) ───────────────────────────────────────
  // 경고 2장. ②는 계정별로 세 가지를 가른다 — ㉮ 실행 중 + 실측 0원 / ㉯ 리소스 0개 + 실측 양수 /
  // ㉰ CONNECTED_EMPTY(정상 0, 경고 아님). 셋을 한 경고로 합치면 정상 0을 문제로 보고하게 된다.
  // 문구는 "누수"가 아니라 "원인 확인 필요"(확정 13) — 조직 소유권을 단정하지 않는다.
  function isZeroAmount(amount) { return amount != null && !/[1-9]/.test(String(amount)); }
  function isPositiveAmount(amount) { return amount != null && /[1-9]/.test(String(amount)) && String(amount).trim().charAt(0) !== "-"; }
  function summaryAccountLabel(a) { return a.account_label || a.external_account_id || a.cloud_account_id; } // summary.accounts[] 객체용

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
        var label = esc(summaryAccountLabel(a));
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
    var html = '<div class="warning-list">' +
      (warnings.length ? warnings.map(function (w) { return '<p class="warning">' + w + "</p>"; }).join("")
                       : '<p class="note">경고 없음 — 확인이 필요한 항목이 없습니다.</p>') +
      "</div>" +
      notes.map(function (t) { return '<p class="note">' + t + "</p>"; }).join("") +
      '<div class="button-row no-print"><button type="button" class="btn" data-action="nav-scroll" data-tab="overview" data-target="CF-034">검토 목록 보기</button></div>';
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-013 클라우드 비용 추이 ─────────────────────────────────────────────────────
  // 집계 단위는 filters.granularity 하나가 진실이다(URL·요청·차트·표가 같은 값을 본다). 예전엔 trendMode가 따로 있어
  // "월별" 버튼이 일별 응답을 선그래프로 바꿔 그릴 뿐 월별 집계를 조회하지 않았다.
  var trendLoadSeq = 0;
  function reloadTrend() {
    var seq = ++trendLoadSeq;
    var host = document.querySelector("#CF-013 .block-content");
    if (host) host.setAttribute("aria-busy", "true");
    return settle(window.MCPApi.request("/costs/trend" + apiQuery("granularity=" + encodeURIComponent(filters.granularity))))
      .then(function (r) {
        if (seq !== trendLoadSeq) return;   // 빠르게 전환했다 — 늦게 온 응답은 버린다
        ctx.trend = r;
        renderBlock("CF-013");
        if (host) host.setAttribute("aria-busy", "false");
      });
  }
  function renderTrend() {
    if (!ctx.trend || !ctx.trend.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.trend, "비용 추이") };
    var d = ctx.trend.value;
    // 응답의 granularity가 진실 — 요청과 다르면(구 응답·중간 상태) 응답 쪽을 따른다
    var trendMode = d.granularity === "monthly" ? "monthly" : (d.granularity === "daily" ? "daily" : filters.granularity);
    var accStatus = ctx.summary && ctx.summary.ok ? aggregateAccountStatus(ctx.summary.value.accounts) : "CONNECTED_OK";
    var labels = [];
    var labelSet = {};
    // 월별은 축 키를 "YYYY-MM"으로 둔다 — 서버가 주는 버킷 시작일("2026-09-01")을 그대로 쓰면 일별처럼 "9/1"로 보인다.
    var keyOf = function (iso) { return trendMode === "monthly" ? String(iso).slice(0, 7) : iso; };
    (d.series || []).forEach(function (s) { s.points.forEach(function (p) { var k = keyOf(p.period_start); if (!labelSet[k]) { labelSet[k] = true; labels.push(k); } }); });
    if (trendMode === "daily") {
      // 라벨을 데이터가 있는 날로만 만들면 미수집일이 축에서 그냥 사라진다 — 조회 기간의 모든 날을 축에 두고
      // 값이 없는 날은 빈 칸(점선 "수집되지 않음")으로 남긴다(QA-08).
      var api = toApiRange(filters.periodStart, filters.periodEnd);
      for (var dd = api.period_start; dd < api.period_end; dd = addDaysISO(dd, 1)) { if (!labelSet[dd]) { labelSet[dd] = true; labels.push(dd); } }
    }
    var monthNote = "";
    if (trendMode === "monthly") {
      var apiM = toApiRange(filters.periodStart, filters.periodEnd);
      for (var mm = apiM.period_start.slice(0, 7) + "-01"; mm < apiM.period_end; mm = addMonthISO(mm)) { var mk = mm.slice(0, 7); if (!labelSet[mk]) { labelSet[mk] = true; labels.push(mk); } }
      var lastDayExcl = apiM.period_end;
      var startsMid = apiM.period_start.slice(8) !== "01", endsMid = lastDayExcl.slice(8) !== "01";
      if (startsMid || endsMid) monthNote = '<p class="tiny muted">조회 기간이 월 경계에 걸쳐 ' + (startsMid ? "첫 달" : "") + (startsMid && endsMid ? "·" : "") + (endsMid ? "마지막 달" : "") + "은 일부 날짜만 포함된 합계입니다.</p>";
    }
    labels.sort();
    var series = (d.series || []).map(function (s) {
      var points = {}, estimated = {};
      s.points.forEach(function (p) { var k = keyOf(p.period_start); points[k] = p.amount; if (p.is_estimated) estimated[k] = true; });
      return { key: s.key, label: s.label, points: points, estimated: estimated, omitted_reason: s.omitted_reason };
    });
    var unitLabel = trendMode === "daily" ? "일별" : "월별";
    var host = document.querySelector("#CF-013 .block-content");
    var width = host && host.clientWidth ? host.clientWidth : 720; // 실제 표시 너비 — 1 user unit = 1px
    var chartOpts = { labels: labels, series: series, currency: d.currency, missingLabels: d.missing_days, state: accStatus, blockName: "비용 추이",
                      title: unitLabel + " 비용", tableLabel: unitLabel + " 비용 내역(표)", width: width };
    var chartHtml = CH.stackedBar(chartOpts);   // 일별·월별 모두 막대 — 월별은 서버 월 집계(granularity=monthly)를 그대로 그린다

    // 헤더: 이 그래프가 무엇을 그리는지 — 통화(하나만 그린다)·결측일·제외 통화. 값은 보정하지 않는다.
    var excluded = (d.currency_selection && d.currency_selection.excluded) || [];
    var mtd = (ctx.summary && ctx.summary.ok && ctx.summary.value.kpis.mtd_actual) || [];
    var totalRow = mtd.filter(function (r) { return r.currency === d.currency; })[0];
    var head = '<div class="cta-row no-print">' +
      '<span class="small">' + (d.currency ? "표시 통화 <strong>" + esc(d.currency) + "</strong>" + (totalRow ? " · 기간 합계 " + esc(F.money(totalRow.amount, totalRow.currency)) : "") : "그릴 실측 데이터가 없습니다") +
        (excluded.length ? ' · <span class="muted">' + esc(excluded.map(function (e) { return e.currency ? e.currency + " 계정 " + (e.account_count || "") + "개" : String(e); }).join(", ")) + " 제외(통화가 달라 한 그래프에 그리지 않음)</span>" : "") + "</span>" +
      '<span class="button-row" style="margin-top:0" role="group" aria-label="집계 단위">' +
        '<button type="button" class="btn' + (filters.granularity === "daily" ? " primary" : "") + '" data-action="trend-mode" data-mode="daily" aria-pressed="' + (filters.granularity === "daily") + '">일별</button>' +
        '<button type="button" class="btn' + (filters.granularity === "monthly" ? " primary" : "") + '" data-action="trend-mode" data-mode="monthly" aria-pressed="' + (filters.granularity === "monthly") + '">월별</button>' +
        (filters.granularity !== trendMode ? '<span class="tiny muted" role="status">' + esc(filters.granularity === "monthly" ? "월별" : "일별") + " 집계 조회 중…</span>" : "") +
      "</span></div>";
    var gaps = (d.missing_days || []).length
      ? '<p class="warning">' + d.missing_days.length + "일이 수집되지 않아 " + (trendMode === "daily" ? "그래프가 끊깁니다(0으로 잇지 않습니다)" : "그 달의 합계가 부족합니다(0으로 채우지 않습니다)") + ": " + esc(d.missing_days.slice(0, 5).join(", ")) + (d.missing_days.length > 5 ? " 외 " + (d.missing_days.length - 5) + "일" : "") + "</p>"
      : "";
    var disp = toDisplayRange(toApiRange(filters.periodStart, filters.periodEnd).period_start, toApiRange(filters.periodStart, filters.periodEnd).period_end);
    return { state: "CONNECTED_OK", html: head + gaps + monthNote + chartHtml + '<p class="note">' + esc(disp.start) + " ~ " + esc(disp.end) + " · " + esc(chargeCategoryLabel(filters.chargeCategory)) + " 기준 실측 · " + esc(unitLabel) + " 집계(서버) · CSP·계정 필터 적용 · 정가 추정은 포함하지 않습니다 · 점·막대에 마우스를 올리면 금액과 잠정 여부가 보이고 같은 값은 아래 표에 있습니다</p>" };
  }

  // ── CF-016 서비스별 비용 비중 ─────────────────────────────────────────────────────
  function renderServiceShare() {
    if (!ctx.breakdownService || !ctx.breakdownService.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.breakdownService, "서비스별 분포") };
    var d = ctx.breakdownService.value;
    var accStatus = ctx.summary && ctx.summary.ok ? aggregateAccountStatus(ctx.summary.value.accounts) : "CONNECTED_OK";
    var excludedCur = (d.currency_selection && d.currency_selection.excluded) || [];
    // 제외 계정은 조회 범위 안(summary.excluded)만 — 전역 capabilities로 세면 필터 밖 계정까지 "제외"로 보인다
    var rc = (ctx.summary && ctx.summary.ok && ctx.summary.value.excluded && ctx.summary.value.excluded.reason_counts) || {};
    var excludedAcc = Object.keys(rc);
    var html = CH.barList({ items: d.items, rest: d.rest, unallocated: d.unallocated, total: d.total, currency: d.currency,
      estimate_unavailable_count: d.estimate_unavailable_count, state: accStatus, blockName: "서비스별 분포" });
    html += '<p class="note">사용료 기준(요금 분류 필터 미적용 — 비중은 항상 사용료로 냅니다) · 통화 ' + esc(d.currency || "—") +
      (excludedCur.length ? " · 통화가 다른 계정 " + excludedCur.map(function (e) { return esc(e.currency ? e.currency + " " + (e.account_count || "") + "개" : String(e)); }).join(", ") + " 제외(합치지 않음)" : "") +
      (excludedAcc.length ? " · 합계 미반영 계정: " + excludedAcc.map(function (k) { return esc(S.exclusionLabel(k)) + " " + rc[k]; }).join(", ") : "") +
      " · 금액 내림차순, 상위 항목 외는 '기타'</p>" +
      '<div class="button-row no-print"><button type="button" class="btn" data-action="open-full-breakdown-dialog">상위 20개 · 기타 · 미분류 보기</button></div>';
    return { state: "CONNECTED_OK", html: html };
  }

  // ── EXT-F12 증가액 Top N ────────────────────────────────────────────────────────
  function pctText(v) { return v == null ? "—" : (Number(v) >= 0 ? "+" : "") + v + "%"; }

  /** comparable=false의 사유 — 1단계 백엔드의 comparability.reasons를 읽는다(예전엔 사유가 "일수 다름" 하나뿐이었다).
      주 사유 한 줄 + 나머지는 개수, 계정별 결측은 접이식으로. reasons가 없으면(구 응답) 일수 기준으로만 말한다. */
  function compareReasonHtml(d, opts) {
    opts = opts || {};
    var cmp = d.comparability;
    if (!cmp || !cmp.reasons) {
      return "이전 기간(" + esc(d.previous.days) + "일)과 조회 기간(" + esc(d.current.days) + "일)의 일수가 달라 비교하지 않습니다.";
    }
    var pr = S.primaryCompareReason(cmp.reasons);
    var text = esc(pr.text || "비교 조건이 맞지 않습니다") + (pr.more ? ' <span class="muted">(외 ' + pr.more + "개 사유)</span>" : "");
    var accts = cmp.accounts || [];
    if (!opts.compact && accts.length) {
      text += '<details class="note" style="display:inline-block;margin:0 0 0 6px"><summary style="cursor:pointer;display:inline">계정별 결측 ' + accts.length + "개</summary>" +
        accts.map(function (a) {
          return esc(accountLabelOf(a.cloud_account_id)) + ": 조회 기간 " + a.current_missing_count + "일 · 이전 기간 " + a.previous_missing_count + "일 미수집";
        }).join("<br>") + "</details>";
    }
    return text;
  }
  /** 비교 불가여도 확인된 두 합계는 "불완전" 표시와 함께 보여준다. delta가 없으니 증감 배지·색은 만들지 않는다. */
  function partialTotalsHtml(d) {
    if (!d.currency || d.totals.current == null) return "";
    var reasons = (d.comparability && d.comparability.reasons) || [];
    var curIncomplete = reasons.indexOf("CURRENT_COVERAGE") >= 0 || reasons.indexOf("INCOMPLETE_PERIOD") >= 0;
    var prevIncomplete = reasons.indexOf("PREVIOUS_COVERAGE") >= 0;
    var prevText = d.totals.previous == null ? "—" : esc(F.money(d.totals.previous, d.currency)) + (prevIncomplete ? " (불완전)" : "");
    return '<p class="tiny muted">확인된 금액 — 현재 ' + esc(F.money(d.totals.current, d.currency)) + (curIncomplete ? " (불완전)" : "") + " · 이전 " + prevText + " · 증감은 계산하지 않음</p>";
  }
  function comparableFailHtml(d) {
    return '<p class="note">이전 기간 비교 안 함 — ' + compareReasonHtml(d) + "</p>" + partialTotalsHtml(d);
  }
  /** 소액: 표시 단위(통화별 소수 자리)로 반올림하면 0으로 보이는 양수·음수 — 정확한 값은 title로. */
  function smallMoneyHtml(amount, currency, signed) {
    var shown = F.money(amount, currency);
    var raw = Number(amount);
    var sign = signed ? (raw > 0 ? "+" : "") : "";
    if (raw !== 0 && /^-?[^0-9]*0(\.0+)?$/.test(shown.replace(/[,\s]/g, ""))) {
      return '<span title="정확한 값 ' + esc(amount + " " + (currency || "")) + '">' + esc(sign + shown) + ' <span class="tiny muted">(표시 단위 미만)</span></span>';
    }
    return '<span title="' + esc(amount + " " + (currency || "")) + '">' + esc(sign + shown) + "</span>";
  }
  function changeRowHtml(it, currency) {
    var prevZero = Number(it.previous) === 0;                                   // 비교 가능한 이전 기간에 행이 있고 합이 0 — 확인된 0원
    var pct = it.previous == null || prevZero ? "—" : pctText(it.delta_pct);   // 이전 0이면 %를 만들지 않는다(100%·무한대 금지)
    var up = Number(it.delta) >= 0;
    return "<tr><td>" + esc(it.label) + (prevZero ? '<span class="sub-amount">이전 기간 확인된 0원 → 신규 비용 발생</span>' : "") + "</td>" +
      '<td class="num">' + smallMoneyHtml(it.previous, currency) + "</td>" +
      '<td class="num">' + smallMoneyHtml(it.current, currency) + "</td>" +
      '<td class="num ' + (up ? "money-positive" : "money-negative") + '">' + smallMoneyHtml(it.delta, currency, true) + "</td>" +
      '<td class="num">' + esc(pct) + "</td></tr>";
  }

  function renderTopIncreases() {
    if (!ctx.changes || !ctx.changes.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.changes, "서비스별 증가액") };
    var d = ctx.changes.value;
    if (!d.comparable) return { state: "CONNECTED_OK", comparable: false, html: comparableFailHtml(d) };
    var cur = toDisplayRange(d.current.start, d.current.end), prev = toDisplayRange(d.previous.start, d.previous.end);
    // 서버가 증가액 내림차순으로 정렬해 준다(원값 기준). delta가 0인 항목은 서버가 increases에 넣지 않는다.
    var incRows = (d.increases || []).map(function (it) { return changeRowHtml(it, d.currency); });
    var decRows = (d.decreases || []).map(function (it) { return changeRowHtml(it, d.currency); });
    // 이전 기간에 행이 없던 서비스 — API로는 "수집된 0원"인지 "미수집"인지 알 수 없다. 증가 순위에 넣지 않고
    // "신규"라고 부르지도 않는다. 별도 묶음으로 현재 금액만 보여준다.
    var noPrev = (d.new_items || []);
    if (!incRows.length && !decRows.length && noPrev.length) {
      // 비교 가능한 항목이 하나도 없다 — 빈 표를 세우지 않고 이유 + 현재 데이터로 가는 행동만
      return { state: "CONNECTED_OK", html: '<p class="note">이전 기간(' + esc(prev.start) + "~" + esc(prev.end) + ")에 수집된 행이 없어 증가액을 계산할 수 없습니다 — 현재 기간 " + noPrev.length + "개 서비스는 왼쪽 분포에서 볼 수 있습니다.</p>" +
        '<div class="button-row no-print"><button type="button" class="btn" data-action="nav-scroll" data-tab="analysis" data-target="CF-016">현재 기간 분포 보기</button></div>' };
    }
    if (!incRows.length && !decRows.length && !noPrev.length) {
      return { state: "CONNECTED_OK", html: '<p class="note">두 기간 모두 비교할 실측 항목이 없습니다.</p>' };
    }
    var head = '<thead><tr><th scope="col">서비스</th><th scope="col" class="num">이전(' + esc(prev.start) + "~" + esc(prev.end) + ')</th><th scope="col" class="num">현재(' + esc(cur.start) + "~" + esc(cur.end) + ')</th><th scope="col" class="num">증가액</th><th scope="col" class="num">증가율</th></tr></thead>';
    var html = '<p class="note">' + esc(d.currency || "") + " · 사용료 기준(요금 분류 필터 미적용) · 증가액 내림차순 · 증가율은 이전 금액 대비 비율(증가액과 다른 정보)" +
      ((d.increases || []).concat(d.decreases || [], d.new_items || []).some(function (it) { return it.key === "__unallocated__"; }) ? " · \"미분류\"는 서비스가 지정되지 않은 금액으로 합계에 포함(상위 항목 밖 \"기타\"와 다름)" : "") + "</p>" +
      '<div class="table-wrap"><table class="changes-table">' + head + "<tbody>" +
      (incRows.length ? incRows.join("") : '<tr><td colspan="5" class="tiny muted">이전 기간보다 늘어난 서비스가 없습니다</td></tr>') +
      "</tbody></table></div>" +
      (noPrev.length ? '<details class="mt-2"><summary class="small muted" style="cursor:pointer">이전 기간에 행이 없던 서비스 ' + noPrev.length + "건 (증가 순위 제외)</summary>" +
        '<p class="tiny muted">이전 기간에 수집된 행이 없어 "수집된 0원"인지 "미수집"인지 현재 API로 구분되지 않습니다 — 증가액·증가율을 만들지 않고 현재 금액만 보여줍니다.</p>' +
        '<div class="table-wrap"><table class="changes-table"><thead><tr><th scope="col">서비스</th><th scope="col" class="num">현재</th></tr></thead><tbody>' +
        noPrev.map(function (it) { return "<tr><td>" + esc(it.label) + '</td><td class="num">' + smallMoneyHtml(it.current, d.currency) + "</td></tr>"; }).join("") +
        "</tbody></table></div></details>" : "") +
      (decRows.length ? '<details class="mt-2"><summary class="small muted" style="cursor:pointer">감소한 서비스 ' + decRows.length + "건 보기</summary>" +
        '<div class="table-wrap"><table class="changes-table">' + head.replace("증가액", "증감액").replace("증가율", "증감률") + "<tbody>" + decRows.join("") + "</tbody></table></div></details>" : "");
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-024 같은 길이 기간 비교 ────────────────────────────────────────────────────
  function renderPeriodCompare() {
    if (!ctx.changes || !ctx.changes.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.changes, "기간 비교") };
    var d = ctx.changes.value;
    var basis = filters.compare === "previous_month" ? "전월 같은 구간" : "직전 동일 기간";
    var prevDisp = toDisplayRange(d.previous.start, d.previous.end);
    var curDisp = toDisplayRange(d.current.start, d.current.end);
    if (!d.comparable) {
      // 비교 불가는 짧게 — 아래 현재 기간 분석(추이·분포·증가액)은 그대로 볼 수 있다. 확인된 합계는 불완전 표시와 함께.
      return { state: "CONNECTED_OK", comparable: false, html:
        '<div class="compare-compact"><span class="badge">비교 안 함</span><span>' + compareReasonHtml(d) + "</span>" +
        '<span class="muted">이전 ' + esc(prevDisp.start) + "~" + esc(prevDisp.end) + " · " + esc(d.previous.days) + "일 / 조회 " + esc(curDisp.start) + "~" + esc(curDisp.end) + " · " + esc(d.current.days) + "일 · 비교 기준: " + esc(basis) + "</span></div>" +
        partialTotalsHtml(d) };
    }
    var noCurrency = !d.currency; // 실측 행이 하나도 없으면 통화도 없다 — 0.00을 찍지 않는다
    var prevZero = d.totals.previous != null && Number(d.totals.previous) === 0;
    var money = function (v) { return noCurrency || v == null ? "—" : F.money(v, d.currency); };
    // comparable=true는 두 기간 모두 수집 확인된 상태다(1단계) — 이전 합계 0은 "확인된 0원"이다.
    var prevCell = '<div class="value">' + esc(money(d.totals.previous)) + "</div>" + (prevZero ? '<span class="tiny muted">수집 확인된 0원</span>' : "");
    var partialNow = ((ctx.summary && ctx.summary.ok && ctx.summary.value.accounts) || []).some(function (a) { return a.status === "CONNECTED_PARTIAL" && a.actual != null; });
    var gapsNow = ((ctx.summary && ctx.summary.ok && ctx.summary.value.warnings) || []).some(function (w) { return w.code === "PARTIAL_PERIOD"; });
    var curNote = (partialNow || gapsNow) ? '<span class="tiny" style="color:var(--cost-warn)">' + (partialNow ? "일부 계정 기준" : "") + (partialNow && gapsNow ? " · " : "") + (gapsNow ? "미수집일 있음" : "") + "</span>" : "";
    var deltaCell;
    if (noCurrency) deltaCell = '<div class="value">—</div><span class="tiny muted">두 기간 모두 실측 없음(0원 아님)</span>';
    else if (d.totals.delta == null) deltaCell = '<div class="value">—</div><span class="tiny muted">증감이 계산되지 않았습니다</span>';
    else {
      var up = Number(d.totals.delta) >= 0;
      deltaCell = '<div class="value ' + (up ? "money-positive" : "money-negative") + '">' + (up ? "+" : "") + esc(F.money(d.totals.delta, d.currency)) + "</div>" +
        '<span class="tiny muted">증감률 ' + esc(pctText(d.totals.delta_pct)) + (d.totals.delta_pct == null && Number(d.totals.previous) <= 0 ? " (이전 0이라 미계산)" : "") + "</span>";
    }
    var html = '<div class="compare-strip">' +
      '<div><div class="cs-label">이전 · ' + esc(prevDisp.start) + " ~ " + esc(prevDisp.end) + " · " + d.previous.days + '일</div>' + prevCell + "</div>" +
      '<div><div class="cs-label">현재 · ' + esc(curDisp.start) + " ~ " + esc(curDisp.end) + " · " + d.current.days + '일</div><div class="value">' + esc(money(d.totals.current)) + "</div>" + curNote + "</div>" +
      '<div class="cs-delta"><div class="cs-label">증감(현재 − 이전)</div>' + deltaCell + "</div>" +
      "</div>" +
      '<p class="note">비교 기준: ' + esc(basis) + " · 같은 길이·두 기간 모두 수집 확인된 경우만 비교 · " + esc(d.currency || "통화 없음") + " · 사용료 기준(요금 분류 필터 미적용) · 같은 CSP·계정 조건</p>";
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-022 비용 상위 리소스 ───────────────────────────────────────────────────────
  // CSP·서비스마다 상태 어휘가 다르다(EC2 RUNNING / Cloud SQL RUNNABLE / RDS AVAILABLE …). 뜻이 다른 것을
  // "실행 중"으로 뭉개지 않고 원문 뜻에 가깝게 적는다. 없는 값은 원문 그대로(title에도 원문).
  var RESOURCE_STATUS_LABEL = {
    RUNNING: "실행 중", RUNNABLE: "실행 가능(Cloud SQL)", AVAILABLE: "사용 가능", DEPLOYED: "배포됨", STAGING: "준비 중", PROVISIONING: "프로비저닝 중",
    PENDING: "생성 중", PENDING_CREATE: "생성 중", STARTING: "시작 중", STOPPING: "중지 중", STOPPED: "중지", DEALLOCATED: "할당 해제(중지)", SUSPENDED: "일시 중지",
    MAINTENANCE: "유지보수 중", REPAIRING: "복구 중", FAILED: "실패", TERMINATED: "종료됨", DELETED: "삭제됨"
  };
  // "상시 가동 가정 · 현재 중지됨" 라벨은 명백히 꺼진 상태에만 붙인다 — RUNNABLE·AVAILABLE을 중지로 오판하지 않는다.
  var RESOURCE_STOPPED = { STOPPED: 1, STOPPING: 1, DEALLOCATED: 1, SUSPENDED: 1, TERMINATED: 1, DELETED: 1 };
  function resourceStatusHtml(st) {
    if (!st) return "—";
    var k = String(st).toUpperCase();
    return '<span title="' + esc(st) + '">' + esc(RESOURCE_STATUS_LABEL[k] || st) + "</span>";
  }

  function renderTopResources() {
    if (!ctx.resources || !ctx.resources.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.resources, "비용 상위 리소스") };
    var items = ctx.resources.value.items || [];
    var withCost = items.filter(function (r) { return r.cost_summary && r.cost_summary.estimated_monthly_cost != null; });
    var currencies = {};
    withCost.forEach(function (r) { currencies[r.cost_summary.currency || "?"] = true; });
    var mixed = Object.keys(currencies).length > 1;
    // 정렬은 표시용 문자열이 아니라 원값(Number)으로. 통화가 섞이면 환산 없이 통화별로 묶은 뒤 그 안에서 내림차순 —
    // 다른 통화를 한 줄로 세우면 "하나의 순위"처럼 읽힌다.
    var sorted = withCost.slice().sort(function (a, b) {
      if (mixed && (a.cost_summary.currency || "") !== (b.cost_summary.currency || "")) return String(a.cost_summary.currency || "").localeCompare(String(b.cost_summary.currency || ""));
      return Number(b.cost_summary.estimated_monthly_cost) - Number(a.cost_summary.estimated_monthly_cost);
    });
    var top = sorted.slice(0, 5);
    var restCount = Math.max(withCost.length - top.length, 0); // "기타"는 정가가 있는 나머지만 — 정가 없는 것은 따로 센다
    var noCostCount = items.length - withCost.length;
    // Owner 태그는 리소스 태그에서 읽을 뿐 여기서 설정하는 기능은 없다 — 하나도 없으면 열을 감추고 한 줄로 알린다.
    var anyOwner = top.some(function (r) { return r.tags && r.tags[OWNER_TAG_KEY]; });

    var rows = top.map(function (r) {
      var stopped = !!(r.status && RESOURCE_STOPPED[String(r.status).toUpperCase()]);
      var owner = (r.tags && r.tags[OWNER_TAG_KEY]) || "";
      return "<tr><td>" + providerCellHtml(r.cloud_account.provider) + " " + esc(r.name || r.external_resource_id) + '<br><span class="tiny muted">' + esc(r.service && r.service.display_name || "") + (r.region ? " · " + esc(r.region) : "") + "</span></td>" +
        '<td class="num">' + esc(mixed ? F.money(r.cost_summary.estimated_monthly_cost, null) : F.money(r.cost_summary.estimated_monthly_cost, r.cost_summary.currency)) +
          (stopped ? '<br><span class="tiny muted">상시 가동 가정 · 현재 중지됨</span>' : "") + "</td>" +
        (mixed ? "<td>" + esc(r.cost_summary.currency || "—") + "</td>" : "") +
        "<td>" + resourceStatusHtml(r.status) + "</td>" + (anyOwner ? "<td>" + (owner ? esc(owner) : '<span class="tiny muted">태그 없음</span>') + "</td>" : "") +
        '<td><button type="button" class="btn no-print" data-action="open-resource-detail" data-resource-id="' + esc(r.id) + '">인벤토리에서 보기</button></td></tr>';
    });
    var html = '<p class="note"><strong>실측 지출 순위가 아닙니다.</strong> 지금 구성이 한 달(730h) 내내 켜져 있다고 가정한 정가 추정이며 <strong>조회 기간·통화·요금 분류와 무관</strong>합니다(CSP·계정 필터만 적용). 실측·월말 전망과 더하지 않습니다. 리소스 단위 실측은 이번 범위에서 제공하지 않습니다.</p>' +
      '<div class="table-wrap"><table class="changes-table"><thead><tr><th scope="col">플랫폼 / 리소스</th><th scope="col" class="num">예상 월 비용(정가)</th>' + (mixed ? '<th scope="col">통화</th>' : "") + '<th scope="col">상태</th>' + (anyOwner ? '<th scope="col">담당(Owner 태그)</th>' : "") + '<th scope="col"><span class="sr-only">행동</span></th></tr></thead><tbody>' +
      (rows.length ? rows.join("") : '<tr><td colspan="6" class="tiny muted">정가표에 있는 리소스가 없습니다</td></tr>') +
      "</tbody></table></div>" +
      (restCount > 0 ? '<p class="note">상위 5개 외 정가 있는 리소스 ' + restCount + "개 · 부분합이 아니라 개수만</p>" : "") +
      (noCostCount > 0 ? '<p class="note">정가표에 없어 금액이 없는 리소스 ' + noCostCount + "개 — 0원이 아닙니다(사용량 기반 서비스 등)</p>" : "") +
      (!anyOwner && rows.length ? '<p class="note">Owner 태그가 있는 리소스가 없어 담당 열을 표시하지 않습니다(태그는 각 CSP 콘솔에서 붙입니다).</p>' : "") +
      (mixed ? '<p class="note">통화가 섞여 있어 통화 열을 따로 두었고 환산 없이 각 통화 그대로입니다 — 하나의 순위로 읽지 마세요.</p>' : "");
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-018 계정별 비용 ────────────────────────────────────────────────────────────
  /** CF-018 계정별 비용 — 금액·비중 모두 summary.accounts[] 하나에서 낸다(같은 요금 분류·같은 통화·같은 대상 계정).
      breakdown?dimension=account는 쓰지 않는다 — top_n 상한(20) 밖 계정의 정상 금액이 사라진다.
      비중은 요금 분류가 usage(사용료)일 때만 낸다(05 §4-4 "share_pct는 usage 기준"). 분모(같은 통화 금액 합)가 0이거나
      없으면 0%가 아니라 값 없음. 통화가 둘 이상이면 통화별로 묶고 하나의 순위로 세우지 않는다.
      금액을 확인할 수 없는 계정(미지원·통화 조건 제외·수집 기록 없음)은 지우지 않고 접이식 목록에 이름·사유를 남긴다(04 §5-6 의도 유지). */
  function renderByAccount() {
    if (!ctx.summary || !ctx.summary.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.summary, "계정별 비용") };
    var accounts = ctx.summary.value.accounts || [];
    var capById = {};
    capAccounts.forEach(function (c) { capById[c.cloud_account_id] = c; });
    var shareOk = filters.chargeCategory === "usage";

    var confirmed = [], unknown = [];
    accounts.forEach(function (a) {
      var cov = a.coverage;
      if (a.filter_excluded === "currency") return unknown.push({ a: a, why: S.exclusionLabel("CURRENCY_FILTERED") });
      if (a.actual != null) return confirmed.push({ a: a, amount: a.actual, partial: !!(cov && cov.missing_count) });
      if (cov && cov.days > 0 && cov.missing_count === 0) return confirmed.push({ a: a, amount: "0.000000", zero: true });
      if (!cov) return unknown.push({ a: a, why: S.kind(a.status) === "unsupported" ? S.exclusionLabel("UNSUPPORTED") : (S.label(a.status) || "확인 불가") });
      if (cov.covered > 0) return unknown.push({ a: a, why: cov.missing_count + "일 미수집(확인된 날은 0원)" });
      unknown.push({ a: a, why: cov.days === 0 ? "완료된 날 없음" : S.exclusionLabel("PERIOD_NOT_COVERED") });
    });
    // 통화별 묶음 — 같은 통화 안에서만 내림차순·비중
    var groups = {};
    confirmed.forEach(function (r) { var cur = r.a.currency || "?"; (groups[cur] = groups[cur] || []).push(r); });
    var currencies = Object.keys(groups).sort();
    var multi = currencies.length > 1;
    var label = function (a) { var cap = capById[a.cloud_account_id] || {}; return providerCellHtml(a.provider) + " " + esc(a.account_label || cap.external_account_id || a.cloud_account_id); };

    var rowsHtml = "";
    currencies.forEach(function (cur) {
      var rows = groups[cur].slice().sort(function (x, y) { return Number(y.amount) - Number(x.amount); });
      var total = rows.reduce(function (sum, r) { return F.addDecimalString(sum, r.amount); }, "0");
      var denomOk = shareOk && Number(total) > 0;
      if (multi) rowsHtml += '<tr class="group-head"><td colspan="5">' + esc(cur) + " · 합계 " + esc(F.money(total, cur)) + (rows.some(function (r) { return r.partial; }) ? " (부분 포함)" : "") + "</td></tr>";
      rows.forEach(function (r) {
        var a = r.a;
        var pct = denomOk ? (Number(r.amount) / Number(total) * 100) : null;
        var pctText = pct == null ? '<span class="tiny muted" title="' + (shareOk ? "합계가 0이라 비중을 낼 수 없습니다" : "비중은 사용료 기준에서만 냅니다") + '">—</span>' : esc(pct.toFixed(1)) + "%";
        var bar = pct == null ? "" : '<div class="bar-track" aria-hidden="true"><div class="bar-fill" style="width:' + Math.min(pct, 100) + '%"></div></div>';
        rowsHtml += '<tr data-account-id="' + esc(a.cloud_account_id) + '"><td data-label="계정">' + label(a) +
          (r.partial ? ' <span class="tiny" style="color:var(--cost-warn)">부분 · ' + a.coverage.missing_count + "일 미수집</span>" : "") +
          (r.zero ? ' <span class="tiny muted">수집 확인된 0원</span>' : "") + "</td>" +
          '<td class="num" data-label="기간 비용">' + esc(F.money(r.amount, a.currency || cur)) + "</td>" +
          '<td class="num" data-label="비중">' + pctText + bar + "</td>" +
          '<td data-label="팀">' + esc(a.team_id ? teamNameOf(a.team_id) : "미배정") + "</td>" +
          '<td class="actions"><button type="button" class="btn no-print" data-action="account-filter-set" data-account-id="' + esc(a.cloud_account_id) + '">이 계정만 보기</button></td></tr>';
      });
    });
    var html = '<div class="table-wrap"><table class="account-table"><thead><tr><th scope="col">계정</th><th scope="col">기간 비용</th><th scope="col">비중' + (shareOk ? "" : ' <span class="tiny muted">(사용료 기준만)</span>') + '</th><th scope="col">팀</th><th scope="col"><span class="sr-only">행동</span></th></tr></thead><tbody>' +
      (rowsHtml || '<tr><td colspan="5" class="tiny muted">' + (accounts.length ? "조회 조건에서 금액이 확인된 계정이 없습니다" : "조회 조건에 맞는 계정이 없습니다") + "</td></tr>") + "</tbody></table></div>";
    if (unknown.length) {
      html += '<details class="note"><summary style="cursor:pointer">금액을 확인할 수 없는 계정 ' + unknown.length + "개</summary>" +
        unknown.map(function (u) { return label(u.a) + ' — <span class="muted">' + esc(u.why) + "</span> · " + statusTagHtml(u.a.status); }).join("<br>") + "</details>";
    }
    html += '<p class="note">금액·비중 모두 조회 조건의 ' + esc(chargeCategoryLabel(filters.chargeCategory)) + " 합(계정별) 기준 · 비중 = 계정 금액 ÷ 같은 통화 계정 합계" +
      (shareOk ? "" : " — 사용료가 아닌 분류에서는 비중을 내지 않습니다") + (multi ? " · 통화가 달라 통화별로 나눠 보며 하나의 순위로 세우지 않습니다" : "") +
      " · 계정 합계와 리소스 정가를 더하지 않습니다</p>";
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
      '<p class="note">비용이 왜 늘었는지, 어느 계정이 많이 쓰는지 등을 물어볼 수 있습니다. 위 조건(기간·CSP·계정' + (selectedTeamId ? "·팀" : "") + ")을 서버에 보내고 서버가 계산한 실측·예산·급증 요약을 바탕으로 답합니다 — 화면에서 금액을 보내거나 AI가 계산하지 않습니다. 팀이 없어도 쓸 수 있으며, 답변은 참고용이고 청구 금액을 보장하지 않습니다.</p>";
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
  /** 팀 종속 블록(CF-026·025·027)이 값을 그리기 전에 확인하는 선행 상태. 조회 실패·조회 중·팀 없음·미선택을
      구분한다 — 조회 실패를 "팀이 없습니다"로, 조회 중을 "값 없음"으로 보여주지 않는다. 그릴 수 있으면 null. */
  function teamGateHtml(blockName) {
    if (ctx.teams && !ctx.teams.ok) {
      return { state: "COLLECT_FAILED", html: '<div class="state-view" role="status"><span class="dash">—</span><p>팀 목록을 가져오지 못해 ' + esc(blockName) + "을(를) 표시할 수 없습니다.</p>" +
        '<p class="tiny muted">' + esc(window.MCErr ? window.MCErr.headline(ctx.teams.error) : (ctx.teams.error && ctx.teams.error.message) || "") + "</p>" +
        '<button type="button" class="btn no-print" data-action="reload-budget-tab">다시 조회</button></div>' };
    }
    if (!selectedTeamId) return { state: "CONNECTED_OK", html: '<div class="value">—</div><p class="note">팀을 선택하면 표시됩니다.</p>' };
    if (teamLoading) return { state: "PENDING", html: '<p class="note" role="status">' + esc(teamNameOf(selectedTeamId)) + " 팀 " + esc(blockName) + " 조회 중…</p>" };
    return null;
  }

  /** 팀이 없으면 예산 카드 3장(CF-026·CF-025·CF-027)이 각각 "팀을 먼저 선택하세요"를 반복한다 — 그 대신
      CFL-03 하나를 시작 안내로 두고 3장은 숨긴다. DOM·id는 그대로(팀이 생기면 다시 보인다). */
  var BUDGET_DEPENDENT_BLOCKS = ["CF-026", "CF-025", "CF-027"];
  function setBudgetBlocksHidden(hidden) {
    BUDGET_DEPENDENT_BLOCKS.forEach(function (id) { var el = document.getElementById(id); if (el) el.hidden = hidden; });
  }

  function renderTeamSelect() {
    if (!ctx.teams || !ctx.teams.ok) { setBudgetBlocksHidden(false); return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.teams, "팀 목록") }; }
    var d = ctx.teams.value;
    var teams = d.items || [];
    var unassigned = d.unassigned || { account_count: 0, accounts: [] };
    setBudgetBlocksHidden(!teams.length);
    var html = "";
    if (!teams.length) {
      setBlockTitle("CFL-03", "예산 시작하기");
      html += '<div class="start-guide">' +
        '<p class="small">팀은 예산을 적용할 <strong>계정 묶음</strong>입니다. 순서대로 하면 예산 대비 실적과 80%·100% 알림을 받을 수 있습니다.</p>' +
        '<ol class="start-steps">' +
          '<li><strong>팀 만들기</strong> — 이름과 예산 통화(USD 등)를 정합니다</li>' +
          '<li><strong>계정 배정</strong> — 미배정 계정 ' + unassigned.account_count + "개 중 이 팀에 넣을 계정을 고릅니다(1계정 1팀)</li>" +
          '<li><strong>예산 설정</strong> — 월간·분기·연간·사용자 지정 중 하나로 한도를 둡니다</li>' +
        "</ol>" +
        '<div class="button-row no-print"><button type="button" class="btn primary" data-action="open-team-dialog">팀 만들기</button></div>' +
        '<p class="note">팀은 조직 권한이나 구성원 초대가 아니라, 내가 연결한 클라우드 계정을 묶는 단위입니다. 아래 급증 탐지·AI 비용 상담은 팀 없이도 동작하고, 비용 작업 큐는 ① 비용 개요 탭에 있습니다.</p>' +
        "</div>";
    } else {
      setBlockTitle("CFL-03", "팀 선택");
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
          "<div><span>계정</span><span>" + (t.accounts.length ? accountSummaryText(t.accounts, mismatched) +
            ' <button type="button" class="btn no-print" data-action="open-team-dialog" style="padding:2px 8px;font-size:11px">목록</button>' : "배정된 계정 없음") + "</span></div>" +
          "<div><span>팀 통화</span><span>" + esc(t.currency) + "</span></div>" +
          "<div><span>현재 예산</span><span>" + (t.active_budget
            ? esc(F.money(t.active_budget.limit_amount, t.active_budget.currency)) + " · " + esc(PERIOD_LABEL[t.active_budget.period_type] || t.active_budget.period_type)
            : "예산 미설정") + "</span></div>" +
          "</div>";
        if (mismatched.length) html += '<p class="warning">팀 통화(' + esc(t.currency) + ")와 다른 통화의 계정 " + mismatched.length + "개는 예산 계산에서 제외됩니다. 팀에서 자동으로 빠지지는 않습니다.</p>";
      }
    }
    // 미배정 계정 목록은 길어지기 쉬우니 개수만 보이고 이름은 접는다
    html += (unassigned.accounts && unassigned.accounts.length
      ? '<details class="note"><summary style="cursor:pointer">미배정 계정 ' + unassigned.account_count + "개 보기</summary>" +
          unassigned.accounts.map(function (a) { return esc(PROVIDER_LABEL[a.provider] || a.provider) + " " + esc(a.account_label || a.external_account_id); }).join(", ") + "</details>"
      : '<p class="note">미배정 계정 없음</p>') +
      (teams.length ? '<p class="note">팀 선택은 이 탭(예산·검토)에만 적용됩니다.</p>' : "");
    return { state: "CONNECTED_OK", html: html };
  }

  var PERIOD_LABEL = { monthly: "월간", quarterly: "분기", annual: "연간", custom: "사용자 지정" };

  /** 팀 계정을 이름으로 다 나열하면 6개만 돼도 한 줄을 넘친다 — 개수·통화 확인/미확인·팀 통화 불일치만 요약하고
      이름 목록은 "팀 관리" 모달(체크박스)에서 본다. 통화 미확인 = 아직 비용 수집이 안 돼 통화를 모르는 계정. */
  function accountSummaryText(accounts, mismatched) {
    var withCur = accounts.filter(function (a) { return a.currency; });
    var currencies = {};
    withCur.forEach(function (a) { currencies[a.currency] = true; });
    var parts = [accounts.length + "개"];
    if (withCur.length) parts.push("통화 확인 " + withCur.length + " (" + esc(Object.keys(currencies).join(", ")) + ")");
    if (accounts.length - withCur.length) parts.push("통화 미확인 " + (accounts.length - withCur.length) + " (수집 전)");
    if (mismatched.length) parts.push('<span style="color:var(--cost-warn)">팀 통화와 다름 ' + mismatched.length + "</span>");
    return parts.join(" · ");
  }

  function teamDialogHtml() {
    var teams = teamsList();
    var t = teamById(selectedTeamId);
    var allAccounts = capAccounts.slice();
    var html = '<p class="note" style="margin-top:0">팀 = 내가 연결한 클라우드 계정의 묶음(예산 적용 단위). 조직 권한·구성원 초대 기능이 아닙니다.</p>' +
      '<h3 class="small" style="font-weight:700">새 팀</h3>' +
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
        '<button type="button" class="btn" data-action="team-delete-ask" data-team-id="' + esc(t.id) + '">팀 삭제…</button></div>' +
        '<div id="team-delete-confirm" class="warning" hidden>팀 <strong>' + esc(t.name) + '</strong>을(를) 삭제합니다. 클라우드 계정과 비용 기록은 지워지지 않고 계정은 미배정으로 돌아갑니다. 이 팀의 예산과 알림 기록은 함께 삭제됩니다. ' +
          '<button type="button" class="btn" data-action="team-delete" data-team-id="' + esc(t.id) + '">삭제 확인</button> <button type="button" class="btn" data-action="team-delete-cancel">취소</button></div>' +
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
    return '<p class="note">예산 기간 ' + esc(b.period_start) + " ~ " + esc(addDaysISO(b.period_end, -1)) + " · 기준일 " + esc(b.basis_date) + " (UTC 일 기준)" +
      " · 팀 전체 계정 · 현재 소속 기준 · <strong>상단 필터 미적용</strong></p>";
  }

  function budgetStateBadge(b) {
    return '<span class="badge">' + esc(STATE_LABEL[b.period_state] || b.period_state) + " · " + esc(PERIOD_LABEL[b.period_type] || b.period_type) + "</span>";
  }

  function renderBudgetStatus() {
    var gate = teamGateHtml("예산 대비 실적");
    if (gate) return gate;
    if (!ctx.budgetStatus || !ctx.budgetStatus.ok) {
      return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.budgetStatus, "예산 대비 실적") + '<div class="button-row no-print"><button type="button" class="btn" data-action="reload-budget-tab">다시 조회</button></div>' };
    }
    var d = ctx.budgetStatus.value;
    var b = d.budget;
    var team = teamById(selectedTeamId);
    var head = '<p class="small" style="margin-bottom:6px"><strong>' + esc(team ? team.name : "팀") + "</strong>" + (b ? " · " + budgetStateBadge(b) + " · 한도 " + esc(F.money(b.limit_amount, b.currency)) : "") + "</p>";
    var html = head;
    if (!d.computable) {
      html += '<div class="state-view" role="status"><span class="dash">—</span><p>' + esc(REASON_TEXT[d.reason_code] || "판정하지 않습니다.") + "</p>" +
        (d.reason_code === "CURRENCY_MISMATCH" && d.excluded_accounts.length ? "<p>제외 계정: " + d.excluded_accounts.map(function (a) { return esc(PROVIDER_LABEL[a.provider] || a.provider) + " " + esc(a.account_label || a.cloud_account_id) + " (" + esc(a.currency || "통화 미확인") + ")"; }).join(", ") + "</p>" : "") +
        (d.usage && d.reason_code === "MISSING_DAYS" ? '<p class="tiny muted">' + (Number(d.usage.amount) > 0 ? "지금까지 합산된 사용액 " + esc(F.money(d.usage.amount, d.usage.currency)) : "아직 합산된 사용액이 없습니다(수집된 날 없음)") + " — 결측일이 채워지면 소진율을 냅니다(0%가 아닙니다)</p>" : "") +
        (REASON_ACTION[d.reason_code] || "") + "</div>";
      if (b) html += budgetScopeNote(b);
      html += '<div class="button-row no-print"><button type="button" class="btn" data-action="open-budget-basis-dialog">계산 근거</button></div>';
      return { state: "CONNECTED_OK", html: html };
    }
    var ratio = Number(d.ratio_pct);
    var over = ratio > 100;
    var remaining = F.subDecimalString(b.limit_amount, d.usage.amount);   // 음수면 초과
    var overAmount = F.subDecimalString(d.usage.amount, b.limit_amount);
    html += '<div class="paired">' +
      '<div><div class="value">' + esc(F.money(d.usage.amount, d.usage.currency)) + ' <span class="tiny muted">/ ' + esc(F.money(b.limit_amount, b.currency)) + "</span></div>" +
        '<p class="small">소진율 <strong>' + esc(d.ratio_pct) + "%</strong> · " +
        (over ? '초과 <span class="money-positive">' + esc(F.money(overAmount, b.currency)) + "</span>" : "남은 금액 " + esc(F.money(remaining, b.currency))) +
        (d.forecast ? " · 월말 전망 " + esc(d.forecast.ratio_pct) + "% (" + esc(F.money(d.forecast.amount, b.currency)) + ")" : "") + "</p>" +
        '<div class="bar-track" aria-hidden="true"><div class="bar-fill" style="width:' + Math.min(ratio, 100) + '%"></div></div>' +
        (over ? '<div class="bar-track" style="margin-top:3px"><div class="bar-fill" style="width:' + Math.min(ratio - 100, 100) + '%;background:var(--cost-up)"></div></div><p class="tiny muted">막대는 100%까지, 두 번째 막대가 초과분 — 수치는 ' + esc(d.ratio_pct) + "% 그대로</p>" : "") +
        (b.period_state === "upcoming" ? '<p class="tiny muted">시작 전 예산입니다 — 사용액은 아직 0입니다.</p>' : "") +
      "</div>" +
      '<div>' + (over ? '<p class="warning">한도를 넘었습니다. 예산 초과는 알림 기준이며 리소스 생성을 막지 않습니다.</p>' : '<p class="note">한도 이내입니다. 80%·100% 도달 시 알림이 만들어집니다(생성 차단 없음).</p>') +
        '<div class="status-list">' + d.thresholds.map(function (t) {
          return "<div><span>" + t.percent + "%</span><span>" + (t.crossed ? "도달" : "미도달") + (t.notified ? " · 알림 생성됨" : (t.crossed ? " · 알림 생성 미확인" : "")) + "</span></div>";
        }).join("") + "</div>" +
        '<p class="tiny muted">사용료 기준(크레딧·환불 제외)' + (d.usage.is_estimated ? " · 잠정치 포함" : "") + "</p>" +
      "</div></div>" +
      budgetScopeNote(b) +
      '<div class="button-row no-print"><button type="button" class="btn" data-action="open-budget-basis-dialog">계산 근거</button><button type="button" class="btn" data-action="nav-scroll" data-tab="budget" data-target="CF-025">예산 설정</button></div>';
    return { state: "CONNECTED_OK", html: html };
  }

  function budgetBasisDialogHtml() {
    var d = ctx.budgetStatus && ctx.budgetStatus.ok ? ctx.budgetStatus.value : null;
    if (!d) return '<p class="note">계산 결과가 없습니다.</p>';
    var b = d.budget, u = d.usage;
    return '<dl class="meta-grid" style="grid-template-columns:1fr">' +
      metaItem("선택된 예산", b ? esc(STATE_LABEL[b.period_state] || b.period_state) + " · " + esc(PERIOD_LABEL[b.period_type] || b.period_type) + " · 한도 " + esc(F.money(b.limit_amount, b.currency)) : "없음") +
      metaItem("적용 구간", b ? esc(b.period_start) + " ~ " + esc(addDaysISO(b.period_end, -1)) + " (기준일 " + esc(b.basis_date) + ")" : "—") +
      metaItem("예산 선택 규칙", "진행 중인 사용자 지정 예산이 있으면 그것을, 없으면 진행 중인 반복 예산, 그다음 가장 가까운 예정 예산 순(서버 결정)") +
      metaItem("사용액", u ? esc(F.money(u.amount, u.currency)) + " · " + esc(u.basis === "usage_before_credits" ? "사용료 기준(크레딧·환불 제외)" : u.basis) + " · 순액 " + esc(F.money(u.net_amount, u.currency)) + (u.as_of ? " · 마지막 수집 " + esc(fmtWhen(u.as_of).text) : "") : "—") +
      metaItem("소진율", d.computable ? esc(d.ratio_pct) + "% = 사용액 ÷ 한도 × 100 (서버 계산, 100% 초과도 그대로)" : "산출 불가 — " + esc(REASON_TEXT[d.reason_code] || d.reason_code || "")) +
      metaItem("월말 전망", d.forecast ? esc(F.money(d.forecast.amount, b.currency)) + " (" + esc(d.forecast.ratio_pct) + "%) · " + esc(d.forecast.based_through) + "까지 실측을 남은 일수로 늘림 · 저장하지 않음" : "없음(진행 중 예산에서 기준일이 오늘일 때만)") +
      metaItem("제외 계정", d.excluded_accounts.length ? d.excluded_accounts.map(function (a) { return esc(a.account_label || a.cloud_account_id) + "(" + esc(a.reason === "currency_mismatch" ? "통화 불일치" : "미지원") + ")"; }).join(", ") : "없음") +
      metaItem("집계 범위", "팀 전체 계정 · 현재 소속 기준 · 상단 기간·CSP·계정 필터 미적용") +
      metaItem("예산 초과", "알림 기준일 뿐 리소스 생성을 막지 않습니다") +
      "</dl>";
  }

  // ── CF-025 예산 설정 ───────────────────────────────────────────────────────────────
  var STATE_LABEL = { upcoming: "예정", in_progress: "진행 중", ended: "종료" };

  // 작성 중인 예산 입력은 팀별로 메모리에 둔다 — renderAll()·팀 전환·리사이즈로 화면이 다시 그려져도 값이 조용히
  // 사라지지 않는다(저장 전까지만, 새로고침하면 사라진다). 폼은 "지금 선택한 팀"의 것이고 제출도 그 팀으로 간다.
  var budgetDrafts = {};
  function captureBudgetDraft() {
    if (!selectedTeamId || !document.getElementById("budget-value")) return;
    var v = budgetFormValues();
    var endDisp = (document.getElementById("budget-end") || {}).value || "";
    budgetDrafts[selectedTeamId] = { period_type: v.period_type, start_date: v.start_date, end_display: endDisp, limit_amount: v.limit_amount };
  }
  function budgetDraftDirty(teamId) {
    var d = budgetDrafts[teamId];
    return !!(d && (d.limit_amount || (d.start_date && d.start_date !== utcStartOfMonthISO()) || d.period_type !== "monthly" || d.end_display));
  }
  var budgetPending = false;

  function renderBudgetForm() {
    var gate = teamGateHtml("예산 설정");
    if (gate) return gate;
    var t = teamById(selectedTeamId);
    var draft = budgetDrafts[selectedTeamId] || {};
    var listOk = ctx.budgets && ctx.budgets.ok;
    var items = listOk ? (ctx.budgets.value.items || []) : [];
    var hasActive = items.some(function (b) { return b.state === "in_progress"; });
    var openForm = !hasActive || budgetDraftDirty(selectedTeamId);
    var pt = draft.period_type || "monthly";
    var html = '<details class="budget-form"' + (openForm ? " open" : "") + '><summary class="small" style="cursor:pointer;font-weight:600">' + (hasActive ? "새 적용일로 예산 등록" : "예산 만들기") +
        (budgetDraftDirty(selectedTeamId) ? ' <span class="filter-dirty">작성 중(저장 전)</span>' : "") + "</summary>" +
      '<p class="note" style="margin-top:6px">반복(월간·분기·연간)은 달력 구간마다 같은 한도가 적용되고 시작일부터 다음 예산 시작 전까지 유효합니다. 사용자 지정은 한 구간(최대 1년)만 씁니다. 이미 시작된 예산의 한도를 바꾸려면 새 적용일로 등록합니다.</p>' +
      '<div class="filter-grid">' +
      '<div class="filter-field">주기<span class="button-row" style="margin-top:0;gap:10px">' +
        ["monthly", "quarterly", "annual", "custom"].map(function (k) {
          return '<label style="display:inline-flex;gap:4px;align-items:center;font-size:12px"><input type="radio" name="budget-period-type" value="' + k + '"' + (pt === k ? " checked" : "") + "> " + PERIOD_LABEL[k] + "</label>";
        }).join("") + "</span></div>" +
      '<label class="filter-box">적용 시작일<input id="budget-start" type="date" value="' + esc(draft.start_date || utcStartOfMonthISO()) + '" aria-describedby="budget-feedback"></label>' +
      '<label class="filter-box" id="budget-end-wrap"' + (pt === "custom" ? "" : " hidden") + '>종료일(포함)<input id="budget-end" type="date" value="' + esc(draft.end_display || "") + '"></label>' +
      '<label class="filter-box">한도(' + esc(t ? t.currency : "USD") + ')<input id="budget-value" type="number" min="0.000001" step="0.01" placeholder="예: 300" value="' + esc(draft.limit_amount || "") + '" aria-describedby="budget-feedback"></label>' +
      '<span class="button-row" style="margin-top:0"><button type="button" class="btn primary no-print" data-action="budget-create"' + (budgetPending ? " disabled" : "") + ">" + (budgetPending ? "저장 중…" : "저장") + "</button>" +
      '<button type="button" class="btn no-print" data-action="budget-reset">취소</button></span>' +
      "</div>" +
      '<p id="budget-feedback" class="note" role="status">통화는 팀 통화(' + esc(t ? t.currency : "USD") + ")로 고정됩니다. 예정(시작 전) 예산만 한도·종료일을 수정할 수 있습니다. 날짜는 <strong>UTC 일 기준</strong>(비용 집계와 같음)이라 한국 시간 새벽엔 오늘이 하루 다르게 보일 수 있습니다.</p>" +
      "</details>";
    if (!listOk) {
      html += '<div class="state-view" role="status"><span class="dash">—</span><p>예산 목록을 가져오지 못했습니다.</p><p class="tiny muted">' +
        esc(ctx.budgets && ctx.budgets.error ? (window.MCErr ? window.MCErr.headline(ctx.budgets.error) : ctx.budgets.error.message) : "") + "</p>" +
        '<button type="button" class="btn no-print" data-action="reload-budget-tab">다시 조회</button></div>';
      return { state: "COLLECT_FAILED", html: html };
    }
    html += '<div class="table-wrap"><table class="changes-table"><thead><tr><th scope="col">주기</th><th scope="col">적용 시작</th><th scope="col">종료</th><th scope="col" class="num">한도</th><th scope="col">상태</th><th scope="col"><span class="sr-only">행동</span></th></tr></thead><tbody>' +
      (items.length ? items.map(function (b) {
        return "<tr><td>" + esc(PERIOD_LABEL[b.period_type] || b.period_type) + "</td><td>" + esc(b.start_date) + "</td><td>" + esc(b.end_date ? addDaysISO(b.end_date, -1) : "다음 예산 전까지") + "</td>" +
          '<td class="num">' + esc(F.money(b.limit_amount, b.currency)) + '</td><td><span class="badge">' + esc(STATE_LABEL[b.state] || b.state) + "</span></td><td>" +
          (b.state === "upcoming"
            ? '<button type="button" class="btn no-print" data-action="budget-edit-open" data-budget-id="' + esc(b.id) + '">수정</button> <button type="button" class="btn no-print" data-action="budget-delete-open" data-budget-id="' + esc(b.id) + '">삭제</button>'
            : '<span class="tiny muted" title="이미 시작된 예산은 한도를 직접 바꾸지 않습니다">수정 불가 · 새 적용일로 등록</span>') +
          "</td></tr>";
      }).join("") : '<tr><td colspan="6" class="tiny muted">등록된 예산이 없습니다 — 예산 미설정은 $0이 아닙니다</td></tr>') +
      "</tbody></table></div>";
    return { state: "CONNECTED_OK", html: html };
  }

  function budgetFormValues() {
    var pt = (document.querySelector('input[name="budget-period-type"]:checked') || {}).value || "monthly";
    var start = (document.getElementById("budget-start") || {}).value || "";
    var endDisp = (document.getElementById("budget-end") || {}).value || "";
    var limit = ((document.getElementById("budget-value") || {}).value || "").trim();
    var body = { period_type: pt, start_date: start, limit_amount: limit };
    if (pt === "custom") body.end_date = endDisp ? addDaysISO(endDisp, 1) : null; // 화면은 포함, API는 제외 경계
    return body;
  }

  /** 저장 전 클라이언트 검증 — 서버 규칙(한도 > 0 · custom 종료일 필수 · 최대 1년)과 같은 것만. 문구는 입력 옆(#budget-feedback). */
  function validateBudgetForm(v) {
    if (!v.start_date) return "적용 시작일을 입력하세요.";
    if (!v.limit_amount || !(Number(v.limit_amount) > 0)) return "한도는 0보다 큰 금액이어야 합니다.";
    if (v.period_type === "custom") {
      if (!v.end_date) return "사용자 지정 예산은 종료일이 필요합니다.";
      if (v.end_date <= v.start_date) return "종료일은 시작일 이후여야 합니다.";
      var days = Math.round((new Date(v.end_date) - new Date(v.start_date)) / 86400000);
      if (days > 366) return "사용자 지정 예산 기간은 최대 1년입니다(현재 " + days + "일).";
    }
    return "";
  }
  function showBudgetError(text) {
    setFeedback("budget-feedback", text, true);
    var details = document.querySelector("#CF-025 details.budget-form");
    if (details) details.open = true; // 접힌 폼 안에 오류가 갇히지 않게
    var el = document.getElementById("budget-value");
    if (el && /한도/.test(text)) el.focus();
  }

  function budgetEditDialogHtml(b) {
    var isCustom = b.period_type === "custom";
    return '<p class="small">예정 예산의 오타 수정입니다. 적용 시작일·주기·통화는 바꿀 수 없습니다(바꾸려면 새 적용일로 등록).</p>' +
      '<dl class="meta-grid" style="grid-template-columns:1fr 1fr 1fr"><div><dt>주기</dt><dd>' + esc(PERIOD_LABEL[b.period_type] || b.period_type) + "</dd></div><div><dt>적용 시작</dt><dd>" + esc(b.start_date) + "</dd></div><div><dt>통화</dt><dd>" + esc(b.currency) + "</dd></div></dl>" +
      '<div class="filter-grid" style="margin-top:10px">' +
      '<label class="filter-box">한도(' + esc(b.currency) + ')<input id="budget-edit-limit" type="number" min="0.000001" step="0.01" value="' + esc(String(Number(b.limit_amount))) + '"></label>' +
      (isCustom ? '<label class="filter-box">종료일(포함)<input id="budget-edit-end" type="date" value="' + esc(addDaysISO(b.end_date, -1)) + '"></label>' : "") +
      '<span class="button-row" style="margin-top:0"><button type="button" class="btn primary" data-action="budget-patch" data-budget-id="' + esc(b.id) + '" data-period-type="' + esc(b.period_type) + '">저장</button></span></div>' +
      '<p id="budget-edit-feedback" class="note" role="status"></p>';
  }
  function budgetDeleteDialogHtml(b) {
    return '<p class="small">이 예산을 삭제할까요?</p><dl class="meta-grid" style="grid-template-columns:1fr 1fr 1fr"><div><dt>주기</dt><dd>' + esc(PERIOD_LABEL[b.period_type] || b.period_type) + "</dd></div><div><dt>적용 시작</dt><dd>" + esc(b.start_date) + "</dd></div><div><dt>한도</dt><dd>" + esc(F.money(b.limit_amount, b.currency)) + "</dd></div></dl>" +
      '<p class="note">예산 이력에서 사라지고 이 예산의 알림 기록도 함께 삭제됩니다. 계정과 비용 기록은 그대로입니다.</p>' +
      '<div class="button-row"><button type="button" class="btn primary" data-action="budget-delete" data-budget-id="' + esc(b.id) + '">삭제 확인</button><button type="button" class="btn" data-modal-close>취소</button></div>' +
      '<p id="budget-edit-feedback" class="note" role="status"></p>';
  }
  function budgetById(id) {
    var items = (ctx.budgets && ctx.budgets.ok && ctx.budgets.value.items) || [];
    return items.filter(function (b) { return b.id === id; })[0] || null;
  }

  // ── CF-027 임계치 알림 ─────────────────────────────────────────────────────────────
  function renderThresholds() {
    var gate = teamGateHtml("임계치 알림");
    if (gate) return gate;
    if (!ctx.budgetStatus || !ctx.budgetStatus.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.budgetStatus, "임계치 알림") + '<div class="button-row no-print"><button type="button" class="btn" data-action="reload-budget-tab">다시 조회</button></div>' };
    var d = ctx.budgetStatus.value;
    var grey = !d.computable;
    var html = '<p class="tiny muted" style="margin-bottom:6px">이 예산 구간' + (d.budget ? "(" + esc(d.budget.period_start) + " ~ " + esc(addDaysISO(d.budget.period_end, -1)) + ")" : "") + "의 임계치 상태</p>" +
      '<div class="status-list">' + d.thresholds.map(function (t) {
      var tag, text;
      if (grey) { tag = "unsupported"; text = "판정 불가 — " + (REASON_TEXT[d.reason_code] || "데이터 부족"); }
      else if (!t.crossed) { tag = "data"; text = "미도달"; }
      else if (t.notified) { tag = "attention"; text = "도달 · 알림 생성됨" + (t.crossed_at ? " (" + fmtWhen(t.crossed_at).text + ")" : ""); }
      else { tag = "waiting"; text = "도달 · 알림 생성 미확인 — 서버가 다음 평가(수집·예산 변경 뒤)에서 만들 수 있습니다"; }
      return "<div><span>" + t.percent + '%</span><span class="status-tag ' + tag + '">' + esc(text) + "</span></div>";
    }).join("") + "</div>";

    // 최근 알림 — /notifications?limit=50 은 "이 사용자의 최근 50건 전체"이지 팀 필터가 아니다. 여기 없다고 이 팀에
    // 알림이 없었다고 말하지 않는다.
    var n = ctx.notifications;
    if (!n || !n.ok) {
      html += '<p class="note warning" style="margin-top:12px">최근 알림을 가져오지 못했습니다(알림이 없는 것이 아닙니다). <button type="button" class="btn no-print" data-action="reload-budget-tab">다시 조회</button></p>';
    } else {
      var notes = (n.value.items || []).filter(function (x) { return x.type === "budget_threshold" && x.message_params && x.message_params.team_id === selectedTeamId; }).slice(0, 5);
      html += '<p class="small" style="margin-top:12px;font-weight:600">최근 알림(이 팀 · 최근 50건 중)</p>' +
        (notes.length ? '<div class="status-list">' + notes.map(function (x) {
          var p = x.message_params;
          return "<div><span>" + esc(fmtWhen(x.created_at).text) + "</span><span>예산 " + p.percent + "% 도달 · " + esc(p.period_start) + " 시작 구간 · 한도 " + esc(F.money(p.limit_amount, p.currency)) + "</span></div>";
        }).join("") + "</div>" : '<p class="tiny muted">최근 50건 범위에 이 팀의 예산 알림이 없습니다.</p>');
    }
    html += '<p class="note">알림은 사이드바 🔔에 쌓이는 서비스 내 알림입니다(이메일·문자 아님). 같은 예산·구간·임계치에는 한 번만 만들어지고, 화면을 여는 것만으로는 만들어지지 않습니다. 예산 초과가 리소스 생성을 막지는 않습니다.</p>' +
      '<div class="button-row no-print"><button type="button" class="btn" data-action="open-threshold-dialog">알림 조건 보기</button></div>';
    return { state: "CONNECTED_OK", html: html };
  }

  // ── CF-033 급증 탐지 ───────────────────────────────────────────────────────────────
  /* 규칙 한 줄 요약 — 긴 기술 규칙(이력 12일·최근 3일 제외·요금 분류·통화)은 "규칙 상세" 모달로. */
  function ruleSummaryText(rule) {
    return "직전 " + rule.baseline_days + "일 평균보다 " + esc(F.money(rule.min_delta_amount, rule.currency)) + " 이상 <strong>그리고</strong> " + rule.min_increase_pct + "% 이상 늘어난 날을 급증으로 봅니다.";
  }
  function ruleDialogHtml(rule) {
    return '<dl class="meta-grid" style="grid-template-columns:1fr">' +
      metaItem("판정 단위", "계정 × 서비스 × 완료된 날(하루)") +
      metaItem("기준선", "그 날 직전 " + rule.baseline_days + "일의 평균. " + rule.baseline_days + "일이 전부 수집 확인됐을 때만 판정하고, 하나라도 미수집이면 그 날은 판정 보류(0으로 넣지 않음)") +
      metaItem("조건", "증가액 ≥ " + esc(F.money(rule.min_delta_amount, rule.currency)) + " AND 증가율 ≥ " + rule.min_increase_pct + "%. 기준선이 0이면 증가율을 낼 수 없어 증가액만으로 판정하고 '신규 비용 발생'으로 표시") +
      metaItem("이력", "판정일까지 수집 확인된 날이 " + rule.min_history_days + "일 이상인 계정만 판정") +
      metaItem("최근 " + rule.exclude_recent_days + "일 제외", "CSP가 아직 확정하지 않은 구간이라 판정하지 않음") +
      metaItem("요금 분류", esc(rule.charge_category.join(", ")) + " 기준 — 크레딧·환불을 섞어 순액으로 판정하면 크레딧이 들어온 날이 급증으로 잡힘") +
      metaItem("통화", "최소 증가액은 " + esc(rule.currency) + " 기준. 다른 통화(KRW 등)는 정책이 정해질 때까지 판정 보류") +
      metaItem("라벨", "항상 \"원인 확인 필요\" — 원인을 단정하지 않음") +
      "</dl>";
  }
  var lastAnomalyRule = null;
  function anomalyPctText(it) {
    return it.delta_pct == null ? "신규 비용 발생(기준선 0 — 증가율 없음)" : "+" + esc(it.delta_pct) + "%";
  }

  /** 표 위에 먼저 말할 판정 상태. "탐지된 급증 없음"과 "평가하지 못함"을 섞지 않고, API가 주지 않는
      "판정 완료"·"모든 계정 정상"은 말하지 않는다. 대상 범위는 전역 capabilities가 아니라 현재 필터의
      summary.accounts(같은 CSP·계정 조건)다. 보류 개수는 계정 id로 중복 제거해 계정 수로 센다. */
  function anomalyStatusHtml(d) {
    var heldIds = {};
    (d.held || []).forEach(function (h) { heldIds[h.cloud_account_id] = "기준선 미수집"; });
    (d.insufficient_history || []).forEach(function (h) { heldIds[h.cloud_account_id] = "이력 부족"; });
    (d.unsupported_currency || []).forEach(function (u) { heldIds[u.cloud_account_id] = "통화 정책 미정"; });
    var heldCount = Object.keys(heldIds).length;
    var reasonCounts = {};
    Object.keys(heldIds).forEach(function (id) { reasonCounts[heldIds[id]] = (reasonCounts[heldIds[id]] || 0) + 1; });
    var reasonText = Object.keys(reasonCounts).map(function (k) { return k + " " + reasonCounts[k]; }).join(" · ");
    var scoped = (ctx.summary && ctx.summary.ok && ctx.summary.value.accounts) || [];
    var supported = scoped.filter(function (a) { return S.kind(a.status) !== "unsupported"; });
    var out = "";
    if ((d.items || []).length) out += '<span class="status-tag attention">급증 ' + d.items.length + "건 탐지 — 원인 확인 필요</span>";
    else if (!supported.length) out += '<span class="status-tag unsupported">판정 대상 계정 없음 — 현재 조건에 비용 수집을 지원하는 계정이 없습니다</span>';
    else out += '<span class="status-tag data">현재 응답에서 탐지된 급증 없음</span>';
    if (heldCount) out += '<span class="status-tag waiting">평가하지 못한 계정 ' + heldCount + "개 (" + esc(reasonText) + ") — 이 계정들은 급증 여부를 판정하지 않았습니다</span>";
    return '<div class="status-row">' + out + "</div>";
  }

  function renderAnomalies() {
    if (!ctx.anomalies || !ctx.anomalies.ok) return { state: "COLLECT_FAILED", html: fetchFailedHtml(ctx.anomalies, "급증 탐지") + '<div class="button-row no-print"><button type="button" class="btn" data-action="apply-filters">다시 조회</button></div>' };
    var d = ctx.anomalies.value;
    lastAnomalyRule = d.rule;
    var rows = (d.items || []).map(function (it) {
      return "<tr><td>" + esc(it.date) + "<br><span class=\"tiny muted\">" + esc(PROVIDER_LABEL[it.provider] || it.provider) + " " + esc(accountLabelOf(it.cloud_account_id)) + " · " + esc(it.service) + "</span>" +
        (it.is_sample_data ? ' <span class="badge">예시 데이터</span>' : "") + "</td>" +
        '<td class="num">' + esc(F.money(it.baseline_amount, it.currency)) + " → " + esc(F.money(it.amount, it.currency)) + "</td>" +
        '<td class="num"><span class="money-positive">+' + esc(F.money(it.delta, it.currency)) + "</span><br><span class=\"tiny muted\">" + anomalyPctText(it) + " · 그 날의 증가액</span></td>" +
        "<td>" + esc(it.label) + (it.review ? '<br><span class="badge">검토 ' + esc(REVIEW_STATUS_LABEL[it.review.status] || it.review.status) + "</span>" : "") + "</td>" +
        '<td><button type="button" class="btn no-print" data-action="open-anomaly-dialog" data-source-key="' + esc(it.source_key) + '">근거 보기</button></td></tr>';
    });
    var html = anomalyStatusHtml(d) +
      '<p class="note">' + ruleSummaryText(d.rule) + ' <button type="button" class="btn no-print" data-action="open-rule-dialog" style="padding:2px 8px;font-size:11px">규칙 상세</button></p>';
    if (rows.length) {
      html += '<div class="table-wrap"><table class="changes-table"><thead><tr><th scope="col">날짜 / 대상</th><th scope="col" class="num">기준 → 실제</th><th scope="col" class="num">증가액</th><th scope="col">상태</th><th scope="col"><span class="sr-only">행동</span></th></tr></thead><tbody>' +
        rows.join("") + "</tbody></table></div>";
    }
    // 계정별 사유 — 위 배지가 "왜"를 요약했으니 여기는 어느 계정인지만
    var reasons = [];
    (d.insufficient_history || []).forEach(function (h) { reasons.push(esc(accountLabelOf(h.cloud_account_id)) + ": 이력 " + h.days_available + "/" + h.days_required + "일 — 판정 전"); });
    (d.held || []).forEach(function (h) { reasons.push(esc(accountLabelOf(h.cloud_account_id)) + ": 기준선 " + d.rule.baseline_days + "일 중 미수집일이 있어 " + h.days.length + "일 보류"); });
    (d.unsupported_currency || []).forEach(function (u) { reasons.push(esc(accountLabelOf(u.cloud_account_id)) + ": " + esc(u.currency || "통화 미확인") + " 임계값 미정 — 보류"); });
    if (reasons.length) html += '<details class="note"><summary style="cursor:pointer">평가하지 못한 계정 사유 ' + reasons.length + "건</summary>" + reasons.join("<br>") + "</details>";
    html += '<p class="note">적용 조건: 상단 기간·CSP·계정 필터 · 팀 선택은 이 블록에 적용되지 않음 · 통화 ' + esc(d.rule.currency) + " 계정만 판정 · 검토 등록 항목은 ① 비용 개요 탭의 비용 작업 큐에서 상태를 바꿉니다</p>";
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
      metaItem("기준값(직전 " + (lastAnomalyRule ? lastAnomalyRule.baseline_days : 7) + "일 평균)", esc(F.money(it.baseline_amount, it.currency))) +
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
        (it.review ? '<span class="badge">이미 검토 등록됨 · ' + esc(REVIEW_STATUS_LABEL[it.review.status] || it.review.status) + '</span> <button type="button" class="btn" data-action="nav-scroll" data-tab="overview" data-target="CF-034">작업 큐에서 보기</button>' : '<button type="button" class="btn primary" data-action="anomaly-add-queue" data-source-key="' + esc(it.source_key) + '">검토 등록</button>') +
        '<a class="btn" href="inventory.html?cloud_account_id=' + encodeURIComponent(it.cloud_account_id) + '" title="인벤토리는 아직 계정 파라미터를 읽지 않습니다 — 열린 뒤 계정 필터를 직접 고르세요">인벤토리 열기(계정 필터는 직접 선택)</a></div>' +
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
        '<td><span class="badge">' + esc(REVIEW_STATUS_LABEL[r.status] || r.status) + "</span>" + (r.resolution ? "<br><span class=\"tiny muted\">" + esc(RESOLUTION_LABEL[r.resolution] || r.resolution) + "</span>" : "") + "</td>" +
        '<td><button type="button" class="btn no-print" data-action="open-review-dialog" data-item-id="' + esc(r.id) + '">검토</button> ' +
        '<button type="button" class="btn no-print" data-action="open-price-compare">비교하기</button></td></tr>';
    });
    var html = '<div class="table-wrap"><table><thead><tr><th scope="col">문제 / 근거</th><th scope="col">증가액</th><th scope="col">메모</th><th scope="col">검토 상태</th><th scope="col">행동</th></tr></thead><tbody>' +
      (rows.length ? rows.join("") : '<tr><td colspan="5" class="tiny muted">확인이 필요한 항목이 없습니다</td></tr>') + "</tbody></table></div>" +
      '<p class="note">미처리 항목 전부(기간 필터 없음) · 이 화면에서 리소스를 중지·삭제하지 않습니다.</p>';
    return { state: "CONNECTED_OK", html: html };
  }

  var REVIEW_STATUS_LABEL = { open: "열림", investigating: "조사 중", resolved: "종결" };
  var RESOLUTION_LABEL = { too_small: "소액(무시)", expected: "예상된 증가", unexpected: "예상 밖" };
  function reviewItemById(id) {
    var items = (ctx.reviewItems && ctx.reviewItems.ok && ctx.reviewItems.value.items) || [];
    return items.filter(function (r) { return r.id === id; })[0] || null;
  }

  function reviewDialogHtml(r) {
    var an = anomalyByKey(r.source_key);
    return (an ? anomalyDialogHtml(an, false) : '<p class="tiny muted">현재 급증 목록에 짝이 없어 금액을 표시하지 않습니다(0원이 아니라 미확인).</p>') +
      '<div class="filter-grid" style="margin-top:12px">' +
      '<label class="filter-box">상태<select id="review-status">' + ["open", "investigating", "resolved"].map(function (k) { return '<option value="' + k + '"' + (r.status === k ? " selected" : "") + ">" + REVIEW_STATUS_LABEL[k] + "</option>"; }).join("") + "</select></label>" +
      '<label class="filter-box">종결 사유<select id="review-resolution"><option value="">—</option>' + ["too_small", "expected", "unexpected"].map(function (k) { return '<option value="' + k + '"' + (r.resolution === k ? " selected" : "") + ">" + RESOLUTION_LABEL[k] + "</option>"; }).join("") + "</select></label>" +
      '<label class="filter-box" style="flex-basis:260px">메모<input id="review-note" type="text" maxlength="2000" value="' + esc(r.note || "") + '"></label>' +
      '<span class="button-row" style="margin-top:0"><button type="button" class="btn primary" data-action="review-patch" data-item-id="' + esc(r.id) + '">저장</button></span></div>' +
      '<p class="note">resolved로 닫힌 항목은 되돌릴 수 없습니다 — 재발은 새 항목입니다. 검토 상태는 금액·합계에 영향을 주지 않습니다.</p>' +
      '<div class="button-row"><a class="btn" href="inventory.html?cloud_account_id=' + encodeURIComponent(r.source_key.split(":")[0]) + '" title="인벤토리는 아직 계정 파라미터를 읽지 않습니다 — 열린 뒤 계정 필터를 직접 고르세요">인벤토리 열기(계정 필터는 직접 선택)</a></div>' +
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
      case "reload-budget-tab":
        reloadBudgetTab();
        return true;
      case "open-team-dialog":
        openDialog("팀 관리", teamDialogHtml());
        return true;
      case "team-delete-ask": {
        var box = document.getElementById("team-delete-confirm");
        if (box) { box.hidden = false; var cb = box.querySelector('[data-action="team-delete"]'); if (cb) cb.focus(); }
        return true;
      }
      case "team-delete-cancel": {
        var box2 = document.getElementById("team-delete-confirm");
        if (box2) box2.hidden = true;
        return true;
      }
      case "open-budget-basis-dialog":
        openDialog("예산 계산 근거", budgetBasisDialogHtml());
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
        btn.disabled = true; btn.textContent = "삭제 중…";
        window.MCPApi.request("/teams/" + encodeURIComponent(btn.getAttribute("data-team-id")), { method: "DELETE", headers: { "X-Action-Confirmed": "true" } })
          .then(function () { selectedTeamId = null; toast("팀을 삭제했습니다. 계정은 미배정으로 돌아갔고 비용 기록은 그대로입니다."); if (window.MCPModal) window.MCPModal.close("#cost-dialog"); return load(); })
          .catch(function (e) { btn.disabled = false; btn.textContent = "삭제 확인"; setFeedback("team-edit-feedback", serverErrorText(e), true); });
        return true;
      }
      case "budget-create": {
        if (budgetPending) return true;                       // 중복 제출 방지
        var b = budgetFormValues();
        var err = validateBudgetForm(b);
        if (err) { showBudgetError(err); return true; }
        var teamForSubmit = selectedTeamId;                   // 응답이 오기 전에 팀을 바꿔도 이 팀으로만 처리
        budgetPending = true; btn.disabled = true; btn.textContent = "저장 중…";
        window.MCPApi.request("/teams/" + encodeURIComponent(teamForSubmit) + "/budgets", { method: "POST", body: b })
          .then(function () { budgetPending = false; delete budgetDrafts[teamForSubmit]; toast("예산을 등록했습니다. 기존 예산은 이력으로 남습니다."); return reloadBudgetTab(); })
          .catch(function (e) { budgetPending = false; btn.disabled = false; btn.textContent = "저장"; showBudgetError(serverErrorText(e)); }); // 입력값은 그대로 남는다
        return true;
      }
      case "budget-reset":
        delete budgetDrafts[selectedTeamId];
        renderAll();
        return true;
      case "budget-edit-open": {
        var bo = budgetById(btn.getAttribute("data-budget-id"));
        if (bo) openDialog("예정 예산 수정", budgetEditDialogHtml(bo));
        return true;
      }
      case "budget-delete-open": {
        var bd = budgetById(btn.getAttribute("data-budget-id"));
        if (bd) openDialog("예산 삭제", budgetDeleteDialogHtml(bd));
        return true;
      }
      case "budget-patch": {
        if (btn.disabled) return true;
        var limitEl = document.getElementById("budget-edit-limit"), endEl = document.getElementById("budget-edit-end");
        var body = {};
        if (limitEl) {
          if (!(Number(limitEl.value) > 0)) { setFeedback("budget-edit-feedback", "한도는 0보다 큰 금액이어야 합니다.", true); return true; }
          body.limit_amount = String(limitEl.value).trim();
        }
        if (endEl && endEl.value) body.end_date = addDaysISO(endEl.value, 1); // 화면 포함 → API 제외 경계
        btn.disabled = true; btn.textContent = "저장 중…";
        window.MCPApi.request("/team-budgets/" + encodeURIComponent(btn.getAttribute("data-budget-id")), { method: "PATCH", body: body })
          .then(function () { toast("예정 예산을 수정했습니다."); if (window.MCPModal) window.MCPModal.close("#cost-dialog"); return reloadBudgetTab(); })
          .catch(function (e) { btn.disabled = false; btn.textContent = "저장"; setFeedback("budget-edit-feedback", serverErrorText(e), true); });
        return true;
      }
      case "budget-delete": {
        if (btn.disabled) return true;
        btn.disabled = true; btn.textContent = "삭제 중…";
        window.MCPApi.request("/team-budgets/" + encodeURIComponent(btn.getAttribute("data-budget-id")), { method: "DELETE", headers: { "X-Action-Confirmed": "true" } })
          .then(function () { toast("예산을 삭제했습니다."); if (window.MCPModal) window.MCPModal.close("#cost-dialog"); return reloadBudgetTab(); })
          .catch(function (e) { btn.disabled = false; btn.textContent = "삭제 확인"; setFeedback("budget-edit-feedback", serverErrorText(e), true); });
        return true;
      }
      case "open-threshold-dialog":
        openDialog("알림 조건", thresholdDialogHtml());
        return true;
      case "open-rule-dialog":
        if (lastAnomalyRule) openDialog("급증 탐지 규칙 상세", ruleDialogHtml(lastAnomalyRule));
        return true;
      case "open-anomaly-dialog": {
        var it = anomalyByKey(btn.getAttribute("data-source-key"));
        if (it) openDialog("급증 근거 · " + it.date, anomalyDialogHtml(it));
        return true;
      }
      case "anomaly-add-queue": {
        if (btn.disabled) return true;                        // 등록 중 중복 클릭 방지
        var key = btn.getAttribute("data-source-key");
        var known = anomalyByKey(key);
        btn.disabled = true; btn.textContent = "등록 중…";
        window.MCPApi.request("/cost-review-items", { method: "POST", body: { source_type: "cost_anomaly", source_key: key, note: null } })
          .then(function () {
            // POST는 멱등(있으면 그대로 200)이라 응답만으로 신규/중복을 구분할 수 없다 — 누르기 전 화면 상태로 안내한다.
            toast(known && known.review ? "이미 등록된 항목입니다 — ① 비용 개요 탭의 비용 작업 큐에서 확인하세요." : "검토 항목으로 등록했습니다. 상태 변경은 ① 비용 개요 탭의 비용 작업 큐에서 합니다.");
            if (window.MCPModal) window.MCPModal.close("#cost-dialog");
            return load();
          })
          .catch(function (e) { btn.disabled = false; btn.textContent = "검토 등록"; setFeedback("anomaly-feedback", serverErrorText(e), true); });
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
    if (document.activeElement && document.activeElement !== document.body) dialogOpener = document.activeElement;
    if (window.MCPModal) window.MCPModal.open("#cost-dialog");
    var closeBtn = document.querySelector("#cost-dialog [data-modal-close]");
    if (closeBtn) closeBtn.focus(); // 열리면 초점을 모달 안으로
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
    // 숨겨진 탭에서 그린 차트는 너비를 0으로 읽어 기본 720으로 그려진다 — 탭을 열 때 실제 너비로 다시 그린다.
    if (key === "analysis" && ctx.trend) renderBlock("CF-013");
  }
  function navScroll(tabKey, targetId) {
    switchTab(tabKey);
    var el = document.getElementById(targetId);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ── 비용 새로고침 ────────────────────────────────────────────────────────────────
  // 요청 접수(202)와 실제 수집 완료는 다르다. POST 응답의 run id로 GET /cost-ingestion-runs/{id}를
  // 폴링해 success / partial_success / failed / cancelled 로 끝난 것만 완료로 본다.
  // ingestion_running=false만 보고 성공이라 하지 않는다. 10초 간격, 최대 5분 — 넘기면 "완료 여부
  // 미확인"으로 두고 새로고침 버튼을 다시 연다. 페이지를 떠나면 타이머를 정리한다.
  var refreshPoll = { active: false, runIds: [], timer: null, startedAt: 0, text: "" };
  var REFRESH_POLL_INTERVAL_MS = 10000, REFRESH_POLL_MAX_MS = 5 * 60 * 1000;
  var TERMINAL_RUN = { success: "성공", partial_success: "부분 성공", failed: "실패", cancelled: "취소" };

  function stopRefreshPoll() {
    if (refreshPoll.timer) clearTimeout(refreshPoll.timer);
    refreshPoll.timer = null; refreshPoll.active = false; refreshPoll.runIds = []; refreshPoll.text = "";
  }
  window.addEventListener("pagehide", stopRefreshPoll);

  function pollRefreshRuns() {
    if (!refreshPoll.active) return;
    if (Date.now() - refreshPoll.startedAt > REFRESH_POLL_MAX_MS) {
      stopRefreshPoll();
      toast("수집 완료 여부를 5분 안에 확인하지 못했습니다. 잠시 뒤 화면을 다시 조회하세요.");
      return load();
    }
    Promise.all(refreshPoll.runIds.map(function (id) { return settle(window.MCPApi.request("/cost-ingestion-runs/" + encodeURIComponent(id))); }))
      .then(function (results) {
        if (!refreshPoll.active) return;
        var states = results.map(function (r) { return r.ok && r.value ? r.value.status : "unknown"; });
        var done = states.every(function (st) { return TERMINAL_RUN[st]; });
        if (!done) {
          refreshPoll.text = "수집 진행 중 (" + states.filter(function (st) { return TERMINAL_RUN[st]; }).length + "/" + states.length + " 완료) · 마지막 적용 조건 유지";
          var el = document.querySelector("#CF-001 .note[role=status]");
          if (el) el.textContent = refreshPoll.text;
          refreshPoll.timer = setTimeout(pollRefreshRuns, REFRESH_POLL_INTERVAL_MS);
          return;
        }
        var counts = {};
        states.forEach(function (st) { counts[st] = (counts[st] || 0) + 1; });
        stopRefreshPoll();
        toast("수집 종료: " + Object.keys(counts).map(function (k) { return TERMINAL_RUN[k] + " " + counts[k]; }).join(" · "));
        return load(); // 마지막으로 적용한 필터 그대로 재조회
      });
  }

  // ── 재수집 대상 확정(3단계 3-C) ─────────────────────────────────────────────────
  // 예전엔 전역 버튼과 계정 행 "다시 수집"이 같은 함수를 불러 전역 filters.accountIds를 썼다 — 계정을 안 고르면
  // null → 백엔드가 **사용자 전체 계정**을 수집했고, 행 버튼도 그 계정 하나가 아니었다. 이제 대상은 항상 내부 계정
  // id의 명시적 목록이다(null을 보내지 않는다). 전역 버튼 = 현재 조회 조건(CSP·계정 필터) 안의 실측 지원 계정,
  // 행 버튼 = 그 계정 하나. 문구·실행 전 안내·실제 body가 같은 목록을 가리킨다.
  function refreshTargets() {
    var caps = capAccounts.filter(function (c) { return c.cost_read !== null && c.status !== "UNSUPPORTED"; }); // 미지원 CSP는 대상이 아니다
    if (filters.accountIds.length) caps = caps.filter(function (c) { return filters.accountIds.indexOf(c.cloud_account_id) >= 0; });
    if (filters.providers.length) caps = caps.filter(function (c) { return filters.providers.indexOf(c.provider) >= 0; });
    return caps;
  }
  function refreshScopeText(targets) {
    if (!targets.length) return "대상 계정 없음(현재 조건에 실측 지원 계정이 없음)";
    var how = filters.accountIds.length ? "선택한 계정" : (filters.providers.length ? filters.providers.join("/").toUpperCase() + " 계정" : "실측 지원 계정 전체");
    return how + " " + targets.length + "개: " + targets.slice(0, 3).map(function (c) { return c.account_label || c.external_account_id; }).join(", ") + (targets.length > 3 ? " 외" : "");
  }
  var SKIP_REASON = { JOB_ALREADY_RUNNING: "이미 수집 진행 중", RATE_LIMITED: "1시간 제한", UNSUPPORTED: "미지원 CSP" };

  /** CSP에 실제 수집을 요청한다(과금 가능). 화면 데이터 재조회(load)와는 다른 일이다.
      accountIds: 내부 계정 id 목록(필수, 빈 배열이면 요청하지 않음). label: 토스트에 붙일 대상 설명. */
  function refreshCost(accountIds, label) {
    if (refreshPoll.active) { toast("이미 수집 완료를 확인하는 중입니다."); return Promise.resolve(); }
    var ids = (accountIds || []).map(String).filter(Boolean);
    if (!ids.length) { toast("재수집 대상 계정이 없습니다 — 현재 조건에 실측 지원 계정이 없거나 계정을 확인할 수 없습니다."); return Promise.resolve(); }
    var api = toApiRange(filters.periodStart, filters.periodEnd);
    return window.MCPApi.request("/cost-ingestion-runs", {
      method: "POST",
      body: { cloud_account_ids: ids, period_start: api.period_start, period_end: api.period_end }   // null(전체) 금지 — 항상 명시 목록
    }).then(function (data) {
      var items = (data && data.items) || [];
      var skipped = (data && data.skipped) || [];
      var skipText = skipped.length ? " · 건너뜀 " + skipped.length + "개(" + skipped.map(function (k) { return (SKIP_REASON[k.reason_code] || k.reason_code) + (k.next_allowed_at ? " → " + fmtWhen(k.next_allowed_at).text.replace(/ \(.*\)$/, "") + " 이후" : ""); }).join(", ") + ")" : "";
      if (!items.length) { toast("새로 시작한 수집이 없습니다" + skipText + "."); return load(); }
      refreshPoll.active = true; refreshPoll.runIds = items.map(function (it) { return it.id; });
      refreshPoll.startedAt = Date.now(); refreshPoll.text = "수집 요청 접수됨(202) — " + (label || items.length + "개 계정") + " · 아직 완료 아님, 완료를 확인하는 중…";
      toast("수집 요청을 접수했습니다(" + items.length + "개 계정). 접수는 완료가 아니며, 끝나면 자동으로 다시 조회합니다" + skipText + ".");
      renderAll(); // 버튼을 "수집 확인 중…"으로
      refreshPoll.timer = setTimeout(pollRefreshRuns, REFRESH_POLL_INTERVAL_MS);
    }).catch(function (e) {
      var code = e && e.code;
      if (code === "RATE_LIMITED") toast("요청한 계정이 모두 1시간 제한에 걸려 있습니다. 제한 시각 이후 다시 시도하세요.");
      else if (code === "JOB_ALREADY_RUNNING") toast("요청한 계정이 모두 이미 수집 중입니다.");
      else toast(window.MCErr ? window.MCErr.headline(e) : "재수집 요청에 실패했습니다.");
    });
  }

  // ── CF-016 서비스 상세 모달(3단계 3-A) ────────────────────────────────────────────
  // 예전엔 CF-016이 받은 상위 6개를 "전체 명세"라 부르며 다시 그렸다. 서버 상한이 top_n=20이라 "전체"는 줄 수
  // 없으므로 top_n=20으로 **따로 재조회**하고 이름도 실제 범위로 부른다(04 §5-2). rest(상위 20개 밖 합)와
  // unallocated(service NULL 행 = 미분류)는 다른 것이라 한 줄로 합치지 않는다. 기타 안의 개별 서비스는 현재
  // API로 볼 수 없다(응답에 목록이 없음) — 이 모달은 그 사실을 말할 뿐 새 API를 만들지 않는다.
  var SERVICE_DETAIL_TOP_N = 20;
  var serviceDetailSeq = 0;
  function dialogIsOpen() { var m = document.getElementById("cost-dialog"); return !!m && !m.classList.contains("hidden"); }
  function serviceDetailTitle() { return "서비스별 상세 — 상위 " + SERVICE_DETAIL_TOP_N + "개 · 기타 · 미분류"; }
  function openServiceDetailDialog() {
    var query = apiQuery("dimension=service&top_n=" + SERVICE_DETAIL_TOP_N);   // CF-016과 같은 기간·CSP·계정·통화, 차원 service
    var seq = ++serviceDetailSeq;
    var disp = toDisplayRange(toApiRange(filters.periodStart, filters.periodEnd).period_start, toApiRange(filters.periodStart, filters.periodEnd).period_end);
    openDialog(serviceDetailTitle(), '<p class="note" role="status">조회 중… (' + esc(disp.start) + " ~ " + esc(disp.end) + ")</p>");
    settle(window.MCPApi.request("/costs/breakdown" + query)).then(function (r) {
      // 늦게 온 응답 폐기: 모달이 닫혔거나, 다시 열렸거나(순번), 그 사이 필터가 바뀌어 조건이 달라졌으면 그린다고 해도 틀린 표다
      if (seq !== serviceDetailSeq || !dialogIsOpen()) return;
      if (query !== apiQuery("dimension=service&top_n=" + SERVICE_DETAIL_TOP_N)) return;
      var bodyEl = document.getElementById("dialog-body");
      if (!bodyEl) return;
      bodyEl.innerHTML = serviceDetailHtml(r, disp);
    });
  }
  function serviceDetailHtml(r, disp) {
    var retry = '<div class="button-row no-print"><button type="button" class="btn" data-action="open-full-breakdown-dialog">다시 조회</button></div>';
    if (!r.ok) return fetchFailedHtml(r, "서비스별 상세") + retry;
    var d = r.value;
    var items = d.items || [];
    var rest = d.rest || { amount: "0", count: 0 };
    var unalloc = d.unallocated || { amount: "0" };
    var cur = d.currency;
    if (!cur || (!items.length && Number(rest.amount) === 0 && Number(unalloc.amount) === 0)) {
      return '<p class="note">이 조건(' + esc(disp.start) + " ~ " + esc(disp.end) + ")에 실측 항목이 없습니다. 0원이 아니라 집계할 행이 없는 것입니다.</p>" + retry;
    }
    // 대사: 상위 항목 합 + 기타 + 미분류 = 전체(total). 화면에서 다시 더해 보여준다(QA-09).
    var itemsSum = items.reduce(function (acc, it) { return F.addDecimalString(acc, it.amount); }, "0");
    var recomputed = F.addDecimalString(F.addDecimalString(itemsSum, rest.amount || "0"), unalloc.amount || "0");
    var matches = Number(recomputed).toFixed(6) === Number(d.total).toFixed(6);
    var rowHtml = function (label, amount, pct, cls, note) {
      return '<tr class="' + (cls || "") + '"><td>' + label + (note ? '<br><span class="tiny muted">' + note + "</span>" : "") + '</td><td class="num">' + smallMoneyHtml(amount, cur, false) + '</td><td class="num">' + (pct == null ? "—" : esc(pct) + "%") + "</td></tr>";
    };
    var rows = items.map(function (it) { return rowHtml(esc(it.label), it.amount, it.share_pct); }).join("");
    if (Number(rest.amount) !== 0 || rest.count)
      rows += rowHtml("기타 (" + (rest.count || 0) + "종)", rest.amount, rest.share_pct, "muted", "상위 " + SERVICE_DETAIL_TOP_N + "개 밖 서비스의 합 — 개별 서비스는 현재 API가 주지 않습니다");
    if (Number(unalloc.amount) !== 0)
      rows += rowHtml("미분류(서비스 미지정)", unalloc.amount, unalloc.share_pct, "muted", "서비스 이름이 없는 실측 행(계정 단위 요금 등) — '기타'와 다릅니다");
    return '<p class="small">' + esc(disp.start) + " ~ " + esc(disp.end) + " · 통화 <strong>" + esc(cur) + "</strong> · 사용료(usage) 고정 집계 — 요금 분류 필터 미적용 · CSP·계정 필터 적용 · 상위 " +
        SERVICE_DETAIL_TOP_N + "개까지(서버 상한)" + (excludedCurrencyText(d)) + "</p>" +
      '<div class="table-wrap"><table class="changes-table"><thead><tr><th scope="col">서비스</th><th scope="col" class="num">금액</th><th scope="col" class="num">비중</th></tr></thead><tbody>' + rows + "</tbody></table></div>" +
      '<p class="note">상위 ' + items.length + "개 합 " + esc(F.money(itemsSum, cur)) + " + 기타 " + esc(F.money(rest.amount || "0", cur)) + " + 미분류 " + esc(F.money(unalloc.amount || "0", cur)) +
        " = " + esc(F.money(recomputed, cur)) + (matches ? " · 전체 합계 " + esc(F.money(d.total, cur)) + "와 일치" : ' · <span style="color:var(--cost-warn)">전체 합계 ' + esc(F.money(d.total, cur)) + "와 다름 — 서버 응답 확인 필요</span>") +
        " · 표시 단위 미만 소액은 반올림된 값이며 정확한 값은 금액에 마우스를 올리면 보입니다</p>";
  }
  function excludedCurrencyText(d) {
    var ex = (d.currency_selection && d.currency_selection.excluded) || [];
    return ex.length ? " · 통화가 다른 계정 " + ex.map(function (e) { return e.currency ? esc(e.currency) + " " + (e.account_count || "") + "개" : ""; }).join(", ") + " 제외(합치지 않음)" : "";
  }

  // ── CF-007 Owner 미지정 대상 목록(3단계 3-B) ─────────────────────────────────────
  // 예전엔 "미지정 대상 보기"가 검토 큐(CF-034)로 스크롤했다 — 큐는 급증 검토 항목이라 미지정 리소스가 없다.
  // 카드가 집계한 것과 **같은 함수**(unassignedResources)로 같은 목록을 보여줘 개수가 일치한다. /resources는
  // 페이지네이션 없이 전부 내려온다(routers/resources.py list_resources) — 일부만 받은 것이 아니다.
  function unassignedDialogHtml() {
    var list = unassignedResources();
    if (!ctx.resources || !ctx.resources.ok) return fetchFailedHtml(ctx.resources, "미지정 리소스");
    if (!list.length) return '<p class="note">' + esc(OWNER_TAG_KEY) + " 태그가 비어 있는 리소스가 없습니다.</p>";
    var priced = list.filter(function (r) { return r.cost_summary && r.cost_summary.estimated_monthly_cost != null; });
    var rows = list.slice().sort(function (a, b) {
      var pa = a.cost_summary && a.cost_summary.estimated_monthly_cost, pb = b.cost_summary && b.cost_summary.estimated_monthly_cost;
      if ((pa == null) !== (pb == null)) return pa == null ? 1 : -1;          // 정가 있는 것 먼저
      return Number(pb || 0) - Number(pa || 0);
    }).map(function (r) {
      var cs = r.cost_summary || {};
      var ca = r.cloud_account || {};
      var acct = ca.account_label || ca.external_account_id || accountLabelOf(ca.id);   // 내부 id가 아니라 사람이 아는 계정 이름/외부 식별자
      var price = cs.estimated_monthly_cost != null ? esc(F.money(cs.estimated_monthly_cost, cs.currency || "USD")) + " /월" : '—<br><span class="tiny muted">정가표에 없음 — 카드 금액에 미포함(0원 아님)</span>';
      return "<tr><td>" + providerCellHtml(r.cloud_account ? r.cloud_account.provider : r.provider) + " " + esc(r.name || r.external_resource_id) +
        '<br><span class="tiny muted">' + esc((r.service && r.service.display_name) || r.original_resource_type || "") + (r.region ? " · " + esc(r.region) : "") + "</span></td>" +
        "<td>" + esc(acct) + "</td>" +
        '<td class="num">' + price + "</td>" +
        "<td>" + resourceStatusHtml(r.status) + "</td>" +
        '<td class="actions"><button type="button" class="btn no-print" data-action="open-resource-detail" data-resource-id="' + esc(r.id) + '">인벤토리에서 보기</button></td></tr>';
    }).join("");
    return '<p class="small">' + esc(OWNER_TAG_KEY) + " 태그가 없는(또는 비어 있는) 리소스 <strong>" + list.length + "개</strong> · 그중 정가 추정이 있는 " + priced.length + "개만 카드 금액에 포함 · 현재 구성 × 730h 정가 기준(실측 배분 아님) · 조회 기간과 무관 · CSP·계정 필터 적용</p>" +
      '<div class="table-wrap"><table class="changes-table"><thead><tr><th scope="col">리소스</th><th scope="col">계정</th><th scope="col" class="num">정가 추정</th><th scope="col">상태</th><th scope="col"><span class="sr-only">행동</span></th></tr></thead><tbody>' + rows + "</tbody></table></div>" +
      '<p class="note">태그 기준 검토 후보입니다 — 태그가 없다는 것만으로 낭비·방치·특정 부서 소유를 뜻하지 않습니다. 담당자 지정은 각 CSP 콘솔에서 태그(' + esc(OWNER_TAG_KEY) + ")를 붙이면 다음 동기화에 반영됩니다. 검토 큐에는 자동 등록되지 않습니다.</p>";
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
        case "refresh-cost": {
          if (btn.disabled) break;
          var targets = refreshTargets();
          refreshCost(targets.map(function (c) { return c.cloud_account_id; }), refreshScopeText(targets));
          break;
        }
        case "refresh-account": {
          if (btn.disabled) break;
          var one = btn.getAttribute("data-account-id");
          refreshCost([one], "계정 " + accountLabelOf(one));
          break;
        }
        case "trend-mode": {
          var mode = btn.getAttribute("data-mode");
          if (mode === filters.granularity) break;
          filters.granularity = mode;
          syncUrl();
          renderBlock("CF-013");   // 버튼 상태를 먼저 바꾸고(aria-pressed) 응답이 오면 다시 그린다
          reloadTrend();
          break;
        }
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
          filters.accountIds = [btn.getAttribute("data-account-id")];   // 기간·통화·요금 분류·CSP는 그대로 — 계정 조건만 바뀐다
          renderFilters();
          syncUrl();
          load();
          break;
        case "account-filter-clear":
          filters.accountIds = [];
          renderFilters();
          syncUrl();
          load();
          break;
        case "open-resource-detail":
          window.location.href = "inventory.html?resource_id=" + encodeURIComponent(btn.getAttribute("data-resource-id"));
          break;
        case "open-basis-dialog":
          openDialog("집계 기준 · 계산 방법", '<dl class="meta-grid" style="grid-template-columns:1fr">' +
            metaItem("조회 기간 누적 비용(실측)", "조회 기간 안의 실제 사용 비용을 계정별로 더한 값. 사용료(usage)만 — 크레딧·환불·세금은 요금 분류를 바꿔야 보입니다. CSP가 아직 확정하지 않은 최근 며칠은 '잠정치'로 표시됩니다. 청구서 금액과 다를 수 있습니다.") +
            metaItem("월말 예상 비용(전망)", "이번 달을 조회할 때만 계산. 이번 달 1일부터 어제까지의 실측을 남은 일수 비율로 늘린 값. 저장하지 않으며 조회할 때마다 다시 계산합니다.") +
            metaItem("현재 구성 예상 월 비용(정가)", "지금 인벤토리에 있는 리소스의 사양을 정가표에 넣어 730시간(한 달 상시 가동) 기준으로 더한 값. 할인·세금·스토리지/네트워크 등 부속 요금은 빠져 있고, 정가표에 없는 리소스(사용량 기반 등)는 제외됩니다. 조회 기간·통화·요금 분류 필터와 무관합니다.") +
            metaItem("세 값의 관계", "서로 더하거나 비교하는 값이 아닙니다 — 실측은 '지난 일', 전망은 '이번 달 끝의 추정', 정가는 '지금 구성이 한 달 내내 켜져 있으면'입니다.") +
            metaItem("통화", "통화가 다른 금액은 합치지 않고 줄을 나눠 보여줍니다. '전체 통화'는 합산이 아니라 통화별 표시입니다.") +
            metaItem("날짜 경계", "날짜는 CSP 청구 기준(UTC)으로 집계됩니다. 화면의 시각 표시는 브라우저 시간대로 바꿔 보여줄 뿐 집계를 바꾸지 않습니다.") + "</dl>");
          break;
        case "open-forecast-dialog":
          openDialog("예측 방법", '<p>방법: 이번 달 1일부터 어제(UTC)까지의 실측 비용 ÷ 경과일 × 이달 총일수(<code>mtd_prorated</code>).</p>' +
            '<p class="note">대상 계정(실측 지원·현재 조건 안) 전부가 그 기간을 빠짐없이 수집 확인했을 때만 계산합니다. 하루라도 빠지면 0원으로 채우지 않고 전망을 내지 않습니다. 이번 달 1일부터 오늘까지를 조회할 때만 제공하며, 저장하지 않는 값입니다.</p>');
          break;
        case "open-setup-dialog": {
          var st = btn.getAttribute("data-status");
          var lead = st === "PERMISSION_DENIED" ? "이 계정의 자격 증명에 비용 조회 권한이 없습니다. 마이페이지에서 권한을 부여한 뒤 재검증하세요."
                   : "이 계정의 비용 조회를 위한 설정이 필요합니다. 마이페이지에서 자격 증명을 확인하세요.";
          openDialog("설정 안내", "<p>" + esc(lead) + "</p>" + (btn.getAttribute("data-hint") ? '<p class="note">' + esc(btn.getAttribute("data-hint")) + "</p>" : "") +
            '<div class="button-row"><a class="btn" href="mypage.html">마이페이지로</a></div>');
          break;
        }
        case "open-account-detail": {
          var cap = capAccounts.filter(function (a) { return a.cloud_account_id === btn.getAttribute("data-account-id"); })[0];
          if (cap) openDialog("계정 상세 · " + (cap.account_label || cap.external_account_id), accountDetailHtml(cap));
          break;
        }
        case "open-warnings-dialog":
          openDialog("데이터 누락·수집 상태 전체", warningsDialogHtml());
          break;
        case "open-unassigned-dialog":
          openDialog(OWNER_TAG_KEY + " 미지정 리소스", unassignedDialogHtml());
          break;
        case "open-permission-dialog":
          openDialog("필요 권한", "<p>이 계정의 비용 조회 권한이 부족합니다. 마이페이지에서 자격 증명의 권한을 확인하세요.</p>");
          break;
        case "open-full-breakdown-dialog":
          openServiceDetailDialog();
          break;
        default: break;
      }
    });

    // 조회 조건 입력이 바뀌면 "수정 중 — 적용 전" 표시(적용 버튼을 눌러야 조회된다)
    document.addEventListener("input", function (e) {
      if (e.target && e.target.closest && e.target.closest("#filter-panel")) updateFilterDirty();
    });
    document.addEventListener("change", function (e) {
      if (e.target && e.target.closest && e.target.closest("#filter-panel")) updateFilterDirty();
    });
    document.addEventListener("click", function (e) {
      if (e.target && e.target.closest && e.target.closest("#filter-panel [data-dd-multi-item], #filter-panel [data-dd-multi-all]")) setTimeout(updateFilterDirty, 0);
    });

    // ③ 탭 팀 선택 — 이 탭에만 적용(①② 탭 재조회 없음)
    document.addEventListener("change", function (e) {
      if (e.target && e.target.id === "team-select") {
        selectedTeamId = e.target.value || null;
        syncUrl();
        ctx.budgetStatus = null; ctx.budgets = null; teamLoading = true;
        renderAll();                       // "조회 중…"을 먼저 보여준다(이전 팀 값이 새 팀 값처럼 남지 않게)
        loadTeamScoped().then(renderAll);
      } else if (e.target && e.target.name === "budget-period-type") {
        var wrap = document.getElementById("budget-end-wrap");
        if (wrap) wrap.hidden = e.target.value !== "custom";
        captureBudgetDraft();
      } else if (e.target && e.target.closest && e.target.closest("#CF-025 details.budget-form")) {
        captureBudgetDraft();
      }
    });
    document.addEventListener("input", function (e) {
      if (e.target && e.target.closest && e.target.closest("#CF-025 details.budget-form")) captureBudgetDraft();
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
    tabs.forEach(function (btn, i) {
      btn.addEventListener("click", function () { switchTab(btn.getAttribute("data-tab")); });
      // WAI-ARIA 탭 관례: ←/→로 이동, Home/End로 양끝. 선택된 탭만 tabindex=0(roving tabindex).
      btn.addEventListener("keydown", function (e) {
        var next = null;
        if (e.key === "ArrowRight") next = tabs[(i + 1) % tabs.length];
        else if (e.key === "ArrowLeft") next = tabs[(i - 1 + tabs.length) % tabs.length];
        else if (e.key === "Home") next = tabs[0];
        else if (e.key === "End") next = tabs[tabs.length - 1];
        if (!next) return;
        e.preventDefault();
        switchTab(next.getAttribute("data-tab"));
        next.focus();
      });
    });
  }

  // 모달이 닫히면 열기 전에 초점이 있던 요소로 돌아간다(modal.js는 열고 닫기만 한다).
  var dialogOpener = null;
  function initDialogFocusReturn() {
    var dlg = document.getElementById("cost-dialog");
    if (!dlg || !window.MutationObserver) return;
    new MutationObserver(function () {
      if (dlg.classList.contains("hidden") && dialogOpener) {
        var el = dialogOpener; dialogOpener = null;
        if (document.contains(el) && typeof el.focus === "function") el.focus();
      }
    }).observe(dlg, { attributes: true, attributeFilter: ["class"] });
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
    initDialogFocusReturn();
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
