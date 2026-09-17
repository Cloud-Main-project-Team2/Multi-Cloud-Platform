/* DASH-01 대시보드 실 데이터 연동.
 *
 * 지금은 AWS만 프로비저닝/리소스 러너가 있지만, provider를 하드코딩하지 않고 실제 API 응답
 * (`/resources/summary`의 by_provider, `/resources`의 각 항목)을 그대로 반복 처리한다 —
 * Azure/GCP 리소스가 나중에 실제로 쌓이기 시작해도 이 파일을 고칠 필요가 없다(PLATFORMS 배열에
 * 이미 셋 다 들어있고, 데이터가 없는 provider는 0으로 표시될 뿐이다).
 *
 * 비용은 `app/pricing.py`의 정가(list price) 기반 추정치(`resources.estimated_monthly_cost`,
 * `/resources` 응답의 `cost_summary`)를 쓴다 — 실제 CSP 비용 API(Cost Explorer 등) 연동은 아직
 * 없다. 그래서 "예상 총 비용"/"클라우드별"/"서비스별 비중"은 실제 값이지만 전부 "추정치" 배지를
 * 붙인다. 월별 추이·예산 임계값은 시계열 스냅샷/예산 설정 자체가 DB에 없어 여전히 "준비 중"이다.
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

  // ── 비용 집계(/resources 각 항목의 cost_summary.estimated_monthly_cost) ────
  // app/pricing.py가 정가 기준으로 채워준 값을 그대로 합산한다 — 사용량 기반 서비스(S3/CDN 등)나
  // 허용 목록 밖 스펙은 cost_summary 자체가 없어 자동으로 missing 카운트에 들어간다.
  function computeCostAggregates(items) {
    var totalCost = 0, hasAny = false, missing = 0;
    var byProvider = {}, byCategory = {};
    items.forEach(function (r) {
      var raw = r.cost_summary && r.cost_summary.estimated_monthly_cost;
      var amount = raw != null ? parseFloat(raw) : NaN;
      if (isNaN(amount)) { missing++; return; }
      hasAny = true;
      totalCost += amount;
      var prov = r.cloud_account && r.cloud_account.provider;
      if (prov) byProvider[prov] = (byProvider[prov] || 0) + amount;
      var cat = r.service && r.service.category;
      if (cat) byCategory[cat] = (byCategory[cat] || 0) + amount;
    });
    return { totalCost: totalCost, hasAny: hasAny, missing: missing, byProvider: byProvider, byCategory: byCategory };
  }

  // ── 전체 리소스 수 + 예상 총 비용 + 클라우드별 카드(/resources/summary + costInfo) ─
  function renderResourceSummary(summary, costInfo) {
    var byProvider = (summary && summary.by_provider) || [];
    costInfo = costInfo || { totalCost: 0, hasAny: false, missing: 0, byProvider: {}, byCategory: {} };

    var totalEl = document.getElementById("dash-total-resources");
    if (totalEl) totalEl.textContent = (summary && summary.total_resources) || 0;

    var breakdownEl = document.getElementById("dash-total-resources-breakdown");
    if (breakdownEl) {
      breakdownEl.textContent = byProvider.length
        ? byProvider.map(function (p) { return (PLATFORM_LABEL[p.provider] || p.provider) + " " + p.count; }).join(" · ")
        : "아직 리소스가 없습니다.";
    }

    var countByProvider = {};
    byProvider.forEach(function (p) { countByProvider[p.provider] = p.count; });

    var costEl = document.getElementById("dash-total-cost");
    var costNoteEl = document.getElementById("dash-total-cost-note");
    if (costEl) costEl.textContent = costInfo.hasAny ? "$" + costInfo.totalCost.toFixed(2) + "/mo" : "—";
    if (costNoteEl) {
      if (!costInfo.hasAny) {
        costNoteEl.textContent = "정가 기준으로 추정 가능한 리소스가 없습니다(사용량 기반 서비스만 있거나 리소스 없음).";
      } else if (costInfo.missing > 0) {
        costNoteEl.textContent = "정가(list price) 기준 추정 · 사용량 기반 리소스 " + costInfo.missing + "개는 제외됨.";
      } else {
        costNoteEl.textContent = "정가(list price) 기준 추정 — 실제 청구액과 다를 수 있습니다.";
      }
    }

    var container = document.getElementById("dash-cloud-cards");
    if (container) {
      container.innerHTML = PLATFORMS.map(function (p) {
        var count = countByProvider[p] || 0;
        var cost = costInfo.byProvider[p];
        var costText = cost != null ? "$" + cost.toFixed(2) + "/mo · 추정치" : "추정 불가";
        return (
          '<div class="rounded-2xl border border-border bg-surface p-5">' +
          '<div class="flex items-center justify-between">' +
          '<p class="flex items-center gap-2 font-semibold">' +
          '<img src="' + PROVIDER_ICON[p] + '" alt="" class="h-5 w-auto align-middle" />' +
          PLATFORM_LABEL[p] + "</p>" +
          '<span class="rounded border border-border px-1.5 text-[11px] text-muted-foreground">' + costText + "</span>" +
          "</div>" +
          '<p class="mt-2 text-2xl font-extrabold">' + count + "개 리소스</p>" +
          '<p class="mt-1 text-xs text-muted-foreground">리소스 수·비용 모두 실시간 조회(비용은 정가 기준 추정치)</p>' +
          "</div>"
        );
      }).join("");
    }
  }

  // ── 서비스별 비용 비중(카테고리별 합산 막대) ────────────────────────────────
  function renderCostBreakdown(costInfo) {
    var el = document.getElementById("dash-cost-by-category");
    if (!el) return;
    if (!costInfo.hasAny) {
      el.innerHTML = '<p class="text-sm text-muted-foreground">추정 가능한 리소스가 없습니다.</p>';
      return;
    }
    var entries = Object.keys(costInfo.byCategory)
      .map(function (cat) { return { cat: cat, amount: costInfo.byCategory[cat] }; })
      .sort(function (a, b) { return b.amount - a.amount; });

    el.innerHTML = entries.map(function (e) {
      var pct = costInfo.totalCost > 0 ? (e.amount / costInfo.totalCost) * 100 : 0;
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

  // 개수 → 마커 지름(px). 개수가 많을수록 큰 점.
  // 원 크기: 숫자 라벨을 없앤 대신 크기가 대략적 규모 힌트다. 작은 점(dot)에 가깝게 잡아
  // 같은 지점에 여러 클라우드 원이 겹쳐도 서로 또렷이 구분되게 한다(예전 18~42px → 7~13px).
  function markerSize(count) {
    return Math.round(7 + Math.min(count, 10) * 0.6); // 7~13px
  }

  // 문자열 → 0..1 결정적 유사난수(FNV-1a). 재렌더 시 원이 튀지 않도록 무작위 대신 해시를 쓴다.
  function hash01(str) {
    var h = 2166136261;
    for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return ((h >>> 0) % 1000) / 1000;
  }

  // 같은 지점(site)의 클라우드 원들을 지점 중심 주변에 흩뿌린다. index로 대략의 방향을 균등
  // 분배하고(서로 반대편으로), 해시로 각도·반경을 흔들어 '흩어진' 느낌을 준다. 반경은 MAXR로
  // 제한해 클라우드 종류가 늘어도 구역을 벗어나지 않는다. 하나여도 살짝 흔들어 정중앙 고정을 피한다.
  function scatterOffset(siteKey, provider, index, count) {
    var seed = siteKey + "|" + provider;
    if (count <= 1) {
      return { dx: Math.round((hash01(seed) - 0.5) * 10), dy: Math.round((hash01(provider + siteKey) - 0.5) * 10) };
    }
    var MAXR = 16; // px — 구역 반경 상한
    var base = (index / count) * 2 * Math.PI - Math.PI / 2; // 균등 분배(서로 반대 방향)
    var angle = base + (hash01(seed) - 0.5) * (Math.PI / count); // 방향 소폭 흔들기
    var r = MAXR * (0.6 + 0.4 * hash01(provider + siteKey)); // 반경 ~10~16px
    return { dx: Math.round(Math.cos(angle) * r), dy: Math.round(Math.sin(angle) * r) };
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
      var siteKey = REGION_SITE[region];
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
      // 지점당 원 하나(파이+숫자) 대신, 클라우드마다 개별 색 원을 그린다 — 같은 리전에
      // AWS·GCP가 함께 있어도 서로 다른 색 원이 각각 보이도록 링 형태로 살짝 흩뿌린다.
      markersEl.innerHTML = Object.keys(bySite).map(function (siteKey) {
        var s = bySite[siteKey];
        var site = SITES[siteKey];
        var provs = Object.keys(s.providers);
        return provs.map(function (p, i) {
          var d = markerSize(s.providers[p]);
          var off = scatterOffset(siteKey, p, i, provs.length);
          var tip = providerTooltip(site, p, s.regions, regionProvider);
          return (
            '<div class="absolute" style="left:' + site.x + "%;top:" + site.y +
            "%;transform:translate(calc(-50% + " + off.dx + "px),calc(-50% + " + off.dy + "px));z-index:" + (10 + i) + '" title="' +
            escHtml(tip) + '">' +
            '<div style="width:' + d + "px;height:" + d + "px;border-radius:9999px;background:" + (PROVIDER_COLOR[p] || "#94a3b8") +
            ';box-shadow:0 0 0 2px #fff,0 1px 3px rgba(0,0,0,.35);"></div>' +
            "</div>"
          );
        }).join("");
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
          '<td class="py-2.5 pr-4">' + escHtml(REGION_LABEL[region] || "—") + "</td>" +
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
      renderActivityRows(all.slice(0, 8));
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

  function init() {
    if (!window.MCPApi) return;

    // /resources/summary(전체 개수·provider별 개수)와 /resources(항목별 cost_summary 포함)를
    // 같이 기다린다 — 비용 집계는 /resources 쪽 데이터로 하고, 그 결과를 요약 카드/클라우드별
    // 카드에도 같이 써야 해서 두 응답이 다 와야 렌더링이 정확하다(따로 부르면 순서에 따라
    // 클라우드별 카드가 비용 없이 먼저 그려질 수 있음).
    Promise.all([
      MCPApi.request("/resources/summary").catch(function () { return null; }),
      MCPApi.request("/resources").catch(function () { return null; }),
      MCPApi.request("/cloud-accounts").catch(function () { return null; }),
    ]).then(function (results) {
      var summary = results[0];
      var items = (results[1] && results[1].items) || [];
      var accounts = (results[2] && results[2].items) || [];
      var costInfo = computeCostAggregates(items);
      renderResourceSummary(summary, costInfo);
      renderCategoriesAndRegions(items);
      renderCostBreakdown(costInfo);
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
