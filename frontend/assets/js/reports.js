/* 보고서 작성 페이지(reports.html) 전용 스크립트.
   비용/미사용/인수인계는 아직 assets/js/reports-data.js(MCReports)의 목업이다(비용 API 미구현) —
   보고서_구현명세_v5.md §9 구현 순서의 "7. 프론트엔드 드롭다운 + 렌더링"을 비용 API 연동 전에
   먼저 완성해 두는 단계. "사용률 90% 초과" 카드만 2026-09-18부터 실 API
   (GET /resources/utilization/top)로 교체했다. */
(function () {
  "use strict";
  if (!window.MCReports) return;

  var PROVIDER_LABEL = { aws: "AWS", azure: "Azure", gcp: "GCP" };
  // 인벤토리(inventory.js)와 같은 CSP 로고를 쓴다 — 색 동그라미 대신 실제 로고로 통일
  // (2026-09-18 실사용자 요청).
  var PROVIDER_ICON = { aws: "assets/imgs/aws.png", azure: "assets/imgs/azure.png", gcp: "assets/imgs/gcp.png" };

  function fmtMoney(n) { return "$" + Math.round(n).toLocaleString(); }
  function fmtPct(n) {
    var s = (n >= 0 ? "▲ " : "▼ ") + Math.abs(n).toFixed(1) + "%";
    return s;
  }
  function fmtDateRange(from, to) {
    return from + " ~ " + MCReports.shortDate(to);
  }

  function dotsHtml(clouds) {
    return clouds
      .map(function (c) {
        return '<img src="' + PROVIDER_ICON[c] + '" alt="' + PROVIDER_LABEL[c] + '" title="' + PROVIDER_LABEL[c] +
          '" class="inline-block h-4 w-4 object-contain align-middle" />';
      })
      .join(" ");
  }

  // ── 최근 보고서 요약 카드 (최신 보고서 = MCReports.latest()) ──────────────
  function renderSummary() {
    var r = MCReports.latest();
    var periodEl = document.getElementById("report-summary-period");
    if (periodEl) periodEl.textContent = fmtDateRange(r.from, r.to) + " · " + r.periodLabel;

    var costEl = document.getElementById("report-sum-cost");
    var costDeltaEl = document.getElementById("report-sum-cost-delta");
    if (costEl) costEl.textContent = fmtMoney(r.cost.total);
    if (costDeltaEl) {
      costDeltaEl.textContent = fmtPct(r.cost.changePct) + " " + r.compareLabel + " 대비";
      costDeltaEl.className = "mt-1 text-xs " + (r.cost.changePct >= 0 ? "text-red-600" : "text-emerald-600");
    }

    var unusedEl = document.getElementById("report-sum-unused");
    var unusedSubEl = document.getElementById("report-sum-unused-sub");
    if (unusedEl) unusedEl.textContent = r.unused.items.length + "건";
    if (unusedSubEl) unusedSubEl.textContent = "월 " + fmtMoney(r.unused.totalSaving) + " 절감 가능";

    // "사용률 90% 초과" 카드만 실 API(2026-09-18)로 교체 — 나머지(비용·미사용·인수인계)는
    // 비용 API가 아직 없어 계속 목업이다. 호출 실패/무자격증명이면 목업으로 그대로 둔다.
    var hotEl = document.getElementById("report-sum-hot");
    var hotSubEl = document.getElementById("report-sum-hot-sub");
    function renderHotFrom(rows) {
      var hotRows = rows.filter(function (u) { return u.cpu >= 90 || u.mem >= 90; });
      if (hotEl) hotEl.textContent = hotRows.length + "건";
      if (hotSubEl) hotSubEl.textContent = hotRows.length ? hotRows.map(function (u) { return u.name; }).join(", ") : "해당 없음";
    }
    renderHotFrom(r.utilization);
    if (window.MCPApi) {
      MCPApi.request("/resources/utilization/top?limit=10").then(function (res) {
        // MCPApi.request()는 응답 envelope의 data를 이미 벗겨서 돌려준다(api.js) — res.data를
        // 또 한 번 벗기면 항상 undefined가 되어 조용히 목업에서 갱신되지 않는다(실제로 겪은 버그).
        var items = (res && res.items) || [];
        renderHotFrom(items.map(function (it) { return { cpu: it.cpu_percent, mem: it.mem_percent, name: it.name }; }));
      }).catch(function () {});
    }

    var expiring = r.handover.filter(function (h) { return h.type === "만료 예정"; }).length;
    var handoverEl = document.getElementById("report-sum-handover");
    var handoverSubEl = document.getElementById("report-sum-handover-sub");
    if (handoverEl) handoverEl.textContent = r.handover.length + "건";
    if (handoverSubEl) handoverSubEl.textContent = "만료 예정 " + expiring + "건 포함";
  }

  // ── 생성 이력 표 ──────────────────────────────────────────────────────
  function renderHistory() {
    var tbody = document.getElementById("report-history-body");
    if (!tbody) return;
    if (!MCReports.list.length) {
      tbody.innerHTML = '<tr><td class="py-3 text-muted-foreground" colspan="4">아직 생성된 보고서가 없습니다.</td></tr>';
      return;
    }
    tbody.innerHTML = MCReports.list
      .map(function (r) {
        return (
          '<tr class="border-b border-border last:border-0">' +
          '<td class="py-2.5 pr-4">' + fmtDateRange(r.from, r.to) + "</td>" +
          '<td class="py-2.5 pr-4"><span class="rounded-full border border-border px-2 py-0.5 text-xs">' + r.periodLabel + "</span></td>" +
          '<td class="py-2.5 pr-4">' + dotsHtml(r.clouds) + "</td>" +
          '<td class="py-2.5 text-right">' +
          '<a href="report-view.html?id=' + encodeURIComponent(r.id) + '" target="_blank" rel="noopener" class="text-primary hover:underline">열기</a>' +
          '<span class="mx-1.5 text-muted-foreground">·</span>' +
          '<a href="report-view.html?id=' + encodeURIComponent(r.id) + '&print=1" target="_blank" rel="noopener" class="text-primary hover:underline">인쇄</a>' +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  // ── ① 클라우드 선택 칩 (프로비저닝 화면과 동일한 select-card 패턴) ────────
  function selectedClouds() {
    return Array.prototype.slice
      .call(document.querySelectorAll("[data-report-cloud]:checked"))
      .map(function (cb) { return cb.getAttribute("data-report-cloud"); });
  }

  function syncCloudChips() {
    document.querySelectorAll("[data-report-cloud]").forEach(function (cb) {
      var label = cb.closest("[data-select-card]");
      if (!label) return;
      label.classList.toggle("border-primary", cb.checked);
      label.classList.toggle("bg-muted", cb.checked);
      label.classList.toggle("border-border", !cb.checked);
    });
    var count = selectedClouds().length;
    var countEl = document.getElementById("report-cloud-count");
    if (countEl) countEl.textContent = String(count);
    var btn = document.getElementById("report-generate-btn");
    if (btn) btn.disabled = count === 0;
  }

  // ── 보고서 생성 흐름 (§4.2: 로딩 표시 → 렌더링, 버튼은 비활성화하지 않음) ──
  function wireGenerate() {
    var btn = document.getElementById("report-generate-btn");
    var status = document.getElementById("report-generate-status");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var clouds = selectedClouds();
      if (!clouds.length) {
        if (status) status.textContent = "클라우드를 하나 이상 선택하세요.";
        return;
      }
      var period = document.getElementById("report-period").value;
      if (status) status.textContent = "데이터 수집 중… AI 요약 생성 중…";
      btn.classList.add("opacity-70");

      // 팝업 차단 회피 — 클릭 이벤트 안에서 동기적으로 빈 탭을 먼저 열고, 데이터가 준비되면 그 탭을 이동시킨다.
      var newTab = window.open("", "_blank");

      window.setTimeout(function () {
        var report = MCReports.createReport(period, clouds);
        renderSummary();
        renderHistory();
        btn.classList.remove("opacity-70");
        if (status) status.textContent = "생성 완료 — 새 탭에서 열립니다.";
        var url = "report-view.html?id=" + encodeURIComponent(report.id);
        if (newTab && !newTab.closed) newTab.location.href = url;
        else window.open(url, "_blank", "noopener");
        window.setTimeout(function () {
          if (status) status.textContent = "";
        }, 4000);
      }, 1200);
    });
  }

  // ── 탭 전환 (생성 이력 / 정기 발송) ────────────────────────────────────
  function wireTabs() {
    var tabs = document.querySelectorAll("[data-report-tab]");
    var panels = { history: document.getElementById("report-panel-history"), schedule: document.getElementById("report-panel-schedule") };
    function activate(name) {
      tabs.forEach(function (t) {
        var on = t.getAttribute("data-report-tab") === name;
        t.classList.toggle("border-primary", on);
        t.classList.toggle("text-primary", on);
        t.classList.toggle("border-transparent", !on);
        t.classList.toggle("text-muted-foreground", !on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
      Object.keys(panels).forEach(function (k) {
        if (panels[k]) panels[k].classList.toggle("hidden", k !== name);
      });
    }
    tabs.forEach(function (t) {
      t.addEventListener("click", function () { activate(t.getAttribute("data-report-tab")); });
    });
    activate("history");
  }

  // ── 다운로드 방식 설정 (§4.3) ────────────────────────────────────────────
  // 웹 다운로드는 "정기 발송" 개념이 아니다 — 주기 없이 그 자리에서 즉시 최신 보고서를 받는다.
  // 메일 전송은 GET/PUT/DELETE /reports/settings로 실제 서버에 저장된다(2026-09-19) —
  // app/report_scheduler.py가 이 값을 읽어 정말로 메일을 보낸다(더는 localStorage 데모가 아님).
  function wireSettings() {
    var deliveryRadios = document.querySelectorAll('input[name="report-delivery"]');
    var webDownloadField = document.getElementById("report-web-download-field");
    var webDownloadBtn = document.getElementById("report-web-download-btn");
    var emailScheduleFields = document.getElementById("report-email-schedule-fields");
    var emailInput = document.getElementById("report-email-input");
    var periodSelect = document.getElementById("report-schedule-period");
    var saveBtn = document.getElementById("report-settings-save");
    var disableBtn = document.getElementById("report-settings-disable");
    var savedNote = document.getElementById("report-settings-saved");
    var statusBox = document.getElementById("report-email-status");
    var timingNote = document.getElementById("report-send-timing");
    if (!webDownloadBtn) return;

    function syncFieldVisibility() {
      var method = document.querySelector('input[name="report-delivery"]:checked');
      var isEmail = method && method.value === "EMAIL";
      if (webDownloadField) webDownloadField.classList.toggle("hidden", isEmail);
      if (emailScheduleFields) emailScheduleFields.classList.toggle("hidden", !isEmail);
      deliveryRadios.forEach(function (r) {
        var label = r.closest("[data-select-card]");
        if (!label) return;
        label.classList.toggle("border-primary", r.checked);
        label.classList.toggle("bg-muted", r.checked);
        label.classList.toggle("border-border", !r.checked);
      });
    }

    // 지금 실제로 서버에 저장돼 있는 상태(폼에서 만지고 있는 값이 아니라)를 항상 보여준다 —
    // "설정했는지 안 했는지 알 수가 없다"는 문제를 고치기 위함(2026-09-19).
    function applySettings(s) {
      var active = s && s.delivery_method === "EMAIL" && s.email;
      deliveryRadios.forEach(function (r) { r.checked = r.value === (active ? "EMAIL" : "WEB"); });
      if (emailInput && s && s.email) emailInput.value = s.email;
      if (periodSelect && s && s.period_type) periodSelect.value = s.period_type;
      syncFieldVisibility();
      if (statusBox) {
        statusBox.classList.toggle("hidden", !active);
        if (active) {
          var label = (window.MCReports && MCReports.PERIOD_LABEL[s.period_type]) || s.period_type;
          statusBox.textContent = "📧 현재 " + s.email + "로 " + label + " 메일 발송이 설정되어 있습니다.";
        }
      }
      if (disableBtn) disableBtn.classList.toggle("hidden", !active);
      // 저장한다고 바로 오는 게 아니라 매일 이 시각에만 발송 대상을 확인한다 — 오해하기 쉬워서
      // 항상 보여준다(2026-09-19 확인).
      if (timingNote && s && typeof s.send_hour_kst === "number") {
        var h = s.send_hour_kst;
        var ampm = h < 12 ? "오전" : "오후";
        var h12 = h % 12 === 0 ? 12 : h % 12;
        timingNote.textContent = "매일 한국 시간 " + ampm + " " + h12 + "시경에 발송 대상을 확인합니다 — 저장 즉시 보내지지 않습니다.";
      }
    }

    if (window.MCPApi) {
      MCPApi.request("/reports/settings").then(applySettings).catch(function () {});
    }
    syncFieldVisibility();

    deliveryRadios.forEach(function (r) { r.addEventListener("change", syncFieldVisibility); });

    // 웹 다운로드 — 저장할 설정이 없으니 클릭하면 즉시 최신 보고서를 새 탭에서 인쇄/PDF 저장
    // 화면으로 연다(생성 이력 탭의 "인쇄" 링크와 동일한 방식, report-view.html?print=1).
    webDownloadBtn.addEventListener("click", function () {
      var r = MCReports.latest();
      window.open("report-view.html?id=" + encodeURIComponent(r.id) + "&print=1", "_blank", "noopener");
    });

    if (saveBtn) {
      saveBtn.addEventListener("click", function () {
        var email = emailInput ? emailInput.value.trim() : "";
        if (!email) {
          if (savedNote) { savedNote.textContent = "메일 전송을 선택하면 수신 주소가 필요합니다."; savedNote.className = "text-xs text-red-600"; }
          return;
        }
        if (!window.MCPApi) return;
        MCPApi.request("/reports/settings", {
          method: "PUT",
          body: { delivery_method: "EMAIL", email: email, period_type: periodSelect ? periodSelect.value : "WEEKLY" },
        }).then(function (s) {
          applySettings(s);
          if (savedNote) { savedNote.textContent = "저장되었습니다."; savedNote.className = "text-xs text-muted-foreground"; }
        }).catch(function (err) {
          if (savedNote) { savedNote.textContent = "저장하지 못했습니다: " + (err.message || "알 수 없는 오류"); savedNote.className = "text-xs text-red-600"; }
        });
      });
    }

    // 발송 해제 — 서버에 저장된 설정 자체를 지운다("웹 다운로드"로 되돌아간다).
    if (disableBtn) {
      disableBtn.addEventListener("click", function () {
        if (!window.MCPApi) return;
        MCPApi.request("/reports/settings", { method: "DELETE" }).then(function (s) {
          applySettings(s);
          if (savedNote) { savedNote.textContent = "발송이 해제되었습니다."; savedNote.className = "text-xs text-muted-foreground"; }
        }).catch(function (err) {
          if (savedNote) { savedNote.textContent = "해제하지 못했습니다: " + (err.message || "알 수 없는 오류"); savedNote.className = "text-xs text-red-600"; }
        });
      });
    }
  }

  function init() {
    document.querySelectorAll("[data-report-cloud]").forEach(function (cb) {
      cb.addEventListener("change", syncCloudChips);
    });
    syncCloudChips();
    renderSummary();
    renderHistory();
    wireGenerate();
    wireTabs();
    wireSettings();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
