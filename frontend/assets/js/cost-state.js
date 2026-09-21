/* frontend/assets/js/cost-state.js — 상태값(9종) → 화면 문구. window.MCPCostState 로 노출한다.
   ES 모듈이 아니다 — 이 프로젝트는 빌드 도구 없이 <script src>로 읽는다.
   상태값 9종 밖의 값을 쓰지 않는다(docs/비용_개발문서/03_데이터계약_표시규칙.md §6). */
window.MCPCostState = (function () {
  "use strict";

  var TEXT = {
    CONNECTED_OK:      function (b)    { return null; },
    CONNECTED_EMPTY:   function (b)    { return "정상 조회된 비용은 0입니다. 리소스 수로 계산한 값이 아닙니다."; },
    CONNECTED_PARTIAL: function (b)    { return b + " 일부 범위가 빠진 부분 합계입니다. 제외된 범위를 확인하세요."; },
    NOT_CONNECTED:     function (b)    { return b + " 계정이 연결되지 않았습니다."; },
    PENDING:           function (b)    { return b + " 첫 수집을 기다리는 중입니다."; },
    SETUP_REQUIRED:    function (b)    { return b + " 조회에 필요한 설정이 되어 있지 않습니다."; },
    PERMISSION_DENIED: function (b)    { return b + " 조회 권한이 부족합니다. 검증된 권한 거절 응답이 있을 때만 표시합니다."; },
    COLLECT_FAILED:    function (b, t) { return b + " 조회에 실패했습니다. 금액을 0으로 대체하지 않습니다. 마지막 성공: " + t; },
    UNSUPPORTED:       function (b)    { return b + " 이 CSP의 비용 조회는 아직 지원하지 않습니다."; }
  };

  /* 상태와 무관하게 덧붙는 표시(03 §6-1-1).
     thresholdHours 는 응답의 staleness_threshold_hours 를 그대로 넘긴다 — 여기에 숫자를 쓰지 않는다. */
  function stalenessText(asOf, now, thresholdHours) {
    if (!asOf) return null;
    var hours = Math.floor((now - new Date(asOf).getTime()) / 3600000);
    if (hours < 1) return "방금 전";
    var stale = (thresholdHours != null) && (hours >= thresholdHours);
    return hours + "시간 전" + (stale ? " · 지연" : "");
  }

  function text(state, blockName, lastSuccessAt) {
    var fn = TEXT[state];
    if (!fn) return null;            // 표에 없는 값은 문구를 만들지 않는다
    return fn(blockName, lastSuccessAt);
  }

  /* 짧은 라벨(표·배지용, 2026-09-20). TEXT와 같은 9종 키만 갖고 뜻도 TEXT를 따른다 — 여기 말고
     다른 곳에 한국어 상태 매핑을 또 쓰지 않는다. 라벨은 "무슨 상태인가"만 말하고 원인(설정·권한·
     미지원)에 맞는 행동은 호출부가 붙인다.
     - CONNECTED_EMPTY: 마지막 수집이 성공했고 행이 0건 — "이 조회 기간이 0원"이라는 뜻은 아니다
       (기간 결측은 summary.warnings가 따로 말한다). 그래서 "0원"이라 하지 않고 "수집 0건"이라 한다.
     - PENDING: 종료된 수집 실행이 아직 없다(첫 수집 대기·진행 중·취소됨 포함). */
  var LABEL = {
    CONNECTED_OK: "정상",
    CONNECTED_EMPTY: "수집 0건",
    CONNECTED_PARTIAL: "부분 수집",
    NOT_CONNECTED: "미연결",
    PENDING: "첫 수집 대기",
    SETUP_REQUIRED: "설정 필요",
    PERMISSION_DENIED: "권한 부족",
    COLLECT_FAILED: "수집 실패",
    UNSUPPORTED: "미지원"
  };
  function label(state) { return LABEL[state] || null; }

  /* 상태의 성격 — 화면이 색·행동을 고를 때 쓴다(03 §6-1 그룹 그대로).
     data: 합계에 들어갈 수 있는 상태 / attention: 사용자가 손봐야 하는 상태 / unsupported: 설정으로
     해결되지 않는 상태 / waiting: 기다리면 되는 상태 */
  var KIND = {
    CONNECTED_OK: "data", CONNECTED_EMPTY: "data", CONNECTED_PARTIAL: "data",
    NOT_CONNECTED: "attention", SETUP_REQUIRED: "attention", PERMISSION_DENIED: "attention", COLLECT_FAILED: "attention",
    UNSUPPORTED: "unsupported", PENDING: "waiting"
  };
  function kind(state) { return KIND[state] || null; }

  /* 1단계 백엔드(2026-09-21)가 새로 주는 사유 키 3종의 문구. 원문 키를 화면에 내보내지 않는다.
     사전에 없는 키가 와도 깨지지 않게 일반 문구로 떨어진다(키는 앞으로 늘 수 있다). */

  /* summary.excluded.reason_counts — 합계에 들어가지 못한 이유. capability 상태 9종도 여기로 올 수 있다.
     - PERIOD_NOT_COVERED: 계정 문제가 아니라 "이 조회 기간에 수집 기록이 없음"
     - CURRENCY_FILTERED: 수집 실패·미지원이 아니라 "현재 통화 조건에서 제외" */
  var EXCLUSION_REASON = {
    UNSUPPORTED: "미지원 CSP",
    CURRENCY_FILTERED: "현재 통화 조건에서 제외",
    PERIOD_NOT_COVERED: "이 기간 수집 기록 없음"
  };
  function exclusionLabel(key) { return EXCLUSION_REASON[key] || LABEL[key] || "기타 사유"; }

  /* summary.kpis.forecast_status.state — 응답 전체 상태(통화별이 아니다).
     - not_current_month: "이번 달이 아님"이 아니라 "이번 달 진행 중 구간(1일~오늘)을 조회할 때만 전망을
       낸다"는 뜻 — 9/1~9/15처럼 이번 달 일부만 조회해도 이 상태다. */
  var FORECAST_STATE = {
    computed: null,
    not_current_month: "이번 달 1일부터 오늘까지를 조회할 때만 전망을 냅니다.",
    first_day: "1일에는 아직 근거가 될 실측이 없어 전망을 내지 않습니다.",
    no_accounts: "조회 조건에 전망 대상 계정(실측 지원 계정)이 없습니다.",
    insufficient_coverage: "이번 달 수집이 빠진 계정이 있어 전망을 내지 않습니다(빠진 날을 0원으로 평균 내지 않습니다).",
    currency_unknown: "이번 달 수집은 확인됐지만(0원) 통화를 알 수 없어 전망을 내지 않습니다."
  };
  function forecastText(state) {
    if (state === "computed") return null;
    return FORECAST_STATE[state] || "이 조건에서는 전망을 내지 않습니다.";
  }

  /* changes.comparability.reasons — 비교하지 않은 이유. 여러 개면 REASON_ORDER 앞의 것을 먼저 말한다. */
  var COMPARE_REASON = {
    LENGTH_MISMATCH: "두 기간의 일수가 달라 같은 길이로 비교할 수 없습니다",
    NO_ACCOUNTS: "비교 대상 계정(실측 지원 계정)이 없습니다",
    NO_CURRENCY: "두 기간 모두 실측이 없어 비교할 통화가 없습니다",
    INCOMPLETE_PERIOD: "조회 기간에 아직 끝나지 않은 날(오늘)이 들어 있습니다 — 어제까지의 기간을 고르면 비교합니다",
    CURRENT_COVERAGE: "조회 기간에 수집되지 않은 날이 있습니다",
    PREVIOUS_COVERAGE: "이전 기간에 수집되지 않은 날이 있습니다"
  };
  var COMPARE_REASON_ORDER = ["LENGTH_MISMATCH", "NO_ACCOUNTS", "NO_CURRENCY", "INCOMPLETE_PERIOD", "CURRENT_COVERAGE", "PREVIOUS_COVERAGE"];
  function compareReasonText(key) { return COMPARE_REASON[key] || "비교에 필요한 조건이 맞지 않습니다"; }
  /* 사유 목록 → [주 사유, 나머지 개수]. 카드에는 주 사유만, 나머지는 개수로. */
  function primaryCompareReason(reasons) {
    var list = (reasons || []).slice().sort(function (a, b) {
      var ia = COMPARE_REASON_ORDER.indexOf(a), ib = COMPARE_REASON_ORDER.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    return { key: list[0] || null, text: list.length ? compareReasonText(list[0]) : null, more: Math.max(list.length - 1, 0), all: list };
  }

  return { TEXT: TEXT, text: text, stalenessText: stalenessText, LABEL: LABEL, label: label, kind: kind,
           exclusionLabel: exclusionLabel, forecastText: forecastText, compareReasonText: compareReasonText,
           primaryCompareReason: primaryCompareReason };
})();
