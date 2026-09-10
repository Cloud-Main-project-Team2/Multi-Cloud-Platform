/* 마이페이지(MY-01) — 연결된 클라우드 계정 표 행 드래그 순서 변경 (순수 프론트엔드).
 *
 * HTML5 Drag and Drop으로 표 행 순서를 바꾸고, 순서를 localStorage에 저장해
 * 새로고침 후에도 유지한다. 서버 통신 없음. 기존 필터(인라인 스크립트)와 공존한다
 * — 필터는 행의 hidden만 토글하고, 여기서는 DOM 순서만 바꾼다.
 *
 * 저장 키(mcp_account_order)는 각 행의 이름(2번째 셀)을 안정적 키로 사용한다.
 * API 연동 후에는 이 순서를 서버(계정 display_order 등)에 저장하도록 교체한다.
 */
(function () {
  "use strict";
  var ORDER_KEY = "mcp_account_order";
  var tbody = document.getElementById("accounts-tbody");
  if (!tbody) return;

  function rowKey(tr) {
    var cell = tr.children[1]; // 이름 셀
    return cell ? (cell.textContent || "").trim() : "";
  }
  function getRows() {
    return Array.prototype.slice.call(tbody.querySelectorAll("tr"));
  }

  function applySavedOrder() {
    var saved;
    try { saved = JSON.parse(localStorage.getItem(ORDER_KEY) || "null"); } catch (e) { saved = null; }
    if (!saved || !saved.length) return;
    var byKey = {};
    getRows().forEach(function (tr) { byKey[rowKey(tr)] = tr; });
    // 저장된 순서대로 재배치(있는 행만)
    saved.forEach(function (key) { if (byKey[key]) tbody.appendChild(byKey[key]); });
  }
  function saveOrder() {
    try { localStorage.setItem(ORDER_KEY, JSON.stringify(getRows().map(rowKey))); } catch (e) {}
  }

  var dragEl = null;
  getRows().forEach(function (tr) {
    tr.setAttribute("draggable", "true");
    tr.style.userSelect = "none";       // 드래그 시 텍스트 선택이 드래그를 가로채지 않도록
    tr.style.webkitUserSelect = "none";
    tr.style.cursor = "grab";
  });

  tbody.addEventListener("dragstart", function (e) {
    var tr = e.target.closest("tr");
    if (!tr) return;
    dragEl = tr;
    tr.classList.add("opacity-50");
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", rowKey(tr)); } catch (e2) {}
    }
  });

  tbody.addEventListener("dragend", function () {
    if (dragEl) dragEl.classList.remove("opacity-50");
    dragEl = null;
  });

  // dragenter/dragover 모두에서 preventDefault를 해야 drop이 허용된다(브라우저별 차이 방어).
  function allowDrop(e) {
    if (!dragEl) return;
    e.preventDefault();
    var over = e.target.closest("tr");
    if (!over || over === dragEl || over.parentNode !== tbody) return;
    var rect = over.getBoundingClientRect();
    var after = (e.clientY - rect.top) > rect.height / 2;
    tbody.insertBefore(dragEl, after ? over.nextSibling : over);
  }
  tbody.addEventListener("dragenter", allowDrop);
  tbody.addEventListener("dragover", allowDrop);

  tbody.addEventListener("drop", function (e) {
    e.preventDefault();
    saveOrder();
  });

  applySavedOrder();
})();
