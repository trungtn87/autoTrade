// Browser-independent regression: a failed Supabase state read must not leave
// chat without a CSRF token. No real network, credentials, or exchange orders.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const clientJs = fs.readFileSync(
  path.join(__dirname, "../autotrade_v2/app/ai/static/app.js"), "utf8"
);
const calls = [];
let sessionCount = 0;
let postCount = 0;
function response(status, payload) {
  return {status, ok: status >= 200 && status < 300, json: async () => payload};
}
const sandbox = {
  document: {addEventListener: () => {}},
  window: {location: {replace: () => {throw new Error("Unexpected login redirect");}}},
  fetch: async (url, opts) => {
    calls.push({url, opts});
    assert.equal(opts.credentials, "same-origin");
    assert.equal(opts.cache, "no-store");
    if (url === "/ai/api/state") {
      return response(503, {detail: "Không thể đọc dữ liệu Supabase"});
    }
    if (url === "/ai/api/session") {
      sessionCount++;
      return response(200, {csrf: "fresh-token-" + sessionCount});
    }
    if (url === "/ai/api/chat") {
      postCount++;
      assert.equal(opts.headers["X-AI-CSRF"], "fresh-token-" + postCount);
      assert.equal(opts.method, "POST");
      assert.equal(opts.body, JSON.stringify({request_id:"same-id"}));
      if (postCount === 1) {
        return response(403, {detail: "CSRF token không hợp lệ"});
      }
      return response(200, {reply: "Đã nhận câu hỏi", session_id: "id-1"});
    }
    throw new Error("Unexpected request " + url);
  },
};
vm.createContext(sandbox);
vm.runInContext(clientJs, sandbox, {filename: "ai-app.js"});

(async () => {
  await assert.rejects(
    vm.runInContext('api("/ai/api/state")', sandbox),
    /Không thể đọc dữ liệu Supabase/
  );
  // Even without state.csrf, chat bootstraps a valid session token separately.
  const result = await vm.runInContext(
    'api("/ai/api/chat", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({request_id:"same-id"})})',
    sandbox
  );
  assert.equal(result.reply, "Đã nhận câu hỏi");
  assert.equal(postCount, 2);
  assert.equal(sessionCount, 2);
  assert.deepEqual(
    calls.map(x => x.url),
    ["/ai/api/state", "/ai/api/session", "/ai/api/chat", "/ai/api/session", "/ai/api/chat"]
  );
  console.log("PASS AI Analyst: DB failure reveals real error; CSRF session bootstrap and one retry");
})().catch(error => {console.error(error); process.exitCode = 1;});
