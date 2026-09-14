/* DASH-01 대시보드 실 데이터 연동.
 *
 * 지금은 AWS만 프로비저닝/리소스 러너가 있지만, provider를 하드코딩하지 않고 실제 API 응답
 * (`/resources/summary`의 by_provider, `/resources`의 각 항목)을 그대로 반복 처리한다 —
 * Azure/GCP 리소스가 나중에 실제로 쌓이기 시작해도 이 파일을 고칠 필요가 없다(PLATFORMS 배열에
 * 이미 셋 다 들어있고, 데이터가 없는 provider는 0으로 표시될 뿐이다).
 *
 * 비용(Cost Explorer/Cost Management 등) 수집은 이번 작업 범위 밖이다 — 관련 카드는
 * dashboard.html에 정직한 "준비 중" placeholder로 남겨뒀고 이 파일은 건드리지 않는다.
 */
(function () {
  "use strict";

  var PLATFORMS = ["aws", "azure", "gcp"];
  var PLATFORM_LABEL = { aws: "AWS", azure: "Azure", gcp: "GCP" };
  var PROVIDER_COLOR = { aws: "#FF9900", azure: "#0078D4", gcp: "#34A853" };

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

  // ── 전체 리소스 수 + 클라우드별 카드(/resources/summary) ────────────────────
  function renderResourceSummary(summary) {
    var byProvider = (summary && summary.by_provider) || [];

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

    var container = document.getElementById("dash-cloud-cards");
    if (container) {
      container.innerHTML = PLATFORMS.map(function (p) {
        var count = countByProvider[p] || 0;
        return (
          '<div class="rounded-2xl border border-border bg-surface p-5">' +
          '<div class="flex items-center justify-between">' +
          '<p class="font-semibold">' + PLATFORM_LABEL[p] + "</p>" +
          '<span class="rounded border border-border px-1.5 text-[11px] text-muted-foreground">비용 미구현</span>' +
          "</div>" +
          '<p class="mt-2 text-2xl font-extrabold">' + count + "개 리소스</p>" +
          '<p class="mt-1 text-xs text-muted-foreground">리소스 수는 실시간 · 비용은 수집 기능 구현 후 표시됩니다.</p>' +
          "</div>"
        );
      }).join("");
    }
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
        var prov = r.provider || "unknown";
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
  function markerSize(count) {
    return Math.round(18 + Math.min(count, 10) * 2.4); // 18~42px
  }

  // provider 비율에 따른 배경. 단일 provider면 단색, 여러 개면 conic-gradient 파이.
  function markerBackground(providers) {
    var entries = Object.keys(providers).map(function (p) { return { p: p, c: providers[p] }; });
    if (entries.length === 1) return PROVIDER_COLOR[entries[0].p] || "#94a3b8";
    var total = entries.reduce(function (s, e) { return s + e.c; }, 0);
    var acc = 0;
    var stops = entries.map(function (e) {
      var start = (acc / total) * 360;
      acc += e.c;
      var end = (acc / total) * 360;
      return (PROVIDER_COLOR[e.p] || "#94a3b8") + " " + start + "deg " + end + "deg";
    });
    return "conic-gradient(" + stops.join(", ") + ")";
  }

  function siteTooltip(site, regionsAtSite, regionProvider) {
    var lines = [site.label];
    regionsAtSite.forEach(function (region) {
      var provs = regionProvider[region];
      Object.keys(provs).sort().forEach(function (p) {
        lines.push((PLATFORM_LABEL[p] || p) + " " + region + ": " + provs[p] + "개");
      });
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
      markersEl.innerHTML = Object.keys(bySite).map(function (siteKey) {
        var s = bySite[siteKey];
        var site = SITES[siteKey];
        var d = markerSize(s.total);
        var tip = siteTooltip(site, s.regions, regionProvider);
        return (
          '<div class="absolute" style="left:' + site.x + "%;top:" + site.y + "%;transform:translate(-50%,-50%)\" title=\"" +
          escHtml(tip) + '">' +
          '<div style="width:' + d + "px;height:" + d + "px;border-radius:9999px;background:" + markerBackground(s.providers) +
          ';box-shadow:0 0 0 2px #fff,0 1px 3px rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;">' +
          '<span style="font-size:11px;font-weight:700;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.6)">' + s.total + "</span>" +
          "</div></div>"
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
        '<td class="py-2.5 pr-4">' + PLATFORM_LABEL[entry.platform] + "</td>" +
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

  function init() {
    if (!window.MCPApi) return;

    MCPApi.request("/resources/summary")
      .then(renderResourceSummary)
      .catch(function () { renderResourceSummary(null); });

    MCPApi.request("/resources")
      .then(function (data) { renderCategoriesAndRegions((data && data.items) || []); })
      .catch(function () { renderCategoriesAndRegions([]); });

    loadRecentActivity();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
