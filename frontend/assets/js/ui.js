/* 순수 UI 상태 전환용 스크립트 (서버 통신·데이터 변경 없음).
   테마 토글 / 드롭다운·모달 열고닫기 / 서비스명 주입만 담당. */
(function () {
  // 브랜드명 미확정 → 이 한 곳만 바꾸면 전체 반영 (data-service-name 엘리먼트에 주입)
  var SERVICE_NAME = "MultiCloud Ops";

  function injectServiceName() {
    document.querySelectorAll("[data-service-name]").forEach(function (el) {
      el.textContent = SERVICE_NAME;
    });
  }

  function currentTheme() {
    return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  }

  function syncThemeIcons() {
    var icon = currentTheme() === "dark" ? "☀️" : "🌙";
    document.querySelectorAll("[data-theme-toggle]").forEach(function (b) {
      b.textContent = icon;
      b.setAttribute("aria-label", currentTheme() === "dark" ? "라이트 모드로" : "다크 모드로");
    });
  }

  // 저장된 테마 복원 (per-viewer 편의값이라 실패해도 무시)
  try {
    var saved = localStorage.getItem("mcops-theme");
    if (saved === "dark" || saved === "light") {
      document.documentElement.dataset.theme = saved;
    }
  } catch (e) {}

  window.MCUI = {
    toggleTheme: function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("mcops-theme", next); } catch (e) {}
      syncThemeIcons();
    },
    // 비밀번호 마스킹 토글 (순수 UI) — 눈 아이콘 버튼에서 호출
    togglePassword: function (inputId, btn) {
      var el = document.getElementById(inputId);
      if (!el) return;
      var show = el.type === "password";
      el.type = show ? "text" : "password";
      if (btn) btn.textContent = show ? "🙈" : "👁";
    },
    // 드롭다운/모달 공용 토글 — 대상 엘리먼트의 .hidden 클래스만 뒤집는다
    toggle: function (id) { var el = document.getElementById(id); if (el) el.classList.toggle("hidden"); },
    open:   function (id) { var el = document.getElementById(id); if (el) el.classList.remove("hidden"); },
    close:  function (id) { var el = document.getElementById(id); if (el) el.classList.add("hidden"); }
  };

  // 바깥 클릭 시 열린 (클릭형) 드롭다운 닫기 — .dropdown 컨테이너 밖 클릭이면 [data-dropdown] 모두 닫음
  document.addEventListener("click", function (e) {
    if (!e.target.closest(".dropdown")) {
      document.querySelectorAll("[data-dropdown]").forEach(function (m) { m.classList.add("hidden"); });
    }
  });

  injectServiceName();
  syncThemeIcons();
})();
