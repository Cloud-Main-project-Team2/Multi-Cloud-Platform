"""SMTP 메일 발송 (표준 라이브러리 smtplib).

이 프로젝트는 원래 "실제 메일 발송 없음(데모형 토큰)"을 전제로 했지만, 이메일 검증(OTP)과
비밀번호 재설정(링크)이 실제 메일함 소유를 확인해야 하므로 실발송을 도입한다.

로컬/데모 환경에서는 docker-compose의 MailHog(host=mailhog, port=1025)가 나가는 모든 메일을
가로채 :8025 웹 UI에서 볼 수 있게 해준다 — 외부 계정·실제 수신함 없이도 "진짜 발송" 경로가
동작한다. 운영은 MAIL_* env만 실제 SMTP(SES/SendGrid/Gmail 등)로 바꾸면 코드 변경 없이 전환된다.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.config import get_settings
from app.logging_config import log_business_event


def send_email(to: str, subject: str, body: str) -> None:
    """평문 메일 1통 발송. 실패하면 예외를 그대로 올린다(호출부가 처리).

    MailHog(로컬)는 인증·TLS가 없으므로 MAIL_USERNAME이 비어 있으면 로그인 단계를 건너뛴다.
    """
    settings = get_settings()

    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    # 이 함수는 대부분 BackgroundTasks로 호출된다 — 응답은 이미 나간 뒤라 실패해도 사용자에게
    # 보이지 않는다. 로그에 남기지 않으면 "인증 메일이 안 온다"를 서버에서 확인할 방법이 없다.
    try:
        with smtplib.SMTP(settings.mail_host, settings.mail_port, timeout=10) as smtp:
            if settings.mail_use_tls:
                smtp.starttls()
            if settings.mail_username:
                smtp.login(settings.mail_username, settings.mail_password)
            smtp.send_message(message)
    except Exception as exc:
        log_business_event(
            "mail.failed", level="ERROR", exc_info=True,
            to=to, subject=subject, host=settings.mail_host, error_type=type(exc).__name__,
        )
        raise

    log_business_event("mail.sent", to=to, subject=subject)
