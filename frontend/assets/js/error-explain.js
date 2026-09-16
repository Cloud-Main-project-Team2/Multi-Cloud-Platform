/* 에러 자연어 설명 공통 모듈 — 백엔드 error_catalog/error_patterns가 응답에 실어 주는
 * explanation(증상·원인·해결책)과 specific_reason(원문 번역)을 화면에 일관되게 보여준다.
 *
 * 그동안 provisioning/inventory/mypage/agent가 각자 에러코드→한글 맵을 따로 들고 있었고
 * 같은 코드가 화면마다 다른 문구였다. 이 모듈이 단일 표시 경로다:
 *  - 동기 API 오류: api.js가 만든 Error(.code/.explanation/.specificReason/.requestId)
 *  - 잡/항목 오류: job.error / item.error / result.error({code,message,explanation,specific_reason})
 *  - 네트워크 단절 등 code 없는 오류: 로컬 폴백
 *
 * 원칙: 서버가 준 설명을 우선 쓰고, 없을 때만 로컬 폴백. 원문(message)은 지우지 않고 '상세 정보'로.
 */
window.MCErr = (function () {
  "use strict";

  var DANGER = "#c0392b"; // provisioning.js와 동일한 오류 강조색(라이트/다크 공통)

  // 서버가 explanation을 못 준 경우(네트워크 단절, 구형 응답 등)만을 위한 최소 폴백.
  // 코드별 상세 문구는 백엔드 error_catalog가 단일 소스이므로 여기서 중복 관리하지 않는다.
  var FALLBACK = {
    NETWORK_ERROR: {
      symptom: "서버에 연결하지 못했습니다.",
      cause: "네트워크가 끊겼거나 서버가 응답하지 않습니다.",
      remedy: "인터넷 연결을 확인하고 잠시 후 다시 시도하세요.",
    },
    UNKNOWN_ERROR: {
      symptom: "작업 중 오류가 발생했습니다.",
      cause: "원인을 확인하지 못했습니다.",
      remedy: "잠시 후 다시 시도하고, 계속되면 아래 코드와 함께 문의하세요.",
    },
    // 백엔드 error_catalog에 아직 없는 코드(실행 3종 범위 밖). 서버가 unknown 설명을 주므로
    // 아래 규칙(category==="unknown"이면 폴백)에 의해 이 문구가 쓰인다.
    BucketNotEmpty: {
      symptom: "버킷이 비어 있어야 삭제할 수 있습니다.",
      cause: "버킷 안에 객체가 남아 있습니다.",
      remedy: "버킷을 비운 뒤 다시 삭제하거나, 강제 삭제 옵션으로 다시 시도하세요.",
    },
    // AI 에이전트(agent) — 카탈로그 범위 밖이라 여기서 폴백 문구를 관리한다.
    AGENT_NOT_CONFIGURED: {
      symptom: "AI 에이전트가 아직 설정되지 않았습니다.",
      cause: "관리자가 API 키를 등록해야 사용할 수 있습니다.",
      remedy: "관리자에게 AI 에이전트 설정을 요청하세요.",
    },
    AGENT_UPSTREAM_ERROR: {
      symptom: "AI 응답을 가져오지 못했습니다.",
      cause: "AI 서비스 호출이 일시적으로 실패했습니다.",
      remedy: "잠시 후 다시 시도하세요.",
    },
  };

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // 다양한 모양의 오류를 표시용 단일 객체로 정규화한다.
  function normalize(err) {
    err = err || {};
    // 네트워크 단절: fetch가 reject하면 code가 없는 TypeError가 온다.
    var isNetwork = !err.code && (err instanceof TypeError || err.name === "TypeError");
    var code = err.code || (isNetwork ? "NETWORK_ERROR" : "UNKNOWN_ERROR");

    // explanation: api.js Error(.explanation)와 잡 오류(error.explanation) 둘 다 같은 키 구조.
    // 단, 서버가 모르는 코드는 category="unknown"의 일반 설명을 주므로, 그럴 땐 로컬 폴백을
    // 우선한다(예: BucketNotEmpty — 백엔드 카탈로그에 아직 없음).
    var exp = err.explanation || null;
    var backendUsable = !!(exp && exp.category && exp.category !== "unknown");
    var fb = FALLBACK[code] || FALLBACK.UNKNOWN_ERROR;

    // specific_reason(잡 오류)와 specificReason(api.js Error) 둘 다 수용.
    var specific = err.specificReason || err.specific_reason || null;

    return {
      code: code,
      symptom: (backendUsable && exp.symptom) || fb.symptom,
      cause: (backendUsable && exp.cause) || fb.cause,
      remedy: (backendUsable && exp.remedy) || fb.remedy,
      category: (backendUsable && exp.category) || null,
      specificReason: specific,
      rawMessage: err.message || null,
      requestId: err.requestId || err.request_id || null,
    };
  }

  // 짧은 한 줄(alert/인라인용). specific_reason이 있으면 그게 가장 구체적이다.
  function headline(err) {
    var n = normalize(err);
    return n.specificReason || n.symptom;
  }

  // 복사·문의용 진단 문자열.
  function diagnosticText(n) {
    var lines = ["에러 코드: " + n.code];
    if (n.requestId) lines.push("요청 ID: " + n.requestId);
    if (n.rawMessage) lines.push("원문: " + n.rawMessage);
    return lines.join("\n");
  }

  // 재사용 에러 패널 HTML. 위: 증상+구체원인+해결책(사용자용). 아래 접기: 코드·요청ID·원문(개발자용).
  function panelHtml(err) {
    var n = normalize(err);
    var secondary = n.specificReason || n.cause;
    var html =
      '<div class="rounded-lg border border-border bg-surface overflow-hidden" ' +
      'style="border-left:4px solid ' + DANGER + '" data-err-panel role="alert">' +
      '<div class="p-3 sm:p-4">' +
      '<div class="flex items-start gap-2">' +
      '<span aria-hidden="true" class="mt-0.5 font-bold" style="color:' + DANGER + '">!</span>' +
      '<div class="min-w-0 flex-1">' +
      '<p class="font-semibold text-foreground">' + escapeHtml(n.symptom) + "</p>";
    if (secondary) {
      html += '<p class="mt-1 text-sm text-muted-foreground break-words">' + escapeHtml(secondary) + "</p>";
    }
    html +=
      '<p class="mt-2 text-sm text-foreground"><span class="font-medium">해결 방법: </span>' +
      escapeHtml(n.remedy) + "</p>" +
      '<div class="mt-3">' +
      '<button type="button" data-err-toggle ' +
      'class="text-xs text-muted-foreground underline underline-offset-2">상세 정보</button>' +
      '<div data-err-details hidden class="mt-2 rounded bg-muted p-2 text-xs text-muted-foreground">' +
      "<div>에러 코드: <code>" + escapeHtml(n.code) + "</code></div>";
    if (n.requestId) {
      html += "<div>요청 ID: <code>" + escapeHtml(n.requestId) + "</code></div>";
    }
    if (n.rawMessage) {
      html +=
        '<div class="mt-1 whitespace-pre-wrap break-words">원문: ' + escapeHtml(n.rawMessage) + "</div>";
    }
    html +=
      '<button type="button" data-err-copy ' +
      'class="mt-2 rounded border border-border px-2 py-1 text-xs hover:bg-surface">복사</button>' +
      "</div></div></div></div></div></div>";
    return html;
  }

  // 배경클릭/토글/복사 동작을 붙인다. panelHtml을 붙인 컨테이너(또는 그 상위)에 대해 위임.
  function wire(root) {
    if (!root || root._mcErrWired) return;
    root._mcErrWired = true;
    root.addEventListener("click", function (e) {
      var toggle = e.target.closest && e.target.closest("[data-err-toggle]");
      if (toggle) {
        var panel = toggle.closest("[data-err-panel]");
        var details = panel && panel.querySelector("[data-err-details]");
        if (details) {
          details.hidden = !details.hidden;
          toggle.textContent = details.hidden ? "상세 정보" : "상세 정보 닫기";
        }
        return;
      }
      var copy = e.target.closest && e.target.closest("[data-err-copy]");
      if (copy) {
        var box = copy.closest("[data-err-details]");
        var text = box ? box.innerText.replace(/\s*복사\s*$/, "").trim() : "";
        copyToClipboard(text, copy);
      }
    });
  }

  function copyToClipboard(text, btn) {
    var done = function () {
      var old = btn.textContent;
      btn.textContent = "복사됨";
      setTimeout(function () { btn.textContent = old; }, 1500);
    };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {});
        return;
      }
    } catch (e) {}
    try {
      var ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      document.execCommand("copy"); document.body.removeChild(ta); done();
    } catch (e2) {}
  }

  // 컨테이너에 패널을 렌더하고 동작까지 연결한다(가장 흔한 사용처).
  function renderInto(container, err) {
    if (!container) return;
    container.innerHTML = panelHtml(err);
    container.hidden = false;
    wire(container);
  }

  function clear(container) {
    if (!container) return;
    container.innerHTML = "";
    container.hidden = true;
  }

  return {
    normalize: normalize,
    headline: headline,
    panelHtml: panelHtml,
    renderInto: renderInto,
    wire: wire,
    clear: clear,
    diagnosticText: function (err) { return diagnosticText(normalize(err)); },
    escapeHtml: escapeHtml,
  };
})();
