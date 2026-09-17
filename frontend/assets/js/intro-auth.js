/* 문서(소개) 페이지 헤더의 로그인 상태 반영.
 *
 * intro-*.html은 보호 화면이 아니라 로그인/비로그인 모두 접근 가능한 소개 페이지다.
 * 토큰(localStorage.mcp_session)은 여기서 지워지지 않는다 — 헤더가 항상 '로그인 전' UI로만
 * 렌더돼서 "로그인이 풀린 것처럼" 보였을 뿐이다. main.html과 동일한 data-auth 규약으로
 * 세션에 맞춰 헤더를 토글한다(로그인 시 로그인/회원가입 → 이름 + 대시보드).
 */
(function () {
  "use strict";
  var api = window.MCPApi;
  var session = api ? api.getSession() : null;
  var loggedIn = !!(api && api.isSessionValid(session));

  document.querySelectorAll('[data-auth="guest"]').forEach(function (el) {
    el.classList.toggle("hidden", loggedIn);
    el.classList.toggle("contents", !loggedIn);
  });
  document.querySelectorAll('[data-auth="user"]').forEach(function (el) {
    el.classList.toggle("hidden", !loggedIn);
    el.classList.toggle("flex", loggedIn);
  });

  if (loggedIn) {
    var name = (session.user && (session.user.name || session.user.email)) || "사용자";
    document.querySelectorAll("[data-user-name]").forEach(function (el) {
      el.textContent = name + "님";
    });
  }
})();
