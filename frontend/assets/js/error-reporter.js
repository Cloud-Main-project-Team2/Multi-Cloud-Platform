/* 프론트엔드 오류 리포터 — 브라우저에서 터진 에러를 서버 로그(app.log)로 보낸다.
 *
 * 서버 로그만 보면 "화면이 안 떠요"의 원인을 알 수 없다. 여기서 보낸 한 건이 `client.error`로
 * 남고, API 실패는 서버가 돌려준 X-Request-Id를 함께 실어 백엔드 로그와 같은 id로 이어진다.
 *
 * 원칙:
 * - **에러만** 보낸다(사용자 행동 추적 없음).
 * - 리포터 자신의 실패는 절대 다시 리포트하지 않는다(무한 루프 방지).
 * - 같은 에러는 페이지당 1번, 총 20건까지만 보낸다(로그 폭탄 방지 — 서버에도 IP당 한도가 있다).
 * - URL의 쿼리스트링은 잘라서 보낸다(비밀번호 재설정 링크의 ?token= 등이 로그에 남지 않게).
 */
window.MCPErrorReporter = (function () {
  "use strict";

  var API_BASE = (window.MCPApi && window.MCPApi.API_BASE) || "http://localhost:8000/api/v1";
  var ENDPOINT = API_BASE + "/client-logs";
  var SESSION_KEY = "mcp_session";
  var MAX_PER_PAGE = 20;
  var BATCH_DELAY_MS = 1000;
  var BATCH_SIZE = 10;

  var queue = [];
  var seen = {};
  var sent = 0;
  var timer = null;

  function pageUrl() {
    try {
      return (location.origin + location.pathname).slice(0, 500);
    } catch (e) {
      return null;
    }
  }

  function truncate(value, max) {
    if (value === null || value === undefined) return null;
    return String(value).slice(0, max);
  }

  function authHeader() {
    try {
      var session = JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
      if (session && session.accessToken) return "Bearer " + session.accessToken;
    } catch (e) {}
    return null;
  }

  function flush() {
    timer = null;
    if (!queue.length) return;
    var entries = queue.splice(0, BATCH_SIZE);
    var headers = { "Content-Type": "application/json" };
    var auth = authHeader();
    if (auth) headers["Authorization"] = auth;

    try {
      // keepalive: 페이지를 떠나는 중에 발생한 에러도 전송되게 한다.
      fetch(ENDPOINT, {
        method: "POST",
        headers: headers,
        body: JSON.stringify({ entries: entries }),
        keepalive: true,
      }).catch(function () {}); // 리포트 실패는 조용히 버린다(다시 리포트하지 않는다)
    } catch (e) {}

    if (queue.length) schedule();
  }

  function schedule() {
    if (timer) return;
    timer = setTimeout(flush, BATCH_DELAY_MS);
  }

  function enqueue(entry) {
    if (sent >= MAX_PER_PAGE) return;
    var signature = entry.kind + "|" + entry.message + "|" + (entry.source || entry.api_path || "");
    if (seen[signature]) return;
    seen[signature] = true;
    sent++;

    entry.page_url = pageUrl();
    entry.occurred_at = new Date().toISOString();
    queue.push(entry);
    schedule();
  }

  function reportJsError(message, source, lineno, colno, error) {
    enqueue({
      kind: "js_error",
      message: truncate(message || (error && error.message) || "unknown error", 500),
      source: truncate(source ? source + ":" + lineno + ":" + colno : null, 300),
      stack: truncate(error && error.stack, 4000),
    });
  }

  function reportRejection(reason) {
    enqueue({
      kind: "unhandled_rejection",
      message: truncate((reason && reason.message) || reason || "unhandled rejection", 500),
      stack: truncate(reason && reason.stack, 4000),
    });
  }

  /* API 실패 리포트. 모든 실패를 보내지는 않는다 — 정상적인 사용자 오류(로그인 실패 401,
   * 중복 이메일 409 등)는 백엔드가 이미 `http.api_error`로 남기므로 중복이고 노이즈다.
   * 프론트에서만 알 수 있는 것(네트워크 단절)과 명백한 결함(5xx, 요청 형식 오류)만 보낸다. */
  function shouldReportApi(status) {
    if (!status) return true; // 네트워크 실패 — 서버에는 아무 기록도 남지 않는다
    return status >= 500 || status === 400 || status === 422;
  }

  function reportApiError(path, error) {
    if (!error || !shouldReportApi(error.status)) return;
    enqueue({
      kind: "api_error",
      message: truncate(error.message || "api request failed", 500),
      api_path: truncate(path, 300),
      api_status: error.status || null,
      api_error_code: truncate(error.code, 100),
      server_request_id: truncate(error.requestId, 100),
    });
  }

  window.addEventListener("error", function (event) {
    reportJsError(event.message, event.filename, event.lineno, event.colno, event.error);
  });

  window.addEventListener("unhandledrejection", function (event) {
    reportRejection(event.reason);
  });

  // 페이지를 떠나기 전에 남은 큐를 밀어낸다.
  window.addEventListener("pagehide", flush);

  return { reportApiError: reportApiError, reportJsError: reportJsError, flush: flush };
})();
