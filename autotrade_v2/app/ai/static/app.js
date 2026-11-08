"use strict";
const $ = (id) => document.getElementById(id);
const state = { csrf: "", reviews: [], threads: [], summary: [], selectedEvent: null,
  currentThread: null, filter: "all", busy: false, postReady: false };
function create(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = String(text);
  return el;
}
function displayNotice(message, error) {
  const el = $("notice");
  el.textContent = message || "";
  el.classList.toggle("hidden", !message);
  el.classList.toggle("error", !!error);
}
async function api(path, options) {
  const opts = Object.assign({credentials: "same-origin", cache: "no-store"}, options || {});
  const headers = Object.assign({"Accept": "application/json"}, opts.headers || {});
  if (opts.method && opts.method !== "GET") headers["X-AI-CSRF"] = state.csrf;
  opts.headers = headers;
  const response = await fetch(path, opts);
  let data;
  try { data = await response.json(); } catch (_e) { data = {}; }
  if (response.status === 401) { window.location.replace("/ai"); throw new Error("Phiên đăng nhập đã hết hạn"); }
  if (!response.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : "Không thể kết nối AI Analyst (" + response.status + ")");
  }
  return data;
}
function dateVN(value) {
  try {
    const date = typeof value === "number" ? new Date(value) : new Date(value);
    if (!Number.isFinite(date.getTime())) return "-";
    return new Intl.DateTimeFormat("vi-VN", {timeZone:"Asia/Ho_Chi_Minh",
      day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}).format(date);
  } catch (_e) { return "-"; }
}
function comboLabel(label) { return String(label || "-"); }
function verdictClass(label) {
  return label === "ALLOW" ? "allow" : label === "BLOCK" ? "block" : "unsure";
}
function verdictText(label) {
  return label === "ALLOW" ? "ALLOW" : label === "BLOCK" ? "BLOCK" : "UNSURE";
}
function renderMetrics() {
  const rows = state.reviews;
  $("metric-total").textContent = rows.length;
  $("metric-allow").textContent = rows.filter(r => r.ai_decision === "ALLOW").length;
  $("metric-block").textContent = rows.filter(r => r.ai_decision === "BLOCK").length;
  $("metric-post").textContent = rows.filter(r => r.post_revision !== null).length;
}
function renderReviews() {
  const parent = $("review-list"); parent.replaceChildren();
  const rows = state.reviews.filter(r => state.filter === "all" || r.symbol === state.filter);
  if (!rows.length) {
    parent.append(create("p", "empty", "Chưa có nhận định Gemini. Khi engine phát tín hiệu và Gemini được kích hoạt, kết quả sẽ hiện ở đây."));
    return;
  }
  for (const r of rows) {
    const b = create("button", "review-item" + (state.selectedEvent === r.event_id ? " active" : ""));
    b.type = "button";
    const top = create("div", "review-top");
    top.append(create("b", null, (r.symbol === "BTC-USDT" ? "BTC" : "ETH") + " · " + comboLabel(r.combo)));
    top.append(create("span", "tag " + verdictClass(r.ai_decision), verdictText(r.ai_decision)));
    b.append(top);
    b.append(create("span", "review-meta", (r.side === "BUY" ? "LONG" : "SHORT") + " · " + dateVN(r.signal_close_ms)));
    const reason = r.output_json && (r.output_json.reasoning_ai_opinion || r.output_json.reasoning_verified);
    b.append(create("span", "review-detail", reason ? String(reason).slice(0,115) : "Nhận định chưa có phân tích chi tiết"));
    if (r.post_revision !== null) {
      b.append(create("span", "review-meta", "✓ Có báo cáo sau đóng lệnh"));
    }
    b.addEventListener("click", () => selectReview(r));
    parent.append(b);
  }
}
function renderThreads() {
  const parent = $("thread-list"); parent.replaceChildren();
  if (!state.threads.length) { parent.append(create("p","empty","Chưa có hội thoại.")); return; }
  for (const t of state.threads) {
    const b = create("button", "thread-item" + (state.currentThread === t.id ? " active" : ""),
      (t.title || "Hội thoại") + " · " + dateVN(t.updated_at));
    b.type = "button";
    b.addEventListener("click", () => openThread(t.id));
    parent.append(b);
  }
}
function renderSelection() {
  const el = $("selected-trade");
  const r = state.reviews.find(x => x.event_id === state.selectedEvent);
  if (!r) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  $("selected-title").textContent = r.symbol + " · " + r.combo + " · " +
    (r.side === "BUY" ? "LONG" : "SHORT") + " · " + verdictText(r.ai_decision);
  $("selected-subtitle").textContent = dateVN(r.signal_close_ms) +
    (r.post_revision === null ? " · Chưa có kết quả đóng được xác minh" : " · Có báo cáo sau Exit");
}
function welcome() {
  const parent = $("messages"); parent.replaceChildren();
  const content = create("div", "welcome");
  content.append(create("span", "welcome-icon", "✦"));
  content.append(create("h2", null, state.selectedEvent ? "Đang xem một tín hiệu" : "Chào mừng tới AI Analyst"));
  content.append(create("p", null, state.selectedEvent
    ? "Hỏi Gemini tại sao đã giả định ALLOW/BLOCK cho tín hiệu này. Nếu có kết quả đã xác minh, AI sẽ so sánh lại."
    : "Chọn nhận định ở bên trái hoặc hỏi Gemini. Câu trả lời được đối chiếu với dữ liệu Supabase, không can thiệp giao dịch."));
  const suggestions = create("div", "suggestions");
  const suggestionsText = state.selectedEvent ?
    ["Tại sao bạn đưa ra nhận định này?", "SMC khung 15m, 1h và 4h có đồng thuận không?", "Có kết quả đóng để đối chiếu dự đoán chưa?"] :
    ["Những tín hiệu Gemini đã nhận định gần đây?", "Tổng hợp các combo bị cảnh báo rủi ro", "Có đủ dữ liệu để đánh giá AI đúng hay sai chưa?"];
  for (const q of suggestionsText) {
    const b = create("button", null, q); b.type = "button";
    b.addEventListener("click", () => { $("chat-input").value = q; $("chat-input").focus(); });
    suggestions.append(b);
  }
  content.append(suggestions); parent.append(content);
}
function selectReview(r) {
  state.selectedEvent = r.event_id;
  state.currentThread = null;
  renderSelection(); renderReviews(); renderThreads(); welcome();
  $("sidebar").classList.remove("open");
  $("chat-input").focus();
}
function addBubble(role, text, pending) {
  const b = create("div", "bubble " + role + (pending ? " pending" : ""));
  b.append(create("span","who",role === "user" ? "BẠN" : "GEMINI ANALYST"));
  b.append(document.createTextNode(text));
  $("messages").append(b);
  $("messages").scrollTop = $("messages").scrollHeight;
  return b;
}
async function openThread(id) {
  if (state.busy) return;
  try {
    const data = await api("/ai/api/thread/" + encodeURIComponent(id));
    state.currentThread = id;
    state.selectedEvent = data.thread.selected_event_id || null;
    renderSelection(); renderReviews(); renderThreads();
    $("messages").replaceChildren();
    if (!data.messages.length) welcome();
    else for (const m of data.messages) addBubble(m.role === "user" ? "user" : "assistant", m.content);
    $("sidebar").classList.remove("open");
  } catch (err) { displayNotice(err.message, true); }
}
async function refreshState(showNotice) {
  try {
    const data = await api("/ai/api/state");
    state.csrf = data.csrf;
    state.reviews = data.reviews || [];
    state.threads = data.threads || [];
    state.summary = data.summary || [];
    state.postReady = !!data.post_ready;
    renderMetrics(); renderReviews(); renderThreads(); renderSelection();
    $("assistant-status").textContent = data.model_configured
      ? "Gemini API sẵn sàng · Chỉ quan sát" : "Chưa cấu hình GEMINI_API_KEY";
    if (!data.model_configured) displayNotice("Gemini API key chưa được cấu hình trên VPS.", true);
    else if (!data.post_ready) displayNotice("Đã có trang chat; phần phân tích sau Exit chỉ chạy khi dữ liệu CLOSED được đối soát.", false);
    else if (showNotice) displayNotice("Đã cập nhật dữ liệu Supabase.", false);
  } catch (err) { displayNotice(err.message, true); }
}
async function sendQuestion(event) {
  event.preventDefault();
  if (state.busy) return;
  const message = $("chat-input").value.trim();
  if (!message) return;
  state.busy = true; $("send-button").disabled = true; $("chat-input").disabled = true;
  if ($("messages").querySelector(".welcome")) $("messages").replaceChildren();
  addBubble("user", message);
  const waiting = addBubble("assistant", "Đang đối chiếu dữ liệu và phân tích...", true);
  try {
    const payload = {
      message: message,
      session_id: state.currentThread,
      event_id: state.currentThread ? null : state.selectedEvent,
      request_id: crypto.randomUUID()
    };
    const data = await api("/ai/api/chat", {
      method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)
    });
    state.currentThread = data.session_id;
    waiting.replaceChildren();
    waiting.append(create("span", "who", "GEMINI ANALYST"));
    waiting.append(document.createTextNode(data.reply || "Không có câu trả lời."));
    waiting.classList.remove("pending");
    $("chat-input").value = "";
    await refreshState(false);
    $("messages").scrollTop = $("messages").scrollHeight;
  } catch (err) {
    waiting.replaceChildren();
    waiting.append(create("span", "who", "LỖI KẾT NỐI"));
    waiting.append(document.createTextNode(err.message));
    waiting.classList.remove("pending");
    displayNotice(err.message, true);
  } finally { state.busy = false; $("send-button").disabled = false; $("chat-input").disabled = false; $("chat-input").focus(); }
}
document.addEventListener("DOMContentLoaded", () => {
  $("chat-form").addEventListener("submit", sendQuestion);
  $("chat-input").addEventListener("keydown", event => {
    if (event.key === "Enter" && event.ctrlKey) { event.preventDefault(); $("chat-form").requestSubmit(); }
  });
  $("refresh-button").addEventListener("click", () => refreshState(true));
  $("new-chat").addEventListener("click", () => {
    if (state.busy) return;
    state.currentThread = null; state.selectedEvent = null;
    renderSelection(); renderReviews(); renderThreads(); welcome(); $("sidebar").classList.remove("open");
    $("chat-input").focus();
  });
  $("clear-selection").addEventListener("click", () => {
    if (state.busy) return;
    state.selectedEvent = null; state.currentThread = null;
    renderSelection(); renderReviews(); renderThreads(); welcome();
  });
  $("mobile-sidebar").addEventListener("click", () => $("sidebar").classList.toggle("open"));
  for (const b of document.querySelectorAll("[data-filter]")) {
    b.addEventListener("click", () => {
      state.filter = b.dataset.filter;
      for (const other of document.querySelectorAll("[data-filter]")) other.classList.toggle("active", other === b);
      renderReviews();
    });
  }
  $("logout-button").addEventListener("click", async () => {
    try { await api("/ai/logout",{method:"POST"}); window.location.replace("/ai"); }
    catch (err) { displayNotice(err.message,true); }
  });
  welcome();
  refreshState(false);
});
