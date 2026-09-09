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
    btn.innerHTML = '<span class="mc-dd__label truncate"></span>' + chevron();
    wrap.appendChild(btn);

    var menu = document.createElement("div");
    menu.className = "mc-dd__menu hidden absolute left-0 top-full z-50 mt-1 min-w-full max-h-64 overflow-auto rounded-xl border border-border bg-surface py-1 shadow-lg";
    wrap.appendChild(menu);

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
    select.addEventListener("change", syncLabel);     // 프로그래밍적 변경(초기화 등)도 라벨 동기화
    syncLabel();
  }

  function closeAll() {
    document.querySelectorAll(".mc-dd__menu").forEach(function (m) { m.classList.add("hidden"); });
  }
  document.addEventListener("click", function (e) { if (!e.target.closest(".mc-dd")) closeAll(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeAll(); });

  document.querySelectorAll("select").forEach(enhance);
})();
