"use strict";
const $ = (id) => document.getElementById(id);
const state = { csrf: "", reviews: [], threads: [], summary: [], selectedEvent: null,
  currentThread: null, filter: "all", busy: false, postReady: false,
  diagnosticsOpen: false, diagnosticsBusy: false };
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
  const changingData = !!opts.method && opts.method.toUpperCase() !== "GET";
  for (let attempt = 0; attempt < (changingData ? 2 : 1); attempt++) {
    if (changingData) {
      // The CSRF token is tied to the current login cookie. Bootstrap it
      // directly from the signed session, never from a Supabase data read.
      const session = await api("/ai/api/session");
      if (!session.csrf) throw new Error("Không nhận được CSRF token. Hãy đăng nhập lại.");
      state.csrf = session.csrf;
      headers["X-AI-CSRF"] = session.csrf;
    }
    opts.headers = headers;
    const response = await fetch(path, opts);
    let data;
    try { data = await response.json(); } catch (_e) { data = {}; }
    if (response.status === 401) {
      window.location.replace("/ai");
      throw new Error("Phiên đăng nhập đã hết hạn");
    }
    if (response.status === 403 && changingData && attempt === 0 &&
        data.detail === "CSRF token không hợp lệ") {
      // Another tab may have rotated the session between GET and POST.
      // Refresh once and retry the SAME idempotent request_id.
      continue;
    }
    if (!response.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : "Không thể kết nối AI Analyst (" + response.status + ")");
    }
    return data;
  }
  throw new Error("Phiên đăng nhập thay đổi. Hãy tải lại trang.");
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
function bingxEvidenceText(p) {
  if (!p || p.status === "SOURCE_UNAVAILABLE") return "Chưa đọc được kết quả BingX";
  if (p.quality === "BINGX_VERIFIED_EXIT") return "BingX · Exit đã xác minh";
  if (p.quality === "BINGX_VERIFIED_EXIT_ESTIMATED_MONEY") return "BingX · Exit xác minh, ROI/PnL ước tính";
  if (p.quality === "MERGED_POSITION_ESTIMATE") return "BingX · Vị thế gộp, kết quả ước tính";
  if (p.quality === "MANUAL_HISTORICAL_RECORD") return "Lịch sử · kết quả nhập trước";
  if (p.status === "NO_CONFIRMED_ENTRY_LINK") return "Chưa xác nhận được Entry trên BingX";
  if (p.status === "ENTRY_LINKED_RESULT_UNAVAILABLE") return "Entry có trên BingX · thiếu kết quả";
  return "Chưa đối soát được lệnh";
}
function renderMetrics() {
  const rows = state.summary;
  $("metric-total").textContent = rows.reduce((a, r) => a + Number(r.signals || 0), 0);
  $("metric-allow").textContent = rows.reduce((a, r) => a + Number(r.allow_count || 0), 0);
  $("metric-block").textContent = rows.reduce((a, r) => a + Number(r.block_count || 0), 0);
  $("metric-post").textContent = state.reviews.filter(r => r.post_revision != null || ["CLOSED", "ESTIMATED_CLOSED"].includes((r.bingx_post || {}).status)).length;
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
    if (r.post_learning && r.post_learning.analysis) {
      const review = r.post_learning;
      b.append(create("span", "review-meta", "✓ Gemini đã phân tích sau EXIT · " +
        (review.assessment === "INCONCLUSIVE" ? "Chưa đủ bằng chứng" :
         review.assessment === "CORRECT" ? "Đúng theo kết quả" : "Sai theo kết quả")));
      b.append(create("span", "review-detail",
        String(review.analysis.explanation || "").slice(0, 140)));
    } else if (r.post_revision != null) {
      b.append(create("span", "review-meta", "✓ Có báo cáo hậu giao dịch"));
    } else {
      b.append(create("span", "review-meta", bingxEvidenceText(r.bingx_post)));
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
    (r.post_learning ? " · Gemini đã lưu phân tích sau EXIT" :
     r.post_revision != null ? " · Có báo cáo sau Exit" :
     " · " + bingxEvidenceText(r.bingx_post));
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
    // This lightweight endpoint requires login but does NOT query Supabase.
    // Chat remains able to report the real database failure when history fails.
    const session = await api("/ai/api/session");
    state.csrf = session.csrf;
    const data = await api("/ai/api/state");
    state.reviews = data.reviews || [];
    state.threads = data.threads || [];
    state.summary = data.summary || [];
    state.postReady = !!data.post_ready;
    renderMetrics(); renderReviews(); renderThreads(); renderSelection();
    $("assistant-status").textContent = data.model_configured
      ? "Gemini API key đã cấu hình · Chưa xác minh kết nối" : "Chưa cấu hình GEMINI_API_KEY";
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

function paintDiagnostic(name, value) {
  const status = $("diag-" + name + "-status");
  const detail = $("diag-" + name + "-detail");
  const kind = value && value.status || "error";
  const label = {
    ok: "Hoạt động", error: "Có lỗi", warning: "Cảnh báo",
    configured: "Đã cấu hình", checking: "Đang thử"
  }[kind] || "Chưa kiểm tra";
  status.textContent = label;
  status.className = "diag-state " + (["ok", "error", "warning", "configured", "checking"].includes(kind) ? kind : "");
  const code = value && value.code ? " [" + value.code + "]" : "";
  const http = value && value.http_status ? " · HTTP " + value.http_status : "";
  const latency = value && value.latency_ms !== undefined ? " · " + value.latency_ms + "ms" : "";
  const model = value && value.model && name === "gemini" ? " · Model: " + value.model : "";
  const counts = value && name === "db" && value.status === "ok"
    ? " · " + value.reviews + " nhận định · " + value.chat_messages + " tin chat" : "";
  detail.textContent = String(value && value.detail || "Chưa có dữ liệu") + code + http + latency + model + counts;
}

async function loadDiagnostics() {
  if (state.diagnosticsBusy || !state.diagnosticsOpen) return;
  state.diagnosticsBusy = true;
  $("diagnostics-refresh").disabled = true;
  $("diagnostics-message").textContent = "Đang kiểm tra cấu hình và kết nối PostgreSQL...";
  try {
    const data = await api("/ai/api/diagnostics");
    paintDiagnostic("db", data.database);
    paintDiagnostic("gemini", data.gemini);
    paintDiagnostic("btc", data.discord && data.discord.BTC);
    paintDiagnostic("eth", data.discord && data.discord.ETH);
    $("diagnostics-message").textContent =
      "Đây chỉ là trạng thái cấu hình. Bấm 'Gọi thử' / 'Gửi tin thử' mới kiểm tra kết nối thực tế.";
  } catch (err) {
    $("diagnostics-message").textContent = "Lỗi chẩn đoán: " + err.message;
  } finally {
    state.diagnosticsBusy = false;
    $("diagnostics-refresh").disabled = false;
  }
}

async function runDiagnostic(name, path) {
  const button = $("diag-test-" + name);
  if (button.disabled) return;
  button.disabled = true;
  paintDiagnostic(name, {status: "checking", detail: "Đang thực hiện bài thử..."});
  try {
    // This POST always gets its own cookie-bound CSRF token via api().
    // No exchange endpoint or user-supplied destination is involved.
    const result = await api(path, {method: "POST"});
    paintDiagnostic(name, result);
    $("diagnostics-message").textContent = result.status === "ok"
      ? "Bài thử hoàn thành. Kiểm tra đúng kênh nếu vừa gửi Discord."
      : "Bài thử trả lỗi đã phân loại; không lộ API key/webhook.";
  } catch (err) {
    paintDiagnostic(name, {status: "error", code: "REQUEST_FAILED", detail: err.message});
    $("diagnostics-message").textContent = "Không thể hoàn thành bài thử.";
  } finally {
    button.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("diagnostics-button").addEventListener("click", () => {
    state.diagnosticsOpen = !state.diagnosticsOpen;
    $("diagnostics-panel").classList.toggle("hidden", !state.diagnosticsOpen);
    $("diagnostics-button").setAttribute("aria-expanded", String(state.diagnosticsOpen));
    if (state.diagnosticsOpen) loadDiagnostics();
  });
  $("diagnostics-refresh").addEventListener("click", loadDiagnostics);
  $("diag-test-gemini").addEventListener("click", () => runDiagnostic("gemini", "/ai/api/diagnostics/gemini"));
  $("diag-test-btc").addEventListener("click", () => runDiagnostic("btc", "/ai/api/diagnostics/discord/BTC"));
  $("diag-test-eth").addEventListener("click", () => runDiagnostic("eth", "/ai/api/diagnostics/discord/ETH"));
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
