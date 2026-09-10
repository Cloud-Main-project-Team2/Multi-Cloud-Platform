/* 마이페이지(MY-01) — 키 & 플랫폼 IAM 계정 관리 폼 + 연결된 클라우드 계정 표.
 *
 * 이제 실데이터: assets/js/api.js(MCPApi)로 백엔드 credentials API(§6)를 직접 호출한다.
 * - 플랫폼 선택에 따라 필요한 입력 필드를 동적으로 렌더링(AWS 2개/Azure 4개/GCP는 서비스
 *   계정 JSON 붙여넣기 1개 — GCP는 JSON 안에 project_id가 있어 계정 식별자를 따로 받지 않는다).
 * - "저장 및 검증" → POST /credentials/{provider} (검증 실패해도 저장되고 표에 반영된다).
 * - 표는 GET /cloud-accounts + 계정별 GET /cloud-accounts/{id}/credentials로 채운다.
 *   "연결 리소스" 열은 리소스 조회 API가 아직 없어 "—"로 고정한다(추후 연동).
 * - 행 드래그 순서 변경은 PUT /credentials/order로 저장한다(더 이상 localStorage 아님).
 * - 회원가입/비밀번호 재설정 등 인증 기능은 이 작업 범위가 아니다.
 */
(function () {
  "use strict";

  var tbody = document.getElementById("accounts-tbody");
  var form = document.getElementById("credential-form");
  if (!tbody || !form) return;

  var loadingRow = document.getElementById("accounts-loading-row");
  var emptyRow = document.getElementById("accounts-empty-row");
  var providerSelect = document.getElementById("cred-provider");
  var accountIdField = document.getElementById("cred-account-id-field");
  var accountIdLabel = document.getElementById("cred-account-id-label");
  var accountIdInput = document.getElementById("cred-external-account-id");
  var fieldsContainer = document.getElementById("cred-provider-fields");
  var submitBtn = document.getElementById("cred-submit");
  var cancelEditBtn = document.getElementById("cred-cancel-edit");
  var formTitle = document.getElementById("cred-form-title");
  var resultEl = document.getElementById("cred-result");
  var nameInput = document.getElementById("cred-name");

  var PROVIDER_LABELS = { aws: "AWS", azure: "Azure", gcp: "GCP" };

  // 수정 모드 상태 — null이면 새 자격 증명 등록, 값이 있으면 그 credential을 PATCH한다.
  var editingCredentialId = null;

  function val(id) {
    var el = document.getElementById(id);
    return el ? el.value.trim() : "";
  }
  function nonEmpty(id) {
    return val(id).length > 0;
  }

  function parseGcpJson() {
    var raw = val("cred-gcp-json");
    if (!raw) return null;
    var data;
    try {
      data = JSON.parse(raw);
    } catch (e) {
      return null;
    }
    if (!data || data.type !== "service_account") return null;
    // token_uri까지 확인한다 — google-auth의 from_service_account_info()가 필수로 요구하는
    // 필드라서, 빠지면 키가 멀쩡해도 서버 검증이 무조건 실패한다.
    if (!data.client_email || !data.private_key_id || !data.private_key || !data.project_id || !data.token_uri) {
      return null;
    }
    return data;
  }

  // --- 플랫폼별 입력 템플릿 -------------------------------------------------------------

  var PROVIDER_TEMPLATES = {
    aws: {
      accountIdLabel: "AWS 계정 ID",
      accountIdPlaceholder: "123456789012",
      showAccountId: true,
      fieldsHtml:
        '<div>' +
          '<label for="cred-aws-access-key-id" class="mb-1 block text-sm font-medium">Access Key ID</label>' +
          '<input id="cred-aws-access-key-id" type="text" placeholder="AKIA..." class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary" />' +
        '</div>' +
        '<div>' +
          '<label for="cred-aws-secret-access-key" class="mb-1 block text-sm font-medium">Secret Access Key</label>' +
          '<div class="relative">' +
            '<input id="cred-aws-secret-access-key" type="password" class="w-full rounded-lg border border-border bg-background px-3 py-2 pr-10 text-sm outline-none focus:border-primary" />' +
            '<button type="button" onclick="MCUI.togglePassword(\'cred-aws-secret-access-key\', this)" class="absolute right-2 top-1/2 -translate-y-1/2 grid h-7 w-7 place-items-center rounded text-base hover:bg-muted" aria-label="키 표시 전환">👁</button>' +
          '</div>' +
        '</div>',
      isValid: function () {
        return nonEmpty("cred-aws-access-key-id") && nonEmpty("cred-aws-secret-access-key");
      },
      hasSecretInput: function () {
        return nonEmpty("cred-aws-access-key-id") || nonEmpty("cred-aws-secret-access-key");
      },
      secretPayload: function () {
        return { access_key_id: val("cred-aws-access-key-id"), secret_access_key: val("cred-aws-secret-access-key") };
      },
      publicIdentifier: function () {
        return val("cred-aws-access-key-id");
      },
      externalAccountId: function () {
        return val("cred-external-account-id");
      },
    },
    azure: {
      accountIdLabel: "구독 ID",
      accountIdPlaceholder: "00000000-0000-0000-0000-000000000000",
      showAccountId: true,
      fieldsHtml:
        '<div>' +
          '<label for="cred-azure-tenant-id" class="mb-1 block text-sm font-medium">테넌트 ID</label>' +
          '<input id="cred-azure-tenant-id" type="text" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary" />' +
        '</div>' +
        '<div>' +
          '<label for="cred-azure-client-id" class="mb-1 block text-sm font-medium">클라이언트 ID</label>' +
          '<input id="cred-azure-client-id" type="text" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary" />' +
        '</div>' +
        '<div>' +
          '<label for="cred-azure-client-secret" class="mb-1 block text-sm font-medium">클라이언트 Secret</label>' +
          '<div class="relative">' +
            '<input id="cred-azure-client-secret" type="password" class="w-full rounded-lg border border-border bg-background px-3 py-2 pr-10 text-sm outline-none focus:border-primary" />' +
            '<button type="button" onclick="MCUI.togglePassword(\'cred-azure-client-secret\', this)" class="absolute right-2 top-1/2 -translate-y-1/2 grid h-7 w-7 place-items-center rounded text-base hover:bg-muted" aria-label="키 표시 전환">👁</button>' +
          '</div>' +
        '</div>',
      isValid: function () {
        return nonEmpty("cred-azure-tenant-id") && nonEmpty("cred-azure-client-id") && nonEmpty("cred-azure-client-secret");
      },
      hasSecretInput: function () {
        return nonEmpty("cred-azure-tenant-id") || nonEmpty("cred-azure-client-id") || nonEmpty("cred-azure-client-secret");
      },
      secretPayload: function () {
        return {
          tenant_id: val("cred-azure-tenant-id"),
          client_id: val("cred-azure-client-id"),
          client_secret: val("cred-azure-client-secret"),
        };
      },
      publicIdentifier: function () {
        return val("cred-azure-client-id");
      },
      externalAccountId: function () {
        return val("cred-external-account-id");
      },
    },
    gcp: {
      showAccountId: false,
      fieldsHtml:
        '<div>' +
          '<label for="cred-gcp-json" class="mb-1 block text-sm font-medium">서비스 계정 키 (JSON)</label>' +
          '<textarea id="cred-gcp-json" rows="6" placeholder=\'{"type":"service_account","project_id":"...","private_key_id":"...","private_key":"...","client_email":"...","token_uri":"https://oauth2.googleapis.com/token"}\'' +
          ' class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary font-mono"></textarea>' +
          '<p id="cred-gcp-hint" class="mt-1 text-xs text-muted-foreground">GCP 콘솔에서 다운로드한 서비스 계정 키 JSON 파일 내용을 그대로 붙여넣으세요.</p>' +
        '</div>',
      isValid: function () {
        return parseGcpJson() !== null;
      },
      hasSecretInput: function () {
        return nonEmpty("cred-gcp-json");
      },
      secretPayload: function () {
        // 서비스 계정 키 JSON을 통째로 보낸다. 필드를 골라 담으면 google-auth가 필수로 쓰는
        // token_uri 같은 값이 빠져 인증이 실패한다(2026-09-11 수정). 이 payload는 서버에서
        // AES-256-GCM으로 암호화 저장되므로 원본을 그대로 보관해도 된다.
        return parseGcpJson();
      },
      publicIdentifier: function () {
        var data = parseGcpJson();
        return data ? data.client_email : "";
      },
      externalAccountId: function () {
        var data = parseGcpJson();
        return data ? data.project_id : "";
      },
    },
  };

  function renderProviderFields() {
    var tpl = PROVIDER_TEMPLATES[providerSelect.value];
    fieldsContainer.innerHTML = tpl.fieldsHtml;
    accountIdField.hidden = !tpl.showAccountId;
    if (tpl.showAccountId) {
      accountIdLabel.textContent = tpl.accountIdLabel;
      accountIdInput.placeholder = tpl.accountIdPlaceholder;
      accountIdInput.value = "";
    }
    hideResult();

    var gcpJson = document.getElementById("cred-gcp-json");
    var gcpHint = document.getElementById("cred-gcp-hint");
    if (gcpJson && gcpHint) {
      gcpJson.addEventListener("input", function () {
        var data = parseGcpJson();
        if (!val("cred-gcp-json")) {
          gcpHint.textContent = "GCP 콘솔에서 다운로드한 서비스 계정 키 JSON 파일 내용을 그대로 붙여넣으세요.";
        } else if (data) {
          gcpHint.textContent = "감지된 프로젝트 ID: " + data.project_id;
        } else {
          gcpHint.textContent = "JSON 형식 또는 필수 필드(type, project_id, client_email, private_key_id, private_key, token_uri)를 확인해 주세요. 파일 내용을 일부만 잘라 붙이지 말고 통째로 넣어야 합니다.";
        }
      });
    }
  }

  function showResult(message, ok) {
    resultEl.textContent = message;
    resultEl.className = "text-sm " + (ok ? "text-primary" : "text-yellow");
    resultEl.classList.remove("hidden");
  }
  function hideResult() {
    resultEl.classList.add("hidden");
  }

  var ERROR_MESSAGES = {
    CREDENTIAL_ALREADY_EXISTS: "같은 이름의 자격 증명이 이미 있습니다. 다른 이름을 사용해 주세요.",
    CREDENTIAL_NOT_FOUND: "자격 증명을 찾을 수 없습니다. 목록을 새로고침해 주세요.",
    CREDENTIAL_IN_USE: "진행 중인 작업이 이 자격 증명을 사용하고 있어 삭제할 수 없습니다. 작업이 끝난 뒤 다시 시도해 주세요.",
    CONFIRMATION_REQUIRED: "확인이 필요한 작업입니다.",
    VALIDATION_ERROR: "입력값을 다시 확인해 주세요.",
    AUTHENTICATION_REQUIRED: "로그인이 만료되었습니다. 다시 로그인해 주세요.",
    INVALID_TOKEN: "로그인이 만료되었습니다. 다시 로그인해 주세요.",
  };
  function errorMessage(err) {
    return ERROR_MESSAGES[err.code] || err.message || "요청 처리 중 오류가 발생했습니다.";
  }

  // --- 등록/수정 모드 전환 -----------------------------------------------------------------

  function enterEditMode(account, credential) {
    editingCredentialId = credential.id;

    providerSelect.value = account.provider;
    providerSelect.disabled = true;
    renderProviderFields();

    nameInput.value = credential.name;
    // 계정 식별자(external_account_id)는 클라우드 계정에 속한 값이라 수정 대상이 아니다.
    accountIdField.hidden = true;

    formTitle.textContent = "키 수정 — " + (PROVIDER_LABELS[account.provider] || account.provider) + " · " + credential.name;
    submitBtn.textContent = "수정 및 재검증";
    cancelEditBtn.hidden = false;
    showResult("새 키 값을 입력하면 교체 후 다시 검증합니다. 비워두면 이름만 수정됩니다.", true);

    form.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function exitEditMode() {
    editingCredentialId = null;
    providerSelect.disabled = false;
    form.reset();
    renderProviderFields();
    formTitle.textContent = "키 & 플랫폼 IAM 계정 관리";
    submitBtn.textContent = "저장 및 검증";
    cancelEditBtn.hidden = true;
  }

  cancelEditBtn.addEventListener("click", function () {
    exitEditMode();
    hideResult();
  });

  providerSelect.addEventListener("change", renderProviderFields);
  renderProviderFields();

  function setSubmitting(busy) {
    submitBtn.disabled = busy;
    submitBtn.classList.toggle("opacity-50", busy);
    submitBtn.classList.toggle("cursor-not-allowed", busy);
  }

  function submitCreate(provider, tpl, name) {
    var externalAccountId = tpl.externalAccountId();
    if (!name || !externalAccountId || !tpl.isValid()) {
      showResult("필수 입력값을 모두 채워 주세요.", false);
      return;
    }

    setSubmitting(true);
    MCPApi.request("/credentials/" + provider, {
      method: "POST",
      body: {
        external_account_id: externalAccountId,
        account_label: name,
        name: name,
        public_identifier: tpl.publicIdentifier(),
        secret_payload: tpl.secretPayload(),
      },
    })
      .then(function (data) {
        // 폼을 먼저 초기화한 뒤 메시지를 띄운다 — renderProviderFields()가 내부에서
        // hideResult()를 호출하기 때문에 순서가 반대면 결과 문구가 바로 사라진다.
        form.reset();
        renderProviderFields();
        showResult(
          data.verified
            ? "저장되었습니다. 검증에 성공했습니다."
            : "저장되었습니다. 다만 검증에는 실패했습니다 — 아래 표에서 확인해 주세요.",
          data.verified
        );
        loadAccounts();
      })
      .catch(function (err) {
        showResult(errorMessage(err), false);
      })
      .then(function () { setSubmitting(false); });
  }

  function submitEdit(tpl, name) {
    if (!name) {
      showResult("이름을 입력해 주세요.", false);
      return;
    }

    var replacingSecret = tpl.hasSecretInput();
    if (replacingSecret && !tpl.isValid()) {
      showResult("키 값을 모두 정확히 입력해 주세요(일부만 채우면 교체할 수 없습니다).", false);
      return;
    }

    var body = { name: name };
    var options = { method: "PATCH", body: body };
    if (replacingSecret) {
      body.secret_payload = tpl.secretPayload();
      body.public_identifier = tpl.publicIdentifier();
      // secret 교체는 파괴적 동작이라 서버가 확인 헤더를 요구한다(§2.4).
      options.headers = { "X-Action-Confirmed": "true" };
    }

    setSubmitting(true);
    MCPApi.request("/credentials/" + editingCredentialId, options)
      .then(function (data) {
        // exitEditMode() → renderProviderFields() → hideResult() 순서라 메시지는 그 뒤에 띄운다.
        exitEditMode();
        if (!replacingSecret) {
          showResult("이름을 수정했습니다.", true);
        } else {
          showResult(
            data.verified ? "키를 교체하고 검증에 성공했습니다." : "키를 교체했지만 검증에는 실패했습니다 — 값을 다시 확인해 주세요.",
            data.verified
          );
        }
        loadAccounts();
      })
      .catch(function (err) {
        showResult(errorMessage(err), false);
      })
      .then(function () { setSubmitting(false); });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var tpl = PROVIDER_TEMPLATES[providerSelect.value];
    var name = val("cred-name");
    if (editingCredentialId) submitEdit(tpl, name);
    else submitCreate(providerSelect.value, tpl, name);
  });

  // --- 연결된 클라우드 계정 표 ------------------------------------------------------------

  function formatRelative(iso) {
    var diffMs = Date.now() - new Date(iso).getTime();
    var minutes = Math.floor(diffMs / 60000);
    if (minutes < 1) return "방금 전";
    if (minutes < 60) return minutes + "분 전";
    var hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + "시간 전";
    return Math.floor(hours / 24) + "일 전";
  }

  function buildRow(account, credential) {
    var tr = document.createElement("tr");
    tr.className = "border-b border-border";
    tr.dataset.credentialId = credential.id;
    tr.style.userSelect = "none";
    tr.style.cursor = "grab";
    tr.style.touchAction = "none";

    function cell(text, className) {
      var td = document.createElement("td");
      td.className = className || "px-3 py-3";
      td.textContent = text;
      tr.appendChild(td);
      return td;
    }

    cell("⋮⋮", "px-3 py-3 cursor-grab text-muted-foreground");
    cell(credential.name, "px-3 py-3 font-medium");
    cell(PROVIDER_LABELS[account.provider] || account.provider);
    cell(account.external_account_id);
    cell(credential.masked_public_identifier || "—");

    var statusTd = cell("");
    var badge = document.createElement("span");
    if (credential.verified) {
      badge.className = "rounded-full bg-muted px-2 py-0.5 text-[11px] text-primary";
      badge.textContent = "✓ 검증" + (credential.verified_at ? " · " + formatRelative(credential.verified_at) : "");
    } else {
      badge.className = "rounded-full px-2 py-0.5 text-[11px] text-white";
      badge.style.background = "#c0392b";
      badge.textContent = "✗ 검증 실패";
    }
    statusTd.appendChild(badge);

    cell("—"); // 연결 리소스 — 리소스 조회 API 연동 전까지 자리표시자

    var actionsTd = document.createElement("td");
    actionsTd.className = "px-3 py-3";
    var actions = document.createElement("div");
    actions.className = "flex flex-wrap gap-1";

    function actionButton(label, className, handler) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = className;
      btn.textContent = label;
      btn.addEventListener("click", function () { handler(btn); });
      actions.appendChild(btn);
      return btn;
    }

    actionButton("수정", "rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-muted", function () {
      enterEditMode(account, credential);
    });
    actionButton("재검증", "rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-muted", function (btn) {
      reverifyCredential(credential, btn);
    });
    actionButton("삭제", "rounded-lg px-2.5 py-1 text-xs text-white", function (btn) {
      deleteCredential(credential, btn);
    }).style.background = "#c0392b";

    actionsTd.appendChild(actions);
    tr.appendChild(actionsTd);

    return tr;
  }

  function setRowButtonBusy(btn, busy, busyLabel, idleLabel) {
    btn.disabled = busy;
    btn.classList.toggle("opacity-50", busy);
    btn.classList.toggle("cursor-not-allowed", busy);
    btn.textContent = busy ? busyLabel : idleLabel;
  }

  function reverifyCredential(credential, btn) {
    setRowButtonBusy(btn, true, "검증 중…", "재검증");
    MCPApi.request("/credentials/" + credential.id + "/verify", { method: "POST" })
      .then(function (data) {
        showResult(
          data.verified
            ? "'" + credential.name + "' 검증에 성공했습니다."
            : "'" + credential.name + "' 검증에 실패했습니다. 키 값이나 권한을 확인해 주세요.",
          data.verified
        );
        loadAccounts();
      })
      .catch(function (err) {
        showResult(errorMessage(err), false);
        setRowButtonBusy(btn, false, "검증 중…", "재검증");
      });
  }

  function deleteCredential(credential, btn) {
    if (!window.confirm("'" + credential.name + "' 자격 증명을 삭제할까요?\n삭제해도 이미 수집된 리소스 기록은 남습니다.")) return;

    setRowButtonBusy(btn, true, "삭제 중…", "삭제");
    MCPApi.request("/credentials/" + credential.id, {
      method: "DELETE",
      headers: { "X-Action-Confirmed": "true" },
    })
      .then(function () {
        // 수정 중이던 항목을 지웠으면 폼부터 정리한다(exitEditMode가 hideResult를 부르므로 순서 주의).
        if (editingCredentialId === credential.id) exitEditMode();
        showResult("'" + credential.name + "' 자격 증명을 삭제했습니다.", true);
        loadAccounts();
      })
      .catch(function (err) {
        showResult(errorMessage(err), false);
        setRowButtonBusy(btn, false, "삭제 중…", "삭제");
      });
  }

  function getDataRows() {
    return Array.prototype.slice.call(tbody.querySelectorAll("tr[data-credential-id]"));
  }

  function loadAccounts() {
    getDataRows().forEach(function (row) { row.remove(); });
    emptyRow.hidden = true;
    loadingRow.hidden = false;
    loadingRow.querySelector("td").textContent = "불러오는 중…";

    MCPApi.request("/cloud-accounts")
      .then(function (data) {
        var accounts = data.items || [];
        if (!accounts.length) return [];
        return Promise.all(
          accounts.map(function (account) {
            return MCPApi.request("/cloud-accounts/" + account.id + "/credentials").then(function (credData) {
              return (credData.items || []).map(function (credential) {
                return { account: account, credential: credential };
              });
            });
          })
        ).then(function (grouped) {
          var flat = [];
          grouped.forEach(function (list) { flat = flat.concat(list); });
          flat.sort(function (a, b) { return a.credential.display_order - b.credential.display_order; });
          return flat;
        });
      })
      .then(function (rows) {
        loadingRow.hidden = true;
        if (!rows.length) {
          emptyRow.hidden = false;
          return;
        }
        rows.forEach(function (r) { tbody.appendChild(buildRow(r.account, r.credential)); });
        applyFilters();
      })
      .catch(function (err) {
        loadingRow.hidden = false;
        loadingRow.querySelector("td").textContent = "목록을 불러오지 못했습니다: " + errorMessage(err);
      });
  }

  // --- 필터 ---------------------------------------------------------------------------

  var platformSelect = document.getElementById("my-f-platform");
  var nameInput = document.getElementById("my-search-name");
  var tagSelect = document.getElementById("my-f-tag");

  function applyFilters() {
    var fPlatform = platformSelect.options[platformSelect.selectedIndex].text;
    var kw = nameInput.value.trim().toLowerCase();
    getDataRows().forEach(function (row) {
      var ok = true;
      if (!/전체/.test(fPlatform) && row.children[2].textContent.trim() !== fPlatform) ok = false;
      if (ok && kw && row.children[1].textContent.trim().toLowerCase().indexOf(kw) === -1) ok = false;
      row.hidden = !ok;
    });
  }
  platformSelect.addEventListener("change", applyFilters);
  nameInput.addEventListener("input", applyFilters);
  // 태그 필터는 API에 태그 조회 정책이 아직 없어 '전체'만 유효(연결만 해둠)
  tagSelect.addEventListener("change", applyFilters);

  // --- 행 드래그 순서 변경 → PUT /credentials/order ----------------------------------------

  var dragging = null;

  function rowUnder(x, y) {
    var el = document.elementFromPoint(x, y);
    var tr = el && el.closest ? el.closest("tr[data-credential-id]") : null;
    return tr && tr.parentNode === tbody ? tr : null;
  }

  function onMove(e) {
    if (!dragging) return;
    if (e.cancelable) e.preventDefault();
    var over = rowUnder(e.clientX, e.clientY);
    if (!over || over === dragging) return;
    var rect = over.getBoundingClientRect();
    var after = e.clientY - rect.top > rect.height / 2;
    tbody.insertBefore(dragging, after ? over.nextSibling : over);
  }

  function persistOrder() {
    var items = getDataRows().map(function (row, idx) {
      return { credential_id: row.dataset.credentialId, display_order: idx };
    });
    MCPApi.request("/credentials/order", { method: "PUT", body: { items: items } }).catch(function (err) {
      // 순서 저장은 편의 기능이라 실패해도 화면은 막지 않는다.
      console.error("순서 저장 실패:", errorMessage(err));
    });
  }

  function onUp() {
    if (dragging) {
      dragging.classList.remove("opacity-50");
      dragging.style.cursor = "grab";
      persistOrder();
    }
    dragging = null;
    document.removeEventListener("pointermove", onMove);
    document.removeEventListener("pointerup", onUp);
  }

  function onDown(e) {
    if (e.button !== undefined && e.button !== 0) return;
    // 관리 열의 버튼에서 시작한 pointerdown은 드래그로 삼지 않는다 — 아래에서 preventDefault를
    // 부르기 때문에 그냥 두면 수정/재검증/삭제 버튼의 click 이벤트가 통째로 먹힌다.
    if (e.target.closest && e.target.closest("button")) return;
    var tr = e.target.closest ? e.target.closest("tr[data-credential-id]") : null;
    if (!tr || tr.parentNode !== tbody) return;
    dragging = tr;
    tr.classList.add("opacity-50");
    tr.style.cursor = "grabbing";
    if (e.cancelable) e.preventDefault();
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
  }

  tbody.addEventListener("pointerdown", onDown);

  loadAccounts();
})();
