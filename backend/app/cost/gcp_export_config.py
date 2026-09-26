"""GCP 청구 Export 테이블 설정만 다루는 조각. **google 라이브러리를 import하지 않는다.**

`app/cost/capability.py`는 "CSP를 호출하지 않는다"가 규칙이라 `gcp_cost.py`(bigquery import)를
끌어올 수 없다. 그래서 설정 해석만 여기로 떼어 둘 다 같은 규칙을 쓰게 한다.

설정 형식: `COST_GCP_EXPORT_TABLES="<project_id>:<프로젝트.데이터셋.테이블>[, ...]"`
- 키는 우리 DB의 계정 id가 아니라 **GCP 프로젝트 id**다(어댑터가 받는 external_account_id).
- 없는 계정은 수집 대상이 아니다 — 테이블을 **추측하지 않는다**(남의 청구 데이터를 읽는 사고 방지).
"""

from __future__ import annotations

from app.config import get_settings

# 테이블 참조는 SQL에 문자열로 들어가는 자리다(파라미터로 넣을 수 없다) — 식별자에 쓸 수 있는
# 문자와 세 토막 형식만 허용하고, 그 외에는 아예 쿼리를 만들지 않는다.
_TABLE_PART_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")

SETUP_HINT = (
    "GCP 비용은 BigQuery 청구 Export에서 읽습니다. 결제 계정에서 Export를 켜고(표준 사용량), "
    "서비스 계정에 BigQuery 조회 권한을 준 뒤, 내보낸 테이블을 등록해 주세요."
)


def is_valid_table_ref(table: str) -> bool:
    parts = (table or "").split(".")
    if len(parts) != 3:
        return False
    return all(part and set(part) <= _TABLE_PART_CHARS for part in parts)


def parse_export_tables(raw: str) -> dict[str, str]:
    """형식이 틀린 항목은 **버린다** — 넓히지 않는다(잘못 읽는 쪽이 더 나쁘다)."""
    out: dict[str, str] = {}
    for chunk in (raw or "").replace("\n", ",").split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        account, _, table = chunk.partition(":")
        account, table = account.strip(), table.strip()
        if account and is_valid_table_ref(table):
            out[account] = table
    return out


def export_table_for(external_account_id: str) -> str | None:
    return parse_export_tables(get_settings().cost_gcp_export_tables).get(external_account_id)


def missing_setup_hint(provider: str, external_account_id: str) -> str | None:
    """이 계정이 **수집 전에 사람 손이 필요한 상태**인가. CSP를 호출하지 않고 설정만 본다.

    GCP만 해당한다 — AWS·Azure는 자격증명만 있으면 바로 조회할 수 있다.
    """
    if provider != "gcp":
        return None
    return None if export_table_for(external_account_id) else SETUP_HINT
