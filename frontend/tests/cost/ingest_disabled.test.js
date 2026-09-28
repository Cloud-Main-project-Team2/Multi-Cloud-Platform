// 수집이 꺼진 계정을 판정에서 뺐다는 사실을 화면이 말하는가(2026-09-28).
//
// 서버는 수집이 꺼져 있고 판정 창에 관측이 없는 계정을 전망·기간 비교에서 빼고
// `ingest_disabled_accounts`로 알려 준다. 화면이 이걸 말하지 않으면 AWS만의 전망이 전체 비용처럼 읽힌다.
// 여기 있는 것은 DOM 픽스처(jsdom)일 뿐 브라우저 확인이 아니다.
//
// 실행: cd frontend/tests/cost && npm install && npm test
const { base, run, has, todayIso } = require("./harness");

const AZURE = [{ provider: "azure", status: "PENDING", label: "az-sub" }];   // id "10"
const DISABLED = [{ cloud_account_id: "10", reason: "INGEST_DISABLED" }];
const computed = (extra) => Object.assign({ state: "computed", based_through: todayIso, required_accounts: 1,
  incomplete_accounts: [], unverified_accounts: [], ingest_disabled_accounts: [] }, extra || {});

(async () => {
  const r = [];

  r.push(await run("전망 계산 + 수집 꺼진 계정 제외 안내", base({ extra: AZURE,
    forecastStatus: computed({ ingest_disabled_accounts: DISABLED }) }), {
    "전망 값이 나온다": (c) => has(c, "#CF-003", "$") && has(c, "#CF-003", "대상 계정 1개 전부 수집 확인"),
    "뺀 계정을 이름과 함께 말한다": (c) => has(c, "#CF-003", "수집이 꺼진 계정 1개(az-sub)는 전망 대상에서 뺐습니다"),
  }));
  r.push(await run("뺀 계정이 없으면 안내도 없다(회귀)", base({ forecastStatus: computed() }), {
    "안내 없음": (c) => !has(c, "#CF-003", "수집이 꺼진 계정"),
  }));
  r.push(await run("전부 수집 꺼짐이면 사유 + 뺀 계정", base({ extra: AZURE,
    forecastStatus: computed({ state: "no_accounts", required_accounts: 0, ingest_disabled_accounts: DISABLED }) }), {
    "값을 지어내지 않는다": (c) => has(c, "#CF-003", "전망 대상 계정(실측 지원 계정)이 없습니다"),
    "뺀 계정 안내": (c) => has(c, "#CF-003", "수집이 꺼진 계정 1개(az-sub)"),
  }));
  r.push(await run("기간 비교에서 뺀 계정 안내", base({ extra: AZURE, comparability: { reasons: [], ingest_disabled_accounts: DISABLED } }), {
    "비교 대상에서 뺐다고 말한다": (c) => has(c, "#CF-024", "수집이 꺼진 계정 1개(az-sub)는 비교 대상에서 뺐습니다"),
  }));

  console.log(r.every(Boolean) ? "\nINGEST-DISABLED DOM PASS" : "\nINGEST-DISABLED DOM FAILED");
  process.exit(r.every(Boolean) ? 0 : 1);
})();
