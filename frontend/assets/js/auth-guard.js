/* 데모 인증 가드 (순수 프론트엔드, 서버 통신 없음).
 *
 * 보호 화면(dashboard/inventory/provisioning/mypage)의 <head>에서 로드한다.
 * - localStorage의 데모 세션 플래그(mcp_demo_session)가 없으면 login.html로 리다이렉트.
 * - 있으면 사이드바 이메일 표시를 세션 값으로 교체하고, 로그아웃 버튼을 연결.
 *
 * 주의: 이 플래그는 실제 인증 토큰이 아니라 데모용 표시 값이다.
 * API 연동 단계에서 이 파일의 세션 확인/로그아웃을 실제 인증(JWT 등)으로 교체한다.
 */
(function () {
  "use strict";
  var SESSION_KEY = "mcp_demo_session";

  function getSession() {
    try {
      return JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
    } catch (e) {
      return null;
    }
  }

  var session = getSession();

  // 세션 없음 → 로그인 화면으로. <head>에서 실행되므로 본문 렌더 전에 이동(콘텐츠 노출 방지).
  if (!session || !session.email) {
    window.location.replace("login.html");
    return;
  }

  function wire() {
    // 사이드바 로그인 이메일 표시(정적 예시 name@company.com 대체)
    document.querySelectorAll(".sidebar__email").forEach(function (el) {
      el.textContent = session.email;
    });
    // 로그아웃 버튼 연결: 세션 삭제 후 로그인으로
    document.querySelectorAll(".sidebar__logout, [data-logout]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        try { localStorage.removeItem(SESSION_KEY); } catch (e) {}
        window.location.href = "login.html";
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }

  // 다른 스크립트에서 세션을 참조할 수 있도록 최소 노출
  window.MCPAuth = { session: session, key: SESSION_KEY };
})();
