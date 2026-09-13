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
    var regionCounts = {};

    items.forEach(function (r) {
      var cat = r.service && r.service.category;
      if (categoryCounts[cat]) {
        categoryCounts[cat].total++;
        if (r.status && RUNNING_LIKE[r.status]) categoryCounts[cat].running++;
        else if (r.status) categoryCounts[cat].other++;
      }
      if (r.region) regionCounts[r.region] = (regionCounts[r.region] || 0) + 1;
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

    var regionKeys = Object.keys(regionCounts).sort();

    var totalRegionsEl = document.getElementById("dash-total-regions");
    if (totalRegionsEl) totalRegionsEl.textContent = regionKeys.length;

    var listEl = document.getElementById("dash-region-list");
    if (listEl) {
      listEl.innerHTML = regionKeys.length
        ? regionKeys.map(function (region) {
            return (
              '<li class="flex items-center justify-between">' +
              "<span>" + escHtml(region) + "</span>" +
              '<span class="text-muted-foreground">' + regionCounts[region] + "개</span></li>"
            );
          }).join("")
        : '<li class="text-muted-foreground">아직 리전 정보가 있는 리소스가 없습니다.</li>';
    }
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
