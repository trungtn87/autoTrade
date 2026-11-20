"use strict";
// Renders the V3 Live Terminal payload. Every value goes through textContent: no HTML from data.
(function () {
  const $ = (id) => document.getElementById(id);
  const fmtNum = (v, d = 4) => (v === null || v === undefined || Number.isNaN(Number(v))) ? "—" : Number(v).toFixed(d);
  const fmtTime = (ms) => (ms ? new Date(ms).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }) : "—");
  const fmtPnl = (v) => (v === null || v === undefined ? "—" : (Number(v) >= 0 ? "+" : "") + Number(v).toFixed(2));

  function cell(tr, text, cls) {
    const td = document.createElement("td");
    if (cls) td.className = cls;
    if (cls && cls.indexOf("pos") >= 0) td.classList.add("pos");
    td.textContent = text;
    tr.appendChild(td);
  }
  function fill(tableId, rows, render) {
    const body = document.querySelector("#" + tableId + " tbody");
    body.replaceChildren();
    if (!rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = document.querySelectorAll("#" + tableId + " thead th").length;
      td.className = "empty";
      td.textContent = "Chưa có dữ liệu";
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    rows.forEach((r) => { const tr = document.createElement("tr"); render(tr, r); body.appendChild(tr); });
  }
  function card(label, value, cls) {
    const el = document.createElement("div");
    el.className = "card";
    const k = document.createElement("div"); k.className = "k"; k.textContent = label;
    const v = document.createElement("div"); v.className = "v" + (cls ? " " + cls : ""); v.textContent = value;
    el.append(k, v);
    return el;
  }
  function pnlClass(v) { return Number(v) > 0 ? "pos" : Number(v) < 0 ? "neg" : ""; }

  function render(data) {
    const s = data.summary;
    const cards = $("cards"); cards.replaceChildren();
    cards.append(
      card("Net PnL", fmtPnl(s.net_pnl), pnlClass(s.net_pnl)),
      card("Lệnh đã đóng", String(s.closed_trades)),
      card("Thắng / Thua", s.wins + " / " + s.losses),
      card("Tỉ lệ thắng", s.win_rate_pct === null ? "—" : fmtNum(s.win_rate_pct, 1) + " %"),
      card("ROI TB", s.avg_roi_pct === null ? "—" : fmtNum(s.avg_roi_pct, 2) + " %"),
      card("Đang mở", String(s.open_positions)),
      card("Tín hiệu (cửa sổ)", String(s.signals_in_window)),
      card("Lỗi 24h", String(s.errors_last_24h), s.errors_last_24h ? "neg" : "")
    );
    fill("combo-table", data.by_combo, (tr, r) => {
      cell(tr, r.symbol); cell(tr, r.combo); cell(tr, String(r.trades), "num");
      cell(tr, r.win_rate_pct === null ? "—" : fmtNum(r.win_rate_pct, 1), "num");
      cell(tr, fmtPnl(r.net_pnl), "num " + pnlClass(r.net_pnl));
    });
    fill("open-table", data.open_positions, (tr, r) => {
      cell(tr, r.symbol); cell(tr, r.combo); cell(tr, r.side);
      cell(tr, fmtNum(r.avg_price, 4), "num"); cell(tr, fmtNum(r.sl, 4), "num"); cell(tr, fmtNum(r.tp, 4), "num");
    });
    fill("trade-table", data.trades, (tr, r) => {
      cell(tr, fmtTime(r.exit_ms)); cell(tr, r.symbol); cell(tr, r.combo); cell(tr, r.side);
      cell(tr, r.close_type || "—");
      cell(tr, fmtPnl(r.net_pnl), "num " + pnlClass(r.net_pnl));
      cell(tr, r.roi_pct === null || r.roi_pct === undefined ? "—" : fmtNum(r.roi_pct, 2), "num");
    });
    fill("signal-table", data.signals, (tr, r) => {
      cell(tr, fmtTime(r.signal_ms)); cell(tr, r.symbol); cell(tr, r.combo); cell(tr, r.side);
      cell(tr, fmtNum(r.entry, 4), "num"); cell(tr, fmtNum(r.tp, 4), "num"); cell(tr, fmtNum(r.sl, 4), "num");
    });

    const health = $("health"); health.replaceChildren();
    data.health.candles.forEach((c) => {
      const item = document.createElement("div"); item.className = "item";
      item.textContent = "Nến 15m " + c.symbol + " mới nhất: " + fmtTime(c.last_close_ms);
      health.appendChild(item);
    });
    data.health.errors_last_24h.slice(0, 20).forEach((e) => {
      const item = document.createElement("div"); item.className = "item";
      const title = document.createElement("div");
      title.textContent = "[" + e.layer + "] " + (e.error_code || "") + " " + (e.symbol || "");
      const meta = document.createElement("div"); meta.className = "meta";
      meta.textContent = fmtTime(e.time_ms) + " · " + (e.message || "") + (e.discord_status ? " · Discord: " + e.discord_status : "");
      item.append(title, meta);
      health.appendChild(item);
    });
    if (!data.health.errors_last_24h.length) {
      const item = document.createElement("div"); item.className = "item";
      item.textContent = "Không có lỗi trong 24 giờ qua.";
      health.appendChild(item);
    }
  }

  async function load() {
    const status = $("status");
    const params = new URLSearchParams({
      symbol: $("symbol").value.trim(), combo: $("combo").value.trim(), days: $("days").value,
    });
    status.textContent = "Đang tải…";
    try {
      const res = await fetch("/api/v3/dashboard?" + params.toString(), { cache: "no-store", credentials: "same-origin" });
      const data = await res.json();
      if (!res.ok) { status.textContent = data.error || "Lỗi tải dữ liệu"; return; }
      render(data);
      status.textContent = "Cập nhật: " + fmtTime(data.generated_ms);
    } catch (err) {
      status.textContent = "Không kết nối được máy chủ.";
    }
  }
  $("apply").addEventListener("click", load);
  load();
  setInterval(load, 60000);
})();
