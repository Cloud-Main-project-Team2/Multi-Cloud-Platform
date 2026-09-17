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

  // AWS는 인증 방식이 둘이다. 기본은 역할 위임이고, 액세스 키는 기존에 등록해 둔 계정을
  // 수정할 때를 위해 남겨 둔 레거시 경로다(서버도 두 방식을 모두 받는다 — auth_type 병존).
  var AWS_ROLE_ARN_RE = /^arn:aws:iam::(\d{12}):role\/(.+)$/;

  var AWS_DELEGATION_TEMPLATE = {
    // 계정 ID는 Role ARN 안에 이미 들어 있다 — 따로 입력받으면 서로 어긋나 원인 모를
    // CREDENTIAL_ACCOUNT_MISMATCH가 난다.
    showAccountId: false,
    fieldsHtml:
      '<div id="cred-aws-delegation-guide" class="rounded-lg border border-border bg-muted/40 p-3 text-sm">' +
        '<p class="text-muted-foreground">연결 안내를 불러오는 중…</p>' +
      '</div>' +
      '<div>' +
        '<label for="cred-aws-role-arn" class="mb-1 block text-sm font-medium">역할 ARN</label>' +
        '<input id="cred-aws-role-arn" type="text" placeholder="arn:aws:iam::123456789012:role/MultiCloudOpsAccess" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary" />' +
        '<p id="cred-aws-role-arn-hint" class="mt-1 text-xs text-muted-foreground">역할을 만든 뒤 표시되는 ARN을 그대로 붙여넣으세요. 계정 ID는 여기서 자동으로 읽습니다.</p>' +
      '</div>' +
      '<div>' +
        '<label for="cred-aws-external-id" class="mb-1 block text-sm font-medium">External ID</label>' +
        '<input id="cred-aws-external-id" type="text" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary" />' +
        '<p class="mt-1 text-xs text-muted-foreground">위 안내에 표시된 값입니다. 역할을 먼저 만들어 뒀다면 그 신뢰 정책에 넣은 값을 입력하세요.</p>' +
      '</div>',
    isValid: function () {
      return AWS_ROLE_ARN_RE.test(val("cred-aws-role-arn")) && nonEmpty("cred-aws-external-id");
    },
    hasSecretInput: function () {
      return nonEmpty("cred-aws-role-arn") || nonEmpty("cred-aws-external-id");
    },
    secretPayload: function () {
      return {
        auth_type: "assume_role",
        role_arn: val("cred-aws-role-arn"),
        external_id: val("cred-aws-external-id"),
      };
    },
    publicIdentifier: function () {
      var match = AWS_ROLE_ARN_RE.exec(val("cred-aws-role-arn"));
      return match ? match[2] : "";
    },
    externalAccountId: function () {
      var match = AWS_ROLE_ARN_RE.exec(val("cred-aws-role-arn"));
      return match ? match[1] : "";
    },
  };

  var AWS_ACCESS_KEY_TEMPLATE = {
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
          '<button type="button" onclick="MCUI.togglePassword(\'cred-aws-secret-access-key\', this)" class="absolute right-2 top-1/2 -translate-y-1/2 grid h-7 w-7 place-items-center rounded text-base hover:bg-muted" aria-label="키 표시 전환"><svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>' +
        '</div>' +
        '<p class="mt-1 text-xs text-yellow">장기 Access Key는 만료가 없어 보관 위험이 큽니다. 새로 연결한다면 역할 위임 방식을 권장합니다.</p>' +
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
  };

  var PROVIDER_TEMPLATES = {
    aws: {
      isAws: true,
      // 인증 방식 선택기는 재렌더링돼도 남아 있어야 해서 AWS 껍데기에 둔다.
      fieldsHtml:
        '<div>' +
          '<label for="cred-aws-auth-type" class="mb-1 block text-sm font-medium">인증 방식</label>' +
          '<select id="cred-aws-auth-type" class="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm">' +
            '<option value="assume_role">역할 위임 (권장 · 키를 저장하지 않음)</option>' +
            '<option value="access_key">액세스 키 (레거시)</option>' +
          '</select>' +
        '</div>' +
        '<div id="cred-aws-auth-fields" class="grid gap-3"></div>',
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
            '<button type="button" onclick="MCUI.togglePassword(\'cred-azure-client-secret\', this)" class="absolute right-2 top-1/2 -translate-y-1/2 grid h-7 w-7 place-items-center rounded text-base hover:bg-muted" aria-label="키 표시 전환"><svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>' +
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

  // 현재 폼이 실제로 쓰고 있는 템플릿. AWS만 인증 방식에 따라 갈린다.
  function currentTemplate() {
    if (providerSelect.value !== "aws") return PROVIDER_TEMPLATES[providerSelect.value];
    return awsAuthType() === "access_key" ? AWS_ACCESS_KEY_TEMPLATE : AWS_DELEGATION_TEMPLATE;
  }

  function awsAuthType() {
    var el = document.getElementById("cred-aws-auth-type");
    return el ? el.value : "assume_role";
  }

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  // 역할 위임 안내는 서버가 만들어 준다(플랫폼 계정 ID·새 ExternalId·붙여넣을 신뢰 정책).
  // ExternalId는 요청할 때마다 새로 발급되므로 입력칸에 그대로 채워 준다.
  function loadDelegationSetup() {
    var guide = document.getElementById("cred-aws-delegation-guide");
    if (!guide) return;

    MCPApi.request("/credentials/aws/delegation-setup")
      .then(function (data) {
        var externalIdInput = document.getElementById("cred-aws-external-id");
        if (externalIdInput && !externalIdInput.value) externalIdInput.value = data.external_id;

        var arnInput = document.getElementById("cred-aws-role-arn");
        if (arnInput && !arnInput.value) {
          arnInput.placeholder = "arn:aws:iam::<내 계정 ID>:role/" + data.suggested_role_name;
        }

        // 인라인 권한 정책은 서버가 완성된 Statement 배열을 그대로 준다(2026-09-17 — mcp-ssm-*
        // 리소스로 좁혀야 하는 IAM 관리 권한이 추가되면서 statement가 2개가 돼 프론트에서
        // "Resource: *" 한 덩어리로 조립할 수 없게 됐다).
        var inlinePolicy = {
          Version: "2012-10-17",
          Statement: data.inline_statements,
        };

        // JSON 블록은 길어서 접어 둔다(<details>는 브라우저 기본 토글이라 JS가 필요 없다).
        function jsonBlock(summary, note, value) {
          return (
            '<details class="mt-2 rounded-lg border border-border bg-background">' +
              '<summary class="cursor-pointer select-none px-3 py-2 text-sm font-medium">' + escapeHtml(summary) + '</summary>' +
              '<div class="border-t border-border px-3 py-2">' +
                (note ? '<p class="mb-2 text-xs text-muted-foreground">' + note + '</p>' : '') +
                '<textarea readonly rows="10" class="w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-[11px]">' +
                  escapeHtml(JSON.stringify(value, null, 2)) +
                '</textarea>' +
              '</div>' +
            '</details>'
          );
        }

        guide.innerHTML =
          '<p class="font-medium">AWS 콘솔에서 역할을 먼저 만들어 주세요</p>' +
          '<ol class="mt-2 list-decimal space-y-1 pl-5 text-muted-foreground">' +
            '<li>IAM → <b>역할</b> → 역할 만들기 → <b>사용자 지정 신뢰 정책</b>을 선택하고 아래 ①을 붙여넣습니다. ' +
              '<b>정책 메뉴가 아니라 역할 메뉴</b>입니다 — ①에는 <code>Principal</code>이 있어 "정책 만들기"로는 생성되지 않습니다.</li>' +
            '<li>권한 추가에서 ' + data.managed_policy_arns.map(function (arn) {
              return escapeHtml(arn.split("/").pop());
            }).join(", ") + '를 선택합니다.</li>' +
            '<li>역할 이름은 <b>' + escapeHtml(data.role_name_prefix) + '</b>로 시작해야 합니다(예: ' + escapeHtml(data.suggested_role_name) + ').</li>' +
            '<li>역할을 만든 뒤 <b>권한 탭 → 인라인 정책 추가</b>로 아래 ②를 붙여넣습니다.</li>' +
            '<li>만들어진 <b>역할 ARN</b>을 아래에 붙여넣습니다.</li>' +
          '</ol>' +
          jsonBlock("① 신뢰 정책 (역할 만들기 중 붙여넣기)", "역할 생성 화면의 \"사용자 지정 신뢰 정책\"에만 들어갑니다.", data.trust_policy) +
          jsonBlock(
            "② 인라인 권한 정책 (역할 생성 후 추가)",
            "관리형 정책에 없는 권한입니다. 첫 번째 항목(비용 표시·권한 자동 판별·기존 네트워크 조회)은 없어도 연결 자체는 되지만, 두 번째 항목(mcp-ssm-* 역할 관리)이 없으면 EC2 생성 시 SSM 콘솔 접속용 역할을 만들지 못해 프로비저닝이 실패합니다.",
            inlinePolicy
          ) +
          '<p class="mt-2"><a href="' + escapeHtml(data.iam_console_url) + '" target="_blank" rel="noopener" class="text-primary underline">IAM 콘솔에서 역할 만들기 ' + MCUI.icons.externalLink + '</a></p>' +
          '<p class="mt-2 text-xs text-muted-foreground">이 방식에서는 Access Key를 저장하지 않습니다. 저장되는 값은 역할 ARN과 External ID뿐이며, 둘 다 그 자체로는 권한이 없습니다.</p>';
      })
      .catch(function (err) {
        guide.innerHTML = '<p class="text-yellow">' + escapeHtml(errorMessage(err)) + '</p>';
      });
  }

  function renderAwsAuthFields() {
    var container = document.getElementById("cred-aws-auth-fields");
    if (!container) return;
    var tpl = currentTemplate();
    container.innerHTML = tpl.fieldsHtml;
    accountIdField.hidden = !tpl.showAccountId;
    if (tpl.showAccountId) {
      accountIdLabel.textContent = tpl.accountIdLabel;
      accountIdInput.placeholder = tpl.accountIdPlaceholder;
    }
    if (awsAuthType() === "assume_role") loadDelegationSetup();
  }

  function renderProviderFields() {
    var tpl = PROVIDER_TEMPLATES[providerSelect.value];
    fieldsContainer.innerHTML = tpl.fieldsHtml;
    accountIdField.hidden = !tpl.showAccountId;
    if (tpl.isAws) {
      var authSelect = document.getElementById("cred-aws-auth-type");
      if (authSelect) authSelect.addEventListener("change", renderAwsAuthFields);
      renderAwsAuthFields();
    }
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

  // 검증 실패·등록 오류는 인라인 문구가 아니라 팝업으로 띄운다(2026-09-16). 성공/안내는 인라인 유지.
  var errModalTitle = document.getElementById("cred-error-modal-title");
  var errModalBody = document.getElementById("cred-error-modal-body");
  function showErrorPopup(title, message) {
    if (errModalTitle) errModalTitle.textContent = title;
    if (errModalBody) errModalBody.textContent = message;
    if (window.MCPModal) window.MCPModal.open("#cred-error-modal");
    else showResult(message, false); // modal.js가 없으면 인라인으로 폴백
  }

  var ERROR_MESSAGES = {
    CREDENTIAL_ALREADY_EXISTS: "같은 이름의 자격 증명이 이미 있습니다. 다른 이름을 사용해 주세요.",
    CREDENTIAL_NOT_FOUND: "자격 증명을 찾을 수 없습니다. 목록을 새로고침해 주세요.",
    CREDENTIAL_IN_USE: "진행 중인 작업이 이 자격 증명을 사용하고 있어 삭제할 수 없습니다. 작업이 끝난 뒤 다시 시도해 주세요.",
    CONFIRMATION_REQUIRED: "확인이 필요한 작업입니다.",
    VALIDATION_ERROR: "입력값을 다시 확인해 주세요.",
    PLATFORM_AWS_NOT_CONFIGURED: "서비스의 AWS 설정이 없어 역할 위임 연결을 안내할 수 없습니다. 관리자에게 문의해 주세요.",
    CLOUD_PERMISSION_DENIED: "역할을 빌릴 수 없습니다. 역할 이름·신뢰 정책의 계정 ID·External ID를 확인해 주세요.",
    CREDENTIAL_ACCOUNT_MISMATCH: "역할이 속한 AWS 계정이 등록하려는 계정과 다릅니다.",
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

    // 편집 대상이 어떤 방식으로 등록됐는지에 맞춰 폼을 연다 — 위임 credential을 열었는데
    // 액세스 키 입력칸이 뜨면 교체가 방식 변경으로 잘못 이어진다.
    if (account.provider === "aws") {
      var authSelect = document.getElementById("cred-aws-auth-type");
      if (authSelect) {
        authSelect.value = credential.auth_type === "access_key" ? "access_key" : "assume_role";
        renderAwsAuthFields();
      }
    }

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

  var submitBusyStop = null;
  function setSubmitting(busy) {
    submitBtn.classList.toggle("opacity-50", busy);
    submitBtn.classList.toggle("cursor-not-allowed", busy);
    if (busy) {
      if (!submitBusyStop) submitBusyStop = MCUI.buttonBusy(submitBtn, "저장 중");
    } else if (submitBusyStop) {
      submitBusyStop();
      submitBusyStop = null;
    } else {
      submitBtn.disabled = false;
    }
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
        // form.reset()은 플랫폼 select를 기본값(AWS)으로 되돌리므로, 방금 등록한 플랫폼을
        // 그대로 유지하도록 복원한다(같은 플랫폼 계정을 이어서 추가하기 편하게).
        form.reset();
        providerSelect.value = provider;
        renderProviderFields();
        if (data.verified) {
          showResult("저장되었습니다. 검증에 성공했습니다.", true);
        } else {
          showResult("저장되었습니다. 다만 검증에는 실패했습니다 — 자세한 내용은 팝업과 아래 표를 확인해 주세요.", false);
          showErrorPopup(
            "검증에 실패했습니다",
            "자격 증명은 저장되었지만 검증에 실패했습니다.\n\n" +
              (data.verification_error_message || "아래 표에서 상태를 확인하고 키 값·권한을 점검한 뒤 재검증해 주세요.")
          );
        }
        loadAccounts();
      })
      .catch(function (err) {
        showErrorPopup("저장하지 못했습니다", errorMessage(err));
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
        } else if (data.verified) {
          showResult("키를 교체하고 검증에 성공했습니다.", true);
        } else {
          showResult("키를 교체했지만 검증에는 실패했습니다 — 자세한 내용은 팝업과 아래 표를 확인해 주세요.", false);
          showErrorPopup(
            "검증에 실패했습니다",
            "키를 교체했지만 검증에 실패했습니다.\n\n" +
              (data.verification_error_message || "값을 다시 확인한 뒤 재검증해 주세요.")
          );
        }
        loadAccounts();
      })
      .catch(function (err) {
        showErrorPopup("수정하지 못했습니다", errorMessage(err));
      })
      .then(function () { setSubmitting(false); });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var tpl = currentTemplate();
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

    cell("⋮⋮", "px-2 py-3 cursor-grab text-muted-foreground");
    cell(credential.name, "px-3 py-3 font-medium");
    cell(PROVIDER_LABELS[account.provider] || account.provider);
    cell(account.external_account_id, "px-2 py-3");
    var keyTd = cell(credential.masked_public_identifier || "—", "px-2 py-3");
    if (account.provider === "aws") {
      var authBadge = document.createElement("span");
      var delegated = credential.auth_type === "assume_role";
      authBadge.className = "ml-1.5 rounded-full px-2 py-0.5 text-[11px] " +
        (delegated ? "bg-muted text-primary" : "bg-muted text-yellow");
      // 레거시는 "지금 당장 문제"가 아니라 "바꾸는 게 좋다"는 신호라 경고색까지는 쓰지 않는다.
      authBadge.textContent = delegated ? "역할 위임" : "레거시 키";
      authBadge.title = delegated
        ? "장기 키를 저장하지 않고 필요할 때마다 임시 자격 증명을 발급받습니다."
        : "장기 Access Key가 저장돼 있습니다. 역할 위임 방식으로 교체하는 것을 권장합니다.";
      keyTd.appendChild(authBadge);
    }

    var statusTd = cell("");
    var badge = document.createElement("span");
    if (credential.verified) {
      badge.className = "inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px] text-primary";
      badge.innerHTML = MCUI.icons.check + "<span>검증" + (credential.verified_at ? " · " + escapeHtml(formatRelative(credential.verified_at)) : "") + "</span>";
    } else {
      badge.className = "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] text-white";
      badge.style.background = "#c0392b";
      badge.innerHTML = MCUI.icons.x + "<span>검증 실패</span>";
    }
    statusTd.appendChild(badge);

    cell("—"); // 연결 리소스 — 리소스 조회 API 연동 전까지 자리표시자

    var actionsTd = document.createElement("td");
    actionsTd.className = "px-3 py-3";
    var actions = document.createElement("div");
    actions.className = "flex flex-nowrap gap-1";

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
        if (data.verified) {
          showResult("'" + credential.name + "' 검증에 성공했습니다.", true);
        } else {
          showErrorPopup(
            "검증에 실패했습니다",
            "'" + credential.name + "' 검증에 실패했습니다.\n\n키 값이나 권한을 확인한 뒤 다시 시도해 주세요."
          );
        }
        loadAccounts();
      })
      .catch(function (err) {
        showErrorPopup("검증하지 못했습니다", errorMessage(err));
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

  // --- 계정 정보 (GET /auth/me) — 하드코딩 대신 실제 로그인 사용자 정보 렌더링 ---------------
  function renderAffiliation(user) {
    if (user.affiliation_type === "company") {
      return user.affiliation_name ? "회사 · " + user.affiliation_name : "회사";
    }
    return "개인";
  }

  function loadProfile() {
    var emailEl = document.getElementById("profile-email");
    var nameEl = document.getElementById("profile-name");
    var affEl = document.getElementById("profile-affiliation");
    if (!emailEl && !nameEl && !affEl) return;
    MCPApi.request("/auth/me")
      .then(function (resp) {
        var user = (resp && resp.data) || {};
        if (emailEl) emailEl.textContent = user.email || "—";
        if (nameEl) nameEl.textContent = user.name || "—";
        if (affEl) affEl.textContent = renderAffiliation(user);
      })
      .catch(function (err) {
        // 실패 시 자리표시자 유지(auth-guard가 세션 만료는 이미 로그인으로 보낸다).
        var msg = errorMessage(err);
        [emailEl, nameEl, affEl].forEach(function (el) { if (el) el.textContent = "불러오지 못했습니다"; });
        if (window.console) console.warn("profile load failed:", msg);
      });
  }

  loadProfile();
  loadAccounts();
})();
