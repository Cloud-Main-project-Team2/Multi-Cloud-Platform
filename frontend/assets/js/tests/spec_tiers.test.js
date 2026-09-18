/*
 * 회귀 테스트(2026-09-18) — Azure 무료 구독을 고려한 Compute 사양 매핑.
 *
 * 이 저장소엔 JS 테스트 프레임워크가 없다(순수 vanilla JS, 빌드 스텝 없음 — CLAUDE.md 기술
 * 스택 참고). 그래서 의존성 없는 plain Node 스크립트로 작성했다: `node
 * frontend/assets/js/tests/spec_tiers.test.js`로 실행하며, 실패하면 종료 코드 1을 반환한다.
 *
 * provisioning.js는 브라우저 DOM을 가정하지만 최상위(top-level)에서 실제로 실행되는 건 상수
 * 선언과 `window.PROV = {...}` 노출뿐이다 — DOM 조작은 전부 `init()` 내부(DOMContentLoaded에서만
 * 호출)에 있어 `document`/`window`를 최소 스텁으로 채운 vm 컨텍스트에서도 안전하게 로드된다.
 *
 * 검증 대상(사용자 요청 회귀 시나리오):
 *   1) Azure B1s가 1 vCPU/1 GiB로 표시·전송된다(3사 공통 라벨에 잘못 합쳐지지 않음).
 *   3) ARM64 대안 B2pts_v2는 이미지 호환이 구현되지 않아 어떤 등급의 실제 선택 가능한 값으로도
 *      절대 등장하지 않는다(주석상 안내 문구는 예외 — 코드 값으로는 없어야 함).
 * (2번 — B2ats_v2 선택 시 "Standard_B2ats_v2"로 정규화되는지는 백엔드 책임이라
 *  backend/tests/test_azure_free_tier_sku.py에서 검증한다.)
 */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC_PATH = path.join(__dirname, "..", "provisioning.js");
const source = fs.readFileSync(SRC_PATH, "utf8");

function makeStubDocument() {
  return {
    addEventListener() {},
    getElementById() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
}

const sandbox = {};
sandbox.window = sandbox; // provisioning.js가 window.PROV = ...로 스스로 노출한다.
sandbox.document = makeStubDocument();
sandbox.console = console;
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: SRC_PATH });

const PROV = sandbox.PROV;

let failures = 0;
function check(cond, label) {
  if (!cond) {
    failures += 1;
    console.error("FAIL: " + label);
  } else {
    console.log("ok: " + label);
  }
}

if (!PROV || !Array.isArray(PROV.SPEC_TIERS)) {
  console.error("FAIL: window.PROV.SPEC_TIERS를 찾을 수 없음 — provisioning.js 로드 실패");
  process.exit(1);
}

const light = PROV.SPEC_TIERS.filter(function (t) { return t.key === "light"; })[0];
check(!!light, "SPEC_TIERS에 light 등급이 있다");

// 1) Azure B1s = 1 vCPU / 1 GiB. AWS/GCP는 2 vCPU라 하나의 공통 라벨로 뭉치면 틀린 값이 된다.
check(light.sku.azure.name === "B1s", "light.azure.name === B1s");
check(light.sku.azure.vcpu === 1, "light.azure.vcpu === 1 (AWS/GCP의 2와 다름)");
check(light.sku.azure.memGiB === 1, "light.azure.memGiB === 1");
check(light.sku.azure.free === true, "light.azure.free === true (무료 대상 표시)");
check(light.sku.aws.name === "t3.micro" && light.sku.aws.vcpu === 2, "light.aws는 2 vCPU로 azure(1)와 다르다");

// 표준 등급은 3사 모두 2 vCPU/4GiB로 실제로 같다(가격 비교 모달과 일치) — 우연히 같은 경우까지
// 깨뜨리지 않았는지 확인.
const standard = PROV.SPEC_TIERS.filter(function (t) { return t.key === "standard"; })[0];
["aws", "azure", "gcp"].forEach(function (p) {
  check(standard.sku[p].vcpu === 2 && standard.sku[p].memGiB === 4, "standard." + p + " === 2vCPU/4GiB");
});

// 3) 무료 대안은 B2ats_v2(x86-64)뿐이고 ARM64(B2pts_v2)는 실제 선택 가능한 값(SPEC_TIERS의 어떤
// sku나 AZURE_LIGHT_FREE_ALT)으로 절대 등장하지 않는다.
check(!!PROV.AZURE_LIGHT_FREE_ALT, "AZURE_LIGHT_FREE_ALT가 노출된다");
check(PROV.AZURE_LIGHT_FREE_ALT && PROV.AZURE_LIGHT_FREE_ALT.name === "B2ats_v2", "무료 대안은 B2ats_v2");

const allSkuNames = [];
PROV.SPEC_TIERS.forEach(function (tier) {
  Object.keys(tier.sku).forEach(function (p) { allSkuNames.push(tier.sku[p].name); });
});
if (PROV.AZURE_LIGHT_FREE_ALT) allSkuNames.push(PROV.AZURE_LIGHT_FREE_ALT.name);
check(allSkuNames.indexOf("B2pts_v2") === -1, "B2pts_v2(ARM64)는 어떤 실제 선택 가능한 SKU 값으로도 없다");

if (failures > 0) {
  console.error(failures + "개 실패");
  process.exit(1);
}
console.log("모두 통과");
