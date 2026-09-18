/* frontend/assets/js/cost.js — 비용 화면 조회·필터·블록 렌더·모달·이동(07_프론트_구현가이드.md §6).
   이번 단계(PR 2 — 와이어프레임 이식)는 정적 화면까지다: MCPApi.request를 부르지 않는다.
   지금 이 파일이 하는 일은 탭 3개 전환뿐이다. 블록 렌더링·필터 적용·모달 내용 채우기는
   비용 조회 API가 붙는 다음 단계(PR 6 — 화면 실 API 연동)에서 이 파일에 이어서 작성한다.
   ES 모듈이 아니다 — 이 프로젝트는 빌드 도구 없이 <script src>로 읽는다. defer를 붙이지 않는다
   (다른 9개 화면과 같은 관례 — inventory.js처럼 DOMContentLoaded를 직접 본다). */
window.MCPCost = (function () {
  "use strict";

  var TAB_PANELS = { overview: "panel-overview", analysis: "panel-analysis", budget: "panel-budget" };

  function initTabs() {
    var tabs = Array.prototype.slice.call(document.querySelectorAll('.tabs [role="tab"][data-tab]'));
    if (!tabs.length) return;

    function activate(key) {
      tabs.forEach(function (btn) {
        var active = btn.getAttribute("data-tab") === key;
        btn.setAttribute("aria-selected", active ? "true" : "false");
        btn.tabIndex = active ? 0 : -1;
      });
      Object.keys(TAB_PANELS).forEach(function (k) {
        var panel = document.getElementById(TAB_PANELS[k]);
        if (panel) panel.hidden = k !== key;
      });
    }

    tabs.forEach(function (btn) {
      btn.addEventListener("click", function () { activate(btn.getAttribute("data-tab")); });
    });
  }

  document.addEventListener("DOMContentLoaded", initTabs);

  return { initTabs: initTabs };
})();
