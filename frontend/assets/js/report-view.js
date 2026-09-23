/* report-view.html 전용 렌더링 스크립트 — 순수 프론트엔드 프로토타입.
   ?id= 쿼리로 assets/js/reports-data.js(MCReports)의 보고서를 찾아 문서 섹션을 채운다.
   인수인계만 아직 목업이고(재사용할 공통 계산 함수가 없음), 비용 요약(app/report_cost.py,
   2026-09-19)·AI 분석 요약(app/report_summary.py, 2026-09-23)은 생성 시점에 고정 계산해
   저장한 실데이터이며, **리소스 사용률(2026-09-18)과 미사용 리소스(2026-09-19)는 실 API**
   (`GET /resources/utilization/top`, `GET /resources/unused/top`, app/metrics.py)로
   교체했다 — 미사용 리소스는 미연결 디스크(AWS EBS)와 유휴 인스턴스(지금 CPU 10% 미만)만
   감지한다(미연결 공인 IP·Azure/GCP 디스크는 discover_resources 확장이 먼저 필요해 범위 밖).
   인수인계도 실 API가 준비되면 MCReports.getById(id) 자리를 GET /api/v1/reports/{id}(§5.2)
   호출로 바꾸면 되고, 아래 렌더링 함수들은 그대로 재사용 가능하도록 payload 모양에만 의존한다.

   차트 좌표 계산은 보고서_구현명세_v5.md §3.2 공식을 그대로 따른다. */
(function () {
  "use strict";

  var PRINT_COLOR = { aws: "#c2680d", azure: "#1a5fb4", gcp: "#1a7f4f" };
  var CSP_LABEL = { aws: "AWS", azure: "Azure", gcp: "GCP" };

  function qs(name) {
    var m = new RegExp("[?&]" + name + "=([^&]*)").exec(window.location.search);
    return m ? decodeURIComponent(m[1]) : null;
  }

  // 미사용 리소스의 "월 예상 비용"은 정가표 추정치라 항상 USD다(app/pricing.py, 이번 세션
  // 앞부분에서 확인) — 그 자리에만 쓴다. 비용 요약 섹션은 계정마다 통화가 다를 수 있어(§7
  // "USD와 KRW는 분리해서 표시") money()가 아니라 moneyIn(currency, amount)을 쓴다.
  function money(n) { return "$" + Math.round(n).toLocaleString(); }
  function moneyIn(currency, amount) {
    var n = parseFloat(amount);
    if (isNaN(n)) return "—";
    var prefix = currency === "USD" ? "$" : currency === "KRW" ? "₩" : currency + " ";
    return prefix + Math.round(n).toLocaleString();
  }
  function pctText(n) { return (n >= 0 ? "▲ " : "▼ ") + Math.abs(n).toFixed(1) + "%"; }
  function deltaClass(n) { return n >= 0 ? "rise" : "fall"; }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // "보기 좋은" 축 눈금 상한 — 실제 비용은 목업과 달리 어떤 크기든 나올 수 있어(몇 센트~몇
  // 만 달러) 예전처럼 "400 단위로 올림" 같은 고정 스케일을 쓸 수 없다(2026-09-19, 비용
  // 요약 실 API 연동).
  function niceCeil(v) {
    if (!(v > 0)) return 1;
    var exp = Math.floor(Math.log(v) / Math.LN10);
    var base = Math.pow(10, exp);
    var frac = v / base;
    var niceFrac = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10;
    return niceFrac * base;
  }

  // ── 기간별 비용 추이(누적 막대) ────────────────────────────────────────
  // rows: [{label, periodStart, aws?, azure?, gcp?}, ...] — pivotTrend()가 만든다.
  // 실 데이터는 기간에 따라 구간 수가 5개가 아닐 수 있어(일간 보고서는 최대 31개 등) 폭·라벨
  // 스킵을 구간 수에 맞춰 계산한다(예전엔 항상 5개 고정이었다).
  function buildBarChart(rows, order) {
    var PLOT_LEFT = 34, PLOT_RIGHT = 396, CHART_TOP = 20, CHART_BOTTOM = 160, CHART_H = CHART_BOTTOM - CHART_TOP;
    var n = rows.length;
    if (!n) return "";
    var slot = (PLOT_RIGHT - PLOT_LEFT) / n;
    var barWidth = Math.max(3, Math.min(46, slot * 0.62));

    var maxTotal = 0;
    rows.forEach(function (pt) {
      var sum = order.reduce(function (s, c) { return s + (pt[c] || 0); }, 0);
      if (sum > maxTotal) maxTotal = sum;
    });
    var yMax = niceCeil(maxTotal);
    var toH = function (v) { return (v / yMax) * CHART_H; };

    var rects = "";
    rows.forEach(function (pt, i) {
      var x = PLOT_LEFT + slot * i + (slot - barWidth) / 2;
      var cum = 0;
      order.forEach(function (csp) {
        var v = pt[csp] || 0;
        var h = toH(v);
        var y = CHART_BOTTOM - toH(cum) - h;
        if (h > 0) {
          rects += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + barWidth.toFixed(1) + '" height="' + h.toFixed(1) + '" fill="' + PRINT_COLOR[csp] + '"/>';
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

    // 라벨이 6개를 넘으면 다 못 읽으니 일정 간격으로만 보여준다(마지막 구간은 항상 표시).
    var labelEvery = Math.max(1, Math.ceil(n / 6));
    var xLabels = "";
    rows.forEach(function (pt, i) {
      var isLast = i === n - 1;
      if (i % labelEvery !== 0 && !isLast) return;
      var x = PLOT_LEFT + slot * i + slot / 2;
      xLabels += '<text x="' + x.toFixed(1) + '" y="176"' + (isLast ? ' font-weight="600" fill="#1a1d21"' : "") + ">" + esc(pt.label) + "</text>";
    });

    return (
      '<g stroke="#eef0f3" stroke-width="1">' + gridLines + "</g>" +
      '<g font-size="9" fill="#8b929c" text-anchor="end">' + yLabels + "</g>" +
      rects +
      '<line x1="34" y1="160" x2="396" y2="160" stroke="#b4b8be" stroke-width="1"/>' +
      '<g font-size="9.5" fill="#5c6470" text-anchor="middle">' + xLabels + "</g>"
    );
  }

  // trend_provider.series([{key,label,points:[{periodStart,amount}]}])를 buildBarChart가
  // 쓰는 "구간별 행"(pivot) 모양으로 바꾼다 — 어떤 provider가 그 구간에 비용이 없으면(row가
  // 아예 없음, cost/query.py는 0을 채우지 않는다) 그 provider만 0으로 둔다. 이건 "그 구간
  // 전체가 미수집"과는 다른 뜻이다 — 특정 CSP가 그날 쓴 비용이 없다는 것은 실제 정보이지,
  // coverage 결측이 아니다(막대 그래프는 provider별 실제 지출을 쌓아 보여주는 용도라 0으로
  // 다뤄도 정확하다).
  function pivotTrend(series) {
    var labelSet = {};
    series.forEach(function (s) {
      s.points.forEach(function (p) { labelSet[p.period_start] = true; });
    });
    var starts = Object.keys(labelSet).sort();
    return starts.map(function (start) {
      var row = { label: start.slice(5), periodStart: start }; // "2026-09-13" → "09-13"
      series.forEach(function (s) {
        var match = s.points.filter(function (p) { return p.period_start === start; })[0];
        row[s.key] = match ? parseFloat(match.amount) : 0;
      });
      return row;
    });
  }

  var CATEGORY_COLOR = ["#3d6fa8", "#5c94c9", "#8fb9dd", "#b9d4e9", "#d9e5ef", "#c9cfd6"];

  // ── 카테고리별 비용 비중(도넛) ─────────────────────────────────────────
  // breakdown(dimension=category) 결과를 그대로 쓴다 — 비율은 API가 준 share_pct 그대로이고
  // (§1 "새로운 업무상 비율 산식을 만들지 마세요"), unallocated만 시각화를 위해 amount/total로
  // 조각 크기를 계산한다(API가 unallocated의 share_pct를 주지 않기 때문 — 차트 좌표 계산은
  // 보고서 재량, §1).
  function buildDonut(breakdown) {
    if (!breakdown) return { svg: "", legend: "" };
    var R = 58, C = 2 * Math.PI * R;
    var total = parseFloat(breakdown.total) || 0;
    var slices = breakdown.items.map(function (it) {
      return { label: it.label, pct: parseFloat(it.share_pct) || 0 };
    });
    if (breakdown.rest && parseFloat(breakdown.rest.share_pct) > 0) {
      slices.push({ label: breakdown.rest.label, pct: parseFloat(breakdown.rest.share_pct) });
    }
    if (breakdown.unallocated && total > 0 && parseFloat(breakdown.unallocated.amount) > 0) {
      slices.push({ label: "미분류", pct: (parseFloat(breakdown.unallocated.amount) / total) * 100 });
    }

    var offset = 0;
    var circles = "";
    slices.forEach(function (s, i) {
      if (s.pct <= 0) return;
      var len = (s.pct / 100) * C;
      circles +=
        '<circle r="58" fill="none" stroke="' + CATEGORY_COLOR[i % CATEGORY_COLOR.length] + '" stroke-width="26" stroke-dasharray="' +
        len.toFixed(1) + " " + (C - len).toFixed(1) + '" stroke-dashoffset="-' + offset.toFixed(1) + '" transform="rotate(-90)"/>';
      offset += len;
    });
    var svg = (
      '<g transform="translate(110,92)">' + circles +
      '<g class="donut-center"><text class="amount" y="0">' + esc(moneyIn(breakdown.currency, total)) + '</text><text class="label" y="14">전체 비용</text></g>' +
      "</g>"
    );
    var legend = slices
      .filter(function (s) { return s.pct > 0; })
      .map(function (s, i) { return '<span><i style="background:' + CATEGORY_COLOR[i % CATEGORY_COLOR.length] + '"></i>' + esc(s.label) + " " + s.pct.toFixed(1) + "%</span>"; })
      .join("");
    return { svg: svg, legend: legend };
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
        // MCPApi.request()는 응답 envelope의 data를 이미 벗겨서 돌려준다(api.js) — res.data를
        // 또 한 번 벗기면 항상 undefined가 되어 이 함수가 매번 null로 떨어지고 목업으로만
        // 폴백하고 있었다(실제로 겪은 버그).
        var items = (res && res.items) || [];
        return {
          asOf: res.as_of,
          items: items
            .filter(function (it) { return clouds.indexOf(it.provider) !== -1; })
            .map(function (it) {
              return { csp: it.provider, name: it.name, type: it.original_resource_type, cpu: it.cpu_percent, mem: it.mem_percent };
            }),
        };
      })
      .catch(function () { return null; });
  }

  // ── 미사용 리소스 실 API 연동(2026-09-19) ─────────────────────────────
  // GET /resources/unused/top 응답 {resource_id,provider,name,original_resource_type,reason,
  // idle_days,estimated_monthly_cost}을 이 화면이 쓰는 모양 {csp,name,type,idleDays,cost}으로
  // 바꾼다. app/metrics.py::get_unused_resources 문서화대로 미연결 디스크(AWS EBS만)와 유휴
  // 인스턴스(지금 이 순간 CPU 10% 미만)만 감지한다 — 미연결 공인 IP·Azure/GCP 디스크는 아직
  // 대상이 아니다. idle_days/estimated_monthly_cost는 모르면 null로 온다(지어내지 않음).
  function fetchRealUnused(clouds) {
    if (!window.MCPApi) return Promise.resolve(null);
    return MCPApi.request("/resources/unused/top?limit=10")
      .then(function (res) {
        var items = (res && res.items) || [];
        return {
          asOf: res.as_of,
          items: items
            .filter(function (it) { return clouds.indexOf(it.provider) !== -1; })
            .map(function (it) {
              return { csp: it.provider, name: it.name, type: it.original_resource_type, reason: it.reason, idleDays: it.idle_days, cost: it.estimated_monthly_cost };
            }),
        };
      })
      .catch(function () { return null; });
  }

  // ── 비용 요약 렌더링 ──────────────────────────────────────────────────
  // report.cost는 reports-data.js::mapCostSnapshot()이 이미 만들어 둔 모양이다 — 여기서는
  // 표시 순서·포맷만 결정한다(§1 "필드 이름 변경·표시 순서·차트 좌표 계산은 보고서가 수행
  // 가능", 새 합계·비율은 만들지 않음).
  function renderCostSection(report) {
    var figsEl = document.getElementById("rv-figs");
    var barSvgEl = document.getElementById("rv-bar-svg");
    var donutSvgEl = document.getElementById("rv-donut-svg");
    var donutLegendEl = document.getElementById("rv-donut-legend");
    var driversEl = document.getElementById("rv-drivers");
    var captionEl = document.getElementById("rv-cost-caption");

    var cost = report.cost;
    if (!cost) {
      // 생성 시점에 비용 계산 자체가 실패했다 — 0원·빈 목록으로 가리지 않는다(§9).
      figsEl.innerHTML = '<p class="caption">비용 정보를 불러오지 못했습니다. 같은 조건으로 다시 생성해 보세요.</p>';
      barSvgEl.innerHTML = ""; donutSvgEl.innerHTML = ""; donutLegendEl.innerHTML = ""; driversEl.innerHTML = "";
      captionEl.textContent = "—";
      return;
    }

    // 전체 카드 — 통화별로 따로 보여준다(§7 "USD와 KRW는 분리해서 표시, 마지막 통화로 합치지
    // 않는다"). 이 기간에 수집된 비용 자체가 없으면(정상 상태, 실패 아님) 그 사실을 그대로 밝힌다.
    var figs;
    if (!cost.totalsByCurrency.length) {
      figs = '<p class="caption">이 기간에 수집된 비용 데이터가 없습니다.</p>';
    } else {
      figs = cost.totalsByCurrency.map(function (t) {
        var badge = "";
        // changes()는 통화 하나만 골라 비교한다 — 그 통화와 일치할 때만 증감 배지를 붙인다
        // (다른 통화에 억지로 갖다붙이면 틀린 비교가 된다).
        if (cost.changes && cost.changes.comparable && cost.changes.currency === t.currency && cost.changes.totals.delta_pct !== null) {
          var deltaPct = parseFloat(cost.changes.totals.delta_pct);
          badge = '<div class="d ' + deltaClass(deltaPct) + '">' + pctText(deltaPct) + " " + report.compareLabel + " 대비</div>";
        }
        var estBadge = t.is_estimated ? '<div class="d" style="color:#8b929c">잠정치</div>' : "";
        return '<div class="fig"><div class="k">전체 (' + esc(t.currency) + ')</div><div class="v">' + esc(moneyIn(t.currency, t.amount)) + "</div>" + badge + estBadge + "</div>";
      }).join("");
    }

    if (cost.byProvider) {
      report.clouds.forEach(function (csp) {
        var item = cost.byProvider.items.filter(function (it) { return it.key === csp; })[0];
        if (!item) return; // 이 기간에 해당 CSP 비용이 없음 — 카드 자체를 만들지 않는다(0으로 지어내지 않음)
        figs += '<div class="fig"><div class="k">' + CSP_LABEL[csp] + '</div><div class="v ' + csp + '">' + esc(moneyIn(cost.byProvider.currency, item.amount)) + '</div>' +
          '<div class="d">비중 ' + esc(item.share_pct) + '%</div></div>';
      });
      if (cost.byProvider.excluded.length) {
        figs += '<p class="caption" style="width:100%">일부 계정(' + cost.byProvider.excluded.reduce(function (s, e) { return s + e.account_count; }, 0) +
          '개)은 통화가 달라 이 비교에서 제외됐습니다.</p>';
      }
    }
    figsEl.innerHTML = figs;

    if (cost.trend && cost.trend.series.length) {
      barSvgEl.innerHTML = buildBarChart(pivotTrend(cost.trend.series), report.clouds);
    } else {
      barSvgEl.innerHTML = "";
    }

    var donut = buildDonut(cost.byCategory);
    donutSvgEl.innerHTML = donut.svg;
    donutLegendEl.innerHTML = donut.legend;

    // 증감 상위 항목 — changes()는 service 단위라 CSP 배지를 붙이지 않는다(어느 CSP인지 이
    // 데이터만으로는 모른다 — 억지로 붙이면 잘못된 귀속이 된다, §5 "실제 원인을 단정하지 않음").
    // comparable=true인데 currency가 null인 경우가 있다 — 이번/이전 기간 둘 다 비용이 아예
    // 없으면(계정은 있지만 수집된 CloudAccountCost가 없음) 기간 길이는 같아 comparable이지만
    // 비교할 통화 자체가 없다(실 데이터로 확인함, GCP처럼 아직 비용이 없는 계정). 이 경우까지
    // "0 증가"로 보여주면 거짓 정보라 데이터 없음 분기로 보낸다.
    if (cost.changes && cost.changes.comparable && cost.changes.currency) {
      // new_items(이전 기간엔 아예 없던 항목)는 delta 필드가 없다 — current 전액이 그대로
      // 증가분이다. increases/decreases와 합쳐서 절대값 기준 상위만 추린다(실제로 이전 기간
      // 데이터가 아예 없을 때 — 방금 실 데이터로 확인함 — increases/decreases가 비고 전부
      // new_items로만 잡히는 경우가 있어 이걸 빠뜨리면 "증감 상위"가 항상 빈 채로 나온다).
      var movers = cost.changes.increases.map(function (d) { return { label: d.label, amount: parseFloat(d.delta), isNew: false }; })
        .concat(cost.changes.decreases.map(function (d) { return { label: d.label, amount: parseFloat(d.delta), isNew: false }; }))
        .concat(cost.changes.newItems.map(function (d) { return { label: d.label, amount: parseFloat(d.current), isNew: true }; }));
      movers.sort(function (a, b) { return Math.abs(b.amount) - Math.abs(a.amount); });
      movers = movers.slice(0, 6);
      driversEl.innerHTML = movers.map(function (d) {
        var sign = d.amount >= 0 ? "+" : "−";
        var tag = d.isNew ? '<span class="who" style="background:#eef0f3;color:#5c6470">신규</span>' : "";
        return '<span class="driver">' + tag + '<span class="what">' + esc(d.label) + '</span>' +
          '<span class="amt ' + deltaClass(d.amount) + '">' + sign + esc(moneyIn(cost.changes.currency, Math.abs(d.amount))) + "</span></span>";
      }).join("");
      var totals = cost.changes.totals;
      var deltaAmt = parseFloat(totals.delta);
      captionEl.textContent = report.compareLabel + " 대비 " + moneyIn(cost.changes.currency, Math.abs(deltaAmt)) +
        (deltaAmt >= 0 ? " 증가" : " 감소") +
        (totals.delta_pct !== null ? " (" + pctText(parseFloat(totals.delta_pct)) + ")" : "") + ".";
    } else {
      driversEl.innerHTML = "";
      if (!cost.totalsByCurrency.length) {
        captionEl.textContent = "이 기간에 수집된 비용 데이터가 없어 비교할 수 없습니다.";
      } else if (cost.changes && !cost.changes.comparable) {
        captionEl.textContent = report.compareLabel + "과 기간 길이가 달라 증감을 비교할 수 없습니다.";
      } else {
        captionEl.textContent = "이전 기간과 비교할 데이터가 없습니다.";
      }
    }
  }

  function render(report, realUtil, realUnused) {
    document.title = report.title + " · MultiCloud Ops";
    var cspList = report.clouds.map(function (c) { return CSP_LABEL[c]; }).join(" · ");

    document.getElementById("rv-title").textContent = report.title;
    document.getElementById("rv-sub").textContent = report.periodLabel + " 보고서 · " + cspList;
    document.getElementById("rv-period").textContent = report.from + " ~ " + MCReports.shortDate(report.to);
    document.getElementById("rv-compare").textContent = report.compareLabel + " 대비";
    document.getElementById("rv-owner").textContent = report.owner;
    document.getElementById("rv-created").textContent = new Date(report.createdAt).toLocaleString("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });

    // AI 분석 요약 — app/report_summary.py가 생성 시점에 비용 스냅샷·실시간 사용률/미사용
    // 리소스를 근거로 LLM에게 만들게 한 값(report.summary)을 그대로 옮겨 담는다(2026-09-23,
    // 사용자 확인 후 실데이터 연동). null이면 OPENAI_API_KEY 미설정이거나 생성이 실패한 것 —
    // 목업 문구로 가리지 않고 실패를 그대로 보여준다(cost와 같은 정책, §9).
    if (report.summary) {
      document.getElementById("rv-summary-p").textContent = report.summary.paragraph;
      document.getElementById("rv-summary-actions").innerHTML = report.summary.actions.map(function (a) { return "<li>" + esc(a) + "</li>"; }).join("");
    } else {
      document.getElementById("rv-summary-p").textContent = "AI 요약을 생성하지 못했습니다(같은 조건으로 다시 생성해 보세요).";
      document.getElementById("rv-summary-actions").innerHTML = "";
    }

    // 비용 요약 — app/report_cost.py가 생성 시점에 계산해 고정 저장한 값(report.cost)을 그대로
    // 옮겨 담을 뿐, 새 합계·비율·기여율을 여기서 만들지 않는다(비용 파트 지시 §1). cost가
    // null이면 생성 시점 계산 자체가 실패한 것 — 0원/목업으로 채우지 않고 "불러오지 못함"으로
    // 표시한다(§9).
    renderCostSection(report);

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

    // "—"만 보여주면 왜 모르는지 알 수 없다 — 이유가 서로 다른 두 경우를 구분해 title(hover 시
    // 설명)로 밝힌다. 미연결 디스크는 상태 변경 기록이 아예 없는 경우(오래된 리소스)만 "—"이고,
    // 유휴 인스턴스는 항상 "—"다(app/metrics.py::get_unused_resources 참고 — 인스턴스의
    // status_changed_at은 실행 상태(RUNNING/STOPPED) 변경 시각이라 CPU가 낮아진 시점과는 다른
    // 값이라 아예 계산하지 않는다).
    function idleCell(u) {
      if (u.idleDays !== null && u.idleDays !== undefined) return u.idleDays + "일";
      var why = u.reason === "idle_compute"
        ? "인스턴스는 실행 상태(RUNNING/STOPPED) 변경 시각만 기록되어 있어 CPU가 낮아진 시점(유휴 시작)과는 다릅니다 — 그래서 계산하지 않습니다."
        : "상태 변경 기록이 없는 오래된 리소스라 미연결 시작 시점을 알 수 없습니다.";
      return '<span title="' + esc(why) + '">—</span>';
    }

    // 미사용 리소스 — realUnused가 있으면(호출 성공) 실 데이터, 없으면 목업
    var isRealUnused = !!(realUnused && realUnused.items.length);
    var unusedItems = isRealUnused ? realUnused.items : report.unused.items;
    var unusedSection = document.getElementById("rv-unused-section");
    if (!unusedItems.length) {
      unusedSection.classList.add("hidden");
    } else {
      document.getElementById("rv-unused-body").innerHTML = unusedItems
        .map(function (u) {
          var idle = idleCell(u);
          var cost = u.cost === null || u.cost === undefined ? '<span title="정가표에 없는 유형이라 단가를 추정하지 않았습니다.">정보 없음</span>' : "$" + Math.round(u.cost);
          return "<tr><td class=\"csp " + u.csp + "\">" + CSP_LABEL[u.csp] + "</td><td>" + esc(u.name) + "</td><td>" + esc(u.type) + "</td>" +
            "<td>" + idle + "</td><td class=\"num\">" + cost + "</td></tr>";
        })
        .join("");
      var knownCostTotal = unusedItems.reduce(function (s, u) { return s + (u.cost || 0); }, 0);
      var basis = isRealUnused
        ? "실시간 조회(" + new Date(realUnused.asOf).toLocaleString("ko-KR") + ") 값입니다 — 미연결 디스크(AWS EBS)와 CPU 10% 미만 유휴 인스턴스만 감지합니다. 유휴 기간은 디스크만 계산되며(인스턴스는 실행 상태 변경 시각뿐이라 정확한 유휴 시작 시점을 알 수 없음), 미연결 공인 IP·Azure/GCP 디스크는 아직 추적하지 않습니다."
        : "감지 기준 — CPU 사용률 10% 이하가 14일 중 4일 이상 지속(샘플 데이터).";
      document.getElementById("rv-unused-caption").textContent =
        basis + " " + unusedItems.length + "건 중 비용 확인 가능한 항목 정리 시 월 " + money(knownCostTotal) + " 절감 예상.";
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
    // MCReports.getById/latest는 실 API(GET /api/v1/reports) 호출이라 Promise다(2026-09-19).
    var reportPromise = id ? MCReports.getById(id) : MCReports.latest();
    reportPromise.then(function (report) {
      var root = document.querySelector(".sheet");
      if (!report) {
        // id가 없는데 못 찾았다는 건 URL 오타가 아니라 "생성된 보고서가 아예 없다"는 뜻이다
        // (2026-09-19, 생성 이력이 실제 생성분만 담도록 바뀌면서 처음엔 항상 비어 있다).
        var detail = id ? "id=" + esc(id) : "아직 생성된 보고서가 없습니다 — 보고서 작성 페이지에서 먼저 생성하세요.";
        root.innerHTML = '<div class="p-10 text-center"><p style="font-size:15px;font-weight:600">보고서를 찾을 수 없습니다.</p>' +
          '<p style="margin-top:6px;font-size:13px;color:#5c6470">' + detail + '</p>' +
          '<a href="reports.html" style="display:inline-block;margin-top:16px;font-size:13px;color:#145d91">← 보고서 목록으로</a></div>';
        return;
      }
      Promise.all([fetchRealUtilization(report.clouds), fetchRealUnused(report.clouds)]).then(function (results) {
        render(report, results[0], results[1]);
        if (qs("print") === "1") {
          window.setTimeout(function () { window.print(); }, 300);
        }
      });
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
