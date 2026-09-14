/* 백엔드 API 공용 클라이언트 (fetch 래퍼) + 세션 저장.
 *
 * API 서버는 다른 오리진(:8000)에서 떠 있다 — API_BASE 한 곳만 바꾸면 배포 시 전체 반영된다.
 * 세션은 실제 로그인(POST /auth/login)이 반환한 access token + refresh token(JWT/opaque)을 저장한다.
 * access token이 만료돼 401이 나면 refresh token으로 자동 재발급 후 원 요청을 1회 재시도한다.
 */
window.MCPApi = (function () {
  "use strict";

  var API_BASE = "http://localhost:8000/api/v1";
  var SESSION_KEY = "mcp_session";
  // refresh 엔드포인트는 401 자동 재시도 대상에서 제외한다(무한 루프 방지).
  var NO_REFRESH_PATHS = ["/auth/login", "/auth/refresh", "/auth/sign-up"];
  var refreshPromise = null; // 동시 401을 하나의 refresh로 합친다.

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

  // 로그인/refresh 응답(data)을 세션 형태로 저장. issuedAt은 클라이언트 시각 기준(만료 판단용).
  function saveLoginResponse(data) {
    setSession({
      accessToken: data.access_token,
      tokenType: data.token_type,
      expiresIn: data.expires_in,
      refreshToken: data.refresh_token,
      refreshExpiresIn: data.refresh_expires_in,
      issuedAt: new Date().toISOString(),
      user: data.user,
    });
  }

  function isAuthPath(path) {
    for (var i = 0; i < NO_REFRESH_PATHS.length; i++) {
      if (path.indexOf(NO_REFRESH_PATHS[i]) === 0) return true;
    }
    return false;
  }

  // refresh token으로 새 세션을 발급받는다. 동시 호출은 하나의 요청으로 합친다.
  function refreshSession() {
    if (refreshPromise) return refreshPromise;
    var session = getSession();
    if (!session || !session.refreshToken) return Promise.reject(new Error("no refresh token"));

    refreshPromise = fetch(API_BASE + "/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: session.refreshToken }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("refresh failed");
        return res.json();
      })
      .then(function (json) {
        saveLoginResponse(json.data);
        return json.data;
      })
      .finally(function () {
        refreshPromise = null;
      });
    return refreshPromise;
  }

  // 실제 fetch 1회. Authorization 헤더를 붙이고 표준 error envelope를 Error로 변환한다.
  function rawRequest(path, options) {
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
      if (res.status === 204) return { _status: 204, data: null };
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

  function request(path, options) {
    options = options || {};
    return rawRequest(path, options).catch(function (e) {
      // access token 만료(401)면 refresh 후 1회만 재시도한다.
      if (e.status === 401 && !options._retried && !isAuthPath(path)) {
        var session = getSession();
        if (session && session.refreshToken) {
          return refreshSession()
            .then(function () {
              var retryOpts = {};
              for (var k in options) retryOpts[k] = options[k];
              retryOpts._retried = true;
              return rawRequest(path, retryOpts);
            })
            .catch(function () {
              clearSession();
              throw e;
            });
        }
      }
      throw e;
    });
  }

  // 서버 세션(refresh token)까지 폐기하고 로컬 세션을 지운다.
  function logout() {
    var session = getSession();
    var done = session && session.refreshToken
      ? fetch(API_BASE + "/auth/logout", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: session.refreshToken }),
        }).catch(function () {})
      : Promise.resolve();
    return done.then(function () {
      clearSession();
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
    refreshSession: refreshSession,
    logout: logout,
  };
})();
