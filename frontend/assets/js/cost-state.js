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

  return { TEXT: TEXT, text: text, stalenessText: stalenessText };
})();
