// 공통 하네스: cost.html을 jsdom으로 띄우고 fetch를 가로채 상태별 응답을 주입한다(실 API·실 브라우저 아님).
// 화면 회귀 고정용 — 브라우저 확인을 대신하지 않는다. 사용법은 같은 폴더 README.md 참고.
const { JSDOM, VirtualConsole } = require("jsdom"); const fs = require("fs"); const path = require("path");
const ROOT = path.resolve(__dirname, "../..");
const today = new Date(); const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
const monthStart = iso(new Date(today.getFullYear(), today.getMonth(), 1));
const todayIso = iso(today); const tomorrow = iso(new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1));
const NOW = new Date().toISOString();

function base(opts) {
  const o = Object.assign({ status: "CONNECTED_OK", actual: "3.330000", rows: true, est: false, warnings: [], prev: "1.000000", comparable: true, extra: [], summaryAccounts: null, summaryFail: false, delay: 0,
    breakdown: null, changesLists: null, resources: [], trendPoints: null, teams: null, anomalies: null, teamsFail: false, budgetStatus: null, budgets: null, budgetsFail: false, notifications: null, notificationsFail: false, teamDelayMs: null,
    forecastStatus: null, coverage: null, filterExcluded: null, comparability: null, granularityEcho: true, trendDelayMs: null,
    breakdown20: null, breakdownFail20: false, breakdownDelayMs: null, ingestion: null }, opts);
  const acct = (id, provider, status, label) => ({ cloud_account_id: id, provider, external_account_id: "x" + id, account_label: label, team_id: null, status, as_of: status === "PENDING" || status === "UNSUPPORTED" ? null : NOW, ingestion_running: false, cost_read: status === "UNSUPPORTED" ? null : true, capability_source: status === "UNSUPPORTED" ? "not_implemented" : "probed", currency: status === "UNSUPPORTED" ? null : "USD", setup_hint: status === "UNSUPPORTED" ? provider.toUpperCase() + " 비용 수집은 아직 구현되지 않았습니다." : null, last_error_code: status === "COLLECT_FAILED" ? "PROVIDER_API_ERROR" : null });
  const caps = [acct("1", "aws", o.status, "prod-aws")].concat(o.extra.map((e, i) => acct(String(10 + i), e.provider, e.status, e.label)));
  const days = Math.max((new Date(todayIso) - new Date(monthStart)) / 86400000, 0);
  const covOf = (c) => {
    if (c.status === "UNSUPPORTED") return null;
    if (o.coverage && o.coverage[c.cloud_account_id] !== undefined) return o.coverage[c.cloud_account_id];
    const missing = o.warnings.length && o.warnings[0].missing_days ? o.warnings[0].missing_days : [];
    // A-2: basis/analysis_ready는 서버가 채우는 값이다. 기본은 기존 AWS 동작(complete_range).
    return { start: monthStart, end: todayIso, days, covered: days - missing.length, missing_count: missing.length, missing_days: missing.slice(0, 31), truncated: missing.length > 31, pending_days: 1,
             basis: "complete_range", analysis_ready: missing.length === 0 };
  };
  const sAcc = (c, actual) => ({ cloud_account_id: c.cloud_account_id, provider: c.provider, account_label: c.account_label, team_id: null, status: c.status, as_of: c.as_of, ingestion_running: false, currency: actual != null ? "USD" : c.currency, actual, list_price_estimate: "10.000000", resource_count: 1, resources_synced_at: NOW, is_estimated: actual != null,
    filter_excluded: (o.filterExcluded && o.filterExcluded[c.cloud_account_id]) || null, coverage: covOf(c) });
  const accounts = o.summaryAccounts !== null ? o.summaryAccounts : [sAcc(caps[0], o.rows ? o.actual : null)].concat(o.extra.map((e, i) => sAcc(caps[i + 1], e.actual == null ? null : e.actual)));
  const included = accounts.filter((a) => a.actual != null); const total = included.reduce((s, a) => s + Number(a.actual), 0);
  const summary = { period: { start: monthStart, end: tomorrow, display: "" }, as_of: caps.some((c) => c.as_of) ? NOW : null, staleness_threshold_hours: 36,
    kpis: { mtd_actual: included.length ? [{ cost_kind: "actual", currency: "USD", amount: total.toFixed(6), is_estimated: o.est, basis: "usage_before_credits" }] : [], mtd_net: [], list_price_monthly: [{ cost_kind: "list_price_estimate", currency: "USD", amount: "10.000000", missing_count: 0, assumptions: [] }],
      forecast_month_end: o.forecastStatus && o.forecastStatus.state === "computed" ? [{ cost_kind: "actual", currency: "USD", amount: (total * 1.5).toFixed(6), method: "mtd_prorated", based_through: o.forecastStatus.based_through }] : [],
      forecast_status: o.forecastStatus || { state: o.warnings.length ? "insufficient_coverage" : "computed", based_through: todayIso, required_accounts: accounts.filter((a) => a.coverage).length, incomplete_accounts: o.warnings.length ? [{ cloud_account_id: "1", missing_count: o.warnings[0].missing_days.length }] : [] } },
    accounts, excluded: (() => { const rc = {}; accounts.forEach((a) => { const k = a.filter_excluded === "currency" ? "CURRENCY_FILTERED" : (a.status === "UNSUPPORTED" ? "UNSUPPORTED" : (a.actual == null && a.coverage && a.coverage.covered === 0 ? (["CONNECTED_OK", "CONNECTED_EMPTY", "CONNECTED_PARTIAL"].includes(a.status) ? "PERIOD_NOT_COVERED" : a.status) : null)); if (k) rc[k] = (rc[k] || 0) + 1; }); return { accounts: Object.values(rc).reduce((x, y) => x + y, 0), reason_counts: rc }; })(),
    warnings: o.warnings.map((w) => w.code === "PARTIAL_PERIOD" ? Object.assign({ missing_count: w.missing_days.length, accounts: [{ cloud_account_id: "1", missing_count: w.missing_days.length }] }, w) : w) };
  const reasons = o.comparability ? o.comparability.reasons : (o.comparable ? [] : ["LENGTH_MISMATCH"]);
  const cmpOk = reasons.length === 0;
  const changes = Object.assign({ current: { start: monthStart, end: tomorrow, days: 20 }, previous: { start: "2026-08-12", end: monthStart, days: reasons.includes("LENGTH_MISMATCH") ? 31 : 20 }, comparable: cmpOk,
    comparability: Object.assign({ same_length: !reasons.includes("LENGTH_MISMATCH"), completed_period: !reasons.includes("INCOMPLETE_PERIOD"), current_covered: !reasons.includes("CURRENT_COVERAGE"), previous_covered: !reasons.includes("PREVIOUS_COVERAGE"), currency: included.length ? "USD" : null, charge_category: "usage", reasons, accounts: [] }, o.comparability || {}),
    charge_category: "usage", currency: included.length ? "USD" : null,
    totals: { current: included.length ? total.toFixed(6) : null, previous: included.length ? o.prev : null, delta: cmpOk && o.prev != null ? (total - Number(o.prev)).toFixed(6) : null, delta_pct: cmpOk && o.prev != null && Number(o.prev) > 0 ? (((total - Number(o.prev)) / Number(o.prev)) * 100).toFixed(1) : null }, increases: [], decreases: [], new_items: [] }, o.changesLists || {});
  const routes = {
    "/costs/capabilities": { staleness_threshold_hours: 36, items: caps, total: caps.length },
    "/costs/summary": o.summaryFail ? { __status: 500 } : summary, "/costs/changes": changes,
    "/costs/collection-status": { staleness_threshold_hours: 36, items: caps.map((c) => ({ cloud_account_id: c.cloud_account_id, provider: c.provider, status: c.status, as_of: c.as_of, ingestion_running: false, last_success_at: c.as_of, last_attempt_at: c.as_of, last_error_code: c.last_error_code, next_manual_allowed_at: null, covered_through: null, missing_days: [], missing_count: 0, coverage: covOf(c) })), total: caps.length },
    "/costs/trend": (search) => { const g = (/granularity=(\w+)/.exec(search || "") || [])[1] || "daily"; const pts = o.trendPoints ? o.trendPoints : []; const mpts = [{ period_start: monthStart, period_end: "", amount: "9.000000", is_estimated: false }];
      return { granularity: o.granularityEcho ? g : "daily", group_by: "provider", currency: "USD", currency_selection: { reason: "single", excluded: [] }, staleness_threshold_hours: 36, series: (g === "monthly" ? mpts : pts).length ? [{ key: "aws", label: "AWS", points: g === "monthly" ? mpts : pts }] : [], missing_days: o.warnings.length ? o.warnings[0].missing_days : [] }; },
    "/costs/breakdown": (search) => { const n = (/top_n=(\d+)/.exec(search || "") || [])[1]; if (n === "20") { if (o.breakdownFail20) return { __status: 500 }; if (o.breakdown20) return o.breakdown20; }
      return o.breakdown || { dimension: "service", currency: "USD", currency_selection: { reason: "single", excluded: [] }, cost_kind: "actual", total: "0", items: [], rest: { amount: "0", count: 0 }, unallocated: { amount: "0", reason: null } }; },
    "/cost-ingestion-runs": o.ingestion || { items: [], skipped: [] },
    "/resources": { items: o.resources, total: o.resources.length },
    "/teams": o.teamsFail ? { __status: 500 } : (o.teams || { items: [], unassigned: { account_count: caps.length, accounts: caps.map((c) => ({ cloud_account_id: c.cloud_account_id, provider: c.provider, external_account_id: c.external_account_id, account_label: c.account_label, currency: null, currency_matches_team: null })) }, total: 0 }),
    "/cost-anomalies": Object.assign({ rule: { baseline_days: 7, min_delta_amount: "5", min_increase_pct: 50, min_history_days: 12, exclude_recent_days: 3, currency: "USD", charge_category: ["usage"] }, items: [], insufficient_history: [], held: [], unsupported_currency: [], total: 0 }, o.anomalies || {}),
    // 5단계 A — status=open|resolved|all에 따라 다른 목록을 주고, reviewFail이면 실패시킨다
    "/cost-review-items": (search) => { if (o.reviewFail) return { __status: 500 };
      const st = (/status=(\w+)/.exec(search || "") || [])[1] || "open";
      const all = o.reviewItems || [];
      const items = st === "all" ? all : (st === "resolved" ? all.filter((r) => r.status === "resolved") : all.filter((r) => r.status !== "resolved"));
      return { items, total: items.length }; }, "/notifications": o.notificationsFail ? { __status: 500 } : (o.notifications || { items: [], unread_count: 0 }),
    __team: { budgetStatus: o.budgetStatus, budgets: o.budgets, budgetsFail: o.budgetsFail, delayMs: o.teamDelayMs },
  };
  return { routes, delay: o.delay, trendDelayMs: o.trendDelayMs, breakdownDelayMs: o.breakdownDelayMs };
}

async function run(name, cfg, checks) {
  let html = fs.readFileSync(path.join(ROOT, "cost.html"), "utf8").replace(/<script src="https:\/\/cdn\.tailwindcss\.com"><\/script>/, "");
  const errors = []; const vc = new VirtualConsole(); vc.on("jsdomError", (e) => errors.push(String(e.message || e))); vc.on("error", (...a) => errors.push(a.map((x) => x && x.stack || String(x)).join(" ")));
  const calls = []; const postBodies = [];
  const dom = new JSDOM(html, { url: "http://localhost:8080/cost.html" + (cfg.url || ""), runScripts: "outside-only", pretendToBeVisual: true, virtualConsole: vc, beforeParse(w) {
    w.localStorage.setItem("mcp_session", JSON.stringify({ accessToken: "t", tokenType: "bearer", expiresIn: 3600, refreshToken: "r", refreshExpiresIn: 99999, issuedAt: (cfg.fixedNow || new Date().toISOString()), user: {} }));
    w.Element.prototype.scrollIntoView = () => {};
    // 5단계 C — 클립보드는 jsdom에 없다. clipboardFail이면 "자동 복사 막힘" 경로를 확인한다.
    const copied = [];
    w.__copied = copied;
    if (!cfg.clipboardFail) Object.defineProperty(w.navigator, "clipboard", { value: { writeText: (t) => { copied.push(t); return Promise.resolve(); } }, configurable: true });
    if (cfg.fixedNow) {   // 모의 시계 — 페이지 스크립트의 new Date()/Date.now()만 고정(하네스 자체 날짜는 실제)
      const fixed = new Date(cfg.fixedNow).getTime(); const RealDate = w.Date;
      class FakeDate extends RealDate { constructor(...a) { if (a.length === 0) super(fixed); else super(...a); } static now() { return fixed; } }
      w.Date = FakeDate;
    }
    w.fetch = (url, opts) => new Promise((resolve) => {
      const p = new URL(url).pathname.replace("/api/v1", ""); calls.push((opts && opts.method || "GET") + " " + p + new URL(url).search);
      if (opts && opts.body) { try { postBodies.push(JSON.parse(opts.body)); } catch (e) { postBodies.push(opts.body); } }
      const finish = (b) => { if (b && b.__status) resolve({ ok: false, status: b.__status, headers: { get: () => null }, json: async () => ({ error: { code: "INTERNAL_ERROR", message: "서버 오류" } }) }); else resolve({ ok: true, status: 200, headers: { get: () => null }, json: async () => ({ data: b }) }); };
      let key = Object.keys(cfg.routes).filter((k) => !k.startsWith("__")).find((k) => p === k || p.startsWith(k + "/"));
      let body = key ? cfg.routes[key] : { items: [], total: 0 };
      if (typeof body === "function") body = body(new URL(url).search);
      if (p === "/costs/trend" && typeof cfg.trendDelayMs === "function") return setTimeout(() => finish(body), cfg.trendDelayMs(new URL(url).search));
      if (p === "/costs/breakdown" && typeof cfg.breakdownDelayMs === "function") return setTimeout(() => finish(body), cfg.breakdownDelayMs(new URL(url).search));
      const tm = p.match(/^\/teams\/(\d+)\/(budget-status|budgets)$/);
      if (tm) { const T = cfg.routes.__team || {};
        if (tm[2] === "budgets") body = T.budgetsFail ? { __status: 500 } : (typeof T.budgets === "function" ? T.budgets(tm[1]) : (T.budgets || { items: [], total: 0 }));
        else body = typeof T.budgetStatus === "function" ? T.budgetStatus(tm[1]) : (T.budgetStatus || { team_id: tm[1], budget: null, usage: null, ratio_pct: null, forecast: null, thresholds: [{ percent: 80, crossed: false, crossed_at: null, notified: false }, { percent: 100, crossed: false, crossed_at: null, notified: false }], excluded_accounts: [], computable: false, reason_code: "NO_BUDGET", staleness_threshold_hours: 36 });
        if (typeof T.delayMs === "function") return setTimeout(() => finish(body), T.delayMs(tm[1])); }
      setTimeout(() => finish(body), cfg.delay || 0);
    });
  } });
  const { window } = dom;
  for (const src of [...window.document.querySelectorAll('script[src^="assets/"]')].map((s) => s.getAttribute("src"))) window.eval(fs.readFileSync(path.join(ROOT, src), "utf8"));
  const d = window.document;
  const text = (sel) => (d.querySelector(sel)?.textContent || "").replace(/\s+/g, " ").trim();
  const visible = (sel) => { const el = d.querySelector(sel); return !!el && !el.hidden; };
  await new Promise((r) => setTimeout(r, 60));
  const early = { cf002: text("#CF-002 .block-content") };
  await new Promise((r) => setTimeout(r, (cfg.delay || 0) * 3 + 500));
  const ctx = { d, text, visible, early, errors, calls, postBodies, window };
  const fails = [];
  for (const [label, fn] of Object.entries(checks)) { let ok = false, info = ""; try { ok = await fn(ctx); } catch (e) { info = e.message; } if (!ok) fails.push(label + (info ? " (" + info + ")" : "")); }
  console.log((fails.length ? "FAIL " : "ok   ") + name + (fails.length ? " → " + fails.join(" | ") : ""));
  if (errors.length) console.log("     JS errors:", errors);
  return fails.length === 0;
}
const has = (c, sel, s) => c.text(sel).indexOf(s) >= 0;
module.exports = { base, run, has, NOW, monthStart, todayIso, tomorrow, iso, today };
