/* Offline regression: chart aggregates only recorded closed PnL by Vietnam exit day. */
'use strict';
const assert = require('node:assert/strict');
const chart = require('../autotrade_live/static/pnl_chart.js');
const at = iso => Date.parse(iso);
const rows = [
  {symbol:'BTC-USDT', combo:'C1', status:'closed', pnl:10,
    exit_ms:at('2026-10-01T18:00:00Z')}, // 02/10 in Vietnam
  {symbol:'BTC-USDT', combo:'C2', status:'estimated_closed', pnl:-4,
    exit_ms:at('2026-10-02T03:00:00Z')},
  {symbol:'BTC-USDT', combo:'C1', status:'closed', pnl:8,
    exit_ms:at('2026-10-04T01:00:00Z')},
  {symbol:'BTC-USDT', combo:'C1', status:'closed', pnl:null,
    exit_ms:at('2026-10-05T01:00:00Z')},
  {symbol:'BTC-USDT', combo:'C1', status:'closed', pnl:7, exit_ms:null},
  {symbol:'BTC-USDT', combo:'C1', status:'open', pnl:900,
    exit_ms:at('2026-10-04T01:00:00Z')},
  {symbol:'BTC-USDT', combo:'C1', status:'closed_pending', pnl:700,
    exit_ms:at('2026-10-04T01:00:00Z')},
  {symbol:'ETH-USDT', combo:'C1', status:'closed', pnl:55,
    exit_ms:at('2026-10-04T01:00:00Z')},
];
const btc = chart.buildSeries(rows,'BTC-USDT');
assert.deepEqual(btc.days, ['2026-10-01','2026-10-02','2026-10-04']);
assert.deepEqual(btc.total, [0,6,14]);
assert.equal(btc.final,14);
assert.equal(btc.counted,3);
assert.equal(btc.omitted,2);
assert.equal(btc.estimated,1);
assert.deepEqual(btc.combos.map(x=>[x.name,x.values]),
  [['C1',[0,10,18]],['C2',[0,-4,-4]]]);
const eth = chart.buildSeries(rows,'ETH-USDT');
assert.equal(eth.final,55);
assert.equal(eth.counted,1);
assert.equal(eth.estimated,0);
assert.equal(eth.combos.length,1);
const empty = chart.buildSeries(rows,'DOGE-USDT');
assert.deepEqual(empty.days,[]);
assert.equal(empty.final,0);
console.log('PnL chart exit-day, per-symbol, coverage and merged-estimate regression PASS');
