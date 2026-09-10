/* 마이페이지(MY-01) — 연결된 클라우드 계정 표 행 드래그 순서 변경 (순수 프론트엔드).
 *
 * 포인터(마우스) 이벤트 기반 정렬 — 표 행에서 불안정한 HTML5 Drag&Drop 대신
 * pointerdown/move/up + elementFromPoint로 직접 재배치한다(브라우저 호환 안정적).
 * 바뀐 순서는 localStorage(mcp_account_order, 행 이름을 키)에 저장해 새로고침 후 유지.
 * 서버 통신 없음. 기존 필터(행 hidden 토글)와 공존한다.
 *
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
    saved.forEach(function (key) { if (byKey[key]) tbody.appendChild(byKey[key]); });
  }
  function saveOrder() {
    try { localStorage.setItem(ORDER_KEY, JSON.stringify(getRows().map(rowKey))); } catch (e) {}
  }

  var dragging = null;

  function rowUnder(x, y) {
    var el = document.elementFromPoint(x, y);
    var tr = el && el.closest ? el.closest("tr") : null;
    return tr && tr.parentNode === tbody ? tr : null;
  }

  function onMove(e) {
    if (!dragging) return;
    if (e.cancelable) e.preventDefault();
    var over = rowUnder(e.clientX, e.clientY);
    if (!over || over === dragging) return;
    var rect = over.getBoundingClientRect();
    var after = (e.clientY - rect.top) > rect.height / 2;
    tbody.insertBefore(dragging, after ? over.nextSibling : over);
  }

  function onUp() {
    if (dragging) {
      dragging.classList.remove("opacity-50");
      dragging.style.cursor = "grab";
      saveOrder();
    }
    dragging = null;
    document.removeEventListener("pointermove", onMove);
    document.removeEventListener("pointerup", onUp);
  }

  function onDown(e) {
    if (e.button !== undefined && e.button !== 0) return; // 좌클릭만
    var tr = e.target.closest ? e.target.closest("tr") : null;
    if (!tr || tr.parentNode !== tbody) return;
    dragging = tr;
    tr.classList.add("opacity-50");
    tr.style.cursor = "grabbing";
    if (e.cancelable) e.preventDefault(); // 텍스트 선택 방지
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
  }

  getRows().forEach(function (tr) {
    tr.style.userSelect = "none";
    tr.style.webkitUserSelect = "none";
    tr.style.cursor = "grab";
    tr.style.touchAction = "none";
  });
  tbody.addEventListener("pointerdown", onDown);

  applySavedOrder();
})();
