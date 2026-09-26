// 수집 근거(coverage_basis) 도입 라운드에서 실제로 났던 화면 회귀를 고정한다.
//
// 각 케이스는 "고치기 전 코드에서 실패하는 것"을 확인한 것만 남긴다 — 통과 사실보다 **무엇을 막는지**가
// 중요하다. 여기 있는 것은 DOM 픽스처(jsdom)일 뿐 브라우저 확인이 아니다.
//
// 실행: cd frontend/tests/cost && npm install && npm test
const { base, run, has, NOW, monthStart, todayIso } = require("./harness");

const days = Math.max((new Date(todayIso) - new Date(monthStart)) / 86400000, 0);
const cov = (o) => Object.assign({ start: monthStart, end: todayIso, days, covered: days, missing_count: 0,
  missing_days: [], truncated: false, pending_days: 1 }, o);
const covObserved = cov({ basis: "observed_only", analysis_ready: false });          // 금액은 다 왔지만 근거 없음
const covComplete = cov({ basis: "complete_range", analysis_ready: true });          // 기존 AWS 동작
const covEmptyNull = cov({ covered: 0, missing_count: days, truncated: true, basis: null, analysis_ready: false });

const TEAMS = { items: [{ id: "7", name: "플랫폼", currency: "USD", account_count: 1, accounts: [], created_at: NOW }],
  unassigned: { account_count: 0, accounts: [] }, total: 1 };
const usage12 = { amount: "12.000000", net_amount: "12.000000", currency: "USD", basis: "usage_before_credits",
  is_estimated: false, as_of: NOW };
const heldOf = (days_) => ({ items: [], held: [{ cloud_account_id: "1", days: days_ }],
  insufficient_history: [], unsupported_currency: [], total: 0 });
const notComputable = (code, extra) => Object.assign({ team_id: "7",
  budget: { id: "b1", team_id: "7", period_type: "monthly", period_state: "in_progress", start_date: monthStart,
    period_start: monthStart, period_end: todayIso, basis_date: todayIso, limit_amount: "200.000000", currency: "USD" },
  usage: null, ratio_pct: null, forecast: null,
  thresholds: [{ percent: 80, crossed: false, crossed_at: null, notified: false },
    { percent: 100, crossed: false, crossed_at: null, notified: false }],
  excluded_accounts: [], computable: false, reason_code: code, staleness_threshold_hours: 36 }, extra || {});

(async () => {
  const r = [];

  // ── ① 개요: "금액은 보이되 판정은 보류" ───────────────────────────────────────────────
  r.push(await run("근거 부족이면 금액은 유지하고 전망만 보류한다", base({ coverage: { "1": covObserved },
    forecastStatus: { state: "coverage_unverified", based_through: todayIso, required_accounts: 1,
      incomplete_accounts: [], unverified_accounts: [{ cloud_account_id: "1" }] } }), {
    "금액 유지": (c) => has(c, "#CF-002", "$") && !has(c, "#CF-002", "0원이라 표시하지 않습니다"),
    "전망 보류를 '수집 실패'로 말하지 않는다": (c) => has(c, "#CF-003", "어느 범위까지 도착했는지 알려 주지 않아")
      && !has(c, "#CF-003", "수집이 빠진") && !/coverage_unverified/.test(c.text("#CF-003")),
    "계정 표는 관측·범위 미확인": (c) => has(c, "#CF-009", "일 금액 관측 · 범위 미확인") && !has(c, "#CF-009", "20/20일 확인"),
  }));
  r.push(await run("신규 CSP의 정상 빈 응답을 0원이라 단정하지 않는다", base({ rows: false, status: "CONNECTED_EMPTY",
    coverage: { "1": covEmptyNull } }), {
    "계정 표": (c) => has(c, "#CF-009", "수집 정상 완료 · 이 기간 0원 여부 미확인") && !has(c, "#CF-009", "수집 확인된 0원"),
    "누적 카드": (c) => has(c, "#CF-002", "도착 범위를 확인할 수 없어 0원이라 표시하지 않습니다"),
  }));
  r.push(await run("근거 있는 빈 응답(AWS)은 기존 '확인된 0원'을 그대로 쓴다", base({ rows: false,
    status: "CONNECTED_EMPTY", coverage: { "1": covComplete } }), {
    "기존 문구 유지(AWS 회귀)": (c) => has(c, "#CF-009", "수집 확인된 0원") && has(c, "#CF-002", "수집 확인된 0원")
      && has(c, "#CF-030", "조회 기간에 실측 비용이 0입니다"),
  }));
  // 반례: 마지막 수집이 0건이어도 **조회 기간에 금액이 있으면** 0원이라 말하면 안 된다.
  r.push(await run("마지막 수집 0건 ≠ 조회 기간 0원", base({ status: "CONNECTED_EMPTY", actual: "10.000000",
    coverage: { "1": covComplete } }), {
    "금액 유지": (c) => has(c, "#CF-009", "$10.00") && has(c, "#CF-002", "$10.00"),
    "'정상 0' 안내 없음": (c) => !has(c, "#CF-030", "조회 기간에 실측 비용이 0입니다") && !has(c, "#CF-030", "정상 0"),
    "둘을 구분해 설명": (c) => has(c, "#CF-030", "마지막 수집은 0건이지만 이 조회 기간에는 실측 $10.00이 있습니다"),
  }));
  r.push(await run("비교 보류 사유는 결측이 아니라 근거 부족으로 적는다",
    base({ comparability: { reasons: ["COVERAGE_UNVERIFIED"], accounts: [] } }), {
    "사람 말로 적고 코드값을 노출하지 않는다": (c) => has(c, "#CF-024", "도착 범위를 확인할 수 없어 비교하지 않습니다")
      && !/COVERAGE_UNVERIFIED/.test(c.text("#CF-024")),
  }));

  // ── ③ 예산: 보류여도 받은 금액은 보여 준다 ────────────────────────────────────────────
  r.push(await run("예산 COVERAGE_UNVERIFIED — 사유 + 받은 금액", base({ teams: TEAMS,
    budgetStatus: notComputable("COVERAGE_UNVERIFIED", { usage: usage12 }) }), {
    "사유 문구": (c) => has(c, "#CF-026", "도착 범위를 확인할 수 없는 CSP 계정"),
    // 안내 문구가 "받은 금액은 그대로 표시합니다"라고 약속하므로 금액이 빠지면 거짓말이 된다.
    "금액 표시·꼬리 문구": (c) => has(c, "#CF-026", "$12.00") && has(c, "#CF-026", "도착 범위가 확인되면")
      && !has(c, "#CF-026", "결측일이 채워지면"),
    // 수치는 내지 않는다(문구 속 "소진율을 냅니다"와 구분하려고 숫자까지 본다)
    "소진율 수치 없음": (c) => !/소진율 [\d.]+%/.test(c.text("#CF-026")),
  }));
  r.push(await run("예산 MISSING_DAYS — 같은 자리, 다른 꼬리 문구", base({ teams: TEAMS,
    budgetStatus: notComputable("MISSING_DAYS", { usage: usage12 }) }), {
    "—": (c) => has(c, "#CF-026", "$12.00") && has(c, "#CF-026", "결측일이 채워지면") && !has(c, "#CF-026", "도착 범위가 확인되면"),
  }));

  // ── ③ 급증: 보류 사유를 뭉뚱그리지 않는다(배지 + 상세 줄 둘 다) ──────────────────────
  r.push(await run("급증 보류 — 근거 부족만 있는 계정", base({ teams: TEAMS,
    anomalies: heldOf([{ date: "2026-09-09", reason: "coverage_unverified" }, { date: "2026-09-10", reason: "coverage_unverified" }]) }), {
    "배지": (c) => has(c, "#CF-033", "평가하지 못한 계정 1개") && has(c, "#CF-033", "수집 범위 근거 부족 1")
      && !has(c, "#CF-033", "기준선 미수집"),
    // 상세 줄이 늘 "미수집일"이라고 적으면 재수집하면 풀린다는 뜻으로 읽힌다 — 풀리지 않는다.
    "상세 줄": (c) => has(c, "#CF-033", "도착 범위 근거가 없어 2일 보류") && !has(c, "#CF-033", "미수집일이 있어"),
  }));
  r.push(await run("급증 보류 — 기준선 미수집만 있는 계정", base({ teams: TEAMS,
    anomalies: heldOf([{ date: "2026-09-09", reason: "day_not_collected" }]) }), {
    "배지": (c) => has(c, "#CF-033", "기준선 미수집 1") && !has(c, "#CF-033", "수집 범위 근거 부족"),
    "상세 줄": (c) => has(c, "#CF-033", "미수집일이 있어 1일 보류") && !has(c, "#CF-033", "도착 범위 근거가 없어"),
  }));
  r.push(await run("급증 보류 — 한 계정에 두 사유(계정은 1개로 센다)", base({ teams: TEAMS,
    anomalies: heldOf([{ date: "2026-09-09", reason: "day_not_collected" }, { date: "2026-09-10", reason: "coverage_unverified" }]) }), {
    "배지": (c) => has(c, "#CF-033", "평가하지 못한 계정 1개") && has(c, "#CF-033", "기준선 미수집 1")
      && has(c, "#CF-033", "수집 범위 근거 부족 1") && !has(c, "#CF-033", "평가하지 못한 계정 2개"),
    "상세 줄": (c) => has(c, "#CF-033", "미수집일이 있어 1일 보류") && has(c, "#CF-033", "도착 범위 근거가 없어 1일 보류"),
  }));

  console.log(r.every(Boolean) ? "\nCOVERAGE-BASIS DOM PASS" : "\nCOVERAGE-BASIS DOM FAILED");
  process.exit(r.every(Boolean) ? 0 : 1);
})();
