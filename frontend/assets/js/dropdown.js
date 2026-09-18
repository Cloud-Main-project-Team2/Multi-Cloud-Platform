/* 커스텀 드롭다운 (순수 프론트엔드).
   네이티브 <select>를 시각적으로 숨기고(값·change 이벤트는 그대로 유지) 그 위에
   버튼 + 라운드 팝업 목록 UI를 씌운다. 기존 필터 스크립트는 여전히 select 값을 읽으므로 무변경. */
(function () {
  function chevron() {
    return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="opacity:.55;flex:0 0 auto"><path d="M6 9l6 6 6-6"/></svg>';
  }

  function enhance(select) {
    if (select.dataset.mcEnhanced) return;
    select.dataset.mcEnhanced = "1";

    var origClass = select.className;                 // select와 동일한 룩을 버튼에 복사
    var isFull = /\bw-full\b/.test(origClass);

    var wrap = document.createElement("div");
    wrap.className = "mc-dd relative " + (isFull ? "w-full" : "inline-block");
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);

    select.classList.add("sr-only");                  // 시각적으로만 숨김
    select.setAttribute("tabindex", "-1");
    select.setAttribute("aria-hidden", "true");

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = origClass + " flex items-center justify-between gap-2 text-left";
    btn.innerHTML = '<span class="mc-dd__label truncate min-w-0"></span>' + chevron();
    wrap.appendChild(btn);

    var menu = document.createElement("div");
    menu.className = "mc-dd__menu hidden absolute left-0 top-full z-50 mt-1 min-w-full max-h-64 overflow-auto rounded-xl border border-border bg-surface py-1 shadow-lg";
    wrap.appendChild(menu);

    function buildItems() {
      menu.innerHTML = "";
      Array.prototype.forEach.call(select.options, function (opt, i) {
        var item = document.createElement("div");
        item.className = "mc-dd__item cursor-pointer whitespace-nowrap px-3 py-2 text-sm hover:bg-muted";
        item.textContent = opt.text;
        item.addEventListener("click", function () {
          select.selectedIndex = i;
          select.dispatchEvent(new Event("change", { bubbles: true }));
          closeAll();
        });
        menu.appendChild(item);
      });
    }
    buildItems();

    function syncLabel() {
      var opt = select.options[select.selectedIndex];
      btn.querySelector(".mc-dd__label").textContent = opt ? opt.text : "";
      Array.prototype.forEach.call(menu.children, function (item, i) {
        item.classList.toggle("bg-muted", i === select.selectedIndex);
        item.classList.toggle("font-medium", i === select.selectedIndex);
      });
    }

    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var willOpen = menu.classList.contains("hidden");
      closeAll();
      if (willOpen) menu.classList.remove("hidden");
    });
    select.addEventListener("change", syncLabel);     // 프로그래밍적 변경도 라벨 동기화
    // form.reset()은 <select> 값을 되돌리지만 change 이벤트를 쏘지 않아 커스텀 라벨이 옛 선택을
    // 그대로 보여준다(예: 마이페이지에서 GCP 저장 후 폼은 AWS로 리셋되는데 라벨만 GCP로 남는 문제).
    // reset 이벤트는 컨트롤이 초기화되기 "전"에 발생하므로 다음 틱에 라벨을 다시 맞춘다.
    if (select.form) {
      select.form.addEventListener("reset", function () { setTimeout(syncLabel, 0); });
    }
    // 계정 목록처럼 페이지 로드 후 비동기로 fetch해 <option>을 통째로 갈아끼우는 select도 있다
    // (예: 보안그룹 관리 화면의 계정 선택) — enhance()는 스크립트 로드 시점에 한 번만 돌아서
    // 그 뒤에 innerHTML로 바뀐 옵션은 네이티브 select엔 반영돼도 커스텀 메뉴/라벨은 그대로 옛
    // 값에 멈춰 있었다(2026-09-17 실사용 중 "불러오는 중…"에 멈춰 보이는 문제로 발견). 옵션
    // 목록(childList) 변경을 감지해 메뉴/라벨을 다시 그린다.
    new MutationObserver(function () {
      buildItems();
      syncLabel();
    }).observe(select, { childList: true });
    syncLabel();
  }

  function closeAll() {
    document.querySelectorAll(".mc-dd__menu").forEach(function (m) { m.classList.add("hidden"); });
  }
  document.addEventListener("click", function (e) { if (!e.target.closest(".mc-dd")) closeAll(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeAll(); });

  document.querySelectorAll("select").forEach(enhance);

  // 페이지 로드 이후 fetch 응답으로 <select>를 통째로 새로 만들어 붙이는 화면(비용 화면의
  // 필터 등)을 위해 enhance()를 외부에 연다 — 위 querySelectorAll은 스크립트 실행 시점의
  // DOM만 보므로 그 뒤에 생긴 select는 저절로 꾸며지지 않는다. closeAll도 같이 연다 — 커스텀
  // 드롭다운(체크박스형 다중 선택 등)을 열 때 다른 드롭다운을 먼저 닫는 용도로 재사용한다.
  window.MCDropdown = { enhance: enhance, closeAll: closeAll };
})();
