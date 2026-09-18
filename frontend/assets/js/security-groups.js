/* 보안그룹 관리(2026-09-17) — 프로비저닝에서 EC2/RDS 등을 생성할 때 자동으로 만들어지는
 * 보안그룹과 별개로, AWS Security Group / Azure NSG / GCP 방화벽 규칙을 직접 생성·조회·삭제하고
 * 규칙을 추가·삭제한다.
 *
 * GCP는 "그룹" 개념이 없다 — 방화벽 규칙 자체가 최상위 객체라 목록이 평면(중첩 없음)이고,
 * "생성" 폼 하나로 완성된 규칙을 만든다(추가 규칙 스텝 없음). AWS/Azure는 그룹을 만든 뒤
 * 규칙을 추가하는 2단계다.
 *
 * VPC/리소스그룹/네트워크 드롭다운은 새로 만들지 않고 기존 `GET /credentials/{id}/network-resources`
 * (프로비저닝 폼 "기존 리소스 사용"용으로 이미 있는 조회 전용 API)를 재사용한다.
 */
(function () {
  "use strict";

  var credentialSelect = document.getElementById("sg-credential-select");
  if (!credentialSelect) return; // 이 페이지가 아니면 무시

  var regionField = document.getElementById("sg-region-field");
  var regionSelect = document.getElementById("sg-region-select");
  var loadBtn = document.getElementById("sg-load-btn");
  var accountMsg = document.getElementById("sg-account-msg");
  var listBody = document.getElementById("sg-list-body");
  var createFields = document.getElementById("sg-create-fields");
  var createForm = document.getElementById("sg-create-form");
  var createSubmit = document.getElementById("sg-create-submit");
  var createResult = document.getElementById("sg-create-result");
  var ruleForm = document.getElementById("sg-rule-form");
  var ruleFieldsEl = document.getElementById("sg-rule-fields");
  var ruleErrorEl = document.getElementById("sg-rule-error");
  var confirmTitle = document.getElementById("sg-confirm-title");
  var confirmMessage = document.getElementById("sg-confirm-message");
  var confirmWord = document.getElementById("sg-confirm-word");
  var confirmInput = document.getElementById("sg-confirm-input");
  var confirmExecute = document.getElementById("sg-confirm-execute");

  var PLATFORM_LABEL = { aws: "AWS", azure: "Azure", gcp: "GCP" };

  // AWS 콘솔 "인바운드/아웃바운드 규칙 편집"의 "유형" 드롭다운과 동일한 발상 — 자주 쓰는
  // 서비스를 고르면 프로토콜/포트를 자동으로 채우고 잠근다("사용자 지정 ..."만 직접 입력).
  // lockProtocol/lockPorts는 AWS 콘솔처럼 자동 채운 값을 실수로 못 바꾸게 disabled 처리할지 여부.
  var AWS_RULE_TYPES = [
    { value: "custom_tcp", label: "사용자 지정 TCP", protocol: "tcp", lockProtocol: true, lockPorts: false },
    { value: "custom_udp", label: "사용자 지정 UDP", protocol: "udp", lockProtocol: true, lockPorts: false },
    { value: "custom_icmp", label: "사용자 지정 ICMP - IPv4", protocol: "icmp", from_port: -1, to_port: -1, lockProtocol: true, lockPorts: true },
    { value: "custom_protocol", label: "사용자 지정 프로토콜", protocol: "", lockProtocol: false, lockPorts: false },
    { value: "all_traffic", label: "모든 트래픽", protocol: "-1", from_port: null, to_port: null, lockProtocol: true, lockPorts: true },
    { value: "all_tcp", label: "모든 TCP", protocol: "tcp", from_port: 0, to_port: 65535, lockProtocol: true, lockPorts: true },
    { value: "all_udp", label: "모든 UDP", protocol: "udp", from_port: 0, to_port: 65535, lockProtocol: true, lockPorts: true },
    { value: "all_icmp", label: "모든 ICMP - IPv4", protocol: "icmp", from_port: -1, to_port: -1, lockProtocol: true, lockPorts: true },
    { value: "ssh", label: "SSH", protocol: "tcp", from_port: 22, to_port: 22, lockProtocol: true, lockPorts: true },
    { value: "http", label: "HTTP", protocol: "tcp", from_port: 80, to_port: 80, lockProtocol: true, lockPorts: true },
    { value: "https", label: "HTTPS", protocol: "tcp", from_port: 443, to_port: 443, lockProtocol: true, lockPorts: true },
    { value: "rdp", label: "RDP", protocol: "tcp", from_port: 3389, to_port: 3389, lockProtocol: true, lockPorts: true },
    { value: "mysql", label: "MySQL/Aurora", protocol: "tcp", from_port: 3306, to_port: 3306, lockProtocol: true, lockPorts: true },
    { value: "postgresql", label: "PostgreSQL", protocol: "tcp", from_port: 5432, to_port: 5432, lockProtocol: true, lockPorts: true },
    { value: "mssql", label: "MSSQL", protocol: "tcp", from_port: 1433, to_port: 1433, lockProtocol: true, lockPorts: true },
    { value: "smtp", label: "SMTP", protocol: "tcp", from_port: 25, to_port: 25, lockProtocol: true, lockPorts: true },
    { value: "dns_udp", label: "DNS (UDP)", protocol: "udp", from_port: 53, to_port: 53, lockProtocol: true, lockPorts: true },
    { value: "dns_tcp", label: "DNS (TCP)", protocol: "tcp", from_port: 53, to_port: 53, lockProtocol: true, lockPorts: true },
    { value: "pop3", label: "POP3", protocol: "tcp", from_port: 110, to_port: 110, lockProtocol: true, lockPorts: true },
    { value: "imap", label: "IMAP", protocol: "tcp", from_port: 143, to_port: 143, lockProtocol: true, lockPorts: true },
    { value: "ldap", label: "LDAP", protocol: "tcp", from_port: 389, to_port: 389, lockProtocol: true, lockPorts: true },
    { value: "smb", label: "SMB", protocol: "tcp", from_port: 445, to_port: 445, lockProtocol: true, lockPorts: true },
  ];

  // Azure 포털 "인바운드 보안 규칙 추가"의 "서비스" 드롭다운과 동일한 발상.
  var AZURE_RULE_TYPES = [
    { value: "custom", label: "사용자 지정", protocol: "*", port: "*", lockProtocol: false, lockPort: false },
    { value: "ssh", label: "SSH", protocol: "Tcp", port: "22", lockProtocol: true, lockPort: true },
    { value: "http", label: "HTTP", protocol: "Tcp", port: "80", lockProtocol: true, lockPort: true },
    { value: "https", label: "HTTPS", protocol: "Tcp", port: "443", lockProtocol: true, lockPort: true },
    { value: "rdp", label: "RDP", protocol: "Tcp", port: "3389", lockProtocol: true, lockPort: true },
    { value: "mysql", label: "MySQL", protocol: "Tcp", port: "3306", lockProtocol: true, lockPort: true },
    { value: "mssql", label: "MS SQL", protocol: "Tcp", port: "1433", lockProtocol: true, lockPort: true },
    { value: "postgresql", label: "PostgreSQL", protocol: "Tcp", port: "5432", lockProtocol: true, lockPort: true },
    { value: "smtp", label: "SMTP", protocol: "Tcp", port: "25", lockProtocol: true, lockPort: true },
    { value: "dns_tcp", label: "DNS (TCP)", protocol: "Tcp", port: "53", lockProtocol: true, lockPort: true },
    { value: "dns_udp", label: "DNS (UDP)", protocol: "Udp", port: "53", lockProtocol: true, lockPort: true },
  ];

  // GCP는 "규칙 추가" 스텝이 따로 없어(§2) 생성 폼 자체의 프로토콜/포트 입력에 적용한다.
  var GCP_RULE_TYPES = [
    { value: "custom", label: "사용자 지정", protocol: "tcp", ports: "", lock: false },
    { value: "ssh", label: "SSH", protocol: "tcp", ports: "22", lock: true },
    { value: "http", label: "HTTP", protocol: "tcp", ports: "80", lock: true },
    { value: "https", label: "HTTPS", protocol: "tcp", ports: "443", lock: true },
    { value: "rdp", label: "RDP", protocol: "tcp", ports: "3389", lock: true },
    { value: "mysql", label: "MySQL", protocol: "tcp", ports: "3306", lock: true },
    { value: "postgresql", label: "PostgreSQL", protocol: "tcp", ports: "5432", lock: true },
    { value: "icmp", label: "모든 ICMP", protocol: "icmp", ports: "", lock: true },
    { value: "all", label: "모든 포트/프로토콜", protocol: "all", ports: "", lock: true },
  ];

  function typeOptionsHtml(types) {
    return types.map(function (t) { return '<option value="' + t.value + '">' + escHtml(t.label) + "</option>"; }).join("");
  }

  function escHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  // 규칙 "방향"은 CSP마다 원시 값이 다르다(AWS ingress/egress, GCP INGRESS/EGRESS,
  // Azure Inbound/Outbound). 화면 표시만 Inbound/Outbound로 통일한다 — 원본 값(API 요청·
  // data-direction 필터 등)은 그대로 두고 이 함수는 렌더링에만 쓴다.
  function directionLabel(dir) {
    switch (String(dir == null ? "" : dir).toLowerCase()) {
      case "ingress":
      case "inbound":
        return "Inbound";
      case "egress":
      case "outbound":
        return "Outbound";
      default:
        return escHtml(dir); // 알 수 없는 값은 원본을 이스케이프해 그대로 노출.
    }
  }
  function errorMessage(err) {
    return (err && err.message) || "요청 처리 중 오류가 발생했습니다.";
  }
  function showError(err) {
    if (window.MCErr) {
      MCErr.renderInto(document.getElementById("sg-error-modal-body"), err);
    }
    if (window.MCPModal) MCPModal.open("#sg-error-modal");
  }

  var accounts = []; // [{credentialId, provider, externalAccountId, label}]
  var networkCache = {}; // credentialId(+region) -> network-resources 응답(드롭다운 재사용)

  // --- 계정 선택 --------------------------------------------------------------------------

  function loadAccounts() {
    credentialSelect.innerHTML = '<option value="">불러오는 중…</option>';
    MCPApi.request("/cloud-accounts")
      .then(function (data) {
        var list = data.items || [];
        return Promise.all(
          list.map(function (account) {
            return MCPApi.request("/cloud-accounts/" + account.id + "/credentials").then(function (credData) {
              return (credData.items || [])
                .filter(function (c) { return c.verified; })
                .map(function (c) {
                  return {
                    credentialId: c.id, provider: account.provider,
                    externalAccountId: account.external_account_id,
                    label: c.name + " (" + account.external_account_id + ")",
                  };
                });
            });
          })
        );
      })
      .then(function (grouped) {
        accounts = [];
        grouped.forEach(function (g) { accounts = accounts.concat(g); });
        if (!accounts.length) {
          credentialSelect.innerHTML = '<option value="">검증된 자격 증명이 없습니다</option>';
          accountMsg.hidden = false;
          accountMsg.textContent = "마이페이지에서 먼저 자격 증명을 등록하고 검증하세요.";
          return;
        }
        credentialSelect.innerHTML = accounts
          .map(function (a, i) {
            return '<option value="' + i + '">' + PLATFORM_LABEL[a.provider] + " · " + escHtml(a.label) + "</option>";
          })
          .join("");
        onAccountChange();
      })
      .catch(function (err) {
        credentialSelect.innerHTML = '<option value="">불러오지 못했습니다</option>';
        accountMsg.hidden = false;
        accountMsg.textContent = errorMessage(err);
      });
  }

  function currentAccount() {
    return credentialSelect.value !== "" ? accounts[Number(credentialSelect.value)] : null;
  }

  function onAccountChange() {
    var acc = currentAccount();
    regionField.hidden = !(acc && acc.provider === "aws");
    listBody.innerHTML = '<p class="text-sm text-muted-foreground">불러오기를 눌러주세요.</p>';
    renderCreateFields();
  }
  credentialSelect.addEventListener("change", onAccountChange);
  regionSelect.addEventListener("change", renderCreateFields);

  // --- network-resources 재사용(드롭다운용) --------------------------------------------------

  function fetchNetworkResources(acc) {
    var key = acc.credentialId + ":" + (acc.provider === "aws" ? regionSelect.value : "");
    if (networkCache[key]) return Promise.resolve(networkCache[key]);
    var path = "/credentials/" + acc.credentialId + "/network-resources";
    if (acc.provider === "aws") path += "?region=" + encodeURIComponent(regionSelect.value);
    return MCPApi.request(path).then(function (data) {
      networkCache[key] = data;
      return data;
    });
  }

  // --- 목록 조회 --------------------------------------------------------------------------

  // 경로(쿼리스트링 없음)와 쿼리스트링 부착을 분리한다 — 전에는 listPath()가 만든
  // "...security-groups/sg-1?region=ap-northeast-2"에 규칙 추가/삭제 코드가 그대로
  // "/rules/..."를 이어 붙여서 "?region=ap-northeast-2/rules/sgr-..."가 돼 버렸다(region
  // 값 안에 경로가 섞여 boto3가 InvalidRegionError로 500을 냄 — 2026-09-17 실사용 중 발견).
  // 항상 경로 조립을 전부 끝낸 뒤 마지막에만 쿼리스트링을 붙인다.
  function resourcePath(acc, groupId) {
    return "/credentials/" + acc.credentialId + "/security-groups" + (groupId ? "/" + encodeURIComponent(groupId) : "");
  }
  function appendQuery(path, key, value) {
    return path + (path.indexOf("?") >= 0 ? "&" : "?") + key + "=" + encodeURIComponent(value);
  }
  function withRegionQuery(path, acc) {
    return acc.provider === "aws" ? appendQuery(path, "region", regionSelect.value) : path;
  }
  function listPath(acc, groupId) {
    return withRegionQuery(resourcePath(acc, groupId), acc);
  }

  function loadList() {
    var acc = currentAccount();
    if (!acc) { window.alert("먼저 계정을 선택하세요."); return; }
    listBody.innerHTML = '<p class="text-sm text-muted-foreground">불러오는 중…</p>';
    MCPApi.request(listPath(acc))
      .then(function (data) { renderList(acc, data.items || []); })
      .catch(function (err) {
        listBody.innerHTML = '<p class="text-sm" style="color:#c0392b">' + escHtml(errorMessage(err)) + "</p>";
        showError(err);
      });
  }
  loadBtn.addEventListener("click", loadList);

  // CSP 목록 API(describe_security_groups 등)는 순서를 보장하지 않는다 — 매번 불러올 때마다
  // 같은 그룹이 위/중간/아래로 옮겨 다니는 것처럼 보였다(2026-09-17 사용자 피드백). 이름(그다음
  // id) 기준으로 항상 같은 순서로 정렬해 위치를 고정한다.
  function sortedByName(items, nameKey) {
    return items.slice().sort(function (a, b) {
      var an = (a[nameKey] || "") + (a.id || "");
      var bn = (b[nameKey] || "") + (b.id || "");
      return an < bn ? -1 : an > bn ? 1 : 0;
    });
  }

  function renderList(acc, items) {
    if (!items.length) {
      listBody.innerHTML = '<p class="text-sm text-muted-foreground">보안그룹/방화벽 규칙이 없습니다.</p>';
      return;
    }
    if (acc.provider === "gcp") renderGcpList(acc, sortedByName(items, "name"));
    else renderGroupList(acc, sortedByName(items, "name"));
  }

  // 방금 만들거나 규칙을 고친 항목을 화면으로 스크롤하고 잠깐 테두리를 밝혀 짚어 준다.
  // highlightGroupId는 일회성이라 적용하자마자 비운다(다음 "불러오기"에는 다시 안 켜짐).
  function applyHighlight(attrName, classes) {
    if (!highlightGroupId) return;
    var id = highlightGroupId;
    highlightGroupId = null;
    var match = null;
    listBody.querySelectorAll("[" + attrName + "]").forEach(function (el) {
      if (!match && el.getAttribute(attrName) === id) match = el;
    });
    if (!match) return;
    match.scrollIntoView({ behavior: "smooth", block: "center" });
    classes.forEach(function (c) { match.classList.add(c); });
    setTimeout(function () { classes.forEach(function (c) { match.classList.remove(c); }); }, 2500);
  }

  function renderGcpList(acc, items) {
    var rows = items
      .map(function (r) {
        return (
          '<tr class="border-b border-border" data-name="' + escHtml(r.name) + '">' +
          '<td class="py-2 pr-4 font-medium">' + escHtml(r.name) + "</td>" +
          '<td class="py-2 pr-4">' + escHtml(r.network) + "</td>" +
          '<td class="py-2 pr-4">' + directionLabel(r.direction) + "</td>" +
          '<td class="py-2 pr-4">' + escHtml(r.action) + "</td>" +
          '<td class="py-2 pr-4">' + escHtml(r.protocol || "-") + (r.ports && r.ports.length ? ":" + r.ports.join(",") : "") + "</td>" +
          '<td class="py-2 pr-4">' + escHtml((r.source_ranges || []).join(", ") || "-") + "</td>" +
          '<td class="py-2"><button type="button" data-gcp-delete class="rounded border border-border px-2 py-1 text-xs hover:bg-muted" style="color:#c0392b">삭제</button></td>' +
          "</tr>"
        );
      })
      .join("");
    listBody.innerHTML =
      '<div class="overflow-x-auto"><table class="w-full text-left text-sm">' +
      '<thead><tr class="border-b border-border text-muted-foreground"><th class="py-2 pr-4">이름</th><th class="py-2 pr-4">네트워크</th><th class="py-2 pr-4">방향</th><th class="py-2 pr-4">액션</th><th class="py-2 pr-4">프로토콜:포트</th><th class="py-2 pr-4">소스 범위</th><th class="py-2">관리</th></tr></thead>' +
      "<tbody>" + rows + "</tbody></table></div>";

    listBody.querySelectorAll("[data-gcp-delete]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var name = btn.closest("tr").getAttribute("data-name");
        openConfirm("방화벽 규칙 삭제", "방화벽 규칙 \"" + name + "\"을(를) 삭제합니다.", name, function () {
          return MCPApi.request(listPath(acc, name), { method: "DELETE", headers: { "X-Action-Confirmed": "true" } });
        });
      });
    });

    applyHighlight("data-name", ["bg-yellow/20"]);
  }

  function awsRuleRowHtml(groupId, rule) {
    return (
      '<tr class="border-b border-border" data-rule-id="' + escHtml(rule.rule_id) + '" data-direction="' + rule.direction + '">' +
      '<td class="py-1.5 pr-3">' + directionLabel(rule.direction) + "</td>" +
      '<td class="py-1.5 pr-3">' + escHtml(rule.protocol) + "</td>" +
      '<td class="py-1.5 pr-3">' + (rule.from_port != null ? rule.from_port + "-" + rule.to_port : "전체") + "</td>" +
      '<td class="py-1.5 pr-3">' + escHtml(rule.cidr || "-") + "</td>" +
      '<td class="py-1.5"><button type="button" data-aws-rule-delete class="text-xs hover:underline" style="color:#c0392b">삭제</button></td>' +
      "</tr>"
    );
  }

  function azureRuleRowHtml(rule) {
    return (
      '<tr class="border-b border-border" data-rule-id="' + escHtml(rule.name) + '">' +
      '<td class="py-1.5 pr-3">' + escHtml(rule.name) + "</td>" +
      '<td class="py-1.5 pr-3">' + rule.priority + "</td>" +
      '<td class="py-1.5 pr-3">' + directionLabel(rule.direction) + "</td>" +
      '<td class="py-1.5 pr-3">' + rule.access + "</td>" +
      '<td class="py-1.5 pr-3">' + escHtml(rule.protocol) + "</td>" +
      '<td class="py-1.5 pr-3">' + escHtml(rule.destination_port_range || "-") + "</td>" +
      '<td class="py-1.5"><button type="button" data-azure-rule-delete class="text-xs hover:underline" style="color:#c0392b">삭제</button></td>' +
      "</tr>"
    );
  }

  function renderGroupList(acc, groups) {
    var isAws = acc.provider === "aws";
    listBody.innerHTML = groups
      .map(function (g, idx) {
        var header = isAws
          ? escHtml(g.name) + ' <span class="text-muted-foreground">(' + escHtml(g.id) + ")</span>"
          : escHtml(g.name) + ' <span class="text-muted-foreground">(rg: ' + escHtml(g.resource_group) + ", " + escHtml(g.location) + ")</span>";
        var rulesHtml = isAws
          ? '<table class="w-full text-left text-xs"><thead><tr class="text-muted-foreground"><th class="py-1 pr-3">방향</th><th class="py-1 pr-3">프로토콜</th><th class="py-1 pr-3">포트</th><th class="py-1 pr-3">CIDR</th><th class="py-1"></th></tr></thead><tbody>' +
            g.ingress_rules.map(function (r) { return awsRuleRowHtml(g.id, r); }).join("") +
            g.egress_rules.map(function (r) { return awsRuleRowHtml(g.id, r); }).join("") +
            "</tbody></table>"
          : '<table class="w-full text-left text-xs"><thead><tr class="text-muted-foreground"><th class="py-1 pr-3">이름</th><th class="py-1 pr-3">우선순위</th><th class="py-1 pr-3">방향</th><th class="py-1 pr-3">허용/차단</th><th class="py-1 pr-3">프로토콜</th><th class="py-1 pr-3">포트</th><th class="py-1"></th></tr></thead><tbody>' +
            g.rules.map(azureRuleRowHtml).join("") +
            "</tbody></table>";

        return (
          '<div class="rounded-xl border border-border p-3" data-group-idx="' + idx + '" data-group-id="' + escHtml(g.id || g.name) +
          '" data-resource-group="' + escHtml(g.resource_group || "") + '">' +
          '<div class="flex flex-wrap items-center justify-between gap-2">' +
          '<p class="font-medium">' + header + "</p>" +
          '<div class="flex gap-2">' +
          '<button type="button" data-add-rule class="rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted">규칙 추가</button>' +
          '<button type="button" data-delete-group class="rounded-lg px-2.5 py-1.5 text-xs font-medium text-white" style="background:#c0392b">그룹 삭제</button>' +
          "</div></div>" +
          '<div class="mt-2 overflow-x-auto">' + rulesHtml + "</div>" +
          "</div>"
        );
      })
      .join("");

    listBody.querySelectorAll("[data-group-idx]").forEach(function (card) {
      var groupId = card.getAttribute("data-group-id");
      var resourceGroup = card.getAttribute("data-resource-group") || null;

      card.querySelector("[data-delete-group]").addEventListener("click", function () {
        openConfirm("보안그룹 삭제", "보안그룹 \"" + groupId + "\"과(와) 그 안의 모든 규칙을 삭제합니다.", groupId, function () {
          var p = listPath(acc, groupId);
          if (resourceGroup) p += (p.indexOf("?") >= 0 ? "&" : "?") + "resource_group=" + encodeURIComponent(resourceGroup);
          return MCPApi.request(p, { method: "DELETE", headers: { "X-Action-Confirmed": "true" } });
        });
      });

      card.querySelector("[data-add-rule]").addEventListener("click", function () {
        openRuleModal(acc, groupId, resourceGroup);
      });

      card.querySelectorAll("[data-aws-rule-delete]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var row = btn.closest("tr");
          var ruleId = row.getAttribute("data-rule-id");
          var direction = row.getAttribute("data-direction");
          openConfirm("규칙 삭제", "규칙을 삭제합니다.", "삭제", function () {
            var p = resourcePath(acc, groupId) + "/rules/" + encodeURIComponent(ruleId);
            p = withRegionQuery(p, acc);
            p = appendQuery(p, "direction", direction);
            return MCPApi.request(p, { method: "DELETE", headers: { "X-Action-Confirmed": "true" } });
          }, groupId);
        });
      });

      card.querySelectorAll("[data-azure-rule-delete]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var ruleId = btn.closest("tr").getAttribute("data-rule-id");
          openConfirm("규칙 삭제", "규칙을 삭제합니다.", "삭제", function () {
            var p = resourcePath(acc, groupId) + "/rules/" + encodeURIComponent(ruleId);
            p = appendQuery(p, "resource_group", resourceGroup);
            return MCPApi.request(p, { method: "DELETE", headers: { "X-Action-Confirmed": "true" } });
          }, groupId);
        });
      });
    });

    applyHighlight("data-group-id", ["ring-2", "ring-primary"]);
  }

  // --- 삭제 확인 모달(그룹/규칙 공용) --------------------------------------------------------

  var pendingDeleteAction = null;
  var pendingHighlightId = null;

  // 목록을 다시 불러오면 AWS describe_security_groups() 등이 순서를 보장하지 않아 방금 만지던
  // 그룹이 위/중간/아래로 매번 옮겨 다니는 것처럼 보였다(2026-09-17 사용자 피드백). renderList()가
  // 항상 같은 기준으로 정렬해 위치를 고정하고, 방금 만들거나 규칙을 고친 그룹은 highlightGroupId에
  // 담아 다시 그려진 뒤 스크롤+하이라이트로 짚어 준다(그룹 자체가 삭제된 경우는 짚을 대상이 없어
  // null로 둔다).
  var highlightGroupId = null;

  function openConfirm(title, message, word, actionFn, highlightId) {
    confirmTitle.textContent = title;
    confirmMessage.textContent = message;
    confirmWord.textContent = word;
    confirmInput.value = "";
    confirmExecute.disabled = true;
    pendingDeleteAction = actionFn;
    pendingHighlightId = highlightId || null;
    if (window.MCPModal) MCPModal.open("#sg-confirm-modal");
    confirmInput.focus();
  }
  confirmInput.addEventListener("input", function () {
    confirmExecute.disabled = confirmInput.value !== confirmWord.textContent;
  });
  confirmExecute.addEventListener("click", function () {
    if (!pendingDeleteAction) return;
    var fn = pendingDeleteAction;
    var highlightId = pendingHighlightId;
    pendingDeleteAction = null;
    pendingHighlightId = null;
    confirmExecute.disabled = true;
    fn()
      .then(function () {
        if (window.MCPModal) MCPModal.close("#sg-confirm-modal");
        highlightGroupId = highlightId;
        loadList();
      })
      .catch(function (err) {
        if (window.MCPModal) MCPModal.close("#sg-confirm-modal");
        showError(err);
      });
  });

  // --- 규칙 추가 모달 ----------------------------------------------------------------------

  var currentRuleTarget = null; // { acc, groupId, resourceGroup }

  // AWS: "유형"이 바뀌면 프로토콜/포트를 자동으로 채우고, named 서비스는 실수로 못 바꾸게 잠근다
  // (직접 입력이 필요한 "사용자 지정 ..."만 편집 가능 — AWS 콘솔과 동일한 동작).
  function applyAwsRuleType(wrap) {
    var typeSelect = wrap.querySelector('[data-rule-type]');
    var preset = AWS_RULE_TYPES.filter(function (t) { return t.value === typeSelect.value; })[0];
    var protocolInput = wrap.querySelector('[data-rf="protocol"]');
    var fromInput = wrap.querySelector('[data-rf="from_port"]');
    var toInput = wrap.querySelector('[data-rf="to_port"]');
    if (!preset) return;
    if (preset.protocol !== undefined) protocolInput.value = preset.protocol;
    protocolInput.disabled = !!preset.lockProtocol;
    if (preset.lockPorts) {
      fromInput.value = preset.from_port != null ? preset.from_port : "";
      toInput.value = preset.to_port != null ? preset.to_port : "";
    }
    fromInput.disabled = !!preset.lockPorts;
    toInput.disabled = !!preset.lockPorts;
  }

  // Azure: "서비스"가 바뀌면 프로토콜/포트를 자동으로 채운다("사용자 지정"만 편집 가능).
  function applyAzureRuleType(wrap) {
    var typeSelect = wrap.querySelector('[data-rule-type]');
    var preset = AZURE_RULE_TYPES.filter(function (t) { return t.value === typeSelect.value; })[0];
    var protocolInput = wrap.querySelector('[data-rf="protocol"]');
    var portInput = wrap.querySelector('[data-rf="destination_port_range"]');
    if (!preset) return;
    protocolInput.value = preset.protocol;
    protocolInput.disabled = !!preset.lockProtocol;
    if (preset.lockPort) portInput.value = preset.port;
    portInput.disabled = !!preset.lockPort;
  }

  function openRuleModal(acc, groupId, resourceGroup) {
    currentRuleTarget = { acc: acc, groupId: groupId, resourceGroup: resourceGroup };
    ruleErrorEl.hidden = true;
    ruleFieldsEl.innerHTML =
      acc.provider === "aws"
        ? '<div><label class="mb-1 block text-xs font-medium">유형</label><select data-rule-type class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm">' + typeOptionsHtml(AWS_RULE_TYPES) + "</select></div>" +
          '<div><label class="mb-1 block text-xs font-medium">방향</label><select data-rf="direction" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"><option value="ingress">Inbound</option><option value="egress">Outbound</option></select></div>' +
          '<div><label class="mb-1 block text-xs font-medium">프로토콜</label><input data-rf="protocol" value="tcp" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:bg-muted disabled:text-muted-foreground" /></div>' +
          '<div class="grid grid-cols-2 gap-2"><div><label class="mb-1 block text-xs font-medium">시작 포트</label><input data-rf="from_port" type="number" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:bg-muted disabled:text-muted-foreground" /></div><div><label class="mb-1 block text-xs font-medium">끝 포트</label><input data-rf="to_port" type="number" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:bg-muted disabled:text-muted-foreground" /></div></div>' +
          '<div><label class="mb-1 block text-xs font-medium">CIDR</label><input data-rf="cidr" value="0.0.0.0/0" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" /></div>' +
          '<div><label class="mb-1 block text-xs font-medium">설명(선택)</label><input data-rf="description" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" /></div>'
        : '<div><label class="mb-1 block text-xs font-medium">서비스</label><select data-rule-type class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm">' + typeOptionsHtml(AZURE_RULE_TYPES) + "</select></div>" +
          '<div><label class="mb-1 block text-xs font-medium">규칙 이름</label><input data-rf="name" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" /></div>' +
          '<div class="grid grid-cols-2 gap-2"><div><label class="mb-1 block text-xs font-medium">우선순위(100-4096)</label><input data-rf="priority" type="number" value="100" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" /></div><div><label class="mb-1 block text-xs font-medium">방향</label><select data-rf="direction" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"><option value="Inbound">Inbound</option><option value="Outbound">Outbound</option></select></div></div>' +
          '<div><label class="mb-1 block text-xs font-medium">허용/차단</label><select data-rf="access" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"><option value="Allow">Allow</option><option value="Deny">Deny</option></select></div>' +
          '<div><label class="mb-1 block text-xs font-medium">프로토콜</label><input data-rf="protocol" value="Tcp" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:bg-muted disabled:text-muted-foreground" /></div>' +
          '<div><label class="mb-1 block text-xs font-medium">Source 주소</label><input data-rf="source_address_prefix" value="*" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" /></div>' +
          '<div><label class="mb-1 block text-xs font-medium">Destination 포트</label><input data-rf="destination_port_range" placeholder="80" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:bg-muted disabled:text-muted-foreground" /></div>';

    var typeSelect = ruleFieldsEl.querySelector("[data-rule-type]");
    var applyType = acc.provider === "aws" ? applyAwsRuleType : applyAzureRuleType;
    typeSelect.addEventListener("change", function () { applyType(ruleFieldsEl); });
    applyType(ruleFieldsEl); // 기본 선택("사용자 지정 TCP"/"사용자 지정")도 잠금 상태를 맞춘다

    if (window.MCPModal) MCPModal.open("#sg-rule-modal");
  }

  ruleForm.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!currentRuleTarget) return;
    var acc = currentRuleTarget.acc;
    var val = {};
    ruleFieldsEl.querySelectorAll("[data-rf]").forEach(function (el) {
      var key = el.getAttribute("data-rf");
      // "모든 트래픽"처럼 유형이 포트를 비워 잠그는 경우 Number("")가 0이 되어 버려서(포트 0은
      // 실제 값과 다르다) 빈 문자열은 null로 보낸다.
      if (el.type === "number") val[key] = el.value === "" ? null : Number(el.value);
      else val[key] = el.value.trim();
    });

    var body, p;
    if (acc.provider === "aws") {
      body = { aws: val };
      p = withRegionQuery(resourcePath(acc, currentRuleTarget.groupId) + "/rules", acc);
    } else {
      body = { azure: val };
      p = appendQuery(resourcePath(acc, currentRuleTarget.groupId) + "/rules", "resource_group", currentRuleTarget.resourceGroup);
    }

    MCPApi.request(p, { method: "POST", body: body, headers: { "X-Action-Confirmed": "true" } })
      .then(function () {
        if (window.MCPModal) MCPModal.close("#sg-rule-modal");
        highlightGroupId = currentRuleTarget.groupId;
        loadList();
      })
      .catch(function (err) {
        ruleErrorEl.hidden = false;
        ruleErrorEl.textContent = errorMessage(err);
      });
  });

  // --- 생성 폼 ----------------------------------------------------------------------------

  function renderCreateFields() {
    var acc = currentAccount();
    createSubmit.disabled = true;
    if (!acc) {
      createFields.innerHTML = '<p class="text-sm text-muted-foreground sm:col-span-2">먼저 위에서 계정을 선택하세요.</p>';
      return;
    }
    createFields.innerHTML = '<p class="text-sm text-muted-foreground sm:col-span-2">불러오는 중…</p>';

    fetchNetworkResources(acc)
      .then(function (net) {
        if (acc.provider === "aws") {
          var vpcOptions = (net.vpcs || [])
            .map(function (v) { return '<option value="' + escHtml(v.id) + '">' + escHtml(v.id) + (v.name ? " - " + escHtml(v.name) : "") + "</option>"; })
            .join("");
          createFields.innerHTML =
            '<div><label class="mb-1 block text-sm font-medium">이름</label><input data-cf="name" placeholder="web-sg" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" /></div>' +
            '<div><label class="mb-1 block text-sm font-medium">설명</label><input data-cf="description" placeholder="web servers" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" /></div>' +
            '<div class="sm:col-span-2"><label class="mb-1 block text-sm font-medium">VPC</label><select data-cf="vpc_id" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"><option value="">선택하세요</option>' + vpcOptions + "</select></div>";
        } else if (acc.provider === "azure") {
          var rgOptions = (net.resource_groups || [])
            .map(function (r) { return '<option value="' + escHtml(r.name) + '">' + escHtml(r.name) + " (" + escHtml(r.location) + ")</option>"; })
            .join("");
          createFields.innerHTML =
            '<div><label class="mb-1 block text-sm font-medium">이름</label><input data-cf="name" placeholder="web-nsg" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" /></div>' +
            '<div><label class="mb-1 block text-sm font-medium">리전</label><select data-cf="location" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"><option value="koreacentral">koreacentral</option><option value="eastus">eastus</option><option value="koreasouth">koreasouth</option><option value="canadacentral">canadacentral</option></select></div>' +
            '<div class="sm:col-span-2"><label class="mb-1 block text-sm font-medium">리소스 그룹</label><select data-cf="resource_group" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"><option value="">선택하세요</option>' + rgOptions + "</select></div>";
        } else {
          var netOptions = (net.networks || [])
            .map(function (n) { return '<option value="' + escHtml(n.name) + '">' + escHtml(n.name) + "</option>"; })
            .join("");
          createFields.innerHTML =
            '<div><label class="mb-1 block text-sm font-medium">이름</label><input data-cf="name" placeholder="allow-ssh" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" /></div>' +
            '<div><label class="mb-1 block text-sm font-medium">네트워크</label><select data-cf="network" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"><option value="">선택하세요</option>' + netOptions + "</select></div>" +
            '<div><label class="mb-1 block text-sm font-medium">방향</label><select data-cf="direction" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"><option value="INGRESS">Inbound</option><option value="EGRESS">Outbound</option></select></div>' +
            '<div><label class="mb-1 block text-sm font-medium">액션</label><select data-cf="action" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"><option value="allow">allow</option><option value="deny">deny</option></select></div>' +
            '<div><label class="mb-1 block text-sm font-medium">우선순위</label><input data-cf="priority" type="number" value="1000" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" /></div>' +
            '<div><label class="mb-1 block text-sm font-medium">유형</label><select data-rule-type class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm">' + typeOptionsHtml(GCP_RULE_TYPES) + "</select></div>" +
            '<div><label class="mb-1 block text-sm font-medium">프로토콜</label><input data-cf="protocol" value="tcp" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:bg-muted disabled:text-muted-foreground" /></div>' +
            '<div><label class="mb-1 block text-sm font-medium">포트(쉼표 구분, 선택)</label><input data-cf="ports" placeholder="22,80" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:bg-muted disabled:text-muted-foreground" /></div>' +
            '<div><label class="mb-1 block text-sm font-medium">소스 범위(쉼표 구분)</label><input data-cf="source_ranges" value="0.0.0.0/0" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" /></div>' +
            '<div class="sm:col-span-2"><label class="mb-1 block text-sm font-medium">대상 태그(쉼표 구분, 선택)</label><input data-cf="target_tags" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" /></div>';

          var gcpTypeSelect = createFields.querySelector("[data-rule-type]");
          var applyGcpType = function () {
            var preset = GCP_RULE_TYPES.filter(function (t) { return t.value === gcpTypeSelect.value; })[0];
            if (!preset) return;
            var protocolInput = createFields.querySelector('[data-cf="protocol"]');
            var portsInput = createFields.querySelector('[data-cf="ports"]');
            protocolInput.value = preset.protocol;
            protocolInput.disabled = !!preset.lock;
            if (preset.lock) portsInput.value = preset.ports;
            portsInput.disabled = !!preset.lock;
          };
          gcpTypeSelect.addEventListener("change", applyGcpType);
          applyGcpType(); // 기본 선택("사용자 지정")도 잠금 상태를 맞춘다
        }
        createSubmit.disabled = false;
      })
      .catch(function (err) {
        createFields.innerHTML = '<p class="text-sm sm:col-span-2" style="color:#c0392b">' + escHtml(errorMessage(err)) + "</p>";
      });
  }

  createForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var acc = currentAccount();
    if (!acc) return;
    var val = {};
    createFields.querySelectorAll("[data-cf]").forEach(function (el) {
      var key = el.getAttribute("data-cf");
      if (key === "priority") val[key] = Number(el.value);
      else if (key === "ports" || key === "source_ranges" || key === "target_tags") {
        val[key] = el.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
      } else val[key] = el.value.trim();
    });

    var body = {};
    body[acc.provider] = val;
    var p = listPath(acc);

    createResult.hidden = true;
    createSubmit.disabled = true;
    MCPApi.request(p, { method: "POST", body: body, headers: { "X-Action-Confirmed": "true" } })
      .then(function (created) {
        createResult.hidden = false;
        createResult.style.color = "";
        createResult.textContent = "생성했습니다.";
        // 새로 만든 것도 목록 어디에 떨어질지 몰라 바로 짚어 보여준다.
        if (acc.provider === "aws") highlightGroupId = created.aws && created.aws.id;
        else if (acc.provider === "azure") highlightGroupId = created.azure && created.azure.name;
        else highlightGroupId = created.gcp && created.gcp.name;
        loadList();
      })
      .catch(function (err) {
        createResult.hidden = false;
        createResult.style.color = "#c0392b";
        createResult.textContent = errorMessage(err);
      })
      .finally(function () { createSubmit.disabled = false; });
  });

  loadAccounts();
})();
