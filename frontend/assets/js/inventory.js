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
  var syncReasons = document.getElementById("sync-reasons");
  var invNotice = document.getElementById("inv-notice");
  var errModalTitle = document.getElementById("inv-error-modal-title");
  var errModalBody = document.getElementById("inv-error-modal-body");

  // CSP/액션 오류를 인라인 배너 대신 팝업으로 띄운다. MCErr 패널을 그대로 담아 정보 손실이 없다
  // (증상·원인·해결 방법·"상세 정보" 펼치기·요청ID·복사). 닫기 버튼/배경 클릭/ESC는 modal.js가 처리.
  function openErrorModal(title, bodyHtml) {
    if (!errModalBody) return;
    if (errModalTitle) errModalTitle.textContent = title;
    errModalBody.innerHTML = bodyHtml;
    MCErr.wire(errModalBody);
    MCPModal.open("#inv-error-modal");
  }
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
  var modalCliBtn = document.getElementById("inv-modal-cli-access");
  var modalCliResult = document.getElementById("inv-modal-cli-result");
  var modalCliCommand = document.getElementById("inv-modal-cli-command");
  var modalCliExpiry = document.getElementById("inv-modal-cli-expiry");
  var modalCliCopy = document.getElementById("inv-modal-cli-copy");

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
  var allResources = [];
  var selectedIds = new Set();
  var currentModalResourceId = null;
  var pollTimer = null;

  // 에러코드→문구 매핑은 백엔드 error_catalog가 단일 소스이며, 표시는 MCErr가 담당한다.
  // 여러 실패를 한 컨테이너에 패널로 쌓아 보여준다(라벨 optional).
  function renderPanels(container, entries) {
    if (!container) return;
    if (!entries.length) { MCErr.clear(container); return; }
    container.innerHTML = entries.map(function (e) {
      var head = e.label
        ? '<p class="mb-1 text-xs font-medium text-muted-foreground">' + MCErr.escapeHtml(e.label) + "</p>"
        : "";
      return "<div>" + head + MCErr.panelHtml(e.err) + "</div>";
    }).join("");
    container.hidden = false;
    MCErr.wire(container);
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
    var symbol = cost.currency === "USD" ? "$" : (cost.currency ? cost.currency + " " : "");
    // 금액 종류 배지를 금액 옆에 항상 붙인다(비용 파트 PR 6) — 실측(cost_source가 있는 값)과
    // 정가 추정을 같은 모양으로 보여주면 사용자가 청구서와 대조할 때 헷갈린다.
    var badge = actual != null ? (cost.source || "실측") : "Estimated · 정가 730h";
    return symbol + parseFloat(amount).toFixed(2) + " · " + badge;
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

    // 내용이 칸 폭을 넘으면 줄바꿈 대신 말줄임표(…)로 자르고, 전체 값은 title 툴팁으로 보여준다.
    // 표가 table-layout:auto라 td max-width가 무시될 수 있어, 안쪽 div에 폭·ellipsis를 건다.
    function truncCell(text, maxW, extraClass) {
      var td = document.createElement("td");
      td.className = "px-3 py-3" + (extraClass ? " " + extraClass : "");
      var inner = document.createElement("div");
      inner.style.cssText = "max-width:" + maxW + "px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
      inner.textContent = text;
      inner.title = text; // 전체 값 툴팁
      td.appendChild(inner);
      tr.appendChild(td);
      return td;
    }

    truncCell(r.original_resource_type, 130); // "CSP 원본 리소스 유형" — 폭을 줄여 다른 칸에 여유
    truncCell(r.name || r.external_resource_id, 200, "font-medium");
    truncCell(r.region || "—", 120);
    cell(r.cloud_account.account_label || r.cloud_account.external_account_id);
    cell(formatCost(r.cost_summary));

    var tdTags = document.createElement("td");
    tdTags.className = "px-3 py-3";
    var tagKeys = Object.keys(r.tags || {});
    if (tagKeys.length) {
      var span = document.createElement("span");
      span.className = "inline-block max-w-[160px] truncate align-bottom rounded bg-muted px-1.5 py-0.5 text-[11px]";
      span.textContent = tagKeys[0] + ":" + r.tags[tagKeys[0]] + (tagKeys.length > 1 ? " +" + (tagKeys.length - 1) : "");
      // 전체 태그를 title 툴팁으로(칸을 넘으면 첫 태그도 말줄임되므로).
      span.title = tagKeys.map(function (k) { return k + ":" + r.tags[k]; }).join(", ");
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
    tdDetail.className = "px-3 py-3 whitespace-nowrap";
    var btn = document.createElement("button");
    btn.type = "button";
    // whitespace-nowrap: 칸이 좁아도 "상세보기"가 두 줄로 깨지지 않게 한 줄로 고정.
    btn.className = "whitespace-nowrap rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-muted";
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
      loadingRow.querySelector("td").textContent = "불러오지 못했습니다: " + MCErr.headline(err);
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

    // 실패한 계정의 원인을 배너 아래에 패널로 보여준다(status 배지만으로는 "왜"를 알 수 없다).
    var failedItems = (job.items || []).filter(function (it) { return it.error; });
    renderPanels(syncReasons, failedItems.map(function (it) {
      return { label: providerLabel(it.provider) + " 동기화 실패", err: it.error };
    }));
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
    MCErr.clear(syncReasons);
    MCPApi.request("/sync-jobs", { method: "POST", body: {} }).then(function (data) {
      pollJob(data.id);
    }).catch(function (err) {
      if (err.code === "JOB_ALREADY_RUNNING") {
        loadLatestSyncStatus();
        return;
      }
      setRefreshBusy(false);
      MCErr.renderInto(syncReasons, err);
    });
  }

  // --- 액션(start/stop/delete) ----------------------------------------------------------
  // 시작(생성)=과금 안내 후 실행, 중지/삭제="중지"/"삭제" 문자열을 정확히 입력해야 실행.
  // (기존 window.confirm 단순 확인을 확인 모달로 대체 — 되돌릴 수 없는 작업의 이중 방어.)

  var ACTION_LABELS = { start: "시작", stop: "중지", delete: "삭제" };
  var confirmModal = document.getElementById("inv-confirm-modal");
  var confirmTitle = document.getElementById("inv-confirm-title");
  var confirmMessage = document.getElementById("inv-confirm-message");
  var confirmBilling = document.getElementById("inv-confirm-billing");
  var confirmTyped = document.getElementById("inv-confirm-typed");
  var confirmWord = document.getElementById("inv-confirm-word");
  var confirmInput = document.getElementById("inv-confirm-input");
  var confirmExecute = document.getElementById("inv-confirm-execute");
  var pendingAction = null;
  var pendingIds = null;

  function needsTyped(action) { return action === "stop" || action === "delete"; }

  function updateExecuteState() {
    if (!confirmExecute) return;
    var label = ACTION_LABELS[pendingAction] || pendingAction;
    confirmExecute.disabled = needsTyped(pendingAction) && confirmInput.value.trim() !== label;
  }

  if (confirmInput) {
    confirmInput.addEventListener("input", updateExecuteState);
    confirmInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !confirmExecute.disabled) { e.preventDefault(); confirmExecute.click(); }
    });
  }
  if (confirmExecute) {
    confirmExecute.addEventListener("click", function () {
      if (confirmExecute.disabled) return;
      // 실행 중에는 확인 모달을 열어 둔 채 버튼에 "…중." 진행 표시를 띄우고, 완료되면 닫는다.
      var label = ACTION_LABELS[pendingAction] || pendingAction;
      var stop = MCUI.buttonBusy(confirmExecute, label + " 중");
      executeAction(pendingAction, pendingIds).then(function () {
        stop();
        MCPModal.close("#inv-confirm-modal");
      });
    });
  }

  function runAction(action, resourceIds) {
    if (!resourceIds.length) return;
    var label = ACTION_LABELS[action] || action;
    // 확인 모달이 없는 환경이면 기존 방식으로 안전 폴백.
    if (!confirmModal) {
      if (!window.confirm(resourceIds.length + "개 리소스를 " + label + "하시겠습니까?")) return;
      executeAction(action, resourceIds);
      return;
    }
    pendingAction = action;
    pendingIds = resourceIds;
    confirmTitle.textContent = resourceIds.length + "개 리소스 " + label;
    confirmMessage.textContent = "선택한 " + resourceIds.length + "개 리소스를 " + label + "합니다.";
    var typed = needsTyped(action);
    confirmBilling.classList.toggle("hidden", action !== "start");
    confirmBilling.classList.toggle("flex", action === "start");
    confirmTyped.classList.toggle("hidden", !typed);
    confirmWord.textContent = label;
    confirmInput.value = "";
    confirmExecute.textContent = label;
    confirmExecute.style.background = action === "delete" ? "#c0392b" : "var(--primary)";
    updateExecuteState();
    MCPModal.open("#inv-confirm-modal");
    if (typed) setTimeout(function () { confirmInput.focus(); }, 50);
  }

  function executeAction(action, resourceIds) {
    MCErr.clear(invNotice);
    return MCPApi.request("/resources/action", {
      method: "POST",
      headers: { "X-Action-Confirmed": "true" },
      body: { action: action, resource_ids: resourceIds },
    }).then(function (data) {
      var succeeded = data.results.filter(function (r) { return r.status === "success"; }).length;
      var problems = data.results.filter(function (r) { return r.status !== "success"; });
      // 성공 요약은 텍스트로, 실패/거부는 각각 원인 패널로 보여준다.
      var summary = succeeded + "개 성공" + (problems.length ? ", " + problems.length + "개 실패/거부" : "");
      // 성공 요약은 인라인에 조용히 남기고, 실패/거부 원인은 팝업으로 띄운다.
      invNotice.innerHTML = '<p class="text-sm font-medium text-foreground">' + MCErr.escapeHtml(summary) + "</p>";
      invNotice.hidden = false;
      if (problems.length) {
        var body = problems.map(function (p) {
          return '<div><p class="mb-1 text-xs text-muted-foreground">#' +
            MCErr.escapeHtml(p.resource_id) + "</p>" + MCErr.panelHtml(p.error || {}) + "</div>";
        }).join("");
        openErrorModal(succeeded ? "일부 작업이 실패했습니다" : "작업이 실패했습니다", body);
      }
      selectedIds.clear();
      loadResources();
      loadSummary();
      if (currentModalResourceId && resourceIds.indexOf(currentModalResourceId) !== -1) {
        MCPModal.close("#inv-modal");
      }
    }).catch(function (err) {
      openErrorModal("작업을 처리하지 못했습니다", MCErr.panelHtml(err));
    });
  }

  // --- 상세 모달 ------------------------------------------------------------------------

  function openDetail(id) {
    currentModalResourceId = id;
    modalTitle.textContent = "불러오는 중…";
    modalSubtitle.textContent = "";
    [modalIdEl, modalAccount, modalStatus, modalSeen, modalCost, modalTags].forEach(function (el) { el.textContent = "—"; });
    modalConsole.href = "#";
    modalCliBtn.hidden = true;
    modalCliResult.classList.add("hidden");

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
      // AWS EC2 인스턴스만 SSM 인스턴스 프로파일이 붙어 있어 CLI 접속을 지원한다.
      modalCliBtn.hidden = !(r.cloud_account.provider === "aws" && r.service.service_code === "ec2" &&
        r.original_resource_type !== "EBS Volume");
    }).catch(function (err) {
      modalTitle.textContent = "불러오지 못했습니다";
      modalSubtitle.textContent = MCErr.headline(err);
    });
  }

  // --- AWS CLI(SSM Session Manager) 접속 ------------------------------------------------
  // 키 페어를 새로 만들지 않고, STS 단기 자격증명을 그때그때 발급받아 SSM으로 접속하게 한다.
  function runCliAccess() {
    if (!currentModalResourceId) return;
    modalCliBtn.disabled = true;
    modalCliBtn.textContent = "발급 중…";
    MCPApi.request("/resources/" + currentModalResourceId + "/cli-access", {
      method: "POST",
      headers: { "X-Action-Confirmed": "true" },
    }).then(function (data) {
      modalCliCommand.textContent = data.command;
      modalCliExpiry.textContent = formatDateTime(data.expires_at);
      modalCliResult.classList.remove("hidden");
    }).catch(function (err) {
      window.alert("AWS CLI 접속 정보를 발급받지 못했습니다: " + MCErr.headline(err));
    }).finally(function () {
      modalCliBtn.disabled = false;
      modalCliBtn.textContent = "AWS CLI로 접속";
    });
  }
  modalCliBtn.addEventListener("click", runCliAccess);
  modalCliCopy.addEventListener("click", function () {
    if (navigator.clipboard) navigator.clipboard.writeText(modalCliCommand.textContent || "");
  });

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

  // 비용 화면(CF-034·CF-022)에서 넘어온 경우 상세를 바로 연다(비용 파트 PR 6).
  // 파라미터가 없으면 아무 일도 하지 않으므로 기존 동작에 영향이 없다. 넘기는 값은
  // resources.id(내부 id)다 — openDetail이 GET /resources/{id}를 부르기 때문이다.
  var wantedResourceId = new RegExp("[?&]resource_id=([^&]*)").exec(window.location.search);
  if (wantedResourceId) {
    MCPModal.open("#inv-modal");
    openDetail(decodeURIComponent(wantedResourceId[1]));
  }
})();
