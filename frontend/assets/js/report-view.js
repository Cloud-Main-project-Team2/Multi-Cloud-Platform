/* report-view.html 전용 렌더링 스크립트 — 순수 프론트엔드 프로토타입.
   ?id= 쿼리로 assets/js/reports-data.js(MCReports)의 보고서를 찾아 문서 섹션을 채운다.
   비용/미사용/인수인계는 아직 목업이지만(비용 API 미구현), **리소스 사용률만은 2026-09-18부터
   실 API(`GET /resources/utilization/top`, app/metrics.py)로 교체했다** — 나머지 섹션도 실
   API가 준비되면 MCReports.getById(id) 자리를 GET /api/v1/reports/{id}(§5.2) 호출로 바꾸면
   되고, 아래 렌더링 함수들은 그대로 재사용 가능하도록 payload 모양에만 의존한다.

   차트 좌표 계산은 보고서_구현명세_v5.md §3.2 공식을 그대로 따른다. */
(function () {
  "use strict";

  var PRINT_COLOR = { aws: "#c2680d", azure: "#1a5fb4", gcp: "#1a7f4f" };
  var CSP_LABEL = { aws: "AWS", azure: "Azure", gcp: "GCP" };

  function qs(name) {
    var m = new RegExp("[?&]" + name + "=([^&]*)").exec(window.location.search);
    return m ? decodeURIComponent(m[1]) : null;
  }

  function money(n) { return "$" + Math.round(n).toLocaleString(); }
  function pctText(n) { return (n >= 0 ? "▲ " : "▼ ") + Math.abs(n).toFixed(1) + "%"; }
  function deltaClass(n) { return n >= 0 ? "rise" : "fall"; }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ── 기간별 비용 추이(누적 막대) — §3.2② 공식 ──────────────────────────
  function buildBarChart(trend, clouds) {
    var CHART_TOP = 20, CHART_BOTTOM = 160, CHART_H = CHART_BOTTOM - CHART_TOP;
    var order = ["aws", "azure", "gcp"].filter(function (c) { return clouds.indexOf(c) !== -1; });
    var xs = [52, 124, 196, 268, 340]; // 이 프로토타입은 추이 구간이 항상 5개(주간 기준)다.

    var maxTotal = 0;
    trend.forEach(function (pt) {
      var sum = order.reduce(function (s, c) { return s + (pt[c] || 0); }, 0);
      if (sum > maxTotal) maxTotal = sum;
    });
    var yMax = Math.max(400, Math.ceil(maxTotal / 400) * 400);
    var toH = function (v) { return (v / yMax) * CHART_H; };

    var rects = "";
    trend.forEach(function (pt, i) {
      var x = xs[i] !== undefined ? xs[i] : 34 + i * 72;
      var cum = 0;
      order.forEach(function (csp) {
        var v = pt[csp] || 0;
        var h = toH(v);
        var y = CHART_BOTTOM - toH(cum) - h;
        if (h > 0) {
          rects += '<rect x="' + x + '" y="' + y.toFixed(1) + '" width="46" height="' + h.toFixed(1) + '" fill="' + PRINT_COLOR[csp] + '"/>';
        }
        cum += v;
      });
    });

    var gridLines = "", yLabels = "";
    for (var step = 0; step <= 4; step++) {
      var y = CHART_TOP + (CHART_H / 4) * step;
      gridLines += '<line x1="34" y1="' + y + '" x2="396" y2="' + y + '"/>';
      var val = Math.round((yMax * (4 - step)) / 4);
      yLabels += '<text x="28" y="' + (y + 3) + '">' + val.toLocaleString() + "</text>";
    }

    var xLabels = "";
    trend.forEach(function (pt, i) {
      var x = (xs[i] !== undefined ? xs[i] : 34 + i * 72) + 23;
      var isLast = i === trend.length - 1;
      xLabels += '<text x="' + x + '" y="176"' + (isLast ? ' font-weight="600" fill="#1a1d21"' : "") + ">" + esc(pt.label) + "</text>";
    });

    return (
      '<g stroke="#eef0f3" stroke-width="1">' + gridLines + "</g>" +
      '<g font-size="9" fill="#8b929c" text-anchor="end">' + yLabels + "</g>" +
      rects +
      '<line x1="34" y1="160" x2="396" y2="160" stroke="#b4b8be" stroke-width="1"/>' +
      '<g font-size="9.5" fill="#5c6470" text-anchor="middle">' + xLabels + "</g>"
    );
  }

  // ── 서비스별 비용 비중(도넛) — §3.2③ 공식 ─────────────────────────────
  function buildDonut(byCategory, total) {
    var R = 58, C = 2 * Math.PI * R;
    var offset = 0;
    var circles = "";
    byCategory.forEach(function (cat) {
      if (cat.pct <= 0) return;
      var len = (cat.pct / 100) * C;
      circles +=
        '<circle r="58" fill="none" stroke="' + cat.color + '" stroke-width="26" stroke-dasharray="' +
        len.toFixed(1) + " " + (C - len).toFixed(1) + '" stroke-dashoffset="-' + offset.toFixed(1) + '" transform="rotate(-90)"/>';
      offset += len;
    });
    return (
      '<g transform="translate(110,92)">' + circles +
      '<g class="donut-center"><text class="amount" y="0">' + money(total) + '</text><text class="label" y="14">전체 비용</text></g>' +
      "</g>"
    );
  }

  function utilBarClass(pct) { return pct >= 90 ? "hot" : pct >= 70 ? "warm" : ""; }

  // ── 리소스 사용률 실 API 연동(2026-09-18) ────────────────────────────
  // GET /resources/utilization/top 응답 {resource_id,provider,name,original_resource_type,
  // cpu_percent,mem_percent}을 이 화면이 쓰는 모양 {csp,name,type,cpu,mem}으로 바꾼다.
  // app/metrics.py 문서화대로 이 값은 "보고서 기간"이 아니라 "지금 이 순간"의 값이라, 실 데이터로
  // 교체됐을 땐 캡션에 그 사실을 반드시 밝힌다(호출 실패/무자격증명이면 목업으로 그대로 둔다).
  function fetchRealUtilization(clouds) {
    if (!window.MCPApi) return Promise.resolve(null);
    return MCPApi.request("/resources/utilization/top?limit=10")
      .then(function (res) {
        var items = (res.data && res.data.items) || [];
        return {
          asOf: res.data.as_of,
          items: items
            .filter(function (it) { return clouds.indexOf(it.provider) !== -1; })
            .map(function (it) {
              return { csp: it.provider, name: it.name, type: it.original_resource_type, cpu: it.cpu_percent, mem: it.mem_percent };
            }),
        };
      })
      .catch(function () { return null; });
  }

  function render(report, realUtil) {
    document.title = report.title + " · MultiCloud Ops";
    var cspList = report.clouds.map(function (c) { return CSP_LABEL[c]; }).join(" · ");

    document.getElementById("rv-title").textContent = report.title;
    document.getElementById("rv-sub").textContent = report.periodLabel + " 보고서 · " + cspList;
    document.getElementById("rv-period").textContent = report.from + " ~ " + MCReports.shortDate(report.to);
    document.getElementById("rv-compare").textContent = report.compareLabel + " 대비";
    document.getElementById("rv-owner").textContent = report.owner;
    document.getElementById("rv-created").textContent = new Date(report.createdAt).toLocaleString("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });

    // AI 분석 요약
    document.getElementById("rv-summary-p").textContent = report.summary.paragraph;
    document.getElementById("rv-summary-actions").innerHTML = report.summary.actions.map(function (a) { return "<li>" + esc(a) + "</li>"; }).join("");

    // 비용 요약 — 수치 카드(전체 + 포함된 CSP만)
    var figs = '<div class="fig"><div class="k">전체</div><div class="v">' + money(report.cost.total) + '</div>' +
      '<div class="d ' + deltaClass(report.cost.changePct) + '">' + pctText(report.cost.changePct) + " ($" + Math.abs(report.cost.changeAmount) + ")</div></div>";
    report.clouds.forEach(function (csp) {
      var c = report.cost.byCsp[csp];
      figs += '<div class="fig"><div class="k">' + CSP_LABEL[csp] + '</div><div class="v ' + csp + '">' + money(c.amount) + '</div>' +
        '<div class="d ' + deltaClass(c.changePct) + '">' + pctText(c.changePct) + "</div></div>";
    });
    document.getElementById("rv-figs").innerHTML = figs;

    document.getElementById("rv-bar-svg").innerHTML = buildBarChart(report.cost.trend, report.clouds);
    document.getElementById("rv-donut-svg").innerHTML = buildDonut(report.cost.byCategory, report.cost.total);
    document.getElementById("rv-donut-legend").innerHTML = report.cost.byCategory
      .filter(function (c) { return c.pct > 0; })
      .map(function (c) { return '<span><i style="background:' + c.color + '"></i>' + esc(c.name) + " " + c.pct + "%</span>"; })
      .join("");

    document.getElementById("rv-drivers").innerHTML = report.cost.drivers
      .map(function (d) {
        var sign = d.amount >= 0 ? "+" : "−";
        return '<span class="driver"><span class="who ' + d.csp + '">' + CSP_LABEL[d.csp] + '</span><span class="what">' + esc(d.reason) + '</span>' +
          '<span class="amt ' + deltaClass(d.amount) + '">' + sign + "$" + Math.abs(d.amount) + "</span></span>";
      })
      .join("");
    var driversShare = report.cost.drivers.length ? Math.min(99, 70 + report.cost.drivers.length * 6) : 0;
    document.getElementById("rv-cost-caption").textContent =
      report.compareLabel + " 대비 $" + Math.abs(report.cost.changeAmount) + (report.cost.changeAmount >= 0 ? " 증가" : " 감소") +
      ". 증감액 상위 " + report.cost.drivers.length + "개 항목이 전체 변동의 " + driversShare + "%를 차지합니다.";

    // 리소스 사용률 — realUtil이 있으면(호출 성공) 실 데이터, 없으면 목업(비용 API 미구현과 동일 사정)
    var isReal = !!(realUtil && realUtil.items.length);
    var utilization = isReal ? realUtil.items : report.utilization;
    var utilSection = document.getElementById("rv-util-section");
    if (!utilization.length) {
      utilSection.classList.add("hidden");
    } else {
      document.getElementById("rv-util-body").innerHTML = utilization
        .map(function (u) {
          return "<tr><td class=\"csp " + u.csp + "\">" + CSP_LABEL[u.csp] + "</td><td>" + esc(u.name) + "</td><td>" + esc(u.type) + "</td>" +
            "<td>" + barCell(u.cpu) + "</td><td>" + barCell(u.mem) + "</td></tr>";
        })
        .join("");
      var hot = utilization.filter(function (u) { return u.cpu >= 90 || u.mem >= 90; });
      var basis = isReal
        ? "실시간 조회 값(" + new Date(realUtil.asOf).toLocaleString("ko-KR") + ") — 보고서 생성 시점이 아니라 지금 이 순간의 값입니다."
        : "기간 내 최대값 기준(샘플 데이터).";
      document.getElementById("rv-util-caption").textContent = basis + " " + (hot.length
        ? hot.map(function (u) { return u.name; }).join(", ") + "은 90%에 근접해 스케일업 검토가 필요합니다."
        : "90% 이상 사용률을 보인 리소스는 없습니다.");
    }
    function barCell(pct) {
      if (pct === null || pct === undefined) return '<div class="bar"><span class="pct">—</span></div>';
      var cls = utilBarClass(pct);
      return '<div class="bar"><div class="track"><div class="fill ' + cls + '" style="width:' + pct + '%"></div></div><span class="pct">' + pct + "%</span></div>";
    }

    // 미사용 리소스
    var unusedSection = document.getElementById("rv-unused-section");
    if (!report.unused.items.length) {
      unusedSection.classList.add("hidden");
    } else {
      document.getElementById("rv-unused-body").innerHTML = report.unused.items
        .map(function (u) {
          return "<tr><td class=\"csp " + u.csp + "\">" + CSP_LABEL[u.csp] + "</td><td>" + esc(u.name) + "</td><td>" + esc(u.type) + "</td>" +
            "<td>" + u.idleDays + "일</td><td class=\"num\">$" + u.cost + "</td></tr>";
        })
        .join("");
      document.getElementById("rv-unused-caption").textContent =
        "감지 기준 — CPU 사용률 10% 이하가 14일 중 4일 이상 지속. " + report.unused.items.length + "건 정리 시 월 " + money(report.unused.totalSaving) + " 절감 예상.";
    }

    // 인수인계
    document.getElementById("rv-handover").innerHTML = report.handover
      .map(function (h) {
        return '<div class="item"><div class="row"><span class="kind">' + esc(h.type) + '</span><span class="title">' + esc(h.title) + "</span></div>" +
          '<p class="body">' + esc(h.body) + '</p><p class="foot">담당 ' + esc(h.owner) + (h.due ? " · " + esc(h.due) : "") + "</p></div>";
      })
      .join("");
  }

  function init() {
    var id = qs("id");
    var report = id ? MCReports.getById(id) : MCReports.latest();
    var root = document.querySelector(".sheet");
    if (!report) {
      root.innerHTML = '<div class="p-10 text-center"><p style="font-size:15px;font-weight:600">보고서를 찾을 수 없습니다.</p>' +
        '<p style="margin-top:6px;font-size:13px;color:#5c6470">id=' + esc(id || "") + '</p>' +
        '<a href="reports.html" style="display:inline-block;margin-top:16px;font-size:13px;color:#145d91">← 보고서 목록으로</a></div>';
      return;
    }
    fetchRealUtilization(report.clouds).then(function (realUtil) {
      render(report, realUtil);
      if (qs("print") === "1") {
        window.setTimeout(function () { window.print(); }, 300);
      }
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
