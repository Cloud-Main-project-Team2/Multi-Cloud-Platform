/* AWS EC2 프로비저닝 실 API 테스트 섹션 (provisioning.html 하단, 위 PROV-01 마법사와 독립적).
 *
 * 위 마법사는 assets/js/provisioning.js 상단 주석대로 아직 서버 통신이 없는 시뮬레이션이다.
 * 이 섹션만 실제 backend/app/routers/provisioning.py(§10.3, AWS EC2 전용 러너)와 통신해서
 * 커밋 전에 사람이 직접 눌러보고 확인할 수 있게 한다. window.MCPApi(assets/js/api.js)로
 * 실제 로그인 세션의 JWT를 그대로 쓴다 — X-User-Id 같은 임시 헤더는 더 이상 없다.
 */
(function () {
  "use strict";

  var tbody = document.getElementById("aws-ec2-jobs-tbody");
  if (!tbody || !window.MCPApi) return; // 이 섹션이 없는 페이지거나 api.js 미로드면 무시

  var credentialSelect = document.getElementById("aws-ec2-credential");
  var resultBox = document.getElementById("aws-ec2-result");

  var ERROR_MESSAGES = {
    IDEMPOTENCY_KEY_REQUIRED: "요청 식별 키가 없습니다(내부 오류).",
    CONFIRMATION_REQUIRED: "확인이 필요한 작업입니다.",
    VALIDATION_ERROR: "입력값을 다시 확인해 주세요(이름 형식·리전·인스턴스 타입).",
    SECRET_FIELD_NOT_ALLOWED: "입력값에 자격 증명으로 보이는 필드가 있습니다.",
    CREDENTIAL_NOT_FOUND: "자격 증명을 찾을 수 없습니다.",
    CLOUD_PERMISSION_DENIED: "이 자격 증명은 검증되지 않았거나 프로비저닝 권한이 없습니다 — 마이페이지에서 검증하세요.",
    JOB_NOT_CANCELLABLE: "이미 실행 중이거나 종료된 작업은 취소할 수 없습니다.",
    PROVISIONING_NOT_IMPLEMENTED: "아직 지원하지 않는 조합입니다.",
    AUTHENTICATION_REQUIRED: "로그인이 만료되었습니다. 다시 로그인해 주세요.",
    INVALID_TOKEN: "로그인이 만료되었습니다. 다시 로그인해 주세요.",
  };
  function errorMessage(err) {
    return ERROR_MESSAGES[err.code] || err.message || "요청 처리 중 오류가 발생했습니다.";
  }

  function showResult(message, isError) {
    resultBox.hidden = false;
    resultBox.classList.toggle("border-red-400", !!isError);
    resultBox.textContent = message;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function fillSelect(select, items, labelFn, placeholder) {
    select.innerHTML = "";
    if (!items.length) {
      var opt = document.createElement("option");
      opt.value = "";
      opt.textContent = placeholder;
      select.appendChild(opt);
      return;
    }
    items.forEach(function (item) {
      var opt = document.createElement("option");
      opt.value = String(item.id);
      opt.textContent = labelFn(item);
      select.appendChild(opt);
    });
  }

  function loadCredentials() {
    MCPApi.request("/cloud-accounts")
      .then(function (data) {
        var accounts = (data.items || []).filter(function (a) { return a.provider === "aws"; });
        if (!accounts.length) {
          fillSelect(credentialSelect, [], null, "등록된 AWS 계정 없음 — 마이페이지에서 먼저 등록");
          return null;
        }
        return Promise.all(
          accounts.map(function (account) {
            return MCPApi.request("/cloud-accounts/" + account.id + "/credentials").then(function (credData) {
              return (credData.items || []).map(function (credential) {
                return {
                  id: credential.id,
                  label:
                    (account.account_label || account.external_account_id) +
                    " · " + credential.name + (credential.verified ? " (검증됨)" : " (미검증)"),
                };
              });
            });
          })
        ).then(function (grouped) {
          var flat = [];
          grouped.forEach(function (list) { flat = flat.concat(list); });
          fillSelect(credentialSelect, flat, function (c) { return c.label; }, "등록된 자격 증명 없음");
        });
      })
      .catch(function (err) {
        fillSelect(credentialSelect, [], null, "조회 실패: " + errorMessage(err));
      });
  }

  function newIdempotencyKey() {
    return "ui-" + Date.now() + "-" + Math.random().toString(36).slice(2, 10);
  }

  function statusBadge(status) {
    var color = {
      queued: "text-muted-foreground", running: "text-sky", success: "text-primary", cancelled: "text-muted-foreground",
    }[status] || "";
    var style = status === "failed" ? ' style="color:#c0392b"' : "";
    return '<span class="' + color + '"' + style + ">" + escapeHtml(status) + "</span>";
  }

  function renderJobs(jobs) {
    if (!jobs.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="py-3 text-xs text-muted-foreground">아직 생성한 job이 없습니다.</td></tr>';
      return;
    }
    tbody.innerHTML = jobs.map(function (job) {
      var resultText = job.result && job.result.instance_id
        ? escapeHtml(job.result.instance_id) + (job.result.public_ip ? " (" + escapeHtml(job.result.public_ip) + ")" : "")
        : (job.error ? escapeHtml((job.error.message || "").slice(0, 60)) : "-");
      var actions = "";
      if (job.status === "queued") {
        actions = '<button type="button" data-cancel-job="' + job.id + '" class="rounded-lg border border-border px-2 py-1 text-xs font-medium hover:bg-muted">취소</button>';
      } else if (job.status === "running") {
        actions = '<span class="text-xs text-muted-foreground">진행 중...</span>';
      } else {
        actions = '<span class="text-xs text-muted-foreground">-</span>';
      }
      var spec = job.common_spec || {};
      var providerSpec = job.provider_spec || {};
      return (
        '<tr class="border-b border-border">' +
        '<td class="py-2 pr-3">#' + job.id + "</td>" +
        '<td class="py-2 pr-3">' + escapeHtml(spec.name) + " / " + escapeHtml(providerSpec.instance_type) + "</td>" +
        '<td class="py-2 pr-3">' + statusBadge(job.status) + "</td>" +
        '<td class="py-2 pr-3">' + resultText + "</td>" +
        '<td class="py-2 pr-3">' + actions + "</td>" +
        "</tr>"
      );
    }).join("");
  }

  function loadJobs() {
    MCPApi.request("/provisioning/jobs")
      .then(function (data) { renderJobs(data.items || []); })
      .catch(function (err) {
        tbody.innerHTML =
          '<tr><td colspan="5" class="py-3 text-xs" style="color:#c0392b">조회 실패: ' + escapeHtml(errorMessage(err)) + "</td></tr>";
      });
  }

  document.getElementById("aws-ec2-refresh-btn").addEventListener("click", loadJobs);

  tbody.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-cancel-job]");
    if (!btn) return;
    var jobId = btn.getAttribute("data-cancel-job");
    if (!confirm("job #" + jobId + "을(를) 취소하시겠습니까?")) return;

    MCPApi.request("/provisioning/jobs/" + jobId + "/cancel", { method: "POST" })
      .then(function () { loadJobs(); })
      .catch(function (err) {
        showResult("취소 실패: " + errorMessage(err), true);
        loadJobs();
      });
  });

  document.getElementById("aws-ec2-create-btn").addEventListener("click", function () {
    var credentialId = credentialSelect.value;
    if (!credentialId) {
      showResult("AWS 자격 증명을 먼저 선택하세요.", true);
      return;
    }
    var name = (document.getElementById("aws-ec2-name").value || "").trim();
    if (!name) {
      showResult("이름을 입력하세요.", true);
      return;
    }
    if (!confirm("실제 AWS에 EC2 인스턴스를 생성합니다. 과금이 발생할 수 있습니다. 계속할까요?")) return;

    var payload = {
      credential_id: credentialId,
      common_spec: { name: name },
      provider_spec: {
        region: document.getElementById("aws-ec2-region").value,
        instance_type: document.getElementById("aws-ec2-instance-type").value,
        ami_id: document.getElementById("aws-ec2-ami").value || null,
      },
    };

    showResult("EC2 프로비저닝 요청을 보내는 중입니다...", false);

    MCPApi.request("/provisioning/aws/ec2", {
      method: "POST",
      headers: { "Idempotency-Key": newIdempotencyKey(), "X-Action-Confirmed": "true" },
      body: payload,
    })
      .then(function (data) {
        showResult("요청 생성됨 — job #" + data.id + ", status=" + data.status, false);
        loadJobs();
      })
      .catch(function (err) {
        showResult("요청 실패: " + errorMessage(err), true);
      });
  });

  loadCredentials();
  loadJobs();
  setInterval(loadJobs, 4000);
})();
