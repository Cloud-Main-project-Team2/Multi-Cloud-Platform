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

  // 선(아웃라인) 아이콘 — 이모지(🌙/☀️) 대신 서비스 로고와 결을 맞춘 SVG를 넣는다.
  var SUN_SVG = '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>';
  var MOON_SVG = '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';

  // 공용 아웃라인 아이콘 세트 — 이모지 대신 재사용(비밀번호 눈, 상태 배지, 화살표 등).
  var ICONS = {
    eye: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>',
    eyeOff: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20C5 20 1 12 1 12a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>',
    check: '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
    x: '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    arrowRight: '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:inline-block;vertical-align:-2px"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>',
    externalLink: '<svg aria-hidden="true" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:inline-block;vertical-align:-2px"><path d="M7 17 17 7"/><polyline points="7 7 17 7 17 17"/></svg>'
  };

  function syncThemeIcons() {
    var dark = currentTheme() === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach(function (b) {
      b.innerHTML = dark ? SUN_SVG : MOON_SVG;
      b.setAttribute("aria-label", dark ? "라이트 모드로" : "다크 모드로");
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
    icons: ICONS,
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
      if (btn) btn.innerHTML = show ? MCUI.icons.eyeOff : MCUI.icons.eye;
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

  // 🔔 알림 팝오버 — 사이드바 유틸 바의 벨 위에 뜬다.
  // 아직 알림 API가 없어 목업 데이터로 채우고, 각 항목은 관련 화면으로 이동한다.
  function initNotifications() {
    var utils = document.querySelector(".sidebar__utils");
    var bell = utils && utils.querySelector(".bell");
    if (!utils || !bell) return;

    var NOTIFS = [
      { icon: "⚡", title: "프로비저닝 완료", desc: "AWS EC2 인스턴스가 생성되었습니다.", time: "방금 전", href: "provisioning.html" },
      { icon: "🔄", title: "동기화 실패", desc: "GCP 리소스 동기화가 실패했습니다.", time: "12분 전", href: "inventory.html" },
      { icon: "🔑", title: "자격 증명 검증 실패", desc: "dev-gcp 자격 증명을 다시 등록해 주세요.", time: "1시간 전", href: "mypage.html" }
    ];

    var pop = document.createElement("div");
    pop.className = "notif-pop hidden";
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-label", "알림");
    pop.innerHTML =
      '<div class="notif-pop__head">알림 <span class="notif-pop__count">' + NOTIFS.length + "</span></div>" +
      '<ul class="notif-pop__list">' +
      NOTIFS.map(function (n) {
        return '<li><a class="notif-pop__item" href="' + n.href + '">' +
          '<span class="notif-pop__icon">' + n.icon + "</span>" +
          '<span class="notif-pop__body">' +
          '<span class="notif-pop__title">' + n.title + "</span>" +
          '<span class="notif-pop__desc">' + n.desc + "</span>" +
          '<span class="notif-pop__time">' + n.time + "</span>" +
          "</span></a></li>";
      }).join("") +
      "</ul>";
    utils.appendChild(pop);

    bell.style.cursor = "pointer";
    bell.addEventListener("click", function (e) {
      e.stopPropagation();
      pop.classList.toggle("hidden");
    });
    document.addEventListener("click", function (e) {
      if (!e.target.closest(".notif-pop") && !e.target.closest(".bell")) pop.classList.add("hidden");
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") pop.classList.add("hidden");
    });
  }

  // 🌐 언어 선택 드롭다운 — 사이드바 유틸 바의 지구본 버튼을 누르면 언어 목록이 열린다.
  // 지원 언어는 한국어/English 2개(코드상 그것뿐). 실제 i18n은 아직 없어 선택만 저장·표시한다
  // (기존 KOR/EN 토글도 번역 동작은 없었다 — UI 형태만 지구본+드롭다운으로 바꾼 것).
  function initLangDropdown() {
    var wrap = document.querySelector(".sidebar__utils .lang-dd");
    var btn = wrap && wrap.querySelector(".lang-dd__btn");
    if (!wrap || !btn) return;

    var LANGS = [
      { code: "ko", label: "한국어", short: "KOR" },
      { code: "en", label: "English", short: "EN" }
    ];
    var current = "ko";
    try { current = localStorage.getItem("mcp_lang") || "ko"; } catch (e) {}

    var currentEl = btn.querySelector(".lang-dd__current");
    var menu = document.createElement("div");
    menu.className = "lang-dd__menu hidden";
    menu.setAttribute("role", "listbox");
    menu.innerHTML = LANGS.map(function (l) {
      return '<button type="button" class="lang-dd__item" role="option" data-lang="' + l.code + '">' + l.label + "</button>";
    }).join("");
    wrap.appendChild(menu);

    function render() {
      var found = LANGS.filter(function (l) { return l.code === current; })[0] || LANGS[0];
      if (currentEl) currentEl.textContent = found.short;
      Array.prototype.forEach.call(menu.querySelectorAll(".lang-dd__item"), function (it) {
        it.classList.toggle("active", it.getAttribute("data-lang") === current);
      });
    }
    function closeMenu() { menu.classList.add("hidden"); btn.setAttribute("aria-expanded", "false"); }
    render();

    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var willOpen = menu.classList.contains("hidden");
      menu.classList.toggle("hidden", !willOpen);
      btn.setAttribute("aria-expanded", String(willOpen));
    });
    menu.addEventListener("click", function (e) {
      var item = e.target.closest(".lang-dd__item");
      if (!item) return;
      current = item.getAttribute("data-lang");
      try { localStorage.setItem("mcp_lang", current); } catch (e2) {}
      render();
      closeMenu();
    });
    document.addEventListener("click", function (e) {
      if (!e.target.closest(".lang-dd")) closeMenu();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeMenu();
    });
  }

  injectServiceName();
  syncThemeIcons();
  initNotifications();
  initLangDropdown();
})();
