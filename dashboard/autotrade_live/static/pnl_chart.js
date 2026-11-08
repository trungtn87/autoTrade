/* AutoTrade read-only cumulative realized/allocated PnL chart.
 * Uses the same /api/dashboard history already fetched for Combo and calendar.
 * Closed time is the accounting date; missing PnL is NEVER guessed or counted.
 */
(function (root) {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var COLORS = ['#73c7ff', '#c596ff', '#ffcb77', '#ff8aa3', '#70dbbe',
    '#a8b6ff', '#e8a0eb', '#c2df76', '#f0a982', '#8bbfd5'];
  var currentRows = [];
  var currentSymbol = 'BTC-USDT';
  var lastSymbol = null;

  function dateKey(ms) {
    // Vietnam has a fixed UTC+7 offset and no DST.
    var date = new Date(Number(ms) + 7 * 3600000);
    return Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 10) : null;
  }
  function numeric(value) {
    return value !== null && value !== undefined && value !== '' &&
      Number.isFinite(Number(value));
  }
  function buildSeries(rows, symbol) {
    var buckets = new Map();
    var omitted = 0, estimated = 0, counted = 0;
    for (var row of rows || []) {
      if (row.symbol !== symbol || (row.status !== 'closed' && row.status !== 'estimated_closed')) continue;
      if (!numeric(row.pnl) || !numeric(row.exit_ms) || Number(row.exit_ms) <= 0) {
        omitted += 1;
        continue;
      }
      var day = dateKey(row.exit_ms);
      if (!day) { omitted += 1; continue; }
      var bucket = buckets.get(day);
      if (!bucket) { bucket = {total: 0, byCombo: new Map()}; buckets.set(day, bucket); }
      var pnl = Number(row.pnl);
      var combo = String(row.combo || 'Chưa phân loại');
      bucket.total += pnl;
      bucket.byCombo.set(combo, (bucket.byCombo.get(combo) || 0) + pnl);
      counted += 1;
      if (row.status === 'estimated_closed') estimated += 1;
    }
    var days = [...buckets.keys()].sort();
    if (!days.length) return {days: [], total: [], combos: [], counted: 0, omitted: omitted, estimated: 0, final: 0};
    var start = new Date(Date.parse(days[0] + 'T00:00:00Z') - 86400000).toISOString().slice(0, 10);
    var axes = [start, ...days];
    var combos = [...new Set([...buckets.values()].flatMap(x => [...x.byCombo.keys()]))]
      .sort((a, b) => a.localeCompare(b, 'vi', {numeric: true}));
    var balances = new Map(combos.map(c => [c, 0]));
    var series = combos.map((name, index) => ({name: name, color: COLORS[index % COLORS.length], values: [0]}));
    var running = 0, total = [0];
    for (var day of days) {
      var bucket = buckets.get(day);
      running += bucket.total;
      total.push(running);
      for (var item of series) {
        balances.set(item.name, balances.get(item.name) + (bucket.byCombo.get(item.name) || 0));
        item.values.push(balances.get(item.name));
      }
    }
    return {days: axes, total: total, combos: series, counted: counted,
      omitted: omitted, estimated: estimated, final: running};
  }
  function node(name, attrs, content) {
    var el = document.createElementNS(NS, name);
    for (var [key, value] of Object.entries(attrs || {})) el.setAttribute(key, String(value));
    if (content !== undefined) el.textContent = String(content);
    return el;
  }
  function money(value) {
    return (value > 0 ? '+' : '') + Number(value).toLocaleString('vi-VN',
      {minimumFractionDigits: 2, maximumFractionDigits: 2});
  }
  function compact(value) {
    var abs = Math.abs(value);
    var scale = abs >= 1000000 ? 1000000 : abs >= 1000 ? 1000 : 1;
    var suffix = scale === 1000000 ? 'tr' : scale === 1000 ? 'k' : '';
    var number = value / scale;
    return (value > 0 ? '+' : '') + number.toLocaleString('vi-VN',
      {maximumFractionDigits: scale === 1 ? 2 : 1}) + suffix;
  }
  function ticks(low, high) {
    var rough = Math.max((high - low) / 4, 0.000001);
    var base = Math.pow(10, Math.floor(Math.log10(rough)));
    var ratio = rough / base;
    var step = base * (ratio > 5 ? 10 : ratio > 2 ? 5 : ratio > 1 ? 2 : 1);
    var bottom = Math.floor(low / step) * step;
    var top = Math.ceil(high / step) * step;
    var values = [];
    for (var i = 0; i <= 12; i++) {
      var y = bottom + step * i;
      if (y > top + step / 100) break;
      values.push(Math.abs(y) < step / 1000 ? 0 : y);
    }
    return {values: values, low: bottom, high: top === bottom ? bottom + step : top};
  }
  function draw(series, selection) {
    var svg = document.getElementById('pnl-svg');
    if (!svg) return;
    svg.replaceChildren();
    var width = Math.max(310, Math.floor(svg.parentElement.getBoundingClientRect().width));
    var height = 250;
    svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    var symbolName = currentSymbol.split('-')[0];
    svg.setAttribute('aria-label', 'Biểu đồ PnL tích lũy ' + symbolName + ', theo ngày đóng lệnh');
    if (!series.days.length) {
      svg.append(node('text', {x: width/2, y: height/2, fill:'#9cb0c9',
        'text-anchor':'middle', 'font-size':12}, 'Chưa có lệnh đóng với PnL trong DB'));
      return;
    }
    var left = 57, right = 14, top = 17, bottom = 34;
    var plotW = width - left - right, plotH = height - top - bottom;
    var visible = selection ? series.combos.filter(x => x.name === selection) : series.combos;
    var values = [0, ...series.total, ...visible.flatMap(x => x.values)];
    var min = Math.min(...values), max = Math.max(...values);
    var padding = Math.max((max - min) * 0.09, Math.abs(max || min) * 0.09, 0.01);
    var scale = ticks(min - padding, max + padding);
    var y = n => top + (scale.high - n) / (scale.high - scale.low) * plotH;
    var first = Date.parse(series.days[0] + 'T00:00:00Z');
    var last = Date.parse(series.days[series.days.length-1] + 'T00:00:00Z');
    var dx = Math.max(last - first, 86400000);
    var x = d => left + (Date.parse(d + 'T00:00:00Z') - first) / dx * plotW;
    for (var tick of scale.values) {
      var yy = y(tick);
      svg.append(node('line', {x1:left, y1:yy, x2:width-right, y2:yy,
        stroke: tick === 0 ? '#527387' : '#293e54', 'stroke-width':tick === 0 ? 1.25 : 1}));
      svg.append(node('text', {x:left-7, y:yy+4, fill:'#9bb1c8',
        'text-anchor':'end', 'font-size':10}, compact(tick)));
    }
    var tickCount = width < 430 ? 3 : 5;
    for (var i = 0; i < tickCount; i++) {
      var time = first + (last - first) * i / Math.max(1, tickCount-1);
      var day = new Date(time).toISOString().slice(0,10);
      var xx = left + plotW * i / Math.max(1, tickCount-1);
      svg.append(node('text', {x:xx, y:height-11, fill:'#9bb1c8', 'font-size':11,
        'text-anchor':i === 0 ? 'start' : i === tickCount-1 ? 'end' : 'middle'},
        day.slice(8,10) + '/' + day.slice(5,7)));
    }
    function path(vals) {
      return series.days.map((d, i) => (i ? 'L' : 'M') +
        x(d).toFixed(2) + ',' + y(vals[i]).toFixed(2)).join(' ');
    }
    for (var combo of visible) {
      svg.append(node('path', {d:path(combo.values), stroke:combo.color,
        'stroke-width':selection ? 2.2 : 1.05, opacity:selection ? 0.95 : 0.45,
        fill:'none', 'stroke-linejoin':'round', 'stroke-linecap':'round'}));
    }
    svg.append(node('path', {d:path(series.total), stroke:'#42dfb9',
      'stroke-width':2.8, fill:'none', 'stroke-linecap':'round', 'stroke-linejoin':'round'}));
    var ending = series.total[series.total.length-1];
    svg.append(node('circle', {cx:x(series.days[series.days.length-1]),
      cy:y(ending), r:4, fill:'#42dfb9', stroke:'#0b1120', 'stroke-width':1.5}));
  }
  function render(rows, symbol) {
    currentRows = rows || [];
    currentSymbol = symbol;
    var select = document.getElementById('pnl-combo-filter');
    var summary = document.getElementById('pnl-summary');
    var count = document.getElementById('pnl-trade-count');
    var title = document.getElementById('pnl-active-symbol');
    var note = document.getElementById('pnl-chart-note');
    if (!select || !summary || !count || !title || !note) return;
    var data = buildSeries(currentRows, currentSymbol);
    title.textContent = symbol.split('-')[0];
    count.textContent = data.counted + ' lệnh có PnL';
    var selection = lastSymbol === symbol ? select.value : '';
    select.replaceChildren(new Option('Tất cả Combo', ''));
    for (var combo of data.combos) select.add(new Option(combo.name, combo.name));
    select.value = data.combos.some(c => c.name === selection) ? selection : '';
    lastSymbol = symbol;
    summary.textContent = data.counted ? (data.estimated ? '≈ ' : '') + money(data.final) + ' USDT' : '—';
    summary.className = data.final >= 0 ? 'positive' : 'negative';
    note.textContent = 'PnL cộng dồn theo ngày đóng lệnh (UTC+7), từ các lệnh có PnL trong DB.' +
      (data.estimated ? ' ≈ Bao gồm ' + data.estimated + ' lệnh phân bổ ước tính từ vị thế gộp.' : '') +
      (data.omitted ? ' Không cộng ' + data.omitted + ' lệnh đóng chưa có PnL hoặc thời gian đóng.' : '') +
      ' Đường xanh ngọc là tổng; đường mảnh là từng Combo.';
    draw(data, select.value);
  }
  if (typeof document !== 'undefined') {
    var select = document.getElementById('pnl-combo-filter');
    if (select) select.addEventListener('change', () => render(currentRows, currentSymbol));
    if (typeof window !== 'undefined') window.addEventListener('resize', () => render(currentRows, currentSymbol));
  }
  root.PnlChart = {buildSeries: buildSeries, render: render};
  if (typeof module !== 'undefined' && module.exports) module.exports = root.PnlChart;
})(typeof window === 'undefined' ? globalThis : window);
