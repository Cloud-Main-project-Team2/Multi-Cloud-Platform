"""app/report_email.py 단위 테스트(2026-09-19) — 보고서 메일 본문(텍스트+HTML) 생성.

실제 CSP 호출 없이(사용자에게 리소스가 없는 경우) 기본 동작을 검증한다. 차트가 실제 데이터로
그려지는지는 `_provider_bars_html`/`_donut_svg` 단위로 따로 확인한다.
"""

from __future__ import annotations

from decimal import Decimal

import app.report_email as report_email


def test_fmt_amount_rounds_to_two_decimals():
    """`money()`(cost/query.py)가 정밀도를 그대로 보존해 돌려주는 값("2.788920")을 그대로
    이메일에 박아 넣던 버그(2026-09-19, 실사용 중 발견) — 2자리로 잘라야 한다."""
    assert report_email._fmt_amount("2.788920") == "2.79"
    assert report_email._fmt_amount("1158") == "1,158.00"
    assert report_email._fmt_amount(None) == "0.00"
    assert report_email._fmt_amount("not-a-number") == "not-a-number"


def test_build_report_email_handles_no_data(db_session, make_user):
    user = make_user()
    text, html_body = report_email.build_report_email(db_session, user)

    assert user.name in text
    assert "아직 수집된 비용 데이터가 없습니다." in text
    assert "컴퓨트 리소스가 없습니다." in text
    assert "reports.html" in text

    assert user.name in html_body
    assert "<html>" in html_body
    assert "샘플 데이터" in html_body  # AI 분석 요약/미사용 리소스는 항상 샘플 라벨이 붙어야 한다
    assert "예시 항목" in html_body


def test_provider_bars_html_renders_each_provider_with_color():
    totals = {"aws": Decimal("500"), "azure": Decimal("250")}
    out = report_email._provider_bars_html(totals, "USD")
    assert "AWS" in out and "Azure" in out
    assert "500.00" in out and "250.00" in out
    assert report_email._PROVIDER_COLOR["aws"] in out


def test_provider_bars_html_empty_when_no_cost_data():
    out = report_email._provider_bars_html({}, "USD")
    assert "아직 수집된 비용 데이터가 없습니다." in out


def test_donut_svg_renders_circles_for_positive_shares():
    items = [
        {"label": "Compute", "amount": "100", "share_pct": "60.0"},
        {"label": "Storage", "amount": "40", "share_pct": "0.0"},  # 0%는 그리지 않는다
    ]
    out = report_email._donut_svg(items)
    assert out.count("<circle") == 1
    assert "Compute" in out


def test_donut_svg_empty_when_no_slices():
    assert report_email._donut_svg([]) == ""
    assert report_email._donut_svg([{"label": "x", "share_pct": "0.0"}]) == ""
