#!/bin/sh
# 제한 실수집 **실행 전/후** 점검. 실제 CSP 호출은 하지 않는다(네트워크로 나가는 요청 0건).
#
# 판정 본문은 scripts/verify_preflight_check.py이고, 이 파일은 호스트 해시를 붙여 컨테이너 stdin으로
# 넘기는 껍데기다. **불일치가 하나라도 있으면 0이 아닌 코드로 끝난다** — 출력 눈으로 보는 점검이 아니다.
#
#   sh scripts/verify_preflight.sh blocked          # 수집 전면 차단 상태인지(평소·원복 뒤)
#   sh scripts/verify_preflight.sh allow <계정id>    # 승인된 그 계정 하나만 열린 상태인지(실수집 직전)
#
# 출력하지 않는 것: DATABASE_URL·전체 환경변수·비밀값.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-blocked}"
ACCOUNT="${2:-}"
C="${VERIFY_API_CONTAINER:-mcp-api-verify}"
CTX="${VERIFY_DOCKER_CONTEXT:-colima-verify}"
DOCKER="docker --context $CTX"
EXPECTED_DB="${VERIFY_DB_NAME:-mcp_db_verify}"

case "$MODE" in
  blocked|allow) ;;
  *) echo "usage: $0 [blocked|allow <cloud_account_id>]" >&2; exit 2 ;;
esac

$DOCKER ps -q -f name="$C" | grep -q . || {
  echo "$C 컨테이너가 없습니다. 먼저: sh scripts/verify_stack.sh up" >&2; exit 2; }

hash_of() { shasum -a 256 "$1" | cut -d' ' -f1; }
HASHES="{\"azure_cost\":\"$(hash_of "$ROOT/backend/app/cost/azure_cost.py")\",\"gating\":\"$(hash_of "$ROOT/backend/app/cost/gating.py")\"}"

set +e
# -i 가 있어야 heredoc/파일이 컨테이너 stdin으로 들어간다(없으면 `python -`가 빈 입력을 읽는다).
$DOCKER exec -i \
  -e PREFLIGHT_HOST_HASHES="$HASHES" \
  -e PREFLIGHT_EXPECTED_DB="$EXPECTED_DB" \
  "$C" python - "$MODE" $ACCOUNT < "$ROOT/scripts/verify_preflight_check.py"
RC=$?
set -e

if [ "$RC" -ne 0 ]; then
  echo "점검 실패(exit $RC) — **실수집으로 진행하지 않는다.** 설정을 고치고 다시 점검한다." >&2
fi
exit "$RC"
