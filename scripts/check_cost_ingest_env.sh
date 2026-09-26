#!/bin/sh
# 비용 수집 활성화 설정(app/cost/gating.py)이 **docker compose 치환 후에도** 의도대로 나오는지 본다.
#
# 왜 필요한가: 앱 단위 테스트는 프로세스 환경변수만 보므로 compose 문법 오류를 잡지 못한다.
# `${VAR:-aws}`는 변수를 **빈 문자열로 지정해도** aws로 치환해서 "명시적으로 비워서 끄기"가
# 통하지 않는다. 그래서 치환 결과 자체를 검사한다.
#
# - `docker compose config`(설정 계산)만 실행한다. 컨테이너를 만들거나 재생성하지 않고 CSP도 부르지 않는다.
# - 출력은 이 세 키만 보여준다(다른 환경변수·비밀값은 찍지 않는다).
# - ⚠️ 이 스크립트는 **사람이 눈으로 보는 점검 도구**다. 기대값 불일치를 스스로 실패 처리하지 않으므로
#   자동 회귀 테스트가 아니다(자동 검증은 backend/tests/test_cost_ingest_gating.py의 문자열 해석 표).
#
# 사용: sh scripts/check_cost_ingest_env.sh
set -e
cd "$(dirname "$0")/.."

KEYS='COST_INGEST_PROVIDERS|COST_AUTO_INGEST_PROVIDERS|COST_INGEST_ACCOUNT_IDS'

show() {   # show <라벨> [env 설정...]
  label="$1"; shift
  printf '%s\n' "── $label"
  # env -u 로 "미설정"을, env VAR= 로 "빈 문자열"을 만든다. compose는 .env도 읽으므로
  # 이 저장소의 .env에 해당 키가 없다는 전제를 함께 확인한다(아래 .env 점검).
  env "$@" docker compose config 2>/dev/null \
    | grep -E "^ +($KEYS):" \
    | sed 's/^ *//' \
    | sed 's/: *$/: (빈 문자열)/'
  printf '\n'
}

if grep -qE "^($KEYS)=" .env 2>/dev/null; then
  echo "⚠️  .env가 이 키들을 이미 지정하고 있어 아래 '미설정' 사례가 정확하지 않습니다:"
  grep -nE "^($KEYS)=" .env
  echo
fi

show "① 미설정 → 기존 AWS 기본 동작" -u COST_INGEST_PROVIDERS -u COST_AUTO_INGEST_PROVIDERS -u COST_INGEST_ACCOUNT_IDS
show "② 명시적 빈 문자열 → 수집 전부 꺼짐" COST_INGEST_PROVIDERS= COST_AUTO_INGEST_PROVIDERS= COST_INGEST_ACCOUNT_IDS=
show "③ aws" COST_INGEST_PROVIDERS=aws COST_AUTO_INGEST_PROVIDERS=aws COST_INGEST_ACCOUNT_IDS=
show "④ aws,azure(수동만) + 계정 지정" COST_INGEST_PROVIDERS=aws,azure COST_AUTO_INGEST_PROVIDERS=aws COST_INGEST_ACCOUNT_IDS=azure:42
show "⑤ 잘못된 provider 이름" COST_INGEST_PROVIDERS=azure-prod COST_AUTO_INGEST_PROVIDERS=azure-prod COST_INGEST_ACCOUNT_IDS=azure:42

cat <<'NOTE'
읽는 법
  ①은 aws여야 한다(기존 동작 유지). ②는 빈 문자열이어야 한다(:-를 쓰면 여기서 aws가 나온다).
  ⑤의 잘못된 이름은 compose가 그대로 넘기고, 앱이 무시한다 — **기본값으로 되돌아가지 않는다.**
  즉 provider 목록에 유효한 값이 하나도 없으면 AWS 수집도 꺼진다(설정 오류가 조용히 넓어지지 않게).
설정 반영 방법 (둘이 다르다)
  · 직접 실행한 앱(uvicorn 등): 새 환경값을 넣고 **프로세스를 재시작**하면 적용된다.
  · docker compose environment로 주입한 값: `docker compose restart`는 **기존 컨테이너를 그대로
    재시작**해서 environment가 갱신되지 않는다. `docker compose up -d`(설정이 바뀌면 재생성) 또는
    `docker compose up -d --force-recreate <서비스>`로 **컨테이너를 다시 만들어야** 적용된다.
  어느 쪽이든 Settings는 프로세스 시작 시 읽혀 @lru_cache로 고정되므로, 실행 직전 재판정이 있다고
  해서 .env 편집이 실행 중인 프로세스에 즉시 반영되지는 않는다.
NOTE
