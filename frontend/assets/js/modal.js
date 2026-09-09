/* 공통 모달 유틸리티 (순수 프론트엔드, 서버 통신 없음).
   - 오버레이 요소: [data-modal] 를 가진 요소(기본 .hidden 상태로 시작)
   - 트리거: [data-modal-open="#모달ID"] 클릭 → 해당 모달 열기
   - 닫기 버튼: [data-modal-close] 클릭 → 자신이 속한 모달 닫기
   - 배경(오버레이) 클릭 / ESC → 닫기
   - 열려 있는 동안 body 스크롤 잠금
   - 전역 API: window.MCPModal.open(sel) / .close(sel)  (fe-basic-js 등 다른 스크립트가 프로그래밍적으로 호출) */
(function () {
  function el(sel) { return typeof sel === "string" ? document.querySelector(sel) : sel; }
  function anyOpen() { return document.querySelector("[data-modal]:not(.hidden)"); }

  function lockScroll() { document.body.style.overflow = "hidden"; }
  function unlockScroll() { if (!anyOpen()) document.body.style.overflow = ""; }

  function open(sel) {
    var m = el(sel);
    if (!m) return;
    m.classList.remove("hidden");
    lockScroll();
  }
  function close(sel) {
    var m = sel ? el(sel) : anyOpen();
    if (!m) return;
    m.classList.add("hidden");
    unlockScroll();
  }

  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-modal-open]");
    if (opener) { e.preventDefault(); open(opener.getAttribute("data-modal-open")); return; }

    var closer = e.target.closest("[data-modal-close]");
    if (closer) { close(closer.closest("[data-modal]")); return; }

    // 배경(오버레이 자기 자신) 클릭 시 닫기 — 내부 콘텐츠 클릭은 오버레이가 아니므로 무시됨
    if (e.target.matches("[data-modal]")) { close(e.target); }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { var m = anyOpen(); if (m) close(m); }
  });

  window.MCPModal = { open: open, close: close };
})();
