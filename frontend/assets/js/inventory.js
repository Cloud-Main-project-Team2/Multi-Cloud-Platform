/* 인벤토리(INV-01/INV-02) — 실API 연동.
 *
 * GET /resources(+summary)로 실데이터를 그리고, 필터·검색·정렬은 이미 받아온 전체 목록 위에서
 * 클라이언트에서 처리한다(서버는 include_stale/provider 등 쿼리도 지원하지만, 페이지네이션
 * 정책이 아직 없어 이번 화면은 "한 번에 다 받아서 클라이언트 필터"로 통일했다).
 *
 * "새로고침" = POST /sync-jobs로 실제 CSP 재수집을 트리거하고, 완료될 때까지 버튼을
 * 비활성화한 채 GET /sync-jobs/{id}를 폴링한다(요청하신 UX). 페이지를 새로 열었을 때도
 * 이미 진행 중인 job이 있으면 그 job을 이어서 폴링한다.
 */
(function () {
  "use strict";

  var tbody = document.getElementById("inv-tbody");
  if (!tbody) return;

  var loadingRow = document.getElementById("inv-loading-row");
  var emptyRow = document.getElementById("inv-empty-row");
  var totalCountEl = document.getElementById("total-count");
  var lastSyncedEl = document.getElementById("last-synced");
  var totalCostEl = document.getElementById("total-cost");
  var syncBanner = document.getElementById("sync-banner");
  var refreshBtn = document.getElementById("refresh-btn");
  var selCount = document.getElementById("sel-count");
  var selectAll = document.getElementById("select-all");
  var actionButtons = Array.prototype.slice.call(document.querySelectorAll(".row-action"));

  var fCloud = document.getElementById("f-cloud");
  var fCategory = document.getElementById("f-category");
  var fAccount = document.getElementById("f-account");
  var fStatus = document.getElementById("f-status");
  var fRegion = document.getElementById("f-region");
  var fTag = document.getElementById("f-tag");
  var fReset = document.getElementById("f-reset");
  var searchField = document.getElementById("search-field");
  var searchInput = document.getElementById("search-input");
  var searchBtn = document.getElementById("search-btn");
  var sortSelect = document.getElementById("sort-select");

  var modalTitle = document.getElementById("inv-modal-title");
  var modalSubtitle = document.getElementById("inv-modal-subtitle");
  var modalIdEl = document.getElementById("inv-modal-id");
  var modalAccount = document.getElementById("inv-modal-account");
  var modalStatus = document.getElementById("inv-modal-status");
  var modalSeen = document.getElementById("inv-modal-seen");
  var modalCost = document.getElementById("inv-modal-cost");
  var modalTags = document.getElementById("inv-modal-tags");
  var modalConsole = document.getElementById("inv-modal-console");

  var PROVIDER_LABELS = { aws: "AWS", azure: "Azure", gcp: "GCP" };
  var PROVIDER_ICON = { aws: "assets/imgs/aws.png", azure: "assets/imgs/azure.png", gcp: "assets/imgs/gcp.png" };
  var SYNC_STATUS_LABELS = {
    pending: "대기", running: "수집중", success: "완료",
    failed: "실패", partial_success: "부분 완료", cancelled: "취소됨",
  };
  var SYNC_STATUS_DOT = {
    pending: "var(--muted-foreground)", running: "var(--yellow)", success: "var(--primary)",
    failed: "#c0392b", partial_success: "var(--yellow)", cancelled: "var(--muted-foreground)",
  };
  var ERROR_MESSAGES = {
    UNSUPPORTED_OPERATION: "이 리소스 종류는 아직 이 동작을 지원하지 않습니다.",
    CLOUD_PERMISSION_DENIED: "사용 가능한 검증된 자격 증명이 없거나 권한이 부족합니다.",
    RESOURCE_ALREADY_DELETED: "이미 삭제된 리소스입니다.",
    RESOURCE_STALE: "최근 동기화에서 확인되지 않은 리소스입니다. 다시 동기화한 뒤 시도해 주세요.",
    RESOURCE_NOT_FOUND: "리소스를 찾을 수 없습니다.",
    JOB_ALREADY_RUNNING: "이미 진행 중인 동기화가 있습니다.",
    PROVIDER_API_ERROR: "클라우드 API 호출에 실패했습니다.",
    BucketNotEmpty: "버킷이 비어 있지 않습니다.",
  };

  var allResources = [];
  var selectedIds = new Set();
  var currentModalResourceId = null;
  var pollTimer = null;

  function errorMessage(err) {
    return ERROR_MESSAGES[err && err.code] || (err && err.message) || "요청 처리 중 오류가 발생했습니다.";
  }
  function providerLabel(p) { return PROVIDER_LABELS[p] || p; }
  function pad(n) { return n < 10 ? "0" + n : "" + n; }

  function formatRelative(iso) {
    var diffMs = Date.now() - new Date(iso).getTime();
    var minutes = Math.floor(diffMs / 60000);
    if (minutes < 1) return "방금 전";
    if (minutes < 60) return minutes + "분 전";
    var hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + "시간 전";
    return Math.floor(hours / 24) + "일 전";
  }
  function formatDateTime(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    var abs = d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
    return abs + " (" + formatRelative(iso) + ")";
  }
  function formatCost(cost) {
    if (!cost) return "—";
    var actual = cost.collected_cost_amount;
    var amount = actual != null ? actual : cost.estimated_monthly_cost;
    if (amount == null) return "—";
    var prefix = actual != null ? "" : "추정 ";
    var symbol = cost.currency === "USD" ? "$" : (cost.currency ? cost.currency + " " : "");
    return prefix + symbol + parseFloat(amount).toFixed(2);
  }
  function costValue(r) {
    if (!r.cost_summary) return 0;
    var v = r.cost_summary.collected_cost_amount != null ? r.cost_summary.collected_cost_amount : r.cost_summary.estimated_monthly_cost;
    return v ? parseFloat(v) : 0;
  }
  function isRunningStatus(status) { return /RUNNING|AVAILABLE/i.test(status || ""); }

  // 콘솔 딥링크는 최선 노력(best-effort)이다 — 정확한 형식은 리소스 종류마다 다르다.
  function consoleUrl(r) {
    var provider = r.cloud_account.provider;
    var region = r.region || "";
    if (provider === "aws") {
      if (r.service.service_code === "ec2" && r.original_resource_type === "EC2 Instance") {
        return "https://" + region + ".console.aws.amazon.com/ec2/home?region=" + region + "#InstanceDetails:instanceId=" + encodeURIComponent(r.external_resource_id);
      }
      if (r.service.service_code === "s3") {
        return "https://s3.console.aws.amazon.com/s3/buckets/" + encodeURIComponent(r.external_resource_id);
      }
      return "https://console.aws.amazon.com/";
    }
    if (provider === "azure") {
      // external_resource_id는 Azure ARM 리소스 ID 전체(예: /subscriptions/.../resourceGroups/...)
      return "https://portal.azure.com/#@/resource" + r.external_resource_id;
    }
    if (provider === "gcp") {
      return "https://console.cloud.google.com/compute/instancesDetail/zones/" + encodeURIComponent(region) +
        "/instances/" + encodeURIComponent(r.external_resource_id) + "?project=" + encodeURIComponent(r.cloud_account.external_account_id);
    }
    return "#";
  }

  // --- 필터/정렬 -----------------------------------------------------------------------

  function getFilteredSorted() {
    var provider = fCloud.value;
    var category = fCategory.value;
    var accountId = fAccount.value;
    var status = fStatus.value;
    var region = fRegion.value;
    var tag = fTag.value;
    var field = searchField.value;
    var kw = searchInput.value.trim().toLowerCase();

    var list = allResources.filter(function (r) {
      if (provider && r.cloud_account.provider !== provider) return false;
      if (category && r.service.category !== category) return false;
      if (accountId && r.cloud_account.id !== accountId) return false;
      if (status && (r.status || "") !== status) return false;
      if (region && (r.region || "") !== region) return false;
      if (tag) {
        var sep = tag.indexOf(":");
        var k = tag.slice(0, sep);
        var v = tag.slice(sep + 1);
        if ((r.tags || {})[k] !== v) return false;
      }
      if (kw) {
        var hay;
        if (field === "service") hay = r.service.display_name + " " + r.service.service_code;
        else if (field === "region") hay = r.region || "";
        else if (field === "account") hay = (r.cloud_account.account_label || "") + " " + r.cloud_account.external_account_id;
        else hay = (r.name || "") + " " + r.external_resource_id + " " + r.original_resource_type;
        if (hay.toLowerCase().indexOf(kw) === -1) return false;
      }
      return true;
    });

    var mode = sortSelect.value;
    list.sort(function (a, b) {
      if (mode === "name") return (a.name || a.external_resource_id).localeCompare(b.name || b.external_resource_id);
      var ca = costValue(a), cb = costValue(b);
      return mode === "cost-asc" ? ca - cb : cb - ca;
    });
    return list;
  }

  function fillSelect(select, values, allLabel) {
    var current = select.value;
    select.innerHTML = "";
    var allOpt = document.createElement("option");
    allOpt.value = "";
    allOpt.textContent = allLabel;
    select.appendChild(allOpt);
    values.forEach(function (v) {
      var opt = document.createElement("option");
      opt.value = v;
      opt.textContent = v;
      select.appendChild(opt);
    });
    if (values.indexOf(current) !== -1) select.value = current;
  }
  function uniqueSorted(arr) {
    var seen = {};
    var out = [];
    arr.forEach(function (v) { if (v && !seen[v]) { seen[v] = true; out.push(v); } });
    out.sort();
    return out;
  }
  function populateDynamicOptions() {
    fillSelect(fStatus, uniqueSorted(allResources.map(function (r) { return r.status; })), "상태: 전체");
    fillSelect(fRegion, uniqueSorted(allResources.map(function (r) { return r.region; })), "리전: 전체");
    var tagPairs = [];
    allResources.forEach(function (r) {
      Object.keys(r.tags || {}).forEach(function (k) {
        var pair = k + ":" + r.tags[k];
        if (tagPairs.indexOf(pair) === -1) tagPairs.push(pair);
      });
    });
    fillSelect(fTag, tagPairs.sort(), "태그: 전체");
  }
  function populateAccountOptions(accounts) {
    fAccount.innerHTML = '<option value="">연결 계정: 전체</option>';
    accounts.forEach(function (a) {
      var opt = document.createElement("option");
      opt.value = a.id;
      opt.textContent = a.account_label || (providerLabel(a.provider) + ":" + a.external_account_id);
      fAccount.appendChild(opt);
    });
  }

  // --- 렌더링 --------------------------------------------------------------------------

  function buildRow(r) {
    var tr = document.createElement("tr");
    tr.className = "border-b border-border";
    tr.dataset.resourceId = r.id;

    var tdCheck = document.createElement("td");
    tdCheck.className = "px-3 py-3";
    var checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selectedIds.has(r.id);
    checkbox.addEventListener("change", function () {
      if (checkbox.checked) selectedIds.add(r.id); else selectedIds.delete(r.id);
      updateSelectionUI();
    });
    tdCheck.appendChild(checkbox);
    tr.appendChild(tdCheck);

    var tdIcon = document.createElement("td");
    tdIcon.className = "px-3 py-3";
    var img = document.createElement("img");
    img.src = PROVIDER_ICON[r.cloud_account.provider] || "";
    img.alt = providerLabel(r.cloud_account.provider);
    img.className = "inline h-5 w-auto align-middle";
    tdIcon.appendChild(img);
    tr.appendChild(tdIcon);

    function cell(text, extraClass) {
      var td = document.createElement("td");
      td.className = "px-3 py-3" + (extraClass ? " " + extraClass : "");
      td.textContent = text;
      tr.appendChild(td);
      return td;
    }

    cell(r.original_resource_type);
    cell(r.name || r.external_resource_id, "font-medium");
    cell(r.region || "—");
    cell(r.cloud_account.account_label || r.cloud_account.external_account_id);
    cell(formatCost(r.cost_summary));

    var tdTags = document.createElement("td");
    tdTags.className = "px-3 py-3";
    var tagKeys = Object.keys(r.tags || {});
    if (tagKeys.length) {
      var span = document.createElement("span");
      span.className = "rounded bg-muted px-1.5 py-0.5 text-[11px]";
      span.textContent = tagKeys[0] + ":" + r.tags[tagKeys[0]] + (tagKeys.length > 1 ? " +" + (tagKeys.length - 1) : "");
      tdTags.appendChild(span);
    } else {
      tdTags.classList.add("text-muted-foreground");
      tdTags.textContent = "—";
    }
    tr.appendChild(tdTags);

    var tdStatus = document.createElement("td");
    tdStatus.className = "px-3 py-3";
    var badge = document.createElement("span");
    if (r.is_stale) {
      badge.className = "rounded-full px-2 py-0.5 text-[11px] text-yellow";
      badge.style.border = "1px solid var(--yellow)";
      badge.textContent = (r.status || "UNKNOWN") + " · 오래됨";
    } else {
      badge.className = "rounded-full bg-muted px-2 py-0.5 text-[11px] " + (isRunningStatus(r.status) ? "text-primary" : "text-muted-foreground");
      badge.textContent = r.status || "UNKNOWN";
    }
    tdStatus.appendChild(badge);
    tr.appendChild(tdStatus);

    var tdDetail = document.createElement("td");
    tdDetail.className = "px-3 py-3";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-muted";
    btn.textContent = "상세보기";
    btn.setAttribute("data-modal-open", "#inv-modal");
    btn.addEventListener("click", function () { openDetail(r.id); });
    tdDetail.appendChild(btn);
    tr.appendChild(tdDetail);

    return tr;
  }

  function render() {
    var list = getFilteredSorted();
    Array.prototype.slice.call(tbody.querySelectorAll("tr[data-resource-id]")).forEach(function (row) { row.remove(); });
    emptyRow.hidden = list.length !== 0 || !loadingRow.hidden;
    list.forEach(function (r) { tbody.appendChild(buildRow(r)); });
    updateSelectionUI();
  }

  function updateSelectionUI() {
    selCount.textContent = selectedIds.size + "개 선택됨";
    actionButtons.forEach(function (b) {
      b.disabled = selectedIds.size === 0;
      b.classList.toggle("opacity-50", selectedIds.size === 0);
      b.classList.toggle("cursor-not-allowed", selectedIds.size === 0);
    });
    var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr[data-resource-id]"));
    selectAll.checked = rows.length > 0 && rows.every(function (r) { return selectedIds.has(r.dataset.resourceId); });
  }

  function updateTotalCost() {
    var sums = {};
    allResources.forEach(function (r) {
      if (!r.cost_summary) return;
      var amount = r.cost_summary.collected_cost_amount != null ? r.cost_summary.collected_cost_amount : r.cost_summary.estimated_monthly_cost;
      if (amount == null) return;
      var currency = r.cost_summary.currency || "?";
      sums[currency] = (sums[currency] || 0) + parseFloat(amount);
    });
    var currencies = Object.keys(sums);
    if (currencies.length === 1) {
      var symbol = currencies[0] === "USD" ? "$" : currencies[0] + " ";
      totalCostEl.textContent = symbol + sums[currencies[0]].toFixed(2) + "/mo";
    } else {
      totalCostEl.textContent = "—";
    }
  }

  // --- 데이터 로드 ----------------------------------------------------------------------

  function loadAccountsFilterOptions() {
    MCPApi.request("/cloud-accounts").then(function (data) {
      populateAccountOptions(data.items || []);
    }).catch(function () {});
  }

  function loadResources() {
    loadingRow.hidden = false;
    emptyRow.hidden = true;
    // 최근 동기화에서 확인되지 않은(=콘솔에서 삭제되는 등으로 사라진) 리소스는 목록에 표시하지
    // 않는다. 서버 기본값(include_stale 미지정)이 is_stale=true 행을 제외한다.
    return MCPApi.request("/resources").then(function (data) {
      allResources = data.items || [];
      loadingRow.hidden = true;
      populateDynamicOptions();
      updateTotalCost();
      render();
    }).catch(function (err) {
      loadingRow.querySelector("td").textContent = "불러오지 못했습니다: " + errorMessage(err);
    });
  }

  function loadSummary() {
    MCPApi.request("/resources/summary").then(function (data) {
      totalCountEl.textContent = "전체 리소스 " + data.total_resources;
      lastSyncedEl.textContent = data.last_synced_at ? "마지막 동기화 " + formatDateTime(data.last_synced_at) : "마지막 동기화 없음";
    }).catch(function () {});
  }

  // --- 동기화 --------------------------------------------------------------------------

  function renderSyncBanner(job) {
    syncBanner.innerHTML = "";
    if (!job) {
      var empty = document.createElement("span");
      empty.className = "text-muted-foreground";
      empty.textContent = '아직 동기화한 적이 없습니다. "새로고침"을 눌러 연결된 계정의 리소스를 가져오세요.';
      syncBanner.appendChild(empty);
      return;
    }
    var overall = document.createElement("span");
    overall.className = "font-medium";
    overall.textContent = SYNC_STATUS_LABELS[job.status] || job.status;
    syncBanner.appendChild(overall);

    if (job.provider_summary.length) {
      var sep = document.createElement("span");
      sep.className = "text-muted-foreground";
      sep.textContent = "·";
      syncBanner.appendChild(sep);
    }
    job.provider_summary.forEach(function (p) {
      var wrap = document.createElement("span");
      wrap.className = "flex items-center gap-1";
      var dot = document.createElement("span");
      dot.className = "h-2 w-2 rounded-full";
      dot.style.background = SYNC_STATUS_DOT[p.status] || "var(--muted-foreground)";
      wrap.appendChild(dot);
      wrap.appendChild(document.createTextNode(providerLabel(p.provider) + " " + (SYNC_STATUS_LABELS[p.status] || p.status)));
      syncBanner.appendChild(wrap);
    });
  }

  function setRefreshBusy(busy) {
    refreshBtn.disabled = busy;
    refreshBtn.classList.toggle("opacity-50", busy);
    refreshBtn.classList.toggle("cursor-not-allowed", busy);
    refreshBtn.textContent = busy ? "⟳ 수집 중…" : "⟳ 새로고침";
  }

  function pollJob(jobId) {
    clearTimeout(pollTimer);
    MCPApi.request("/sync-jobs/" + jobId).then(function (job) {
      renderSyncBanner(job);
      if (job.status === "pending" || job.status === "running") {
        pollTimer = setTimeout(function () { pollJob(jobId); }, 2000);
      } else {
        setRefreshBusy(false);
        loadResources();
        loadSummary();
      }
    }).catch(function () {
      setRefreshBusy(false);
    });
  }

  function loadLatestSyncStatus() {
    MCPApi.request("/sync-jobs").then(function (data) {
      var latest = (data.items || [])[0] || null;
      renderSyncBanner(latest);
      if (latest && (latest.status === "pending" || latest.status === "running")) {
        setRefreshBusy(true);
        pollJob(latest.id);
      }
    }).catch(function () {});
  }

  function triggerSync() {
    setRefreshBusy(true);
    MCPApi.request("/sync-jobs", { method: "POST", body: {} }).then(function (data) {
      pollJob(data.id);
    }).catch(function (err) {
      if (err.code === "JOB_ALREADY_RUNNING") {
        loadLatestSyncStatus();
        return;
      }
      setRefreshBusy(false);
      window.alert("동기화 요청에 실패했습니다: " + errorMessage(err));
    });
  }

  // --- 액션(start/stop/delete) ----------------------------------------------------------

  function runAction(action, resourceIds) {
    if (!resourceIds.length) return;
    var label = { start: "시작", stop: "중지", delete: "삭제" }[action] || action;
    if (!window.confirm(resourceIds.length + "개 리소스를 " + label + "하시겠습니까?")) return;

    MCPApi.request("/resources/action", {
      method: "POST",
      headers: { "X-Action-Confirmed": "true" },
      body: { action: action, resource_ids: resourceIds },
    }).then(function (data) {
      var succeeded = data.results.filter(function (r) { return r.status === "success"; }).length;
      var problems = data.results.filter(function (r) { return r.status !== "success"; });
      var message = succeeded + "개 성공";
      if (problems.length) {
        message += ", " + problems.length + "개 실패/거부: " + problems.map(function (p) {
          return "#" + p.resource_id + " " + errorMessage(p.error || {});
        }).join(", ");
      }
      window.alert(message);
      selectedIds.clear();
      loadResources();
      loadSummary();
      if (currentModalResourceId && resourceIds.indexOf(currentModalResourceId) !== -1) {
        MCPModal.close("#inv-modal");
      }
    }).catch(function (err) {
      window.alert("요청에 실패했습니다: " + errorMessage(err));
    });
  }

  // --- 상세 모달 ------------------------------------------------------------------------

  function openDetail(id) {
    currentModalResourceId = id;
    modalTitle.textContent = "불러오는 중…";
    modalSubtitle.textContent = "";
    [modalIdEl, modalAccount, modalStatus, modalSeen, modalCost, modalTags].forEach(function (el) { el.textContent = "—"; });
    modalConsole.href = "#";

    MCPApi.request("/resources/" + id).then(function (r) {
      modalTitle.textContent = r.name || r.external_resource_id;
      modalSubtitle.textContent = providerLabel(r.cloud_account.provider) + " · " + r.original_resource_type + (r.region ? " · " + r.region : "");
      modalIdEl.textContent = r.external_resource_id;
      modalAccount.textContent = r.cloud_account.account_label || r.cloud_account.external_account_id;
      modalStatus.textContent = (r.status || "UNKNOWN") + (r.is_stale ? " · 최근 동기화에서 확인 안 됨" : "");
      modalSeen.textContent = formatDateTime(r.first_seen_at) + " / " + formatDateTime(r.last_seen_at);
      modalCost.textContent = formatCost(r.cost_summary);
      var tagText = Object.keys(r.tags || {}).map(function (k) { return k + ":" + r.tags[k]; }).join(" · ");
      modalTags.textContent = tagText || "없음";
      modalConsole.href = consoleUrl(r);
    }).catch(function (err) {
      modalTitle.textContent = "불러오지 못했습니다";
      modalSubtitle.textContent = errorMessage(err);
    });
  }

  // --- 이벤트 바인딩 --------------------------------------------------------------------

  [fCloud, fCategory, fAccount, fStatus, fRegion, fTag, sortSelect].forEach(function (el) {
    el.addEventListener("change", render);
  });
  searchBtn.addEventListener("click", render);
  searchInput.addEventListener("keydown", function (e) { if (e.key === "Enter") render(); });
  fReset.addEventListener("click", function () {
    [fCloud, fCategory, fAccount, fStatus, fRegion, fTag].forEach(function (el) { el.value = ""; });
    searchField.value = "resource";
    searchInput.value = "";
    render();
  });

  selectAll.addEventListener("change", function () {
    var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr[data-resource-id]"));
    rows.forEach(function (row) {
      var id = row.dataset.resourceId;
      if (selectAll.checked) selectedIds.add(id); else selectedIds.delete(id);
      var cb = row.querySelector('input[type=checkbox]');
      if (cb) cb.checked = selectAll.checked;
    });
    updateSelectionUI();
  });

  actionButtons.forEach(function (btn) {
    btn.addEventListener("click", function () { runAction(btn.dataset.action, Array.from(selectedIds)); });
  });
  document.querySelectorAll("[data-modal-action]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (currentModalResourceId) runAction(btn.dataset.modalAction, [currentModalResourceId]);
    });
  });

  refreshBtn.addEventListener("click", triggerSync);

  loadAccountsFilterOptions();
  loadResources();
  loadSummary();
  loadLatestSyncStatus();
})();
