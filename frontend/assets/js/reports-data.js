/* 보고서 작성 기능의 목업 데이터 저장소 (순수 프론트엔드, 서버 통신 없음 — 보고서_구현명세_v5.md
   §0/§9 기준: 비용 대시보드 연동 전까지 이 목업으로 화면만 먼저 완성한다).

   실제 백엔드가 붙으면 이 파일을 지우고 GET /api/v1/reports·/reports/{id}(§5.2/5.3) 응답으로
   MCReports.list/getById를 교체하면 된다 — reports.js/report-view.js는 이미 그 응답 모양
   (payload 구조)을 기대하도록 짜여 있다.

   로컬 프로토타입 전용 파일 — git 커밋 대상 아님. */
(function () {
  "use strict";

  var PERIOD_LABEL = { DAILY: "일간", WEEKLY: "주간", MONTHLY: "월간", HALF_YEARLY: "반기" };
  var PERIOD_DAYS = { DAILY: 1, WEEKLY: 7, MONTHLY: 30, HALF_YEARLY: 182 };
  var COMPARE_LABEL = { DAILY: "전일", WEEKLY: "전주", MONTHLY: "전월", HALF_YEARLY: "전반기" };

  // 최신 보고서(2026-09-07~09-13, 3사 전체) — 명세서 §3 프롬프트 입력 JSON 예시값 그대로.
  // 나머지 이력은 이 값을 clouds/factor로 스케일링해서 만든다(아래 buildReport).
  var BASE = {
    title: "멀티클라우드 운영 보고서",
    owner: "안권형",
    summary: {
      paragraph: "이번 주 전체 비용은 전주 대비 8.3% 증가했으며, Azure VM 2대 신규 생성이 가장 큰 요인입니다. GCP는 미사용 인스턴스 정리 효과로 유일하게 비용이 감소했습니다. 운영 측면에서는 prod-api-01의 자원 여유가 부족한 상태입니다.",
      actions: [
        "prod-api-01의 CPU가 94%에 도달했습니다. 스케일업을 검토하세요",
        "Azure 비용이 12.7% 증가했습니다. 신규 VM 2대의 필요성을 확인하세요",
        "미사용 리소스 4건을 정리하면 월 $125를 절감할 수 있습니다",
        "GCP 서비스 계정 키 3건이 10월 만료 예정입니다",
      ],
    },
    cost: {
      byCsp: {
        aws: { amount: 526, changePct: 5.1 },
        azure: { amount: 384, changePct: 12.7 },
        gcp: { amount: 294, changePct: -2.4 },
      },
      changePct: 8.3,
      changeAmount: 92,
      trend: [
        { label: "8월 2주", aws: 471, azure: 318, gcp: 289 },
        { label: "8월 3주", aws: 489, azure: 330, gcp: 298 },
        { label: "8월 4주", aws: 502, azure: 341, gcp: 301 },
        { label: "9월 1주", aws: 500, azure: 341, gcp: 301 },
        { label: "9월 2주", aws: 526, azure: 384, gcp: 294 },
      ],
      byCategory: [
        { name: "Compute", amount: 506, color: "#3d6fa8" },
        { name: "Database", amount: 289, color: "#5c94c9" },
        { name: "Storage", amount: 205, color: "#8fb9dd" },
        { name: "Network", amount: 132, color: "#b9d4e9" },
        { name: "기타", amount: 72, color: "#d9e5ef" },
      ],
      drivers: [
        { csp: "azure", reason: "VM 2대 신규 생성", amount: 43 },
        { csp: "aws", reason: "S3 데이터 전송량 증가", amount: 26 },
        { csp: "azure", reason: "MySQL 스토리지 확장", amount: 19 },
        { csp: "gcp", reason: "미사용 인스턴스 정리", amount: -7 },
      ],
    },
    utilization: [
      { csp: "aws", name: "prod-api-01", type: "EC2 t3.large", cpu: 94, mem: 88 },
      { csp: "gcp", name: "prod-web-01", type: "CE n2-standard-4", cpu: 81, mem: 62 },
      { csp: "azure", name: "prod-db-01", type: "MySQL D2ds_v4", cpu: 76, mem: 79 },
      { csp: "aws", name: "prod-worker-02", type: "EC2 t3.medium", cpu: 58, mem: 44 },
      { csp: "gcp", name: "prod-batch-01", type: "CE n2-standard-2", cpu: 51, mem: 37 },
    ],
    unused: [
      { csp: "aws", name: "dev-test-04", type: "EC2 t3.medium", idleDays: 18, cost: 34 },
      { csp: "aws", name: "vol-0a3f2b", type: "EBS 미연결 볼륨", idleDays: 25, cost: 12 },
      { csp: "azure", name: "stg-vm-02", type: "VM D2s_v3", idleDays: 16, cost: 41 },
      { csp: "gcp", name: "old-batch-01", type: "CE n2-standard-2", idleDays: 21, cost: 38 },
    ],
    handover: [
      { type: "진행 중", title: "GCP Cloud SQL 비공개 네트워크 전환", body: "인스턴스 3개 중 2개 완료. 나머지 1개는 서비스 점검 창 대기 중.", owner: "안권형", due: "예정일 09-18" },
      { type: "만료 예정", title: "GCP 서비스 계정 키 3건", body: "10-13 만료 예정. 갱신 후 관련 파이프라인 환경변수 교체 필요.", owner: "안권형", due: "기한 10-06" },
      { type: "주의", title: "prod-api-01 스케일업 검토", body: "CPU·메모리 모두 90% 근접. 인스턴스 타입 변경 시 재시작이 필요하므로 점검 창 확보 필요.", owner: "김종국", due: "" },
    ],
  };

  // 생성 이력 메타 — 보고서 작성 페이지 프로토타입 스크린샷 5건 그대로.
  var HISTORY_META = [
    { id: "rpt-1", from: "2026-09-07", to: "2026-09-13", periodType: "WEEKLY", clouds: ["aws", "azure", "gcp"], factor: 1, createdAt: "2026-09-14T09:00:00Z" },
    { id: "rpt-2", from: "2026-08-31", to: "2026-09-06", periodType: "WEEKLY", clouds: ["aws", "azure", "gcp"], factor: 0.93, createdAt: "2026-09-07T09:00:00Z" },
    { id: "rpt-3", from: "2026-08-01", to: "2026-08-31", periodType: "MONTHLY", clouds: ["aws", "azure", "gcp"], factor: 4.15, createdAt: "2026-09-01T09:00:00Z" },
    { id: "rpt-4", from: "2026-08-24", to: "2026-08-30", periodType: "WEEKLY", clouds: ["aws", "azure"], factor: 0.89, createdAt: "2026-08-31T09:00:00Z" },
    { id: "rpt-5", from: "2026-08-17", to: "2026-08-23", periodType: "WEEKLY", clouds: ["azure"], factor: 0.34, createdAt: "2026-08-24T09:00:00Z" },
  ];

  function round(n) { return Math.round(n); }
  function shortDate(iso) { return iso.slice(5); } // "2026-09-13" → "09-13"

  // BASE + 이력 메타를 조합해 완전한 보고서 payload를 만든다(실 API의 GET /reports/{id} 대용).
  // clouds에 없는 CSP는 모든 섹션에서 제외한다 — "선택한 클라우드만 포함해 생성" 규칙(§1).
  function buildReport(m) {
    var clouds = m.clouds;
    var order = ["aws", "azure", "gcp"].filter(function (c) { return clouds.indexOf(c) !== -1; });

    var byCsp = {};
    var total = 0;
    order.forEach(function (csp) {
      var amount = round(BASE.cost.byCsp[csp].amount * m.factor);
      byCsp[csp] = { amount: amount, changePct: BASE.cost.byCsp[csp].changePct };
      total += amount;
    });

    var trend = BASE.cost.trend.map(function (pt) {
      var out = { label: pt.label };
      order.forEach(function (csp) { out[csp] = round(pt[csp] * m.factor); });
      return out;
    });

    var categoryTotal = 0;
    var byCategory = BASE.cost.byCategory.map(function (c) {
      var amount = round(c.amount * m.factor * (order.length / 3));
      categoryTotal += amount;
      return { name: c.name, amount: amount, color: c.color };
    });
    byCategory.forEach(function (c) { c.pct = categoryTotal ? Math.round((c.amount / categoryTotal) * 100) : 0; });

    var changeAmount = round(BASE.cost.changeAmount * m.factor);
    var drivers = BASE.cost.drivers
      .filter(function (d) { return order.indexOf(d.csp) !== -1; })
      .map(function (d) { return { csp: d.csp, reason: d.reason, amount: round(d.amount * m.factor) }; });
    if (!drivers.length) drivers = [{ csp: order[0], reason: "사용량 변동", amount: changeAmount }];

    var utilization = BASE.utilization.filter(function (r) { return order.indexOf(r.csp) !== -1; });
    var unusedItems = BASE.unused.filter(function (r) { return order.indexOf(r.csp) !== -1; });
    var unusedSaving = unusedItems.reduce(function (s, r) { return s + r.cost; }, 0);

    return {
      id: m.id,
      periodType: m.periodType,
      periodLabel: PERIOD_LABEL[m.periodType],
      compareLabel: COMPARE_LABEL[m.periodType],
      from: m.from,
      to: m.to,
      clouds: order,
      isDraft: true,
      createdAt: m.createdAt,
      title: BASE.title,
      owner: BASE.owner,
      summary: BASE.summary,
      cost: {
        total: total,
        changePct: BASE.cost.changePct,
        changeAmount: changeAmount,
        byCsp: byCsp,
        trend: trend,
        byCategory: byCategory,
        drivers: drivers,
      },
      utilization: utilization,
      unused: { items: unusedItems, totalSaving: unusedSaving },
      handover: BASE.handover,
    };
  }

  var REPORTS = HISTORY_META.map(buildReport);

  function getById(id) {
    for (var i = 0; i < REPORTS.length; i++) if (REPORTS[i].id === id) return REPORTS[i];
    return null;
  }

  function latest() { return REPORTS[0]; }

  // 새 보고서 생성 시뮬레이션 — 실제로는 POST /api/v1/reports(§5.1)가 대신한다.
  function createReport(periodType, clouds) {
    var days = PERIOD_DAYS[periodType] || 7;
    var to = new Date();
    var from = new Date(to.getTime() - (days - 1) * 86400000);
    var report = buildReport({
      id: "rpt-" + Date.now(),
      from: from.toISOString().slice(0, 10),
      to: to.toISOString().slice(0, 10),
      periodType: periodType,
      clouds: clouds,
      factor: 0.85 + Math.random() * 0.3,
      createdAt: to.toISOString(),
    });
    REPORTS.unshift(report);
    return report;
  }

  window.MCReports = {
    PERIOD_LABEL: PERIOD_LABEL,
    PERIOD_DAYS: PERIOD_DAYS,
    list: REPORTS,
    getById: getById,
    latest: latest,
    createReport: createReport,
    shortDate: shortDate,
  };
})();
