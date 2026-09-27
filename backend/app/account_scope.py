"""화면에 보이는 클라우드 계정의 범위 — "자격 증명이 하나라도 남아 있는 계정"만.

마이페이지의 삭제는 `DELETE /credentials/{id}`라서 credential 행만 지우고 `cloud_accounts` 행은
남긴다(계정 삭제 API는 스냅샷 보존 정책 미확정으로 501 보류 — CLAUDE.md 결정 기록). 그래서 마지막
키를 지운 계정이 비용·인벤토리·대시보드·팀 화면에 계속 남아 있었다.

계정을 지우지 않고 **조회에서만 뺀다**:
- 리소스·비용 이력은 그대로 남는다. 같은 `external_account_id`로 다시 등록하면
  (`POST /credentials/{provider}`가 기존 계정 행을 재사용) 이력이 그대로 다시 보인다.
- 검증 여부(`verified`)는 보지 않는다 — 검증에 실패한 키도 마이페이지에 보이는 "등록된" 키다.
- 등록 경로(`routers/credentials.py`의 계정 조회)에는 쓰지 않는다. 쓰면 재등록 때 같은 계정이
  하나 더 생긴다.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.models import CloudAccount, Credential


def has_credential():
    """`CloudAccount`에 credential이 1개 이상 있는가(상관 서브쿼리 — 바깥 쿼리에 CloudAccount가 있어야 한다)."""
    return sa.exists().where(Credential.cloud_account_id == CloudAccount.id)


def owned_active_account(user_id: int):
    """`CloudAccount.user_id == user_id` 자리에 그대로 쓰는 필터 — 소유 + 자격 증명 남아 있음."""
    return sa.and_(CloudAccount.user_id == user_id, has_credential())
