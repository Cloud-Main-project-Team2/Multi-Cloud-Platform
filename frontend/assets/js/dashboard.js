/* DASH-01 대시보드 실 데이터 연동.
 *
 * 지금은 AWS만 프로비저닝/리소스 러너가 있지만, provider를 하드코딩하지 않고 실제 API 응답
 * (`/resources/summary`의 by_provider, `/resources`의 각 항목)을 그대로 반복 처리한다 —
 * Azure/GCP 리소스가 나중에 실제로 쌓이기 시작해도 이 파일을 고칠 필요가 없다(PLATFORMS 배열에
 * 이미 셋 다 들어있고, 데이터가 없는 provider는 0으로 표시될 뿐이다).
 *
 * 비용(2026-09-18, `/costs/*` 연동): `/costs/summary`·`/costs/breakdown`·`/costs/trend`
 * (`app/cost/query.py`)를 우선 쓴다 — 지금은 AWS 계정만 실측 데이터가 있고(`app/cost/__init__.py`의
 * COST_ADAPTERS), Azure/GCP는 아직 없다. "월말 예상 비용" 카드는 `kpis.forecast_month_end`
 * (이번 달 진행 중일 때만, 어제까지 실측을 남은 일수 비율로 늘린 값)를 쓰고, 전망을 낼 실측이
 * 없으면 `app/pricing.py` 정가(list price) 추정치로 자동 대체된다(서버가 이미 `/costs/summary`의
 * kpis.list_price_monthly·accounts[].list_price_estimate에 같이 담아 준다 — 클라이언트에서
 * 따로 계산하지 않는다). "월말 전망"과 "추정치"는 항상 배지로 구분해 보여준다.
 * 월별 추이는 `/costs/trend`(AWS 실측만) — 팀·예산(임계값 경고)은 여전히 백엔드가 없어 "준비 중"이다.
 */
(function () {
  "use strict";

  var PLATFORMS = ["aws", "azure", "gcp"];
  var PLATFORM_LABEL = { aws: "AWS", azure: "Azure", gcp: "GCP" };
  var PROVIDER_COLOR = { aws: "#FF9900", azure: "#0078D4", gcp: "#34A853" };
  // inventory.js의 PROVIDER_ICON과 같은 에셋 — 클라우드별 카드 제목 앞에 로고를 붙인다.
  var PROVIDER_ICON = { aws: "assets/imgs/aws.png", azure: "assets/imgs/azure.png", gcp: "assets/imgs/gcp.png" };

  // 리전 → 지도상의 "지점(site)". map.png(세계 지도, 대서양 중심)에 맞춰 좌표를 보정했다.
  // x/y는 이미지 대비 백분율. 프로젝트가 쓰는 리전 집합이 작고 고정이라 위경도 자동 변환 대신
  // 지점별 좌표를 직접 둔다(디자인용 실루엣이라 그게 더 정확). 서울권(한국 리전)은 한 지점으로 묶는다.
  var REGION_SITE = {
    "ap-northeast-2": "seoul", koreacentral: "seoul", "asia-northeast3": "seoul", koreasouth: "seoul",
    "us-east-1": "us-east", eastus: "us-east",
    "us-central1": "us-central",
    canadacentral: "toronto",
  };
  // 좌표는 map.png(정사각도법 전폭, lon -180..180 / lat +83..-55)에 맞춰 측정·보정한 값.
  var SITES = {
    seoul: { x: 85.3, y: 32.9, label: "한국(서울권)" },
    "us-east": { x: 28.2, y: 32.6, label: "미국 동부(버지니아)" },
    "us-central": { x: 24.0, y: 29.7, label: "미국 중부(아이오와)" },
    toronto: { x: 27.9, y: 28.5, label: "캐나다(토론토)" },
  };
  // 표의 "위치" 열에 쓰는 리전별 도시 라벨(지점보다 세분).
  var REGION_LABEL = {
    "ap-northeast-2": "서울", koreacentral: "서울", "asia-northeast3": "서울", koreasouth: "부산",
    "us-east-1": "미국 버지니아", eastus: "미국 버지니아", "us-central1": "미국 아이오와",
    canadacentral: "캐나다 토론토",
  };

  // GCP는 스키마에 zone 컬럼이 없어 resources.region에 zone 값을 그대로 저장한다(CLAUDE.md
  // "GCP zone 단순화", 예: asia-northeast3-c / us-central1-a). 지도·표 매핑 키는 리전 단위라
  // 존 단위 값은 직접 매치되지 않는다 — 그래서 매핑 실패 시 zone 접미사(-a/-b/-c…)를 떼고
  // 리전 단위로 재조회한다. 이 정규화가 없으면 실제 GCP 리소스가 지도에 안 뜨고 각주로만 빠진다.
  function regionToRegionKey(region) {
    if (region == null) return region;
    var m = /^(.*)-[a-z]$/.exec(String(region));
    return m ? m[1] : region;
  }
  // 리전(또는 GCP zone) → 지도상의 지점(site). 직접 매치가 없으면 zone 접미사를 떼고 재시도한다.
  function siteForRegion(region) {
    if (REGION_SITE[region]) return REGION_SITE[region];
    var base = regionToRegionKey(region);
    return base !== region ? REGION_SITE[base] : undefined;
  }
  // 표 "위치" 라벨 — zone 값도 리전 단위 라벨로 보여준다.
  function regionLabelFor(region) {
    return REGION_LABEL[region] || REGION_LABEL[regionToRegionKey(region)] || "—";
  }

  // service_catalog.category -> dashboard.html의 카드 id 접미사.
  var CATEGORY_ID = { compute: "compute", db_rdbms: "db", storage_object: "storage_object", cdn: "cdn" };
  // service_catalog.category -> "서비스별 비용 비중"에 쓰는 표시 라벨.
  var CATEGORY_LABEL = { compute: "Compute", db_rdbms: "Database", storage_object: "Object Storage", cdn: "CDN" };
  // 이 상태값들은 "실행 중"으로 간주한다(EC2 running, RDS/S3 available, CloudFront deployed 등).
  var RUNNING_LIKE = { RUNNING: true, AVAILABLE: true, DEPLOYED: true };

  var JOB_STATUS_LABEL = { queued: "대기", running: "진행중", success: "성공", failed: "실패", cancelled: "취소" };

  function escHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function formatDateTime(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    return pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  // ── 정가 추정 집계(/resources 각 항목의 cost_summary.estimated_monthly_cost) ──────────
  // "서비스별 비용 비중"의 실측 데이터(/costs/breakdown)가 없을 때만 쓰는 대체 경로다 —
  // 총 비용·클라우드별 카드는 이제 서버가 낸 /costs/summary 값을 그대로 쓴다(중복 계산 제거).
  // app/pricing.py가 정가 기준으로 채워준 값을 그대로 합산한다 — 사용량 기반 서비스(S3/CDN 등)나
  // 허용 목록 밖 스펙은 cost_summary 자체가 없어 자동으로 missing 카운트에 들어간다.
  // 합계는 MCPCostFormat.sumWithGuard로 낸다(비용 파트 PR 6) — Number() 합산은 통화가 섞이면
  // $와 ₩을 그대로 더하는 사고로 이어진다(docs/비용_개발문서 QA-03). byProvider/byCategory는
  // 카드·막대 보조 지표라 이번 범위에서는 통화 혼재를 가정하지 않고 그대로 둔다.
  function computeCostAggregates(items) {
    var rawItems = [], missing = 0;
    var byProvider = {}, byCategory = {};
    items.forEach(function (r) {
      var raw = r.cost_summary && r.cost_summary.estimated_monthly_cost;
      var amount = raw != null ? parseFloat(raw) : NaN;
      if (raw == null || isNaN(amount)) { missing++; return; }
      var currency = (r.cost_summary && r.cost_summary.currency) || "USD";
      rawItems.push({ amount: raw, cost_kind: "list_price_estimate", currency: currency, period_start: "current", period_end: "current" });
      var prov = r.cloud_account && r.cloud_account.provider;
      if (prov) byProvider[prov] = (byProvider[prov] || 0) + amount;
      var cat = r.service && r.service.category;
      if (cat) byCategory[cat] = (byCategory[cat] || 0) + amount;
    });
    var guarded = window.MCPCostFormat ? window.MCPCostFormat.sumWithGuard(rawItems) : { mixed: false, groups: [] };
    var totalCost = 0, currency = null;
    if (guarded.groups.length) { totalCost = parseFloat(guarded.groups[0].total); currency = guarded.groups[0].currency; }
    return {
      totalCost: totalCost, currency: currency, mixedCurrencies: guarded.mixed,
      hasAny: rawItems.length > 0, missing: missing, byProvider: byProvider, byCategory: byCategory
    };
  }

  // ── /costs/summary.accounts를 provider별로 합친다 ──────────────────────────
  // 계정마다 실측(actual, MTD 누적)이 있으면 실측을 쓰고, 없으면 정가 추정(list_price_estimate)
  // 으로 대체한다 — 둘을 같은 provider 안에서 더하지 않는다(하나가 실측이면 그 provider는
  // "실측"으로 표시하고, 정가만 있는 계정 몫은 반영되지 않는다). 지금은 AWS만 실측이 나온다
  // (app/cost/__init__.py의 COST_ADAPTERS).
  function costByProviderFromSummary(costSummary) {
    var F = window.MCPCostFormat;
    var buckets = {}; // provider -> { actual: [items for sumWithGuard], estimate: [items] }
    (costSummary && costSummary.accounts || []).forEach(function (a) {
      if (!buckets[a.provider]) buckets[a.provider] = { actual: [], estimate: [] };
      // a.currency는 "실측 비용의 통화"다(app/cost/query.py의 summary() — actual_currency or
      // cap["currency"]) — list_price_estimate는 별도로 resources.cost_currency 기준이라(항상
      // "USD" 기본, app/cost/query.py의 list_price_monthly()) 실측이 없는 계정은 currency가
      // null이어도 정가는 있을 수 있다. 정가 쪽은 여기서 같은 기본값(USD)을 맞춰 준다.
      var cost_kind = "x", period_start = "current", period_end = "current";
      if (a.actual != null) {
        buckets[a.provider].actual.push({ amount: a.actual, currency: a.currency, cost_kind: cost_kind, period_start: period_start, period_end: period_end });
      }
      if (a.list_price_estimate != null) {
        buckets[a.provider].estimate.push({ amount: a.list_price_estimate, currency: a.currency || "USD", cost_kind: cost_kind, period_start: period_start, period_end: period_end });
      }
    });
    var out = {};
    Object.keys(buckets).forEach(function (p) {
      var b = buckets[p];
      var actualSum = F.sumWithGuard(b.actual);
      if (actualSum.groups.length) { out[p] = { amount: actualSum.groups[0].total, currency: actualSum.groups[0].currency, kind: "actual" }; return; }
      var estSum = F.sumWithGuard(b.estimate);
      if (estSum.groups.length) out[p] = { amount: estSum.groups[0].total, currency: estSum.groups[0].currency, kind: "estimate" };
    });
    return out;
  }

  // ── 전체 리소스 수 + 예상 총 비용 + 클라우드별 카드(/resources/summary + /costs/summary) ─
  function renderResourceSummary(summary, costSummaryRes, costEstimateFallback, failures) {
    failures = failures || {};
    var byProvider = (summary && summary.by_provider) || [];
    var F = window.MCPCostFormat;

    var totalEl = document.getElementById("dash-total-resources");
    if (totalEl) totalEl.textContent = failures.summaryFailed ? "—" : (summary && summary.total_resources) || 0;

    var breakdownEl = document.getElementById("dash-total-resources-breakdown");
    if (breakdownEl) {
      breakdownEl.textContent = failures.summaryFailed
        ? "조회 실패 — 리소스 요약을 불러오지 못했습니다."
        : (byProvider.length
          ? byProvider.map(function (p) { return (PLATFORM_LABEL[p.provider] || p.provider) + " " + p.count; }).join(" · ")
          : "아직 리소스가 없습니다.");
    }

    var countByProvider = {};
    byProvider.forEach(function (p) { countByProvider[p.provider] = p.count; });

    var costEl = document.getElementById("dash-total-cost");
    var costNoteEl = document.getElementById("dash-total-cost-note");
    var badgeEl = document.getElementById("dash-total-cost-badge");
    var costSummary = costSummaryRes && costSummaryRes.ok ? costSummaryRes.value : null;
    // "월말 예상 비용" — app/cost/query.py의 forecast_month_end(): 이번 달 진행 중일 때만
    // (1일 제외) 어제까지의 실측(AWS만)을 남은 일수 비율로 늘린 전망치를 낸다. 아직 실측이
    // 없거나 계산 조건이 안 맞으면 빈 배열이라 정가(list price) 추정으로 내려간다.
    var forecastRows = (costSummary && costSummary.kpis && costSummary.kpis.forecast_month_end) || [];
    var estRows = (costSummary && costSummary.kpis && costSummary.kpis.list_price_monthly) || [];

    function setBadge(text, cls) { if (badgeEl) { badgeEl.textContent = text; badgeEl.className = "rounded border px-1.5 text-[11px] " + cls; } }

    if (!costSummaryRes || !costSummaryRes.ok) {
      if (costEl) costEl.textContent = "—";
      if (costNoteEl) costNoteEl.textContent = "조회 실패 — 비용 정보를 불러오지 못했습니다.";
      setBadge("오류", "border-border text-muted-foreground");
    } else if (forecastRows.length) {
      var frow = forecastRows[0]; // 통화 하나만 고른다(ADR-023) — 여러 통화면 첫 값만 보여준다
      if (costEl) costEl.textContent = F.money(frow.amount, frow.currency);
      setBadge("월말 전망", "border-primary text-primary");
      if (costNoteEl) {
        costNoteEl.textContent = "이번 달 말 전망(AWS 실측 기준) — 어제(" + frow.based_through + ")까지의 실측을 남은 일수 비율로 늘린 값 · 저장하지 않음." +
          (forecastRows.length > 1 ? " 통화가 섞여 있어 " + frow.currency + " 기준만 표시합니다." : "");
      }
    } else if (estRows.length) {
      var erow = estRows[0];
      if (costEl) costEl.textContent = F.money(erow.amount, erow.currency) + "/mo";
      setBadge("추정치", "border-yellow text-yellow");
      if (costNoteEl) {
        costNoteEl.textContent = "월말 전망을 낼 실측 데이터가 아직 없어 정가(list price) 기준 추정으로 대신 표시합니다." +
          (erow.missing_count > 0 ? " 사용량 기반 리소스 " + erow.missing_count + "개는 제외됨." : "");
      }
    } else {
      if (costEl) costEl.textContent = "—";
      setBadge("—", "border-border text-muted-foreground");
      if (costNoteEl) costNoteEl.textContent = "집계할 수 있는 비용 데이터가 없습니다.";
    }

    var costByProvider = costSummary ? costByProviderFromSummary(costSummary) : {};

    var container = document.getElementById("dash-cloud-cards");
    // "실시간"이 아니다 — 마지막 동기화 시점에 저장된 값이다(비용 파트 확정 16). 기준 시각을 함께 적는다.
    var syncedAtText = summary && summary.last_synced_at ? new Date(summary.last_synced_at).toLocaleString("ko-KR") : "동기화 이력 없음";
    if (container) {
      container.innerHTML = PLATFORMS.map(function (p) {
        var count = countByProvider[p] || 0;
        var c = costByProvider[p];
        var estFallback = costEstimateFallback.byProvider[p];
        var costText;
        if (c && c.kind === "actual") costText = F.money(c.amount, c.currency) + " · 실측(MTD)";
        else if (c) costText = F.money(c.amount, c.currency) + "/mo · Estimated · 정가 730h";
        else if (estFallback != null) costText = "$" + estFallback.toFixed(2) + "/mo · Estimated · 정가 730h";
        else costText = "추정 불가";
        return (
          '<div class="rounded-2xl border border-border bg-surface p-5">' +
          '<div class="flex items-center justify-between">' +
          '<p class="flex items-center gap-2 font-semibold">' +
          '<img src="' + PROVIDER_ICON[p] + '" alt="" class="h-5 w-auto align-middle" />' +
          PLATFORM_LABEL[p] + "</p>" +
          '<span class="rounded border border-border px-1.5 text-[11px] text-muted-foreground">' + costText + "</span>" +
          "</div>" +
          '<p class="mt-2 text-2xl font-extrabold">' + count + "개 리소스</p>" +
          '<p class="mt-1 text-xs text-muted-foreground">저장된 값 · 기준 시각 ' + escHtml(syncedAtText) + " · 비용은 실측 우선, 없으면 정가 추정</p>" +
          "</div>"
        );
      }).join("");
    }
  }

  // ── 서비스별 비용 비중(카테고리별 합산 막대) — 실측(/costs/breakdown) 우선, 없으면 정가 추정 ──
  function costBreakdownBarsHtml(entries) {
    var total = entries.reduce(function (sum, e) { return sum + e.amount; }, 0);
    return entries.map(function (e) {
      var pct = total > 0 ? (e.amount / total) * 100 : 0;
      return (
        "<div>" +
        '<div class="flex items-center justify-between text-sm"><span>' + (CATEGORY_LABEL[e.cat] || escHtml(e.cat)) +
        '</span><span class="font-medium">$' + e.amount.toFixed(2) + "</span></div>" +
        '<div class="mt-1 h-1.5 rounded-full bg-muted"><div class="h-1.5 rounded-full bg-primary" style="width:' +
        pct.toFixed(1) + '%"></div></div>' +
        '<p class="mt-0.5 text-right text-xs text-muted-foreground">' + pct.toFixed(1) + "%</p>" +
        "</div>"
      );
    }).join("");
  }

  function setCostByCategoryBadge(text, cls) {
    var el = document.getElementById("dash-cost-by-category-badge");
    if (el) { el.textContent = text; el.className = "rounded border px-1.5 text-[11px] " + cls; }
  }

  function renderCostBreakdown(breakdownRes, costInfoEstimate, itemsFailed) {
    var el = document.getElementById("dash-cost-by-category");
    if (!el) return;

    // dimension=category 실측이 있으면 그걸 쓴다(AWS만 실측 지원 — app/cost/query.py의
    // _category_for). 총액이 0이면 아직 수집된 실측이 없다는 뜻이라 정가 추정으로 내려간다.
    if (breakdownRes && breakdownRes.ok) {
      var d = breakdownRes.value;
      var items = (d.items || []).filter(function (it) { return CATEGORY_LABEL[it.key]; });
      if (Number(d.total) > 0 && items.length) {
        setCostByCategoryBadge("실측", "border-primary text-primary");
        var entries = items.map(function (it) { return { cat: it.key, amount: Number(it.amount) }; })
          .sort(function (a, b) { return b.amount - a.amount; });
        el.innerHTML = costBreakdownBarsHtml(entries);
        return;
      }
    }

    setCostByCategoryBadge("추정치", "border-yellow text-yellow");
    if (itemsFailed) {
      el.innerHTML = '<p class="text-sm text-muted-foreground">조회 실패 — 리소스 목록을 불러오지 못했습니다.</p>';
      return;
    }
    if (!costInfoEstimate.hasAny) {
      el.innerHTML = '<p class="text-sm text-muted-foreground">추정 가능한 리소스가 없습니다.</p>';
      return;
    }
    var estEntries = Object.keys(costInfoEstimate.byCategory)
      .map(function (cat) { return { cat: cat, amount: costInfoEstimate.byCategory[cat] }; })
      .sort(function (a, b) { return b.amount - a.amount; });
    el.innerHTML = costBreakdownBarsHtml(estEntries);
  }

  // ── 월별 비용 추이(/costs/trend, granularity=monthly, group_by=provider) — AWS 실측만 ──
  function renderCostTrendChart(trendRes) {
    var el = document.getElementById("dash-cost-trend");
    if (!el) return;
    if (!trendRes || !trendRes.ok) {
      el.innerHTML = '<p class="text-sm text-muted-foreground">비용 추이를 불러오지 못했습니다.</p>';
      return;
    }
    var d = trendRes.value;
    if (!d.series || !d.series.length) {
      el.innerHTML = '<p class="text-sm text-muted-foreground">아직 수집된 실측 비용 데이터가 없습니다 — 비용 관리 화면에서 "비용 새로고침"을 먼저 실행하세요.</p>';
      return;
    }
    var labelSet = {}, labels = [];
    d.series.forEach(function (s) {
      s.points.forEach(function (p) { if (!labelSet[p.period_start]) { labelSet[p.period_start] = true; labels.push(p.period_start); } });
    });
    labels.sort();
    var series = d.series.map(function (s) {
      var points = {};
      s.points.forEach(function (p) { points[p.period_start] = p.amount; });
      return { key: s.key, label: PLATFORM_LABEL[s.key] || s.key, points: points };
    });
    el.innerHTML = window.MCPCostChart.lineChart({
      labels: labels, series: series, currency: d.currency, missingLabels: d.missing_days,
      state: "CONNECTED_OK", title: "월별 비용 추이(AWS 실측)"
    });
  }

  // ── 주요 리소스 요약 + 리전 분포(/resources 목록, 기본 필터=활성 리소스만) ───
  function renderCategoriesAndRegions(items) {
    var categoryCounts = {};
    Object.keys(CATEGORY_ID).forEach(function (cat) { categoryCounts[cat] = { total: 0, running: 0, other: 0 }; });
    // region -> { provider -> count }. provider별 색·표 모두 이 집계에서 그린다.
    var regionProvider = {};

    items.forEach(function (r) {
      var cat = r.service && r.service.category;
      if (categoryCounts[cat]) {
        categoryCounts[cat].total++;
        if (r.status && RUNNING_LIKE[r.status]) categoryCounts[cat].running++;
        else if (r.status) categoryCounts[cat].other++;
      }
      if (r.region) {
        // r.provider가 아니라 r.cloud_account.provider — ResourceOut엔 최상위 provider 필드가
        // 없다(2026-09-14 발견한 버그, PR #52). 이걸 안 고치면 마커/표가 전부 "unknown"으로 나온다.
        var prov = (r.cloud_account && r.cloud_account.provider) || "unknown";
        if (!regionProvider[r.region]) regionProvider[r.region] = {};
        regionProvider[r.region][prov] = (regionProvider[r.region][prov] || 0) + 1;
      }
    });

    Object.keys(CATEGORY_ID).forEach(function (cat) {
      var id = CATEGORY_ID[cat];
      var c = categoryCounts[cat];
      var countEl = document.getElementById("dash-cat-" + id + "-count");
      if (countEl) countEl.textContent = c.total;

      // Compute/Database만 실행·중지 세부를 보여준다 — Storage/CDN은 그 개념이 없다(html에 "—" 고정).
      if (cat === "compute" || cat === "db_rdbms") {
        var subEl = document.getElementById("dash-cat-" + id + "-sub");
        if (subEl) {
          if (c.total === 0) subEl.textContent = "—";
          else if (c.other > 0) subEl.textContent = "실행 " + c.running + " · 중지 " + c.other;
          else subEl.textContent = "실행 " + c.running;
        }
      }
    });

    var regionKeys = Object.keys(regionProvider).sort();

    var totalRegionsEl = document.getElementById("dash-total-regions");
    if (totalRegionsEl) totalRegionsEl.textContent = regionKeys.length;

    renderRegionMap(regionProvider, regionKeys);
    renderRegionTable(regionProvider, regionKeys);
    wireRegionToggle();
  }

  // 한 지점(site)에서 같은 provider의 리소스가 이 개수 이상이면 개별 점 대신 큰 원 하나로 집계한다.
  var REGION_AGG_THRESHOLD = 5;

  // 개수 → 집계 원 지름(px). 개수(숫자 라벨)가 들어가야 하므로 최소 18px는 확보한다.
  function markerSize(count) {
    return Math.round(18 + Math.min(count, 10) * 1.4); // 18~32px
  }

  // 원(클라우드) 하나에 대한 툴팁 — 그 지점에서 해당 클라우드의 리전별 개수.
  function providerTooltip(site, provider, regionsAtSite, regionProvider) {
    var lines = [(PLATFORM_LABEL[provider] || provider) + " · " + site.label];
    regionsAtSite.forEach(function (region) {
      var c = regionProvider[region] && regionProvider[region][provider];
      if (c) lines.push(region + ": " + c + "개");
    });
    return lines.join("\n");
  }

  // ── 지도 보기: 지점별 마커 렌더 ──────────────────────────────────────────────
  function renderRegionMap(regionProvider, regionKeys) {
    var markersEl = document.getElementById("region-markers");
    var legendEl = document.getElementById("region-legend");
    var noteEl = document.getElementById("region-map-note");
    if (!markersEl) return;

    // 지점(site)별로 리전·provider 집계.
    var bySite = {}; // siteKey -> { total, providers:{}, regions:[] }
    var unmapped = []; // 좌표 미등록 리전
    regionKeys.forEach(function (region) {
      var siteKey = siteForRegion(region);
      if (!siteKey) { unmapped.push(region); return; }
      if (!bySite[siteKey]) bySite[siteKey] = { total: 0, providers: {}, regions: [] };
      var s = bySite[siteKey];
      s.regions.push(region);
      Object.keys(regionProvider[region]).forEach(function (p) {
        var c = regionProvider[region][p];
        s.total += c;
        s.providers[p] = (s.providers[p] || 0) + c;
      });
    });

    if (!regionKeys.length) {
      markersEl.innerHTML =
        '<div class="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">아직 리전 정보가 있는 리소스가 없습니다.</div>';
    } else {
      // 지점(site)마다 provider별 마커를 그린다. 한 provider의 리소스가 임계치
      // (REGION_AGG_THRESHOLD=5) 이상이면 "큰 원" 하나로 집계(개수 표시)하고, 미만이면 리소스
      // 하나당 "작은 점"을 찍는다(분포도). provider는 항상 색으로 구분해 따로 표시하므로 같은
      // 지점에 AWS·Azure·GCP가 섞여 있어도(예: AWS 2개는 점 2개, GCP 6개는 큰 원 1개) 어느 것도
      // 가려지지 않는다.
      //
      // 배치: 일자(row)로 늘어서면 far-right 지점(서울 x≈85%)에서 마지막 마커가 국가에서 멀리
      // 밀려나 잘 안 보였다. 그래서 지점 중심을 기준으로 황금각(golden angle) 나선으로 마커를
      // 방사형(원형)으로 흩뿌린다 — 큰 원(집계)은 중앙, 작은 점들은 그 둘레에 동그랗게 분포한다.
      markersEl.innerHTML = Object.keys(bySite).map(function (siteKey) {
        var s = bySite[siteKey];
        var site = SITES[siteKey];
        // provider별 마커를 한 리스트로 모은다(작은 점은 리소스 개수만큼, 큰 원은 하나).
        var items = [];
        PLATFORMS.filter(function (p) { return s.providers[p]; }).forEach(function (p) {
          var count = s.providers[p];
          var color = PROVIDER_COLOR[p] || "#94a3b8";
          var tip = providerTooltip(site, p, s.regions, regionProvider);
          if (count >= REGION_AGG_THRESHOLD) {
            items.push({ big: true, size: markerSize(count), color: color, tip: tip, count: count });
          } else {
            for (var i = 0; i < count; i++) items.push({ big: false, size: 15, color: color, tip: tip });
          }
        });
        // 큰 원이 중앙(i=0)에 오도록 큰 것부터 배치한다(뒤에 그린 작은 점이 위로 올라와 안 가려진다).
        items.sort(function (a, b) { return b.size - a.size; });
        var hasBig = items.length && items[0].big;
        // 황금각 나선: r = step·√i, θ = i·137.5°. step을 마커 지름보다 작게 잡아 마커들이 살짝
        // 겹치도록(사용자 요청) 촘촘히 모은다. 큰 원이 있으면 점이 그 안으로 파묻히지 않게 step을 키운다.
        var step = hasBig ? (items[0].size * 0.5 + 4) : 10;
        var GOLDEN = Math.PI * (3 - Math.sqrt(5)); // ≈2.399 rad (137.5°)
        var markers = items.map(function (it, i) {
          var r = items.length === 1 ? 0 : step * Math.sqrt(i);
          var dx = Math.round(r * Math.cos(i * GOLDEN));
          var dy = Math.round(r * Math.sin(i * GOLDEN));
          var pos = "position:absolute;left:0;top:0;transform:translate(calc(-50% + " + dx + "px),calc(-50% + " + dy + "px));";
          if (it.big) {
            return (
              '<div title="' + escHtml(it.tip) + '" style="' + pos + "width:" + it.size + "px;height:" + it.size +
              "px;border-radius:9999px;background:" + it.color +
              ";box-shadow:0 0 0 2px #fff,0 0 0 3.5px rgba(2,6,23,.5),0 1px 3px rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;\">" +
              '<span style="font-size:11px;font-weight:700;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.6)">' + it.count + "</span>" +
              "</div>"
            );
          }
          return (
            '<span title="' + escHtml(it.tip) + '" style="' + pos + "width:" + it.size + "px;height:" + it.size +
            "px;border-radius:9999px;background:" + it.color +
            ';box-shadow:0 0 0 2px #fff,0 0 0 3.5px rgba(2,6,23,.5),0 1px 2px rgba(0,0,0,.35);display:block;"></span>'
          );
        }).join("");
        // 0×0 앵커를 지점 좌표에 두고, 그 안에서 각 마커를 중심(0,0) 기준으로 방사 배치한다.
        return (
          '<div class="absolute" style="left:' + site.x + "%;top:" + site.y +
          '%;width:0;height:0;">' + markers + "</div>"
        );
      }).join("");
    }

    // 범례: provider 색 + (있으면) 좌표 미등록 안내.
    if (legendEl) {
      legendEl.innerHTML = PLATFORMS.map(function (p) {
        return (
          '<span class="inline-flex items-center gap-1.5">' +
          '<span style="width:10px;height:10px;border-radius:9999px;display:inline-block;background:' + PROVIDER_COLOR[p] + '"></span>' +
          PLATFORM_LABEL[p] + "</span>"
        );
      }).join("");
    }
    if (noteEl) {
      if (unmapped.length) {
        noteEl.textContent = "표에만 표시되는 리전(지도 좌표 미등록): " + unmapped.join(", ");
        noteEl.classList.remove("hidden");
      } else {
        noteEl.classList.add("hidden");
      }
    }
  }

  // ── 표 보기: 리전 / 위치 / 클라우드 / 개수 ──────────────────────────────────
  function renderRegionTable(regionProvider, regionKeys) {
    var body = document.getElementById("region-table-body");
    if (!body) return;
    if (!regionKeys.length) {
      body.innerHTML = '<tr><td class="py-2.5 text-muted-foreground" colspan="4">아직 리전 정보가 있는 리소스가 없습니다.</td></tr>';
      return;
    }
    var rows = [];
    regionKeys.forEach(function (region) {
      var provs = regionProvider[region];
      Object.keys(provs).sort().forEach(function (p) {
        rows.push(
          '<tr class="border-b border-border">' +
          '<td class="py-2.5 pr-4">' + escHtml(region) + "</td>" +
          '<td class="py-2.5 pr-4">' + escHtml(regionLabelFor(region)) + "</td>" +
          '<td class="py-2.5 pr-4"><span class="inline-flex items-center gap-1.5">' +
          '<span style="width:9px;height:9px;border-radius:9999px;display:inline-block;background:' +
          (PROVIDER_COLOR[p] || "#94a3b8") + '"></span>' + (PLATFORM_LABEL[p] || escHtml(p)) + "</span></td>" +
          '<td class="py-2.5 text-right">' + provs[p] + "개</td></tr>"
        );
      });
    });
    body.innerHTML = rows.join("");
  }

  // 지도/표 토글. 여러 번 호출돼도 리스너가 중복되지 않게 onclick으로 바인딩한다.
  function wireRegionToggle() {
    var mapBtn = document.getElementById("region-view-map");
    var tableBtn = document.getElementById("region-view-table");
    var mapView = document.getElementById("region-map-view");
    var tableView = document.getElementById("region-table-view");
    if (!mapBtn || !tableBtn || !mapView || !tableView) return;

    var ACTIVE = "bg-primary text-white";
    function show(which) {
      var isMap = which === "map";
      mapView.classList.toggle("hidden", !isMap);
      tableView.classList.toggle("hidden", isMap);
      mapBtn.className = "px-3 py-1.5 font-medium" + (isMap ? " " + ACTIVE : "");
      tableBtn.className = "border-l border-border px-3 py-1.5 font-medium" + (isMap ? "" : " " + ACTIVE);
    }
    mapBtn.onclick = function () { show("map"); };
    tableBtn.onclick = function () { show("table"); };
    show("map");
  }

  // ── 최근 프로비저닝 활동(/provisioning/jobs, provider별로 조회 후 합쳐서 정렬) ─
  function renderActivityRows(entries) {
    var tbody = document.getElementById("dash-activity-body");
    if (!tbody) return;

    if (!entries.length) {
      tbody.innerHTML = '<tr><td class="py-2.5 text-muted-foreground" colspan="5">아직 프로비저닝한 리소스가 없습니다.</td></tr>';
      return;
    }

    tbody.innerHTML = entries.map(function (entry) {
      var job = entry.job;
      var name = (job.common_spec && job.common_spec.name) || job.workspace_name;
      var label = JOB_STATUS_LABEL[job.status] || job.status;
      var badgeClass = "rounded-full px-2 py-0.5 text-xs ";
      var badgeStyle = "";
      if (job.status === "success") {
        badgeClass += "bg-muted text-primary";
      } else if (job.status === "failed") {
        badgeClass += "text-white";
        badgeStyle = ' style="background:#c0392b"';
      } else {
        badgeClass += "bg-muted text-muted-foreground";
      }

      return (
        '<tr class="border-b border-border">' +
        '<td class="py-2.5 pr-4">' + formatDateTime(job.created_at) + "</td>" +
        '<td class="py-2.5 pr-4">CREATE</td>' +
        '<td class="py-2.5 pr-4"><span class="inline-flex items-center gap-1.5">' +
        '<img src="' + PROVIDER_ICON[entry.platform] + '" alt="" class="h-4 w-auto align-middle" />' +
        PLATFORM_LABEL[entry.platform] + "</span></td>" +
        '<td class="py-2.5 pr-4">' + escHtml(name) + "</td>" +
        '<td class="py-2.5"><span class="' + badgeClass + '"' + badgeStyle + ">" + label + "</span></td>" +
        "</tr>"
      );
    }).join("");
  }

  function loadRecentActivity() {
    Promise.all(
      PLATFORMS.map(function (p) {
        return MCPApi.request("/provisioning/jobs?provider=" + p)
          .then(function (data) {
            return ((data && data.items) || []).map(function (job) { return { job: job, platform: p }; });
          })
          .catch(function () { return []; });
      })
    ).then(function (groups) {
      var all = [];
      groups.forEach(function (g) { all = all.concat(g); });
      all.sort(function (a, b) { return new Date(b.job.created_at) - new Date(a.job.created_at); });
      renderActivityRows(all.slice(0, 5));
    });
  }

  // ── 클라우드 콘솔 런처 ──────────────────────────────────────────────────────────
  // 3사 Cloud Shell을 한 카드에서 탭으로 골라 새 창으로 연다. 임베드(iframe)는
  // X-Frame-Options/CSP로 불가하므로 여기서는 "여는 링크"만 만든다. 계정/구독/프로젝트·리전은
  // 셸을 여는 데 필수가 아니라 "어디로 열지 사용자가 고르고, 로그인 계정을 확인"하기 위한
  // 값이며 로그인과는 무관하다(로그인은 사용자 브라우저 세션 몫). URL엔 토큰·비밀키를 넣지 않는다.
  var AWS_REGION_FALLBACK = "ap-northeast-2";
  // 정책 허용 리전(프로비저닝 폼과 동일 집합). Azure는 Cloud Shell URL에 리전 파라미터가 없어 제외.
  var CONSOLE_REGIONS = {
    aws: [["ap-northeast-2", "서울"], ["us-east-1", "미국 버지니아"]],
    gcp: [["asia-northeast3", "서울"], ["us-central1", "미국 아이오와"]],
  };
  var _consoleAccounts = { aws: [], azure: [], gcp: [] }; // { <provider>: [{value,label}] }
  var _consoleInferredRegion = { aws: "", gcp: "" };
  var _consoleSel = { aws: {}, azure: {}, gcp: {} }; // { <provider>: {account, region} }
  var _consoleTab = "aws";

  function consoleUrlFor(provider) {
    var sel = _consoleSel[provider] || {};
    if (provider === "aws") {
      return "https://console.aws.amazon.com/cloudshell/home?region=" +
        encodeURIComponent(sel.region || AWS_REGION_FALLBACK);
    }
    if (provider === "azure") {
      // 테넌트 ID는 계정 컬럼에 없고 암호화 payload 안에만 있어 쓰지 않는다.
      return "https://portal.azure.com/#cloudshell/";
    }
    // gcp: 선택한 프로젝트로 진입 + Cloud Shell 자동 오픈 시도.
    if (sel.account) {
      return "https://console.cloud.google.com/home/dashboard?project=" +
        encodeURIComponent(sel.account) + "&cloudshell=true";
    }
    return "https://shell.cloud.google.com/";
  }

  function accountOptionsHtml(provider) {
    var sel = (_consoleSel[provider] || {}).account;
    return _consoleAccounts[provider].map(function (a) {
      return '<option value="' + escHtml(a.value) + '"' + (a.value === sel ? " selected" : "") + ">" + escHtml(a.label) + "</option>";
    }).join("");
  }

  function regionOptionsHtml(provider) {
    var sel = (_consoleSel[provider] || {}).region;
    var opts = (CONSOLE_REGIONS[provider] || []).slice();
    // 유추 리전이 허용 목록 밖이면 실제 값을 반영할 수 있게 옵션으로 추가한다.
    if (sel && opts.map(function (o) { return o[0]; }).indexOf(sel) < 0) opts.unshift([sel, sel]);
    return opts.map(function (o) {
      return '<option value="' + escHtml(o[0]) + '"' + (o[0] === sel ? " selected" : "") +
        ">" + escHtml(o[1]) + " (" + escHtml(o[0]) + ")</option>";
    }).join("");
  }

  function selectHtml(id, optionsHtml) {
    return '<select id="' + id + '" class="rounded-md border border-border bg-surface px-2 py-1 text-sm">' + optionsHtml + "</select>";
  }

  function consoleBodyHtml(provider) {
    var sel = _consoleSel[provider] || {};
    var hasAccount = _consoleAccounts[provider].length > 0;
    var accountLabel = provider === "azure" ? "대상 구독" : provider === "gcp" ? "대상 프로젝트" : "대상 계정";
    var accountField = hasAccount
      ? accountLabel + " " + selectHtml("console-account-select", accountOptionsHtml(provider))
      : '<span class="text-muted-foreground">' + accountLabel + " 미연결</span>";

    var regionField = "";
    var copyBtn = "";
    if (provider === "aws") {
      regionField = " · region " + selectHtml("console-region-select", regionOptionsHtml(provider));
    } else if (provider === "gcp") {
      regionField = " · region " + selectHtml("console-region-select", regionOptionsHtml(provider));
      if (sel.region) copyBtn = consoleCopyBtnHtml("gcloud config set compute/region " + sel.region);
    } else if (provider === "azure") {
      if (sel.account) copyBtn = consoleCopyBtnHtml('az account set --subscription "' + sel.account + '"');
    }

    var note = '<p class="mt-1 text-xs text-muted-foreground">ⓘ 로그인은 브라우저에서 직접 하세요.' +
      (provider === "gcp"
        ? " Cloud Shell 패널이 자동으로 안 열리면 우측 상단 Cloud Shell 아이콘을 눌러주세요."
        : "") + "</p>";

    return (
      '<div class="flex flex-wrap items-end justify-between gap-3">' +
        '<div>' +
          '<div class="flex flex-wrap items-center gap-1.5">' + accountField + regionField + "</div>" +
          note +
          (copyBtn ? '<div class="mt-2">' + copyBtn + "</div>" : "") +
        "</div>" +
        '<button type="button" id="console-open-btn" class="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:opacity-90">' +
          escHtml(PLATFORM_LABEL[provider]) + " Cloud Shell 새 창으로 열기 " + MCUI.icons.externalLink + "</button>" +
      "</div>"
    );
  }

  function consoleCopyBtnHtml(cmd) {
    return '<button type="button" class="console-copy rounded-lg border border-border bg-surface px-2.5 py-1 text-xs hover:bg-muted" ' +
      'data-copy="' + escHtml(cmd) + '"><code>' + escHtml(cmd) + "</code> · 복사</button>";
  }

  function renderConsoleLauncher() {
    var tabs = document.getElementById("console-launcher-tabs");
    var body = document.getElementById("console-launcher-body");
    if (!tabs || !body) return;

    // 탭 활성 스타일 반영.
    tabs.querySelectorAll(".console-tab").forEach(function (btn) {
      var active = btn.getAttribute("data-console-tab") === _consoleTab;
      btn.setAttribute("aria-selected", active ? "true" : "false");
      btn.classList.toggle("bg-primary", active);
      btn.classList.toggle("text-white", active);
      btn.classList.toggle("border", !active);
      btn.classList.toggle("border-border", !active);
      btn.classList.toggle("hover:bg-muted", !active);
    });

    body.innerHTML = consoleBodyHtml(_consoleTab);

    var accountSel = document.getElementById("console-account-select");
    if (accountSel) {
      accountSel.addEventListener("change", function () {
        _consoleSel[_consoleTab].account = accountSel.value;
        renderConsoleLauncher(); // 복사 명령·열기 URL을 갱신.
      });
    }
    var regionSel = document.getElementById("console-region-select");
    if (regionSel) {
      regionSel.addEventListener("change", function () {
        _consoleSel[_consoleTab].region = regionSel.value;
        renderConsoleLauncher();
      });
    }

    var openBtn = document.getElementById("console-open-btn");
    if (openBtn) {
      openBtn.addEventListener("click", function () {
        // 사용자 제스처 내에서 동기적으로 연다(팝업 차단 회피). opener 접근 차단.
        window.open(consoleUrlFor(_consoleTab), "_blank", "noopener,noreferrer");
      });
    }
    body.querySelectorAll(".console-copy").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var cmd = btn.getAttribute("data-copy") || "";
        var done = function () {
          var original = btn.innerHTML;
          btn.innerHTML = '<span class="inline-flex items-center gap-1">' + MCUI.icons.check + "복사됨</span>";
          setTimeout(function () { btn.innerHTML = original; }, 1500);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(cmd).then(done, function () {});
        }
      });
    });
  }

  function initConsoleLauncher(accounts, items) {
    // provider별 계정 목록(= /cloud-accounts는 id 오름차순). 라벨은 account_label 우선.
    (accounts || []).forEach(function (a) {
      if (!_consoleAccounts[a.provider]) return;
      var label = a.account_label ? a.account_label + " · " + a.external_account_id : a.external_account_id;
      _consoleAccounts[a.provider].push({ value: a.external_account_id, label: label });
    });
    // provider별 최빈 region을 리소스에서 유추.
    var counts = { aws: {}, azure: {}, gcp: {} };
    (items || []).forEach(function (r) {
      var p = r.cloud_account && r.cloud_account.provider;
      if (!counts[p] || !r.region) return;
      counts[p][r.region] = (counts[p][r.region] || 0) + 1;
    });
    ["aws", "gcp"].forEach(function (p) {
      var best = "", bestN = 0;
      Object.keys(counts[p]).forEach(function (region) {
        if (counts[p][region] > bestN) { best = region; bestN = counts[p][region]; }
      });
      _consoleInferredRegion[p] = best;
    });

    // 초기 선택값: 계정=첫 계정, 리전=유추값(없으면 AWS는 폴백, GCP는 허용목록 첫 값).
    PLATFORMS.forEach(function (p) {
      _consoleSel[p].account = _consoleAccounts[p].length ? _consoleAccounts[p][0].value : "";
    });
    _consoleSel.aws.region = _consoleInferredRegion.aws || AWS_REGION_FALLBACK;
    _consoleSel.gcp.region = _consoleInferredRegion.gcp || (CONSOLE_REGIONS.gcp[0] && CONSOLE_REGIONS.gcp[0][0]) || "";

    var tabs = document.getElementById("console-launcher-tabs");
    if (tabs) {
      tabs.querySelectorAll(".console-tab").forEach(function (btn) {
        btn.addEventListener("click", function () {
          _consoleTab = btn.getAttribute("data-console-tab");
          renderConsoleLauncher();
        });
      });
    }
    renderConsoleLauncher();
  }

  // 월별 추이 조회 구간: 이번 달을 포함해 최근 6개월(1일 시작 ~ 내일, API는 끝 제외 경계).
  function trendQueryRange() {
    var today = new Date();
    var start = new Date(today.getFullYear(), today.getMonth() - 5, 1);
    var end = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
    function iso(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
    return "period_start=" + iso(start) + "&period_end=" + iso(end);
  }

  function init() {
    if (!window.MCPApi) return;

    // /resources/summary(전체 개수·provider별 개수)와 /resources(항목별 cost_summary 포함)를
    // 같이 기다린다 — 카테고리·리전 집계는 /resources 쪽 데이터로 하고, 그 결과를 요약 카드/
    // 클라우드별 카드에도 같이 써야 해서 두 응답이 다 와야 렌더링이 정확하다(따로 부르면 순서에
    // 따라 클라우드별 카드가 비용 없이 먼저 그려질 수 있음). 비용은 /costs/summary·
    // /costs/breakdown·/costs/trend(실측 우선, app/cost/query.py)로 따로 받는다.
    // .catch(()=>null) 대신 {ok, value|error}로 감싼다 — null로 뭉개면 "조회 실패"와
    // "데이터 없음"이 같은 모양이 되어 실패한 조회가 0/빈 상태로 보인다(비용 파트 PR 6,
    // docs/비용_개발문서 QA-01과 같은 원칙).
    function settle(promise) {
      return promise.then(
        function (v) { return { ok: true, value: v }; },
        function (e) { return { ok: false, error: e }; }
      );
    }
    Promise.all([
      settle(MCPApi.request("/resources/summary")),
      settle(MCPApi.request("/resources")),
      settle(MCPApi.request("/cloud-accounts")),
      settle(MCPApi.request("/costs/summary")),
      settle(MCPApi.request("/costs/breakdown?dimension=category&top_n=6")),
      settle(MCPApi.request("/costs/trend?" + trendQueryRange() + "&granularity=monthly&group_by=provider")),
    ]).then(function (results) {
      var summaryRes = results[0], itemsRes = results[1], accountsRes = results[2];
      var costSummaryRes = results[3], costBreakdownRes = results[4], costTrendRes = results[5];
      var summary = summaryRes.ok ? summaryRes.value : null;
      var items = itemsRes.ok ? (itemsRes.value && itemsRes.value.items) || [] : [];
      var accounts = accountsRes.ok ? (accountsRes.value && accountsRes.value.items) || [] : [];
      var costEstimateFallback = computeCostAggregates(items);
      renderResourceSummary(summary, costSummaryRes, costEstimateFallback, { summaryFailed: !summaryRes.ok, itemsFailed: !itemsRes.ok });
      renderCategoriesAndRegions(items);
      renderCostBreakdown(costBreakdownRes, costEstimateFallback, !itemsRes.ok);
      renderCostTrendChart(costTrendRes);
      initConsoleLauncher(accounts, items);
    });

    loadRecentActivity();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
