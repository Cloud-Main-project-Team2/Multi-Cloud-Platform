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

  // 남은 시간 표시: access token 만료(issuedAt + expiresIn)까지 카운트다운.
  // 매 tick마다 세션을 다시 읽으므로, 다른 요청이 401→refresh로 토큰을 갱신하면 값이 리셋된다.
  function formatRemaining(ms) {
    if (ms <= 0) return "만료됨";
    var total = Math.floor(ms / 1000);
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var s = total % 60;
    var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
    return (h > 0 ? h + ":" + pad(m) : m + "") + ":" + pad(s);
  }

  function wireRemaining() {
    document.querySelectorAll(".sidebar__foot").forEach(function (foot) {
      var email = foot.querySelector(".sidebar__email");
      if (!email) return;
      // 자리(높이)는 정적 마크업이 미리 잡아 둔다 — 삽입으로 인한 레이아웃 시프트(메뉴 딸칵거림)를
      // 피하기 위해서다. 마크업에 없으면(구버전 페이지) 생성해 하위 호환한다.
      var value = foot.querySelector(".sidebar__remaining-value");
      if (!value) {
        var box = document.createElement("div");
        box.className = "sidebar__remaining";
        var label = document.createElement("span");
        label.className = "sidebar__remaining-label";
        label.textContent = "세션 남은 시간 ";
        value = document.createElement("span");
        value.className = "sidebar__remaining-value";
        box.appendChild(label);
        box.appendChild(value);
        foot.insertBefore(box, email);
      }

      function tick() {
        var s = window.MCPApi ? window.MCPApi.getSession() : null;
        if (!s || !s.issuedAt || !s.expiresIn) {
          value.textContent = "-";
          return;
        }
        var expiresAt = new Date(s.issuedAt).getTime() + s.expiresIn * 1000;
        value.textContent = formatRemaining(expiresAt - Date.now());
      }
      tick();
      setInterval(tick, 1000);
    });
  }

  function wire() {
    wireRemaining();
    document.querySelectorAll(".sidebar__email").forEach(function (el) {
      el.textContent = (session.user && session.user.email) || "";
    });
    document.querySelectorAll(".sidebar__logout, [data-logout]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        // 서버의 refresh token까지 폐기한 뒤 로그인 화면으로 이동.
        window.MCPApi.logout().then(function () {
          window.location.href = "login.html";
        });
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
