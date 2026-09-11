from app.services import terraform_runner


def test_redact_removes_all_secret_occurrences():
    text = "error: authenticating client_id=abcd1234 client_secret=topsecret1 failed"

    redacted = terraform_runner.redact(text, ["topsecret1", "abcd1234"])

    assert "topsecret1" not in redacted
    assert "abcd1234" not in redacted
    assert "***REDACTED***" in redacted


def test_redact_ignores_empty_secret_values():
    text = "no secrets in this message"

    assert terraform_runner.redact(text, [""]) == text


def test_redact_ignores_too_short_values_to_avoid_mangling_message():
    # 실제로 발견된 문제: tenant_id="t" 같은 한 글자 값을 redact 대상에 넣으면 "Trace ID" 같은
    # 흔한 단어의 "T"까지 지워져 메시지 전체가 알아볼 수 없게 뭉개진다.
    text = "AADSTS900023: Trace ID: abc123"

    redacted = terraform_runner.redact(text, ["t", "c"])

    assert redacted == text
