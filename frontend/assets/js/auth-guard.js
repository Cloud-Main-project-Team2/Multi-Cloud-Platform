/* 인증 가드 — 실제 로그인(POST /auth/login) 세션 기준.
 *
 * 보호 화면(dashboard/inventory/provisioning/mypage)의 <head>에서 api.js 다음에 로드한다.
 * - 세션이 없거나 access token이 만료됐으면 login.html로 리다이렉트(본문 렌더 전).
 * - 있으면 사이드바 이메일 표시를 세션 값으로 교체하고, 로그아웃 버튼을 연결.
 *
 * 이전에는 localStorage 데모 플래그(mcp_demo_session)만 봤지만, 이제 실제 JWT 세션
 * (MCPApi, assets/js/api.js)을 기준으로 한다. 회원가입/비밀번호 재설정/refresh token 등
 * 나머지 인증 기능은 여전히 범위 밖이다 — 만료되면 재로그인해야 한다.
 */
(function () {
  "use strict";

  var session = window.MCPApi ? window.MCPApi.getSession() : null;

  if (!window.MCPApi || !window.MCPApi.isSessionValid(session)) {
    if (window.MCPApi) window.MCPApi.clearSession();
    window.location.replace("login.html");
    return;
  }

  function wire() {
    document.querySelectorAll(".sidebar__email").forEach(function (el) {
      el.textContent = (session.user && session.user.email) || "";
    });
    document.querySelectorAll(".sidebar__logout, [data-logout]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        window.MCPApi.clearSession();
        window.location.href = "login.html";
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }

  // 다른 스크립트(mypage.js 등)에서 세션/유저 정보를 참조할 수 있도록 최소 노출.
  window.MCPAuth = { session: session, user: session.user };
})();
