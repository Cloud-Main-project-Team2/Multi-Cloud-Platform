/* 진행 중인 프로비저닝 job을 페이지 이동(풀 리로드)에도 유지한다.
 *
 * 프론트는 빌드 과정 없는 순수 HTML/Vanilla JS 멀티페이지라 페이지 이동 = 항상 풀 리로드다.
 * 그래서 "작은 창"은 그 페이지의 JS 메모리에만 존재해 이동하면 사라진다. 이를 막기 위해
 *   - provisioning.js가 job 생성/종결 시 localStorage("mcp_active_provisioning")에 기록하고,
 *   - 로그인 후 공통 셸(이 스크립트를 로드하는 모든 페이지)이 페이지 로드 시 이를 읽어
 *     우측 하단 작은 창을 복원하고 GET /provisioning/jobs/{id} 폴링을 재개한다.
 *   - 모든 job이 success/failed/cancelled로 끝나면 localStorage에서 제거하고 창을 닫는다.
 *
 * - 메인홈(main.html, 로그인 전)에는 이 스크립트를 로드하지 않는다(방어적으로 여기서도 한 번 더 확인).
 * - 실 프로비저닝 화면(#prov-progress-list 존재)에서는 provisioning.js가 라이브 진행 UI를 소유하므로
 *   이 스크립트는 저장소 API만 노출하고 작은 창은 직접 그리지 않는다.
 * - intro-*.html은 api.js(MCPApi)를 로드하지 않으므로, 폴링은 MCPApi가 있으면 그것을 쓰고
 *   없으면 최소 fetch(세션 토큰 직접 첨부)로 대체한다.
 */
window.MCProvTracker = (function () {
  "use strict";

  var KEY = "mcp_active_provisioning";
  var SESSION_KEY = "mcp_session";
  var TERMINAL = { success: 1, failed: 1, cancelled: 1 };
  var POLL_MS = 2500;
  var MAX_AGE_MS = 30 * 60 * 1000; // 오래된 잔여 job 정리용 안전장치
  var pollTimer = null;

  // ── 저장소 ────────────────────────────────────────────────────────────────
  function read() {
    try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { return null; }
  }
  function write(store) {
    try {
      if (store && store.jobs && store.jobs.length) localStorage.setItem(KEY, JSON.stringify(store));
      else localStorage.removeItem(KEY);
    } catch (e) {}
  }
  function activeJobs(store) {
    if (!store || !store.jobs) return [];
    return store.jobs.filter(function (j) { return !TERMINAL[j.status]; });
  }

  // provisioning.js가 호출: 진행 중 real job 목록을 통째로 저장한다(멱등).
  function save(jobs) {
    var existing = read();
    write({ jobs: jobs.slice(), startedAt: (existing && existing.startedAt) || Date.now() });
  }
  function updateStatus(id, status) {
    var store = read();
    if (!store) return;
    store.jobs.forEach(function (j) { if (j.id === id) j.status = status; });
    write(store);
  }
  function clear() { write(null); }

  // ── 폴링용 최소 fetch (MCPApi가 없는 intro 페이지에서도 동작) ─────────────────
  function fetchJob(id) {
    if (window.MCPApi && MCPApi.request) return MCPApi.request("/provisioning/jobs/" + id);
    var base = (window.MCPApi && MCPApi.API_BASE) || "http://localhost:8000/api/v1";
    var token = null;
    try { var s = JSON.parse(localStorage.getItem(SESSION_KEY) || "null"); token = s && s.accessToken; } catch (e) {}
    var headers = {};
    if (token) headers["Authorization"] = "Bearer " + token;
    return fetch(base + "/provisioning/jobs/" + id, { headers: headers }).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (json) {
        if (!res.ok) throw new Error("poll failed");
        return json ? json.data : null;
      });
    });
  }

  // ── 작은 창(우측 하단 고정) ───────────────────────────────────────────────
  function ensureWidget() {
    var w = document.getElementById("mcp-prov-tracker");
    if (w) return w;
    w = document.createElement("div");
    w.id = "mcp-prov-tracker";
    w.className = "fixed bottom-4 right-4 z-40 w-64 cursor-pointer rounded-xl border border-border bg-surface p-4 shadow-lg";
    w.setAttribute("role", "button");
    w.setAttribute("aria-label", "프로비저닝 진행 상황 — 프로비저닝 페이지로 이동");
    w.addEventListener("click", function () {
      if (location.pathname.indexOf("provisioning.html") < 0) location.href = "provisioning.html";
    });
    document.body.appendChild(w);
    return w;
  }
  function removeWidget() {
    var w = document.getElementById("mcp-prov-tracker");
    if (w && w.parentNode) w.parentNode.removeChild(w);
  }
  function renderWidget(store) {
    var jobs = (store && store.jobs) || [];
    if (!jobs.length) return;
    var done = 0, failed = 0;
    jobs.forEach(function (j) {
      if (j.status === "success") done++;
      else if (j.status === "failed" || j.status === "cancelled") failed++;
    });
    var running = jobs.length - done - failed;
    var w = ensureWidget();
    w.innerHTML =
      '<div class="text-sm font-semibold">프로비저닝 ' + (done + failed) + "/" + jobs.length +
        (running ? " · 진행 중" : " · 완료") + "</div>" +
      '<div class="mt-2 flex gap-3 text-xs text-muted-foreground">' +
      '<span class="flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-primary"></span>완료 ' + done + "</span>" +
      '<span class="flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-sky"></span>진행 ' + running + "</span>" +
      '<span class="flex items-center gap-1"><span class="h-2 w-2 rounded-full" style="background:#c0392b"></span>실패 ' + failed + "</span></div>" +
      '<div class="mt-2 text-xs text-muted-foreground">클릭하면 상세로 이동</div>';
  }

  // ── 폴링 루프 ──────────────────────────────────────────────────────────────
  function tick() {
    var store = read();
    if (!store) { removeWidget(); stopPolling(); return; }
    if (store.startedAt && Date.now() - store.startedAt > MAX_AGE_MS) {
      clear(); removeWidget(); stopPolling(); return;
    }
    renderWidget(store);
    var active = activeJobs(store);
    if (!active.length) {
      // 모두 종결 — 최종 상태를 잠시 보여준 뒤 정리한다.
      stopPolling();
      setTimeout(function () { clear(); removeWidget(); }, 4000);
      return;
    }
    Promise.all(active.map(function (j) {
      return fetchJob(j.id)
        .then(function (job) { if (job && job.status) updateStatus(j.id, job.status); })
        .catch(function () { /* 네트워크 오류는 무시하고 다음 폴링에서 재시도 */ });
    })).then(function () {
      var s2 = read();
      if (s2) renderWidget(s2);
    });
  }
  function startPolling() {
    if (pollTimer) return;
    tick();
    pollTimer = setInterval(tick, POLL_MS);
  }
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  // ── 페이지 로드 시 자동 복원 ───────────────────────────────────────────────
  function autoInit() {
    // 메인홈(로그인 전)에는 로드되지 않지만 방어적으로 한 번 더 확인.
    if (/(^|\/)main\.html$/.test(location.pathname)) return;
    // 실 프로비저닝 화면: provisioning.js가 라이브 진행 UI를 소유한다 — 저장소만 두고 창은 그리지 않는다.
    if (document.getElementById("prov-progress-list")) return;
    if (activeJobs(read()).length) startPolling();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", autoInit);
  else autoInit();

  return {
    save: save,
    updateStatus: updateStatus,
    clear: clear,
    read: read,
    activeJobs: function () { return activeJobs(read()); },
  };
})();
