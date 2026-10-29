/* Autotrade Live Terminal v4 — read-only strategy and BingX history. */
'use strict';
const $ = id => document.getElementById(id);
const number = value => value === null || value === undefined || value === '' ? 0 : Number(value) || 0;
const valid = value => value !== null && value !== undefined && Number.isFinite(Number(value));
const fmt = (value, decimals = 2) => Number(value).toLocaleString('vi-VN', {
  minimumFractionDigits: decimals, maximumFractionDigits: decimals
});
const roi = value => valid(value) ? (number(value) > 0 ? '+' : '') + fmt(value) + '%' : '—';
const cash = value => valid(value) ? (number(value) > 0 ? '+' : '') + fmt(value) + ' USDT' : '—';
const classFor = value => number(value) > 0 ? 'positive' : number(value) < 0 ? 'negative' : '';
const dateFormatter = new Intl.DateTimeFormat('vi-VN', {
  timeZone: 'Asia/Ho_Chi_Minh', day: '2-digit', month: '2-digit',
  year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
});
const dateParts = ms => {
  if (!valid(ms) || number(ms) <= 0) return null;
  const values = Object.fromEntries(dateFormatter.formatToParts(new Date(Number(ms)))
    .filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
  return { date: values.day + '/' + values.month + '/' + values.year,
    clock: values.hour + ':' + values.minute + ':' + values.second,
    key: values.year + '-' + values.month + '-' + values.day };
};
const timeVN = ms => {
  const p = dateParts(ms);
  return p ? p.date + ' · ' + p.clock : 'Chưa có dữ liệu';
};
function set(id, value) { const node = $(id); if (node) node.textContent = value; }
function element(tag, className = '', value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined && value !== null) node.textContent = String(value);
  return node;
}
function selectCombos(items) {
  const node = $('combo'), previous = node.value;
  node.replaceChildren(new Option('Tất cả Combo', ''));
  for (const item of items || []) node.add(new Option(item, item));
  node.value = (items || []).includes(previous) ? previous : '';
}
function renderCombos(rows) {
  const root = $('combo-list'); root.replaceChildren();
  set('combo-count', rows.length + ' Combo');
  if (!rows.length) { root.append(element('div', 'empty', 'Chưa có Combo trong khoảng đã chọn.')); return; }
  for (const row of rows) {
    const card = element('article', 'strategy-card');
    const head = element('div', 'strategy-head');
    const pair = (row.symbol || '').replace('-USDT', '');
    const name = element('div', 'strategy-name');
    name.append(element('span', 'pair-label', pair), element('strong', '', row.combo || '—'));
    head.append(name, element('strong', 'strategy-roi ' + classFor(row.roi_total), row.closed ? roi(row.roi_total) : '—'));
    card.append(head);
    const line = element('div', 'strategy-line');
    const percent = valid(row.win_rate) ? Math.max(0, Math.min(100, number(row.win_rate))) : 0;
    const track = element('div', 'strategy-track'), fill = element('div');
    fill.style.width = percent + '%'; track.append(fill);
    line.append(track, element('span', 'strategy-win', row.closed ? fmt(row.win_rate, 1) + '% win' : 'Chưa có kết quả'));
    card.append(line);
    const stats = element('div', 'strategy-stats');
    for (const [label, value] of [
      ['Hoàn tất', number(row.closed) + '/' + number(row.signals)],
      ['RR', valid(row.rr) ? fmt(row.rr) : '—'],
      ['ROI / lệnh', valid(row.roi_avg) ? roi(row.roi_avg) : '—'],
      ['Chưa đủ dữ liệu', number(row.unresolved)]
    ]) {
      const node = element('div'); node.append(element('small', '', label), element('strong', '', value));
      stats.append(node);
    }
    card.append(stats, element('div', 'strategy-foot',
      'TP ' + number(row.tp_hits) + ' · Trailing ' + number(row.trailing) + ' · Thua ' + number(row.loss)));
    root.append(card);
  }
}
function renderCurve(items) {
  const root = $('equity-chart'); root.replaceChildren();
  const closed = (items || []).filter(r => r.status === 'closed' &&
    valid(r.roi_pct) && valid(r.exit_ms) && number(r.exit_ms) > 0)
    .sort((a, b) => number(a.exit_ms) - number(b.exit_ms));
  if (!closed.length) {
    root.append(element('div', 'empty', 'Chưa có đủ tín hiệu đã xác nhận để vẽ ROI.'));
    set('chart-start', '—'); set('chart-end', '—'); return;
  }
  const points = [{ t: closed[0].exit_ms, v: 0 }];
  let total = 0;
  for (const r of closed) { total += number(r.roi_pct); points.push({ t: r.exit_ms, v: total }); }
  const minimum = Math.min(0, ...points.map(p => p.v));
  const maximum = Math.max(0, ...points.map(p => p.v));
  const padding = Math.max((maximum - minimum) * .15, .15);
  const low = minimum - padding, high = maximum + padding;
  const w = 700, h = 236, left = 46, right = 14, top = 12, bottom = 28;
  const plotW = w - left - right, plotH = h - top - bottom;
  const x = i => left + (i / Math.max(1, points.length - 1)) * plotW;
  const y = value => top + (high - value) / (high - low) * plotH;
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('aria-hidden', 'true');
  const mk = (tag, attrs, parent = svg) => {
    const node = document.createElementNS(ns, tag);
    for (const [key, val] of Object.entries(attrs)) node.setAttribute(key, String(val));
    parent.appendChild(node); return node;
  };
  const defs = mk('defs', {});
  const gradient = mk('linearGradient', { id: 'roi-fill', x1: '0', y1: '0', x2: '0', y2: '1' }, defs);
  mk('stop', { offset: '0%', 'stop-color': total < 0 ? '#fa7286' : '#36dbb1', 'stop-opacity': '.24' }, gradient);
  mk('stop', { offset: '100%', 'stop-color': '#36dbb1', 'stop-opacity': '0' }, gradient);
  for (let i = 0; i < 5; i++) {
    const v = low + (high - low) * i / 4, posY = y(v);
    mk('line', { x1: left, x2: w-right, y1: posY, y2: posY, stroke: '#29374d', 'stroke-dasharray': '3 6' });
    const label = mk('text', { x: left-8, y: posY+4, 'text-anchor': 'end', fill: '#8297b3', 'font-size': '11' });
    label.textContent = fmt(v, 1);
  }
  const trace = points.map((p, i) => (i === 0 ? 'M' : 'L') + x(i).toFixed(2) + ' ' + y(p.v).toFixed(2)).join(' ');
  const area = trace + ' L ' + x(points.length-1).toFixed(2) + ' ' + (h-bottom) + ' L ' + x(0) + ' ' + (h-bottom) + ' Z';
  mk('path', { d: area, fill: 'url(#roi-fill)' });
  mk('path', { d: trace, fill: 'none', stroke: total < 0 ? '#fb7185' : '#45e0b8',
    'stroke-width': '2.8', 'stroke-linejoin': 'round', 'stroke-linecap': 'round' });
  mk('circle', { cx: x(points.length-1), cy: y(total), r: '5', fill: total < 0 ? '#fb7185' : '#45e0b8',
    stroke: '#142238', 'stroke-width': '2.5' });
  root.append(svg);
  set('chart-start', dateParts(closed[0].exit_ms)?.date || '—');
  set('chart-end', dateParts(closed[closed.length-1].exit_ms)?.date || '—');
}
const resultLabels = {
  SL: 'STOP LOSS', TP_TRAILING: 'TP + TRAILING', TP_SL: 'TP + SL',
  TRAILING_SL: 'TRAILING + SL', TP_PARTIAL: 'TP · CHƯA ĐỦ',
  TRAILING_PARTIAL: 'TRAILING · CHƯA ĐỦ',
  UNRESOLVED: 'CHƯA ĐỦ DỮ LIỆU', ENTRY_FAILED: 'ENTRY THẤT BẠI',
  ENTRY_UNKNOWN: 'ENTRY CHƯA XÁC ĐỊNH'
};
const statusClass = row => row.status === 'closed' ?
  (number(row.roi_pct) >= 0 ? 'good' : 'bad') :
  row.status === 'entry_failed' ? 'bad' : 'neutral';
function dateCell(label, ms, missing = 'Chưa có dữ liệu') {
  const node = element('div', 'trade-date-cell');
  node.append(element('small', '', label));
  const parts = dateParts(ms);
  node.append(element('strong', '', parts ? parts.date : missing));
  if (parts) node.append(element('span', '', parts.clock));
  return node;
}
function createHistoryCard(row) {
  const card = element('article', 'trade-card');
  const head = element('div', 'trade-head');
  const title = element('div', 'trade-title');
  const sym = (row.symbol || '—').replace('-USDT', '');
  title.append(element('strong', '', sym + '-USDT'));
  const side = String(row.side || '').toUpperCase();
  const direction = side === 'BUY' || side === 'LONG' ? 'LONG' :
                    side === 'SELL' || side === 'SHORT' ? 'SHORT' : '—';
  title.append(element('span', 'direction ' + (direction === 'LONG' ? 'long' : direction === 'SHORT' ? 'short' : ''), direction));
  title.append(element('small', 'trade-combo', row.combo || 'Chưa có Combo'));
  const outcome = element('div', 'trade-outcome');
  outcome.append(element('strong', classFor(row.roi_pct),
    row.status === 'closed' ? roi(row.roi_pct) : '—'));
  outcome.append(element('span', 'result-badge ' + statusClass(row), resultLabels[row.result] || 'CHƯA XÁC ĐỊNH'));
  head.append(title, outcome); card.append(head);
  const times = element('div', 'trade-times');
  const entryTime = row.entry_ms || row.entry_created_ms;
  times.append(dateCell(row.entry_ms ? 'Khớp Entry' : row.entry_created_ms ? 'Tạo Entry (chưa rõ giờ khớp)' : 'Mở lệnh', entryTime,
    'Chưa có giờ Entry'));
  times.append(dateCell('Đóng lệnh', row.exit_ms, row.status === 'closed' ? 'Thiếu giờ thoát' : 'Chưa xác nhận đóng'));
  card.append(times);
  card.append(element('div', 'trade-foot', 'Tín hiệu: ' + timeVN(row.signal_ms) +
    ' · ROI giá lý thuyết' + (row.status === 'closed' ? '' : ' chưa xác định')));
  return card;
}
let currentData = null;
let historyLimit = 16;
function matchResult(row, choice) {
  if (!choice) return true;
  if (choice === 'win') return row.status === 'closed' && number(row.roi_pct) > 0;
  if (choice === 'loss') return row.status === 'closed' && number(row.roi_pct) < 0;
  if (choice === 'flat') return row.status === 'closed' && number(row.roi_pct) === 0;
  return row.status === choice;
}
function renderHistory(reset = false) {
  if (reset) historyLimit = 16;
  const all = currentData?.theory?.history || [];
  const type = $('history-result').value;
  const from = $('history-from').value, to = $('history-to').value;
  const filtered = all.filter(row => {
    if (!matchResult(row, type)) return false;
    if (from || to) {
      const date = dateParts(row.exit_ms || row.signal_ms)?.key;
      if (!date || (from && date < from) || (to && date > to)) return false;
    }
    return true;
  });
  set('history-count', filtered.length + ' / ' + all.length + ' tín hiệu');
  const list = $('history-list'); list.replaceChildren();
  if (!currentData?.theory?.available) {
    list.append(element('div', 'empty', currentData?.theory?.reason || 'Chưa có nhật ký tín hiệu V2.'));
  } else if (!filtered.length) {
    list.append(element('div', 'empty', 'Không có giao dịch phù hợp bộ lọc.')); 
  } else {
    for (const row of filtered.slice(0, historyLimit)) list.append(createHistoryCard(row));
  }
  const more = $('history-more');
  more.hidden = filtered.length <= historyLimit;
  more.textContent = 'Xem thêm · ' + Math.min(16, filtered.length - historyLimit) + ' / ' +
    Math.max(0, filtered.length - historyLimit) + ' lệnh còn lại';
}
function renderReliability(data) {
  const summary = data.summary || {};
  if (!data.available) {
    set('entryrate', '—'); set('botstatus', 'Chưa có dữ liệu');
    set('coverage', data.reason || 'Chưa có trạng thái V2');
    $('entrybar').style.width = '0%';
    for (const key of ['signals', 'confirmed', 'failed', 'pending', 'unprotected']) set(key, '—');
    return;
  }
  const percent = valid(summary.entry_rate) ? Math.max(0, Math.min(100, number(summary.entry_rate))) : 0;
  set('entryrate', valid(summary.entry_rate) ? fmt(summary.entry_rate, 1) + '%' : '—');
  $('entrybar').style.width = percent + '%';
  for (const key of ['signals', 'confirmed', 'failed', 'pending', 'unprotected']) set(key, summary[key] ?? '—');
  set('botstatus', 'Theo tín hiệu đã ghi');
  set('coverage', data.coverage || '');
}
function renderBingX(data) {
  const summary = data.summary || {}, exits = data.exit_summary || {};
  set('pnl', cash(summary.pnl_usdt));
  set('pnlpct', valid(summary.pnl_pct) ? roi(summary.pnl_pct) : '—');
  set('closed', summary.closed ?? '—'); set('unmapped', summary.unmapped ?? '—');
  set('open', summary.open_positions ?? '—');
  set('margin', valid(summary.margin_used_usdt) ? fmt(summary.margin_used_usdt) + ' USDT' : '—');
  set('matchdetail', 'Thoát khớp ID: ' + number(exits.matched_by_id) +
    ' · Chưa phân bổ / còn mơ hồ: ' + number(exits.unmapped_or_ambiguous));
  const tradeRoot = $('bingx-trade-list'); tradeRoot.replaceChildren();
  const trades = data.trades || [];
  if (!trades.length) tradeRoot.append(element('div', 'empty', 'Chưa có vị thế BingX đã đóng.'));
  for (const trade of trades.slice(0, 30)) {
    const card = element('div', 'bingx-row');
    const heading = element('div', 'bingx-heading');
    heading.append(element('strong', '', (trade.symbol || '—') + ' · ' + (trade.combo || 'Chưa gắn Combo')));
    heading.append(element('strong', 'bingx-pnl ' + classFor(trade.pnl), cash(trade.pnl)));
    card.append(heading);
    card.append(element('div', 'bingx-meta',
      (trade.side || '—') + ' · ' + (trade.liquidated ? 'Thanh lý' : (trade.exit_types || []).join(' + ') || 'Đã đóng')));
    const dates = element('div', 'bingx-times');
    dates.append(dateCell('Mở thực tế', trade.entry_ms, 'Chưa lưu giờ mở'));
    dates.append(dateCell('Đóng thực tế', trade.exit_ms, 'Chưa lưu giờ đóng'));
    card.append(dates); tradeRoot.append(card);
  }
  set('exitstatus', number(exits.matched_by_id) + ' khớp ID');
  const exitRoot = $('exit-list'); exitRoot.replaceChildren();
  for (const event of (data.exit_events || []).slice(0, 20)) {
    const row = element('div', 'exit-row');
    row.append(element('span', '', timeVN(event.time_ms) + ' · ' +
      (event.combo || 'Không rõ Combo') + ' · ' + (event.type || '—')));
    row.append(element('strong', classFor(number(event.bingx_realized_pnl) + number(event.exit_fees)),
      cash(number(event.bingx_realized_pnl) + number(event.exit_fees))));
    exitRoot.append(row);
  }
  if (!(data.exit_events || []).length) exitRoot.append(element('div', 'empty', 'Chưa có fill thoát lệnh.'));
}
let requestNumber = 0;
async function refresh() {
  const request = ++requestNumber;
  const params = new URLSearchParams({ days: $('period').value });
  if ($('symbol').value) params.set('symbol', $('symbol').value);
  if ($('combo').value) params.set('combo', $('combo').value);
  set('sync', 'Đang đọc dữ liệu…');
  try {
    const response = await fetch('/api/dashboard?' + params.toString(), {
      credentials: 'same-origin', cache: 'no-store'
    });
    if (request !== requestNumber) return;
    if (response.status === 401) { location.href = '/login'; return; }
    if (!response.ok) throw Error('Không đọc được dữ liệu');
    const data = await response.json();
    if (request !== requestNumber) return;
    currentData = data;
    const theory = data.theory || {}, summary = theory.summary || {};
    selectCombos(data.filters?.combos || []);
    set('net', number(summary.closed) ? roi(summary.roi_total) : '—');
    $('net').className = 'hero-value ' + classFor(summary.roi_total);
    set('roi-note', number(summary.closed) + ' lệnh hoàn tất · Trung bình ' +
      (valid(summary.roi_avg) ? roi(summary.roi_avg) : '—') + ' / lệnh');
    set('winrate', valid(summary.win_rate) ? fmt(summary.win_rate, 1) + '%' : '—');
    set('wins', number(summary.win) + ' thắng · ' + number(summary.loss) +
      ' thua · ' + number(summary.flat) + ' hòa');
    set('pf', valid(summary.rr) ? fmt(summary.rr) : '—');
    set('count', summary.closed ?? '—');
    set('unknown', 'Chưa đủ dữ liệu: ' + number(summary.unresolved));
    set('roiavg', valid(summary.roi_avg) ? roi(summary.roi_avg) : '—');
    set('baseline', 'ROI biến động giá, không tính phí / funding / đòn bẩy · Từ ' +
      (data.theory_since || 'đầu dữ liệu') + '. Không phải PnL tài khoản BingX.');
    renderCombos(theory.combos || []);
    renderCurve(theory.history || []);
    renderHistory(true);
    renderReliability(theory);
    renderBingX(data);
    const syncTime = data.last_sync?.time ? Date.parse(data.last_sync.time) : 0;
    const synced = syncTime && data.last_sync?.status === 'success' &&
      Date.now() - syncTime < 2.5 * 3600000;
    $('status-dot').classList.toggle('status-muted', !synced);
    set('sync', syncTime ? 'BingX đồng bộ ' + timeVN(syncTime) : 'Chưa có giờ đồng bộ BingX');
    set('updated', 'Xem lúc ' + new Date().toLocaleTimeString('vi-VN', {
      timeZone: 'Asia/Ho_Chi_Minh', hour: '2-digit', minute: '2-digit'
    }));
  } catch (error) {
    if (request !== requestNumber) return;
    $('status-dot').classList.add('status-muted');
    set('sync', 'Tạm thời không đọc được dữ liệu');
    set('updated', '—');
    if (!currentData) {
      for (const id of ['combo-list', 'history-list', 'equity-chart']) {
        $(id).replaceChildren(element('div', 'empty', 'Dữ liệu chưa khả dụng. Mở lại để thử lần nữa.'));
      }
    }
  }
}
for (const id of ['symbol', 'period', 'combo']) $(id).addEventListener('change', refresh);
for (const id of ['history-result', 'history-from', 'history-to'])
  $(id).addEventListener('change', () => renderHistory(true));
$('history-more').addEventListener('click', () => { historyLimit += 16; renderHistory(); });
refresh();
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/static/sw.js').catch(() => {});
