/* 백엔드 API 공용 클라이언트 (fetch 래퍼) + 세션 저장.
 *
 * API 서버는 다른 오리진(:8000)에서 떠 있다 — API_BASE 한 곳만 바꾸면 배포 시 전체 반영된다.
 * 세션은 실제 로그인(POST /auth/login)이 반환한 JWT를 저장한다(더 이상 데모 플래그가 아님).
 * 로그인/회원가입/비밀번호 재설정 등 나머지 인증 기능은 이번 범위가 아니다 — 로그인 API만 붙인다.
 */
window.MCPApi = (function () {
  "use strict";

  var API_BASE = "http://localhost:8000/api/v1";
  var SESSION_KEY = "mcp_session";

  function getSession() {
    try {
      return JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
    } catch (e) {
      return null;
    }
  }

  function setSession(session) {
    try {
      localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    } catch (e) {}
  }

  function clearSession() {
    try {
      localStorage.removeItem(SESSION_KEY);
    } catch (e) {}
  }

  function isSessionValid(session) {
    session = session || getSession();
    if (!session || !session.accessToken || !session.issuedAt || !session.expiresIn) return false;
    var expiresAt = new Date(session.issuedAt).getTime() + session.expiresIn * 1000;
    return Date.now() < expiresAt;
  }

  // 로그인 응답(data)을 세션 형태로 저장. issuedAt은 클라이언트 시각 기준(만료 판단용).
  function saveLoginResponse(data) {
    setSession({
      accessToken: data.access_token,
      tokenType: data.token_type,
      expiresIn: data.expires_in,
      issuedAt: new Date().toISOString(),
      user: data.user,
    });
  }

  function request(path, options) {
    options = options || {};
    var headers = {};
    for (var k in options.headers || {}) headers[k] = options.headers[k];

    var session = getSession();
    if (session && session.accessToken) headers["Authorization"] = "Bearer " + session.accessToken;

    var body = options.body;
    if (body !== undefined && typeof body !== "string") {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }

    return fetch(API_BASE + path, {
      method: options.method || "GET",
      headers: headers,
      body: body,
    }).then(function (res) {
      if (res.status === 204) return null;
      return res
        .json()
        .catch(function () {
          return null;
        })
        .then(function (json) {
          if (!res.ok) {
            var err = (json && json.error) || {
              code: "UNKNOWN_ERROR",
              message: "요청 처리 중 오류가 발생했습니다.",
            };
            var e = new Error(err.message);
            e.code = err.code;
            e.details = err.details;
            e.status = res.status;
            throw e;
          }
          return json ? json.data : null;
        });
    });
  }

  return {
    API_BASE: API_BASE,
    request: request,
    getSession: getSession,
    setSession: setSession,
    clearSession: clearSession,
    isSessionValid: isSessionValid,
    saveLoginResponse: saveLoginResponse,
  };
})();
