/* 공유 클라이언트 검증 헬퍼 (순수 프론트엔드, 서버 통신 없음).
   login/password-reset/signup 화면 전용 스크립트에서 재사용. */
window.MCVAL = {
  // 이메일 형식
  isEmail: function (v) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test((v || "").trim());
  },
  // 비어있지 않은지
  isNonEmpty: function (v) {
    return (v || "").trim().length > 0;
  },
  // 비밀번호 규칙: 8자 이상 + 영문·숫자·기호 중 2종 이상
  isStrongPassword: function (v) {
    v = v || "";
    if (v.length < 8) return false;
    var types = 0;
    if (/[A-Za-z]/.test(v)) types++;
    if (/[0-9]/.test(v)) types++;
    if (/[^A-Za-z0-9]/.test(v)) types++;
    return types >= 2;
  },
  // 버튼 활성/비활성 + 눌림 방지 스타일 토글
  setEnabled: function (btn, ok) {
    if (!btn) return;
    btn.disabled = !ok;
    btn.classList.toggle("opacity-50", !ok);
    btn.classList.toggle("cursor-not-allowed", !ok);
  }
};
