/* frontend/assets/js/cost-format.js — 비용 금액 합산·서식 공용 유틸.
   window.MCPCostFormat 으로 노출한다. api.js·ui.js와 같은 방식이다(빌드 도구 없음).
   노출 함수는 4개뿐이다 — sumWithGuard · addDecimalString · subDecimalString · money.
   money()가 표시의 유일한 경로다. toLocaleString()·toFixed()를 화면에서 직접 부르지 않는다 —
   서버가 주는 6자리 문자열을 Number()로 바꾸는 순간 큰 금액에서 자리가 틀어진다. */
window.MCPCostFormat = (function () {
  "use strict";

/**
 * 금액 항목 배열을 (cost_kind, currency, period)로 그룹지어 소계를 낸다.
 * @param {Array<{amount: string, cost_kind: string, currency: string,
 *                period_start: string, period_end: string}>} items
 * @returns {{ mixed: boolean, groups: Array<{key: string, cost_kind: string,
 *             currency: string, period_start: string, period_end: string,
 *             total: string, count: number}> }}
 */
function sumWithGuard(items) {
  if (!Array.isArray(items) || items.length === 0) {
    return { mixed: false, groups: [] };
  }
  const map = new Map();
  for (const it of items) {
    if (it == null || it.amount == null) continue;           // null은 건너뛴다 (0으로 세지 않는다)
    const key = `${it.cost_kind}|${it.currency}|${it.period_start}~${it.period_end}`;
    const prev = map.get(key);
    if (prev) {
      prev.total = addDecimalString(prev.total, it.amount);
      prev.count += 1;
    } else {
      map.set(key, {
        key,
        cost_kind: it.cost_kind,
        currency: it.currency,
        period_start: it.period_start,
        period_end: it.period_end,
        total: it.amount,
        count: 1,
      });
    }
  }
  const groups = [...map.values()];
  return { mixed: groups.length > 1, groups };
}

/** 문자열 금액 덧셈 — 소수 6자리 고정(DB numeric(19,6)과 동일), 부동소수점 오차 회피 */
function addDecimalString(a, b) {
  const SCALE = 6n;
  const toBig = (s) => {
    const neg = s.trim().startsWith('-');
    const [i, f = ''] = s.trim().replace('-', '').split('.');
    const frac = (f + '0'.repeat(Number(SCALE))).slice(0, Number(SCALE));
    const v = BigInt(i + frac);
    return neg ? -v : v;
  };
  const sum = toBig(a) + toBig(b);
  const neg = sum < 0n;
  const abs = (neg ? -sum : sum).toString().padStart(Number(SCALE) + 1, '0');
  const i = abs.slice(0, abs.length - Number(SCALE));
  const f = abs.slice(abs.length - Number(SCALE)).replace(/0+$/, '');
  return `${neg ? '-' : ''}${i}${f ? '.' + f : ''}`;
}

/** 문자열 금액 뺄셈 — 증감액·초과액용. 덧셈을 재사용한다. */
function subDecimalString(a, b) {
  const t = String(b).trim();
  const negated = t.startsWith('-') ? t.slice(1) : '-' + t;
  return addDecimalString(a, negated);
}

/** 표시용 서식 — 8장 표의 규칙을 그대로 구현한다. 값을 Number 로 바꾸지 않는다.
 *  @param {string|null} amount 서버가 준 6자리 문자열
 *  @param {string|null} currency ISO 4217. null 이면 기호를 붙이지 않는다
 *  @returns {string} 금액 문자열. amount 가 null 이면 '—' */
function money(amount, currency) {
  if (amount == null) return '—';
  const raw = String(amount).trim();
  const neg = raw.startsWith('-');
  const [i, f = ''] = raw.replace('-', '').split('.');
  const digits = (currency === 'KRW' || currency === 'JPY') ? 0 : 2;   // 소수 없는 통화

  // 반올림(half-up). 자르면 큰 금액에서 매번 아래로 치우친다.
  const scaled = BigInt(i + (f + '0'.repeat(digits + 1)).slice(0, digits + 1));
  const rounded = (scaled + 5n) / 10n;                    // 마지막 한 자리로 반올림
  const pad = rounded.toString().padStart(digits + 1, '0');
  const whole = pad.slice(0, pad.length - digits) || '0';
  const frac = digits === 0 ? '' : '.' + pad.slice(pad.length - digits);

  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const sym = { USD: '$', KRW: '₩', EUR: '€', JPY: '¥' }[currency];
  const body = (sym || '') + grouped + frac;
  const tail = sym ? '' : (currency ? ' ' + currency : '');
  return (neg ? '-' : '') + body + tail;
}

  return {
    sumWithGuard: sumWithGuard,
    addDecimalString: addDecimalString,
    subDecimalString: subDecimalString,
    money: money
  };
})();
