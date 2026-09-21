"""보고서 정기 메일 본문(텍스트 + HTML) 생성(2026-09-19).

비용은 `app/cost/query.py`(**DB만 읽는다**, ADR-040과 동일 원칙 — CSP를 직접 호출하지 않는다)로,
리소스 사용률은 `app/metrics.py`(실시간 CSP 조회, 기존과 동일)로 만든다. **AI 분석 요약·미사용
리소스·인수인계는 아직 실 API가 없다**(reports-data.js의 BASE 목업과 같은 사정) — 이 파일도
같은 원칙으로 하드코딩된 예시를 쓰고, 메일 안에서도 "샘플"/"예시"로 명시한다. 나중에 그 세
섹션이 실 API로 바뀌면 `_SAMPLE_*` 상수 자리만 실 조회로 바꾸면 된다.

**차트 범위(2026-09-19 결정)**: report-view.js와 완전히 같은 "기간별 비용 추이"(다주 막대
그래프)는 넣지 않았다 — `cost/query.py`의 `trend()`가 날짜 키로 흩어진 시계열을 주는데, 이걸
메일에서 안전하게 주 단위로 묶는 로직은 검증 없이 넣기엔 버그 위험이 커서(이번 세션에 이미
같은 이유로 여러 번 겪음) 범위를 줄였다. 대신 **더 단순하고 안전한 두 가지**를 실 데이터로
넣는다 — ① provider별 비용 비교(표 기반 막대, HTML/CSS만 — 이메일 클라이언트 호환성이 SVG보다
좋다), ② 서비스 카테고리별 비중 도넛(SVG, report-view.js의 `buildDonut()`과 동일한 원 둘레
공식). 두 개 다 실제 비용 데이터 기반이다.
"""

from __future__ import annotations

import html
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.config import get_settings
from app.cost.query import CostQuery, breakdown, default_period, summary
from app.metrics import get_top_utilization
from app.models import User

_PROVIDER_LABEL = {"aws": "AWS", "azure": "Azure", "gcp": "GCP"}
# report-view.html의 인쇄용 팔레트(--aws/--azure/--gcp)와 동일 — 화면과 시각적 일관성을 맞춘다.
_PROVIDER_COLOR = {"aws": "#c2680d", "azure": "#1a5fb4", "gcp": "#1a7f4f"}
_CATEGORY_COLOR = ["#3d6fa8", "#5c94c9", "#8fb9dd", "#b9d4e9", "#d9e5ef", "#c9cfd6"]

# AI 분석 요약·미사용 리소스·인수인계는 아직 실 API가 없다 — reports.html 웹 화면과 동일한
# 원칙으로 "샘플"/"예시"임을 항상 명시한다.
_SAMPLE_UNUSED = [
    {"provider": "aws", "name": "dev-test-04(예시)", "idle_days": 18, "cost": "34.00"},
    {"provider": "azure", "name": "stg-vm-02(예시)", "idle_days": 16, "cost": "41.00"},
]
_SAMPLE_HANDOVER = [
    {"type": "예시", "title": "사용률 90% 이상 리소스 스케일업 검토", "body": "실제 인수인계 입력 기능이 준비되면 이 자리에 표시됩니다."},
]


def _esc(s: object) -> str:
    return html.escape(str(s))


def _fmt_amount(raw: str | None) -> str:
    """`money()`(cost/query.py)는 정밀도를 그대로 보존해 돌려준다 — 메일에서는 2자리로 자른다
    (2026-09-19, "2.788920"처럼 그대로 나가던 버그 수정과 동일 이유)."""
    if raw is None:
        return "0.00"
    try:
        return f"{Decimal(raw):,.2f}"
    except InvalidOperation:
        return raw


def _provider_totals(cost: dict) -> tuple[dict[str, Decimal], str]:
    """summary()의 계정별 실측 비용을 provider 단위로 합산한다(수치 카드·비교 막대용)."""
    totals: dict[str, Decimal] = {}
    currency = "USD"
    for acc in cost["accounts"]:
        if acc["actual"] is None:
            continue
        try:
            amount = Decimal(acc["actual"])
        except InvalidOperation:
            continue
        totals[acc["provider"]] = totals.get(acc["provider"], Decimal("0")) + amount
        if acc["currency"]:
            currency = acc["currency"]
    return totals, currency


def _provider_bars_html(totals: dict[str, Decimal], currency: str) -> str:
    if not totals:
        return '<p style="color:#8b929c;font-size:13px;margin:0;">아직 수집된 비용 데이터가 없습니다.</p>'
    max_amount = max(totals.values()) or Decimal("1")
    rows = []
    for provider in ("aws", "azure", "gcp"):
        if provider not in totals:
            continue
        amount = totals[provider]
        pct = float(amount / max_amount * 100) if max_amount else 0.0
        color = _PROVIDER_COLOR[provider]
        rows.append(
            '<tr>'
            f'<td style="padding:4px 10px 4px 0;font-size:13px;font-weight:600;color:{color};white-space:nowrap;">{_PROVIDER_LABEL[provider]}</td>'
            '<td style="padding:4px 10px;width:60%;">'
            '<div style="background:#eef0f3;border-radius:3px;height:10px;overflow:hidden;">'
            f'<div style="background:{color};height:10px;width:{pct:.1f}%;"></div>'
            '</div></td>'
            f'<td style="padding:4px 0 4px 10px;font-size:13px;text-align:right;white-space:nowrap;">{_esc(currency)} {_fmt_amount(str(amount))}</td>'
            '</tr>'
        )
    return f'<table role="presentation" style="width:100%;border-collapse:collapse;">{"".join(rows)}</table>'


def _donut_svg(items: list[dict]) -> str:
    """report-view.js의 buildDonut()과 동일한 원 둘레 공식(R=58, stroke-dasharray 누적)을
    그대로 옮겼다 — 웹 화면과 같은 모양이 나온다."""
    slices = [it for it in items if it.get("share_pct") and float(it["share_pct"]) > 0]
    if not slices:
        return ""
    radius = 58
    circumference = 2 * 3.14159265 * radius
    offset = 0.0
    circles = []
    for i, item in enumerate(slices):
        share = float(item["share_pct"])
        length = (share / 100) * circumference
        color = _CATEGORY_COLOR[i % len(_CATEGORY_COLOR)]
        circles.append(
            f'<circle r="{radius}" cx="0" cy="0" fill="none" stroke="{color}" stroke-width="26" '
            f'stroke-dasharray="{length:.1f} {circumference - length:.1f}" '
            f'stroke-dashoffset="-{offset:.1f}" transform="rotate(-90)"/>'
        )
        offset += length
    svg = (
        '<svg width="150" height="150" viewBox="-75 -75 150 150" xmlns="http://www.w3.org/2000/svg">'
        + "".join(circles) + "</svg>"
    )
    legend = "".join(
        '<span style="display:inline-block;margin:2px 10px 2px 0;font-size:12px;color:#5c6470;">'
        f'<span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:{_CATEGORY_COLOR[i % len(_CATEGORY_COLOR)]};margin-right:5px;"></span>'
        f'{_esc(item["label"])} {item["share_pct"]}%</span>'
        for i, item in enumerate(slices)
    )
    return f'<div>{svg}</div><div style="margin-top:6px;">{legend}</div>'


def _utilization_rows_html(utilization: list[dict]) -> str:
    if not utilization:
        return '<p style="color:#8b929c;font-size:13px;margin:0;">컴퓨트 리소스가 없습니다.</p>'
    rows = []
    for r in utilization:
        cpu = r["cpu_percent"]
        color = _PROVIDER_COLOR.get(r["provider"], "#5c6470")
        if cpu is None:
            bar = '<span style="font-size:12px;color:#8b929c;">조회 실패</span>'
        else:
            bar_color = "#b3261e" if cpu >= 90 else "#8a5a00" if cpu >= 70 else "#8b929c"
            bar = (
                '<div style="display:flex;align-items:center;gap:8px;">'
                '<div style="flex:1;background:#eef0f3;border-radius:3px;height:6px;overflow:hidden;min-width:60px;">'
                f'<div style="background:{bar_color};height:6px;width:{min(cpu, 100):.0f}%;"></div></div>'
                f'<span style="font-size:12px;white-space:nowrap;">{cpu}%</span></div>'
            )
        rows.append(
            '<tr>'
            f'<td style="padding:5px 8px 5px 0;font-size:12px;font-weight:600;color:{color};white-space:nowrap;">{_PROVIDER_LABEL.get(r["provider"], r["provider"].upper())}</td>'
            f'<td style="padding:5px 8px;font-size:13px;">{_esc(r["name"])}</td>'
            f'<td style="padding:5px 0 5px 8px;width:140px;">{bar}</td>'
            '</tr>'
        )
    return f'<table role="presentation" style="width:100%;border-collapse:collapse;">{"".join(rows)}</table>'


def _sample_unused_html() -> str:
    rows = "".join(
        '<tr>'
        f'<td style="padding:4px 8px 4px 0;font-size:12px;font-weight:600;color:{_PROVIDER_COLOR.get(u["provider"], "#5c6470")};">{_PROVIDER_LABEL.get(u["provider"], u["provider"].upper())}</td>'
        f'<td style="padding:4px 8px;font-size:13px;">{_esc(u["name"])}</td>'
        f'<td style="padding:4px 8px;font-size:12px;color:#8b929c;">{u["idle_days"]}일 유휴</td>'
        f'<td style="padding:4px 0 4px 8px;font-size:13px;text-align:right;">${u["cost"]}</td>'
        '</tr>'
        for u in _SAMPLE_UNUSED
    )
    return f'<table role="presentation" style="width:100%;border-collapse:collapse;">{rows}</table>'


def _sample_handover_html() -> str:
    return "".join(
        f'<p style="margin:0 0 8px;font-size:13px;"><b>[{_esc(h["type"])}]</b> {_esc(h["title"])}<br>'
        f'<span style="color:#5c6470;">{_esc(h["body"])}</span></p>'
        for h in _SAMPLE_HANDOVER
    )


def _section(title: str, badge: str | None, body_html: str) -> str:
    badge_html = (
        f'<span style="font-size:11px;font-weight:500;padding:2px 8px;border-radius:3px;background:#f5eefb;color:#7a3ea3;margin-left:8px;">{_esc(badge)}</span>'
        if badge else ""
    )
    return (
        '<tr><td style="padding:22px 0 0;">'
        f'<h2 style="font-size:15px;font-weight:600;margin:0 0 12px;padding-bottom:7px;border-bottom:1px solid #e2e5ea;">{_esc(title)}{badge_html}</h2>'
        f'{body_html}'
        '</td></tr>'
    )


def build_report_email(db: Session, user: User) -> tuple[str, str]:
    """(평문, HTML) 본문을 함께 만든다 — 비용·사용률 데이터를 한 번만 조회해 둘 다에 쓴다
    (사용률은 실시간 CSP 호출이라 두 번 부르면 낭비다)."""
    period_start, period_end = default_period()
    q = CostQuery(period_start=period_start, period_end=period_end)
    cost = summary(db, user.id, q)
    category_breakdown = breakdown(db, user.id, q, dimension="category", top_n=5, currency_param=None)
    utilization = get_top_utilization(db, user, limit=5)
    provider_totals, currency = _provider_totals(cost)

    text = _build_text(user, cost, utilization, provider_totals, currency)
    html_body = _build_html(user, cost, utilization, provider_totals, currency, category_breakdown)
    return text, html_body


def _build_text(user: User, cost: dict, utilization: list[dict], provider_totals: dict, currency: str) -> str:
    lines = [f"{user.name}님, MultiCloud Ops 정기 보고서입니다.", ""]
    lines.append(f"[이번 달 비용 — {cost['period']['display']}]")
    if provider_totals:
        for provider, amount in provider_totals.items():
            lines.append(f"- {_PROVIDER_LABEL.get(provider, provider.upper())}: {currency} {_fmt_amount(str(amount))}")
    else:
        lines.append("- 아직 수집된 비용 데이터가 없습니다.")
    lines.append("")

    lines.append("[리소스 사용률 상위]")
    if utilization:
        for r in utilization:
            cpu = f"{r['cpu_percent']}%" if r["cpu_percent"] is not None else "조회 실패"
            lines.append(f"- [{r['provider'].upper()}] {r['name']} — CPU {cpu}")
    else:
        lines.append("- 컴퓨트 리소스가 없습니다.")
    lines.append("")

    lines.append("[미사용 리소스 — 샘플]")
    for u in _SAMPLE_UNUSED:
        lines.append(f"- [{u['provider'].upper()}] {u['name']} — {u['idle_days']}일 유휴, ${u['cost']}")
    lines.append("")

    lines.append("[인수인계 — 예시]")
    for h in _SAMPLE_HANDOVER:
        lines.append(f"- [{h['type']}] {h['title']}: {h['body']}")
    lines.append("")

    lines.append(
        "AI 분석 요약 섹션은 아직 준비 중입니다. 전체 보고서는 웹에서 확인하세요: "
        + get_settings().frontend_base_url + "/reports.html"
    )
    return "\n".join(lines)


def _build_html(
    user: User, cost: dict, utilization: list[dict], provider_totals: dict, currency: str, category_breakdown: dict
) -> str:
    reports_url = get_settings().frontend_base_url + "/reports.html"
    body = "".join([
        _section(
            f"비용 요약 — {_esc(cost['period']['display'])}", None,
            _provider_bars_html(provider_totals, currency),
        ),
        _section("서비스 카테고리별 비용 비중", None, _donut_svg(category_breakdown.get("items", []))),
        _section("리소스 사용률 상위", None, _utilization_rows_html(utilization)),
        _section("AI 분석 요약", "샘플 데이터", '<p style="margin:0;font-size:13px;color:#5c6470;">AI 기반 자동 분석 요약은 아직 준비 중입니다.</p>'),
        _section("미사용 리소스", "샘플 데이터", _sample_unused_html()),
        _section("인수인계", "예시 항목", _sample_handover_html()),
    ])
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f6f7f9;font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;color:#1a1d21;">
<table role="presentation" width="100%" style="background:#f6f7f9;padding:24px 0;">
<tr><td align="center">
<table role="presentation" width="600" style="background:#ffffff;border:1px solid #e2e5ea;padding:32px;">
<tr><td>
  <h1 style="font-size:20px;margin:0 0 4px;">MultiCloud Ops 정기 보고서</h1>
  <p style="font-size:13px;color:#5c6470;margin:0;">{_esc(user.name)}님</p>
</td></tr>
{body}
<tr><td style="padding-top:24px;border-top:1px solid #e2e5ea;margin-top:22px;">
  <p style="font-size:12px;color:#8b929c;">전체 보고서(차트·상세 내역)는 웹에서 확인하세요:
  <a href="{_esc(reports_url)}" style="color:#145d91;">{_esc(reports_url)}</a></p>
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""
