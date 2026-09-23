/* 보고서 작성 기능의 데이터 계층.
   "생성 이력"(언제 무슨 조건으로 생성했는가)은 2026-09-19부터 실 API
   (GET/POST/DELETE /api/v1/reports, app/routers/reports.py)로 저장된다 — MCReports.list/
   getById/createReport/remove가 전부 Promise를 반환하는 이유다.

   비용 요약(mapCostSnapshot)은 서버가 생성 시점에 비용 파트(app/cost/query.py)의 공통 함수로
   계산해 `cost_snapshot`에 고정 저장한 값을 그대로 옮겨 담는다 — 여기서 다시 계산하지 않는다.
   AI 분석 요약(mapAiSummary, 2026-09-23)도 같은 방식이다 — 서버(app/report_summary.py)가
   생성 시점의 비용 스냅샷·실시간 사용률/미사용 리소스를 근거로 LLM에게 만들게 한 문단·조치
   목록을 `ai_summary`에 고정 저장한 값 그대로 옮겨 담는다. OPENAI_API_KEY 미설정이거나 생성이
   실패했으면 null — report-view.js가 "생성 실패"로 표시한다(목업으로 가리지 않는다).
   인수인계는 아직 재사용할 API가 없어 BASE 목업 그대로다. 사용률·미사용 리소스는 report-view.js가
   실 API로 실시간 조회해 덮어쓴다(비용과 달리 "지금 이 순간" 값이 맞는 섹션이라 스냅샷 저장
   대상이 아니다). */
(function () {
  "use strict";

  var PERIOD_LABEL = { DAILY: "일간", WEEKLY: "주간", MONTHLY: "월간", HALF_YEARLY: "반기" };
  var PERIOD_DAYS = { DAILY: 1, WEEKLY: 7, MONTHLY: 30, HALF_YEARLY: 182 };
  var COMPARE_LABEL = { DAILY: "전일", WEEKLY: "전주", MONTHLY: "전월", HALF_YEARLY: "전반기" };

  // 인수인계는 비용/AI 요약과 달리 재사용할 공통 계산 함수가 없어 여전히 목업이다(2026-09-23
  // 기준). 비용(cost)·AI 분석 요약(summary)은 더 이상 여기 없다 — 각각 report_generations의
  // cost_snapshot/ai_summary를 mapCostSnapshot()/mapAiSummary()로 그대로 옮겨 쓴다.
  var BASE = {
    title: "멀티클라우드 운영 보고서",
    owner: "안권형",
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

  function shortDate(iso) { return iso.slice(5); } // "2026-09-13" → "09-13"

  // 비용 요약 실 API 연동(2026-09-19, `kwonhyeong/be-next`) — 비용 파트(이승현)의 공통 계산
  // 함수 결과(app/report_cost.py::build_cost_snapshot(), cost/query.py를 그대로 재사용)를
  // 옮겨 담을 뿐, 새 합계·비율·기여율을 여기서 다시 계산하지 않는다(권형님_보고서_비용연동_
  // 개발프롬프트_20260919.md §1 — "보고서에서 별도 공식으로 다시 계산하지 마세요"). 필드
  // 이름만 이 화면 관례(camelCase)에 맞춘다.
  //
  // **생성 시점 값이 고정**이다 — `report_generations.cost_snapshot`에 저장된 그대로이고, 이
  // 함수는 조회 때마다 다시 계산하지 않는다(사용률/미사용 리소스와는 다른 정책 — §9. "같은
  // 보고서를 다시 열 때 현재 비용을 재조회해서 덮어쓰지 마세요"). snapshot이 null이면 생성
  // 시점에 계산 자체가 실패한 것 — 0원이나 목업으로 채우지 않고 그대로 null을 돌려준다.
  function mapBreakdown(b) {
    if (!b || !b.currency) return null; // 통화 자체가 없음(계정 없음 등, §7 "null은 0이 아니다")
    return {
      currency: b.currency,
      excluded: (b.currency_selection && b.currency_selection.excluded) || [],
      total: b.total,
      items: b.items || [],
      rest: b.rest,
      unallocated: b.unallocated,
    };
  }

  function mapCostSnapshot(snapshot) {
    if (!snapshot) return null;
    var summary = snapshot.summary || {};
    var trendProvider = snapshot.trend_provider;
    var changes = snapshot.changes;
    return {
      periodDisplay: summary.period && summary.period.display,
      asOf: summary.as_of || null,
      warnings: summary.warnings || [],
      totalsByCurrency: (summary.kpis && summary.kpis.mtd_actual) || [],
      byProvider: mapBreakdown(snapshot.breakdown_provider),
      byCategory: mapBreakdown(snapshot.breakdown_category),
      trend: trendProvider && trendProvider.currency ? { currency: trendProvider.currency, series: trendProvider.series || [] } : null,
      changes: changes ? {
        comparable: changes.comparable, currency: changes.currency,
        current: changes.current, previous: changes.previous, totals: changes.totals,
        increases: changes.increases || [], decreases: changes.decreases || [], newItems: changes.new_items || [],
      } : null,
    };
  }

  // AI 분석 요약 실 API 연동(2026-09-23) — app/report_summary.py::build_ai_summary()의 결과
  // (`ai_summary`)를 그대로 옮겨 담는다. null이면 생성 시점에 계산 자체가 실패한 것(설정
  // 없음/LLM 오류/파싱 실패) — 목업 문구로 채우지 않고 그대로 null을 돌려준다(cost_snapshot과
  // 같은 정책, §9).
  function mapAiSummary(s) {
    if (!s) return null;
    return { paragraph: s.paragraph, actions: s.actions || [] };
  }

  // BASE + 이력 메타를 조합해 완전한 보고서 payload를 만든다.
  // clouds에 없는 CSP는 모든 섹션에서 제외한다 — "선택한 클라우드만 포함해 생성" 규칙(§1).
  function buildReport(m) {
    var clouds = m.clouds;
    var order = ["aws", "azure", "gcp"].filter(function (c) { return clouds.indexOf(c) !== -1; });

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
      summary: mapAiSummary(m.aiSummary),
      cost: mapCostSnapshot(m.costSnapshot),
      utilization: utilization,
      unused: { items: unusedItems, totalSaving: unusedSaving },
      handover: BASE.handover,
    };
  }

  // "생성 이력"은 실제로 "생성하기"를 눌러 만든 보고서만 담는다 — `GET/POST/DELETE
  // /api/v1/reports`(2026-09-19)가 실 저장소다. 예전엔 브라우저 localStorage에 담아서
  // 팀원끼리 공유가 안 되고, 같은 조건으로 다시 생성할 때마다 새 행이 계속 쌓여 지저분해지는
  // 문제가 있었다(사용자 실사용 중 확인 — 8월/3월 등 뒤죽박죽 날짜가 중복으로 쌓임). 서버가
  // `(user_id, period_type, period_from, period_to, providers)` UNIQUE로 같은 조건을 병합하고
  // `ON CONFLICT DO UPDATE`로 갱신하므로, 프론트는 그냥 매번 생성 요청만 보내면 된다.
  //
  // 비용은 서버가 생성 시점에 계산해 `cost_snapshot`에 고정 저장한다(app/report_cost.py) —
  // 조회할 때마다 값이 바뀌던 예전 문제(매번 랜덤 factor)는 스냅샷 저장으로 근본적으로
  // 해결됐다(같은 id는 항상 같은 값).
  function toReport(row) {
    return buildReport({
      id: row.id,
      from: row.period_from,
      to: row.period_to,
      periodType: row.period_type,
      clouds: row.clouds,
      createdAt: row.generated_at,
      costSnapshot: row.cost_snapshot,
      aiSummary: row.ai_summary,
    });
  }

  function list() {
    if (!window.MCPApi) return Promise.resolve([]);
    return MCPApi.request("/reports?limit=50")
      .then(function (res) { return ((res && res.items) || []).map(toReport); })
      .catch(function () { return []; });
  }

  function getById(id) {
    if (!window.MCPApi) return Promise.resolve(null);
    return MCPApi.request("/reports/" + encodeURIComponent(id))
      .then(toReport)
      .catch(function () { return null; });
  }

  function latest() {
    return list().then(function (items) { return items[0] || null; });
  }

  function createReport(periodType, clouds) {
    if (!window.MCPApi) return Promise.reject(new Error("MCPApi not loaded"));
    var days = PERIOD_DAYS[periodType] || 7;
    var to = new Date();
    var from = new Date(to.getTime() - (days - 1) * 86400000);
    return MCPApi.request("/reports", {
      method: "POST",
      body: {
        period_type: periodType,
        period_from: from.toISOString().slice(0, 10),
        period_to: to.toISOString().slice(0, 10),
        clouds: clouds,
      },
    }).then(toReport);
  }

  function remove(id) {
    if (!window.MCPApi) return Promise.resolve(false);
    return MCPApi.request("/reports/" + encodeURIComponent(id), { method: "DELETE" })
      .then(function () { return true; })
      .catch(function () { return false; });
  }

  window.MCReports = {
    PERIOD_LABEL: PERIOD_LABEL,
    PERIOD_DAYS: PERIOD_DAYS,
    list: list,
    getById: getById,
    latest: latest,
    createReport: createReport,
    remove: remove,
    shortDate: shortDate,
  };
})();
