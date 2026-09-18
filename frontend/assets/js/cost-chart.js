/* frontend/assets/js/cost-chart.js — 비용 차트(인라인 SVG). window.MCPCostChart 로 노출한다.
   ES 모듈이 아니다 — 이 프로젝트는 빌드 도구 없이 <script src>로 읽는다(api.js와 동일 형태).
   색은 var(--*)와 currentColor만 쓴다. 16진수 색을 쓰면 다크모드에서 읽히지 않는다. */
window.MCPCostChart = (function () {
  "use strict";

  var F = window.MCPCostFormat;

  /* 금액 칸을 비우는 상태 6종(03 §6-1). 이 상태에서는 차트를 그리지 않는다 —
     축만 있는 빈 그래프는 "0원"으로 읽힌다. */
  var BLANK_STATES = [
    "NOT_CONNECTED", "PENDING", "SETUP_REQUIRED",
    "PERMISSION_DENIED", "COLLECT_FAILED", "UNSUPPORTED"
  ];

  /* 계열 색 6개. 순서를 고정하고 돌려쓰지 않는다 — 7번째 계열은 만들지 않고 '기타'로 접는다.
     이 값들은 03 §8-1 에서 dataviz 검증기로 6개 검사를 통과시킨 것이다(theme.css에 선언됨).
     이전 판의 --primary/--sky/--yellow/--aero 조합은 검증에서 떨어졌다:
     --sky 와 --aero 가 ΔE 5.5 로 사실상 같은 색이고 --yellow 는 흰 배경 대비 1.64 다. */
  var SERIES_COLOR = [
    "var(--cost-series-1)", "var(--cost-series-2)", "var(--cost-series-3)",
    "var(--cost-series-4)", "var(--cost-series-5)", "var(--cost-series-6)"
  ];
  /* 잔여 항목은 대상이 아니므로 계열 색을 주지 않는다(03 §8-1-2). */
  var RESIDUAL_COLOR = "var(--muted-foreground)";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* 눈금 상한 — 1·2·5 배수로 올림한다. 데이터 최대값을 그대로 쓰면 눈금이 8.37 같은 값이 된다. */
  function niceMax(max) {
    if (!(max > 0)) return 1;
    var exp = Math.floor(Math.log10(max));
    var base = Math.pow(10, exp);
    var n = max / base;
    var step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
    return step * base;
  }

  /* 좌표 계산에만 Number를 쓴다 — 표시는 반드시 F.money 를 통한다. */
  function num(amountString) {
    var v = Number(amountString);
    return isFinite(v) ? v : 0;
  }

  /* 상태 가드. 그릴 수 없으면 문자열을 돌려주고, 그릴 수 있으면 null 을 돌려준다. */
  function guard(state, blockName, lastSuccessAt) {
    if (BLANK_STATES.indexOf(state) >= 0) {
      var msg = window.MCPCostState.text(state, blockName, lastSuccessAt);
      return '<div class="state-view" role="status"><span class="dash">—</span>' +
             '<p>' + esc(msg) + '</p></div>';
    }
    return null;
  }

  /* ── 공통 축 ───────────────────────────────────────────────────────────────
     labels: x축 라벨 배열 · max: 눈금 상한 · currency: 단위 표기용 */
  function axes(geo, labels, max, currency) {
    var out = "";
    [0, 0.5, 1].forEach(function (f) {
      var y = geo.y(max * f);
      out += '<line x1="' + geo.left + '" y1="' + y + '" x2="' + geo.right + '" y2="' + y +
             '" stroke="var(--border)" stroke-dasharray="4 4"/>';
      out += '<text x="2" y="' + (y + 4) + '" fill="currentColor" font-size="11">' +
             esc(F.money(String(max * f), currency)) + '</text>';
    });
    // x 라벨은 겹치지 않을 만큼만 찍는다(최대 8개).
    var stride = Math.max(1, Math.ceil(labels.length / 8));
    labels.forEach(function (label, i) {
      if (i % stride !== 0) return;
      out += '<text x="' + geo.x(i) + '" y="' + (geo.bottom + 18) + '" text-anchor="middle" ' +
             'fill="currentColor" font-size="11">' + esc(label) + '</text>';
    });
    return out;
  }

  function geometry(labelCount) {
    var W = 720, H = 280, left = 64, right = 700, top = 16, bottom = 228;
    return {
      W: W, H: H, left: left, right: right, top: top, bottom: bottom, max: 1,
      x: function (i) {
        if (labelCount <= 1) return (left + right) / 2;
        return left + i * (right - left) / (labelCount - 1);
      },
      y: function (v) { return bottom - (v / this.max) * (bottom - top); }
    };
  }

  /* 슬롯을 돌려쓰지 않는다. 6개를 넘으면 색이 아니라 중립색을 준다 —
     같은 색 두 개가 다른 대상을 가리키는 것보다 "색이 없다"가 정직하다. */
  function slotColor(i, key) {
    if (key === "__rest" || key === "__unallocated") return RESIDUAL_COLOR;
    return i < SERIES_COLOR.length ? SERIES_COLOR[i] : RESIDUAL_COLOR;
  }

  function legend(series, note) {
    var out = '<div class="legend">';
    series.forEach(function (s, i) {
      var label = esc(s.label || s.key);
      if (s.omitted_reason) label += " (" + esc(reasonText(s.omitted_reason)) + ")";
      out += '<span><i class="dot" style="background:' + slotColor(i, s.key) +
             '"></i> ' + label + '</span>';
    });
    if (note) out += '<span class="tiny muted">' + esc(note) + '</span>';
    return out + '</div>';
  }

  var REASON_TEXT = {
    UNSUPPORTED: "미지원", NOT_CONNECTED: "미연결", PENDING: "수집 대기",
    SETUP_REQUIRED: "설정 필요", PERMISSION_DENIED: "권한 부족", COLLECT_FAILED: "조회 실패",
    CONNECTED_EMPTY: "0원", CONNECTED_PARTIAL: "부분", CONNECTED_OK: ""
  };
  function reasonText(code) { return REASON_TEXT[code] || code; }

  /* ── ① 선 그래프 — CF-013 ────────────────────────────────────────────────
     opts = { labels, series:[{key,label,points:{label:amountString},omitted_reason}],
              currency, missingLabels:[], thresholdLine:{value,label},
              state, blockName, lastSuccessAt, title }
     points 에 없는 라벨에서는 선을 끊는다. 0 으로 채우지 않는다. */
  function lineChart(opts) {
    var blocked = guard(opts.state, opts.blockName, opts.lastSuccessAt);
    if (blocked) return blocked;

    var labels = opts.labels || [];
    var series = opts.series || [];
    var geo = geometry(labels.length);

    var peak = 0;
    series.forEach(function (s) {
      labels.forEach(function (l) {
        var v = s.points && s.points[l] != null ? num(s.points[l]) : null;
        if (v != null && v > peak) peak = v;
      });
    });
    if (opts.thresholdLine && num(opts.thresholdLine.value) > peak) peak = num(opts.thresholdLine.value);
    geo.max = niceMax(peak);

    var svg = '<svg class="chart" viewBox="0 0 ' + geo.W + ' ' + geo.H + '" role="img" aria-label="' +
              esc((opts.title || "비용 추이") + ". 같은 값을 표로도 제공합니다.") + '">';
    svg += axes(geo, labels, geo.max, opts.currency);

    series.forEach(function (s, si) {
      var color = slotColor(si, s.key);
      // 끊긴 구간을 나눠 path 를 여러 개 만든다 — 한 path 로 이으면 빠진 날이 직선으로 메워진다.
      var run = [];
      var segments = [];
      labels.forEach(function (l, i) {
        var has = s.points && s.points[l] != null;
        if (has) run.push({ i: i, v: num(s.points[l]), raw: s.points[l], label: l });
        else { if (run.length) segments.push(run); run = []; }
      });
      if (run.length) segments.push(run);

      segments.forEach(function (seg) {
        if (seg.length === 1) {
          svg += '<circle cx="' + geo.x(seg[0].i) + '" cy="' + geo.y(seg[0].v) + '" r="4" fill="' + color + '"/>';
          return;
        }
        svg += '<path d="' + seg.map(function (p, k) {
          return (k ? "L" : "M") + geo.x(p.i) + "," + geo.y(p.v);
        }).join(" ") + '" fill="none" stroke="' + color + '" stroke-width="2.5"/>';
      });

      // 값 확인 — 점마다 <title>. 네이티브 툴팁이라 인쇄·스크린리더에서도 남는다.
      segments.forEach(function (seg) {
        seg.forEach(function (p) {
          svg += '<circle cx="' + geo.x(p.i) + '" cy="' + geo.y(p.v) + '" r="3.5" fill="' + color + '">' +
                 '<title>' + esc(p.label + " · " + (s.label || s.key) + " · " +
                                 F.money(p.raw, opts.currency)) + '</title></circle>';
        });
      });
    });

    if (opts.thresholdLine) {
      var ty = geo.y(num(opts.thresholdLine.value));
      svg += '<line x1="' + geo.left + '" y1="' + ty + '" x2="' + geo.right + '" y2="' + ty +
             '" stroke="var(--muted-foreground)" stroke-dasharray="3 5"/>';
      svg += '<text x="' + (geo.right - 4) + '" y="' + (ty - 6) + '" text-anchor="end" ' +
             'fill="currentColor" font-size="11">' + esc(opts.thresholdLine.label) + '</text>';
    }
    svg += "</svg>";

    var note = (opts.missingLabels && opts.missingLabels.length)
      ? "수집되지 않은 구간 " + opts.missingLabels.length + "개 — 선을 끊었습니다(0으로 채우지 않음)"
      : null;
    return svg + legend(series, note) + dataTable(labels, series, opts.currency, opts.missingLabels);
  }

  /* ── ② 누적 막대 — CF-013 일별 모드 ─────────────────────────────────────── */
  function stackedBar(opts) {
    var blocked = guard(opts.state, opts.blockName, opts.lastSuccessAt);
    if (blocked) return blocked;

    var labels = opts.labels || [];
    var series = opts.series || [];
    var geo = geometry(labels.length);

    var peak = 0;
    labels.forEach(function (l) {
      var sum = 0;
      series.forEach(function (s) { if (s.points && s.points[l] != null) sum += num(s.points[l]); });
      if (sum > peak) peak = sum;
    });
    geo.max = niceMax(peak);

    var bw = Math.max(6, Math.min(28, (geo.right - geo.left) / Math.max(labels.length, 1) * 0.6));
    var svg = '<svg class="chart" viewBox="0 0 ' + geo.W + ' ' + geo.H + '" role="img" aria-label="' +
              esc((opts.title || "일별 비용") + ". 같은 값을 표로도 제공합니다.") + '">';
    svg += axes(geo, labels, geo.max, opts.currency);

    labels.forEach(function (l, i) {
      var base = 0;
      var drawn = 0;
      series.forEach(function (s, si) {
        if (!s.points || s.points[l] == null) return;      // 빠진 값은 칸을 비운다
        drawn += 1;
        var v = num(s.points[l]);
        var h = (v / geo.max) * (geo.bottom - geo.top);
        svg += '<rect x="' + (geo.x(i) - bw / 2) + '" y="' + (geo.bottom - base - h) +
               '" width="' + bw + '" height="' + Math.max(h, 0) +
               '" fill="' + slotColor(si, s.key) + '">' +
               '<title>' + esc(l + " · " + (s.label || s.key) + " · " +
                               F.money(s.points[l], opts.currency)) + '</title></rect>';
        base += h;
      });
      // 그 라벨에 계열이 하나도 없으면 막대를 0 높이로 그리지 않고, 바닥에 표시만 남긴다.
      if (!drawn) {
        svg += '<line x1="' + (geo.x(i) - bw / 2) + '" y1="' + geo.bottom + '" x2="' + (geo.x(i) + bw / 2) +
               '" y2="' + geo.bottom + '" stroke="var(--muted-foreground)" stroke-dasharray="2 2">' +
               '<title>' + esc(l + " · 수집되지 않음") + '</title></line>';
      }
    });
    svg += "</svg>";

    var note = (opts.missingLabels && opts.missingLabels.length)
      ? "수집되지 않은 구간 " + opts.missingLabels.length + "개 — 막대를 비웠습니다"
      : null;
    return svg + legend(series, note) + dataTable(labels, series, opts.currency, opts.missingLabels);
  }

  /* ── ③ 도넛 — CF-016 ────────────────────────────────────────────────────
     opts = { items:[{key,label,amount,share_pct}], rest, unallocated, total, currency, ... }
     share_pct 는 서버 값을 그대로 쓴다 — 화면이 비중을 다시 계산하지 않는다. */
  function donut(opts) {
    var blocked = guard(opts.state, opts.blockName, opts.lastSuccessAt);
    if (blocked) return blocked;

    var slices = (opts.items || []).slice();
    if (opts.rest && opts.rest.amount != null) slices.push(opts.rest);
    if (opts.unallocated && opts.unallocated.amount != null && num(opts.unallocated.amount) !== 0) {
      slices.push({ key: "__unallocated", label: "미배분", amount: opts.unallocated.amount,
                    share_pct: opts.unallocated.share_pct });
    }

    var R = 90, r = 56, cx = 110, cy = 110, C = 2 * Math.PI * ((R + r) / 2);
    var stroke = R - r;
    var svg = '<svg class="chart donut" viewBox="0 0 520 220" role="img" aria-label="' +
              esc((opts.title || "구성 비중") + ". 같은 값을 표로도 제공합니다.") + '">';
    svg += '<circle cx="' + cx + '" cy="' + cy + '" r="' + ((R + r) / 2) +
           '" fill="none" stroke="var(--muted)" stroke-width="' + stroke + '"/>';

    var offset = 0;
    slices.forEach(function (s, i) {
      var pct = s.share_pct != null ? Number(s.share_pct) : 0;
      if (!(pct > 0)) return;
      var len = C * pct / 100;
      svg += '<circle cx="' + cx + '" cy="' + cy + '" r="' + ((R + r) / 2) + '" fill="none" ' +
             'stroke="' + slotColor(i, s.key) + '" stroke-width="' + stroke + '" ' +
             'stroke-dasharray="' + len.toFixed(2) + " " + (C - len).toFixed(2) + '" ' +
             'stroke-dashoffset="' + (-offset).toFixed(2) + '" ' +
             'transform="rotate(-90 ' + cx + " " + cy + ')">' +
             '<title>' + esc((s.label || s.key) + " · " + F.money(s.amount, opts.currency) +
                             " · " + pct.toFixed(1) + "%") + '</title></circle>';
      offset += len;
    });

    // 가운데 합계 — 단위를 여기 한 번만 쓴다.
    svg += '<text x="' + cx + '" y="' + (cy - 4) + '" text-anchor="middle" fill="currentColor" ' +
           'font-size="16" font-weight="600">' + esc(F.money(opts.total, opts.currency)) + '</text>';
    svg += '<text x="' + cx + '" y="' + (cy + 16) + '" text-anchor="middle" fill="currentColor" ' +
           'font-size="11" opacity="0.7">합계</text>';

    // 범례를 SVG 안에 둔다 — 인쇄할 때 도넛과 떨어지지 않는다.
    slices.forEach(function (s, i) {
      var y = 26 + i * 22;
      svg += '<rect x="240" y="' + (y - 9) + '" width="10" height="10" rx="2" fill="' +
             slotColor(i, s.key) + '"/>';
      svg += '<text x="258" y="' + y + '" fill="currentColor" font-size="12">' +
             esc((s.label || s.key) + "  " + F.money(s.amount, opts.currency) +
                 (s.share_pct != null ? "  " + Number(s.share_pct).toFixed(1) + "%" : "")) + '</text>';
    });
    svg += "</svg>";

    var notes = [];
    if (opts.estimate_unavailable_count) {
      notes.push("정가를 알 수 없어 제외된 리소스 " + opts.estimate_unavailable_count + "개");
    }
    return svg + (notes.length ? '<p class="note">' + esc(notes.join(" · ")) + "</p>" : "") +
           shareTable(slices, opts.total, opts.currency);
  }

  /* ── 동일 값 표 — 차트를 못 읽는 사용자를 위한 대안이자 대조용 ────────────── */
  function dataTable(labels, series, currency, missingLabels) {
    var missing = {};
    (missingLabels || []).forEach(function (l) { missing[l] = true; });

    var head = "<tr><th scope=\"col\">기간</th>" + series.map(function (s) {
      return '<th scope="col">' + esc(s.label || s.key) + "</th>";
    }).join("") + "</tr>";

    var rows = labels.map(function (l) {
      var cells = series.map(function (s) {
        var has = s.points && s.points[l] != null;
        return "<td>" + (has ? esc(F.money(s.points[l], currency))
                             : (missing[l] ? "<span class=\"muted\">미수집</span>" : "—")) + "</td>";
      }).join("");
      return "<tr><th scope=\"row\">" + esc(l) + "</th>" + cells + "</tr>";
    }).join("");

    return '<details class="no-print"><summary class="tiny muted">동일 값 표 보기</summary>' +
           '<div class="table-wrap"><table><thead>' + head + "</thead><tbody>" + rows +
           "</tbody></table></div></details>";
  }

  function shareTable(slices, total, currency) {
    var rows = slices.map(function (s) {
      return "<tr><th scope=\"row\">" + esc(s.label || s.key) + "</th><td>" +
             esc(F.money(s.amount, currency)) + "</td><td>" +
             (s.share_pct != null ? esc(Number(s.share_pct).toFixed(1)) + "%" : "—") + "</td></tr>";
    }).join("");
    return '<details class="no-print"><summary class="tiny muted">동일 값 표 보기</summary>' +
           '<div class="table-wrap"><table><thead><tr><th scope="col">항목</th>' +
           '<th scope="col">금액</th><th scope="col">비중</th></tr></thead><tbody>' + rows +
           '<tr><th scope="row">합계</th><td>' + esc(F.money(total, currency)) +
           "</td><td>100.0%</td></tr></tbody></table></div></details>";
  }

  return {
    lineChart: lineChart,
    stackedBar: stackedBar,
    donut: donut,
    dataTable: dataTable,
    niceMax: niceMax,
    BLANK_STATES: BLANK_STATES
  };
})();
