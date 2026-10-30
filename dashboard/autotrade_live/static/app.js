/* Autotrade read-only · BTC/ETH Combo table and daily close calendar. */
'use strict';
const $ = id => document.getElementById(id);
const fmt = (value, decimals = 2) => Number(value).toLocaleString('vi-VN', {
  minimumFractionDigits: decimals, maximumFractionDigits: decimals
});
const number = value => value === null || value === undefined || value === '' ? NaN : Number(value);
const valid = value => Number.isFinite(number(value));
const roi = value => valid(value) ? (number(value) > 0 ? '+' : '') + fmt(value) + '%' : '—';
const classFor = value => valid(value) && number(value) > 0 ? 'positive' :
  valid(value) && number(value) < 0 ? 'negative' : '';
const dateFormatter = new Intl.DateTimeFormat('vi-VN', {
  timeZone: 'Asia/Ho_Chi_Minh', year: 'numeric', month: '2-digit',
  day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
});
function dateParts(ms) {
  if (!valid(ms) || number(ms) <= 0) return null;
  const parts = Object.fromEntries(dateFormatter.formatToParts(new Date(Number(ms)))
    .filter(x => x.type !== 'literal').map(x => [x.type, x.value]));
  return { key: parts.year + '-' + parts.month + '-' + parts.day,
    date: parts.day + '/' + parts.month + '/' + parts.year,
    time: parts.hour + ':' + parts.minute };
}
const todayKey = () => dateParts(Date.now()).key;
const firstMonth = '2026-10';
const $text = (tag, name, value) => {
  const node = document.createElement(tag);
  if (name) node.className = name;
  node.textContent = String(value);
  return node;
};
function emptyRow(bodyId, columns, message) {
  const body = $(bodyId); body.replaceChildren();
  const tr = document.createElement('tr');
  const td = $text('td', 'empty', message);
  td.colSpan = columns;
  tr.append(td); body.append(tr);
}
function setText(id, value) { $(id).textContent = String(value); }
let symbol = 'BTC-USDT';
let history = [];
let selectedDay = '';
let monthKey = '';
let requestId = 0;
const selectedDays = new Map();
const dayRows = key => history.filter(row => dateParts(row.exit_ms)?.key === key);
const winOrLoss = row => {
  const value = valid(row.recorded_roi_pct) ? number(row.recorded_roi_pct) : number(row.roi_pct);
  return value > 0 ? 'TP' : value < 0 ? 'SL' : 'HÒA';
};

function renderCombos(rows) {
  const items = (rows || []).filter(row => row.closed > 0 && row.symbol === symbol)
    .sort((a,b) => String(a.combo).localeCompare(String(b.combo), 'vi', {numeric:true}));
  setText('combo-count', items.length + ' Combo');
  if (!items.length) { emptyRow('combo-list', 6, 'Chưa có giao dịch hoàn tất.'); return; }
  const body = $('combo-list'); body.replaceChildren();
  for (const row of items) {
    const tr = document.createElement('tr');
    const pair = $text('th', 'combo-cell', row.combo || '—'); pair.scope = 'row'; tr.append(pair);
    for (const [value, className] of [
      [row.closed, ''], [row.tp_count ?? row.win ?? 0, 'positive'],
      [row.sl_count ?? row.loss ?? 0, 'negative'],
      [valid(row.win_rate) ? fmt(row.win_rate, 1) + '%' : '—', ''],
      [roi(row.real_roi_total), classFor(row.real_roi_total)]
    ]) tr.append($text('td', className, value));
    body.append(tr);
  }
}
function monthParts(month) { const p = month.split('-').map(Number); return {year:p[0],month:p[1]}; }
function monthShift(value, delta) {
  const {year, month} = monthParts(value);
  return new Date(Date.UTC(year, month - 1 + delta, 1)).toISOString().slice(0, 7);
}
function renderCalendar() {
  const {year,month} = monthParts(monthKey);
  setText('calendar-month', 'Tháng ' + month + ', ' + year);
  const currentMonth = todayKey().slice(0,7);
  $('prev-month').disabled = monthKey <= firstMonth;
  $('next-month').disabled = monthKey >= currentMonth;
  const firstWeekday = (new Date(Date.UTC(year, month-1, 1)).getUTCDay() + 6) % 7;
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const root = $('calendar-grid'); root.replaceChildren();
  const counts = new Map();
  for (const row of history) {
    const key = dateParts(row.exit_ms)?.key;
    if (key) counts.set(key, (counts.get(key) || 0) + 1);
  }
  for (let i=0; i<firstWeekday; i++) {
    const blank = $text('span','calendar-blank','');blank.setAttribute('aria-hidden','true');root.append(blank);
  }
  for (let day=1; day<=days; day++) {
    const key = monthKey + '-' + String(day).padStart(2,'0');
    const count = counts.get(key) || 0;
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'calendar-day';
    button.textContent = String(day);
    button.setAttribute('aria-label', day + '/' + month + '/' + year +
      (count ? ': ' + count + ' lệnh đóng' : ': không có lệnh'));
    button.setAttribute('aria-pressed', String(key === selectedDay));
    if (count) {
      button.classList.add('has-trades');
      const dot = $text('span','trade-dot','');
      dot.setAttribute('aria-hidden','true'); button.append(dot);
    }
    if (key === selectedDay) button.classList.add('selected');
    if (key === todayKey()) button.classList.add('today');
    if (key > todayKey() || key < firstMonth + '-01') button.disabled = true;
    button.addEventListener('click', () => {
      selectedDay = key;
      selectedDays.set(symbol, selectedDay);
      renderCalendar();
      renderDay();
    });
    root.append(button);
  }
}
function renderDay() {
  const rows = dayRows(selectedDay).sort((a,b) => number(b.exit_ms) - number(a.exit_ms));
  const parts = selectedDay.split('-');
  setText('selected-date', 'Ngày ' + parts[2] + '/' + parts[1] + '/' + parts[0]);
  setText('history-count', rows.length + ' lệnh');
  setText('day-wins', rows.filter(r=>winOrLoss(r)==='TP').length);
  setText('day-losses', rows.filter(r=>winOrLoss(r)==='SL').length);
  const actual = rows.map(row => number(row.recorded_roi_pct));
  const full = rows.length > 0 && actual.every(Number.isFinite);
  const total = full ? actual.reduce((a,b)=>a+b,0) : null;
  setText('day-roi', roi(total));
  $('day-roi').className = classFor(total);
  if (!rows.length) { emptyRow('history-list', 5, 'Không có lệnh đóng trong ngày này.'); return; }
  const body = $('history-list'); body.replaceChildren();
  for (const row of rows) {
    const tr = document.createElement('tr');
    const side = row.side === 'BUY' ? 'Long' : row.side === 'SELL' ? 'Short' : '—';
    const status = winOrLoss(row);
    const columns = [
      [dateParts(row.exit_ms)?.time || '—', ''],
      [row.combo || '—', ''],
      [side, side === 'Long' ? 'positive' : side === 'Short' ? 'negative' : ''],
      [status, status === 'TP' ? 'positive' : status === 'SL' ? 'negative' : ''],
      [roi(row.recorded_roi_pct), classFor(row.recorded_roi_pct)]
    ];
    for (const [value,cl] of columns) tr.append($text('td', cl, value));
    body.append(tr);
  }
}
function chooseLatestDate() {
  const saved = selectedDays.get(symbol);
  const current = todayKey();
  if (saved && saved >= firstMonth + '-01' && saved <= current) return saved;
  const dates = history.map(r=>dateParts(r.exit_ms)?.key).filter(x=>x && x<=current).sort();
  return dates.length ? dates[dates.length - 1] : current;
}
function setTabs() {
  for (const key of ['BTC','ETH']) {
    const btn = $('tab-' + key.toLowerCase());
    const active = symbol === key + '-USDT';
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
  }
  setText('active-symbol', symbol.split('-')[0]);
}
async function loadSymbol() {
  const token = ++requestId;
  setTabs();
  setText('sync','Đang đọc dữ liệu ' + symbol.split('-')[0] + '…');
  $('status-dot').classList.add('status-muted');
  try {
    const response = await fetch('/api/dashboard?symbol=' + encodeURIComponent(symbol) + '&days=all', {
      cache:'no-store', credentials:'same-origin'
    });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const data = await response.json();
    if (token !== requestId) return;
    renderCombos(data.theory?.combos || []);
    history = (data.theory?.history || []).filter(row =>
      row.symbol === symbol && row.status === 'closed' && valid(row.exit_ms));
    selectedDay = chooseLatestDate();
    monthKey = selectedDay.slice(0, 7);
    renderCalendar();
    renderDay();
    const sync = data.last_sync?.time ? dateParts(Date.parse(data.last_sync.time)) : null;
    const synced = data.last_sync?.status === 'success' && sync &&
      Date.now() - Date.parse(data.last_sync.time) < 2.5 * 3600000;
    $('status-dot').classList.toggle('status-muted', !synced);
    setText('sync', sync ? 'BingX đồng bộ ' + sync.date + ' · ' + sync.time : 'Chưa có giờ đồng bộ');
    setText('updated', 'Xem lúc ' + (dateParts(Date.now())?.time || '—'));
  } catch (error) {
    if (token !== requestId) return;
    history = [];
    emptyRow('combo-list', 6, 'Không đọc được dữ liệu.');
    emptyRow('history-list', 5, 'Không đọc được dữ liệu.');
    setText('combo-count', '—'); setText('history-count','—');
    setText('sync', 'Lỗi kết nối dashboard · ' + error.message);
  }
}
for (const name of ['BTC','ETH']) {
  $('tab-' + name.toLowerCase()).addEventListener('click', () => {
    if (symbol === name + '-USDT') return;
    symbol = name + '-USDT';
    loadSymbol();
  });
}
$('prev-month').addEventListener('click', () => {
  if (monthKey > firstMonth) { monthKey = monthShift(monthKey,-1); renderCalendar(); }
});
$('next-month').addEventListener('click', () => {
  if (monthKey < todayKey().slice(0,7)) { monthKey = monthShift(monthKey,1); renderCalendar(); }
});
loadSymbol();
