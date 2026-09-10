from app.services import terraform_runner


def test_redact_removes_all_secret_occurrences():
    text = "error: authenticating client_id=abc client_secret=topsecret failed"

    redacted = terraform_runner.redact(text, ["topsecret", "abc"])

    assert "topsecret" not in redacted
    assert "abc" not in redacted
    assert "***REDACTED***" in redacted


def test_redact_ignores_empty_secret_values():
    text = "no secrets in this message"

    assert terraform_runner.redact(text, [""]) == text
