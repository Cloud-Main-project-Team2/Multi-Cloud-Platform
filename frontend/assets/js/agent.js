/* AI 비용 어시스턴트 — POST /agent/chat 채팅 UI.
 *
 * `docs/design/확장기능/agent1.png`/`agent2.png` 목업의 "플랫폼 비용을 비교하고, 절감 방법을
 * 찾아보세요" 채팅 인터페이스를 실제로 동작하게 만든다. 대화 기록은 서버에 저장하지 않는다
 * (무상태 API) — 브라우저 메모리에만 두고, 매 요청마다 지금까지의 history를 그대로 같이 보낸다.
 * 페이지를 새로고침하면 대화가 초기화된다(이번 범위에서는 그 정도로 충분하다고 판단).
 *
 * 비용 데이터 자체는 app/pricing.py의 정가 기반 추정치뿐이라, 답변에도 그 한계가 그대로 반영된다
 * (백엔드 시스템 프롬프트가 강제함) — 이 파일은 UI만 맡는다.
 */
(function () {
  "use strict";

  var SUGGESTED_PROMPTS = [
    "비용을 줄일 수 있는 서비스를 추천해줘",
    "플랫폼별 비용을 비교해줘",
    "약정 없이 절약할 방법이 있을까?",
  ];

  // 에러 문구는 MCErr(error-explain.js)가 담당한다. agent 전용 코드(AGENT_*)는 백엔드
  // error_catalog 범위 밖이라 MCErr의 로컬 폴백에서 관리한다.

  function escHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // 서버는 대화 기록을 안 들고 있으니, 요청마다 여기 쌓아둔 걸 그대로 같이 보낸다.
  var history = [];
  var sending = false;

  function init() {
    var modal = document.getElementById("agent-chat-modal");
    var messagesEl = document.getElementById("agent-chat-messages");
    var form = document.getElementById("agent-chat-form");
    var input = document.getElementById("agent-chat-input");
    if (!modal || !messagesEl || !form || !input || !window.MCPApi) return;

    function scrollToBottom() {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function renderIntro() {
      messagesEl.innerHTML =
        '<div class="rounded-xl bg-muted p-3 text-muted-foreground">어떤 비용 고민이 있으신가요? 서비스를 비교하고, 절감 기회를 찾아보세요.</div>' +
        '<div class="space-y-2" id="agent-suggested-prompts">' +
        SUGGESTED_PROMPTS.map(function (p) {
          return (
            '<button type="button" data-agent-prompt="' + escHtml(p) + '" ' +
            'class="block w-full rounded-lg border border-border bg-background px-3 py-2 text-left text-sm hover:bg-muted">' +
            escHtml(p) + "</button>"
          );
        }).join("") +
        "</div>";
    }

    function addBubble(role, text) {
      var wrap = document.createElement("div");
      if (role === "user") {
        wrap.className = "ml-auto max-w-[85%] rounded-xl bg-primary px-3 py-2 text-white";
      } else if (role === "error") {
        wrap.className = "max-w-[85%] rounded-xl px-3 py-2 text-white";
        wrap.style.background = "#c0392b";
      } else {
        wrap.className = "max-w-[85%] rounded-xl bg-muted px-3 py-2";
      }
      wrap.style.whiteSpace = "pre-wrap";
      wrap.textContent = text;
      messagesEl.appendChild(wrap);
      scrollToBottom();
    }

    function addTypingIndicator() {
      var el = document.createElement("div");
      el.id = "agent-typing";
      el.className = "max-w-[85%] rounded-xl bg-muted px-3 py-2 text-muted-foreground";
      el.textContent = "생각하는 중…";
      messagesEl.appendChild(el);
      scrollToBottom();
      return el;
    }

    function send(message) {
      if (sending || !message.trim()) return;
      sending = true;
      input.value = "";

      // 첫 메시지 전송 시 추천 질문 안내는 지운다(이미 대화가 시작됐으니).
      var prompts = document.getElementById("agent-suggested-prompts");
      if (prompts) prompts.remove();

      addBubble("user", message);
      var typing = addTypingIndicator();

      MCPApi.request("/agent/chat", {
        method: "POST",
        body: { message: message, history: history },
      })
        .then(function (data) {
          typing.remove();
          var reply = (data && data.reply) || "";
          addBubble("assistant", reply);
          history.push({ role: "user", content: message });
          history.push({ role: "assistant", content: reply });
        })
        .catch(function (err) {
          typing.remove();
          addBubble("error", MCErr.headline(err));
        })
        .then(function () {
          sending = false;
        });
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      send(input.value);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send(input.value);
      }
    });
    messagesEl.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-agent-prompt]");
      if (btn) send(btn.getAttribute("data-agent-prompt"));
    });

    // 모달을 열 때마다가 아니라 처음 한 번만 인트로를 그린다(대화 중 다시 열어도 이어지게).
    var introRendered = false;
    document.querySelectorAll('[data-modal-open="#agent-chat-modal"]').forEach(function (opener) {
      opener.addEventListener("click", function () {
        if (!introRendered) {
          renderIntro();
          introRendered = true;
        }
      });
    });

    // "새 세션" — 새로고침 없이 대화 상태(history·DOM)를 초기화하고 인트로를 다시 그린다.
    var newSessionBtn = document.getElementById("agent-new-session");
    if (newSessionBtn) {
      newSessionBtn.addEventListener("click", function () {
        history = [];
        sending = false;
        input.value = "";
        renderIntro();
        introRendered = true;
        input.focus();
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
