// 页面自行带请求头发起业务请求并解析响应流分帧（ADR-002）。
// 凭据只保存在 sessionStorage：离开标签页即失效，页面跳转与静态资源一律不附带认证头。
"use strict";

const KEY_NAME = "mcpbot.key";
const $ = (id) => document.getElementById(id);

const ui = {
  bar: $("stage"), login: $("login"), thread: $("thread"), composer: $("composer"),
  keyInput: $("key-input"), keySubmit: $("key-submit"), loginHint: $("login-hint"),
  draft: $("draft"), send: $("send"), logout: $("logout"), earlier: $("load-earlier"),
};

const state = { cursor: null, boundary: null, busy: false, current: null };

function key() { return sessionStorage.getItem(KEY_NAME) || ""; }

function showLocked() {
  ui.login.hidden = false;
  ui.thread.hidden = true;
  ui.composer.hidden = true;
  ui.logout.hidden = true;
  ui.earlier.hidden = true;
  setStage("未连接");
}

function showUnlocked() {
  ui.login.hidden = true;
  ui.thread.hidden = false;
  ui.composer.hidden = false;
  ui.logout.hidden = false;
}

function setStage(text, bad) {
  ui.bar.textContent = text;
  ui.bar.classList.toggle("warn", Boolean(bad));
}

// 业务请求的唯一出口：只有这里附加 Authorization。
async function api(path, options) {
  const init = Object.assign({}, options || {});
  init.headers = Object.assign({}, init.headers, { Authorization: "Bearer " + key() });
  const response = await fetch(path, init);
  if (response.status === 401) {
    // 本站 401：清理本标签页凭据并回到锁定视图
    sessionStorage.removeItem(KEY_NAME);
    showLocked();
    ui.loginHint.textContent = "凭据校验未通过，已清除本站凭据";
    throw new Error("unauthorized");
  }
  return response;
}

function turnNode(role, text) {
  const node = document.createElement("div");
  node.className = "turn " + role;
  const body = document.createElement("div");
  body.textContent = text;
  const meta = document.createElement("div");
  meta.className = "meta muted";
  node.append(body, meta);
  ui.thread.append(node);
  ui.thread.scrollTop = ui.thread.scrollHeight;
  return { node, body, meta };
}

function setMeta(turn, text, bad) {
  turn.meta.textContent = text;
  turn.meta.classList.toggle("bad", Boolean(bad));
}

// ---------- 事件帧消费（IF-003） ----------

function renderEvent(evt) {
  const turn = state.current;
  if (!turn) return;
  switch (evt.kind) {
    case "接纳":
      setMeta(turn.user, "已保存并确认");
      break;
    case "阶段":
      setStage(evt.stage + (evt.reason ? "：" + evt.reason : ""));
      break;
    case "回答增量":
      state.assistant = state.assistant || turnNode("assistant", "");
      state.assistant.body.textContent = evt.text;
      setMeta(state.assistant, evt.complete ? "回答完整" : "回答未完成")
      if (evt.saved_confirmed === false) setMeta(state.assistant, "回答未确认保存", true);
      break;
    case "故障":
      setStage("故障：" + evt.category, true);
      setMeta(turn.user, "故障：" + evt.boundary + " · " + evt.detail, true);
      break;
    case "执行结束":
      setStage("已结束：" + evt.outcome + (evt.reason ? " · " + evt.reason : ""), evt.outcome !== "完成");
      // 完整性事实保留在最后一条可见标注上，不被结局覆盖（DOM-002）
      setMeta(state.assistant || turn.user,
        "执行结束：" + evt.outcome + " · " + evt.reason +
        " · " + (evt.answer_complete ? "回答完整" : "回答未完成") +
        (evt.saved_confirmed ? " · 已确认保存" : " · 结束记录未确认保存"),
        evt.outcome !== "完成" || evt.saved_confirmed === false);
      break;
  }
}

async function consume(response) {
  if (!response.body) {
    const payload = await response.json();
    applySnapshot(payload.snapshot);
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  for (;;) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });
    let cut;
    // ndjson：每行一条事件，未换行的尾部留在缓冲里等下一块
    while ((cut = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, cut).trim();
      buffer = buffer.slice(cut + 1);
      if (!line) continue;
      try {
        renderEvent(JSON.parse(line));
      } catch (error) {
        setStage("事件帧解析失败", true);
      }
    }
  }
}

function applySnapshot(snapshot) {
  // IF-002 同标识重复提交：按一次性快照呈现已保存状态，不创建新执行
  if (!snapshot) return;
  for (const message of snapshot.messages || []) {
    const turn = turnNode(message.role, message.text);
    const parts = [];
    // 完整性只对助手内容成立（DOM-002）；用户消息只报保存事实
    if (message.role === "assistant") parts.push(message.complete ? "回答完整" : "回答未完成");
    parts.push(message.saved ? "已保存" : "未确认保存");
    setMeta(turn, parts.join(" · "), !message.saved);
  }
  setStage("快照：" + (snapshot.lifecycle || "") + (snapshot.outcome ? " · " + snapshot.outcome : ""));
}

// ---------- 发送 ----------

async function send() {
  const text = ui.draft.value.trim();
  if (!text || state.busy) return;
  state.busy = true;
  ui.send.disabled = true;
  state.assistant = null;
  state.current = { user: turnNode("user", text) };
  ui.draft.value = "";
  setStage("接纳中");
  try {
    const response = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, request_id: crypto.randomUUID() }),
    });
    if (!response.ok) {
      const detail = (await response.json().catch(() => ({}))).detail || "请求被拒绝";
      const retry = response.headers.get("Retry-After");
      setStage(detail + (retry ? "（约 " + retry + " 秒后重试）" : ""), true);
      setMeta(state.current.user, detail, true);
    } else {
      await consume(response);
    }
  } catch (error) {
    if (error.message !== "unauthorized") setStage("请求未完成：" + error.message, true);
  } finally {
    state.busy = false;
    ui.send.disabled = false;
    state.current = null;
    state.assistant = null;
  }
}

// ---------- 历史（IF-004 映射 IF-009） ----------

async function loadEarlier() {
  const params = new URLSearchParams({ limit: "20" });
  if (state.cursor) params.set("cursor", state.cursor);
  const response = await api("/api/history?" + params.toString());
  if (!response.ok) {
    setStage("历史读取失败", true);
    return;
  }
  const payload = await response.json();
  state.cursor = payload.cursor;
  state.boundary = payload.boundary;
  const fragment = document.createDocumentFragment();
  for (const execution of payload.items || []) {
    for (const message of execution.messages || []) {
      const turn = turnNode(message.role, message.text);
      const parts = [];
      if (execution.outcome) parts.push("执行：" + execution.outcome);
      // 完整性只对助手内容成立（DOM-002）；用户消息只报保存事实
      if (message.role === "assistant") parts.push(message.complete ? "回答完整" : "回答未完成");
      if (message.saved === false) parts.push("未确认保存");
      setMeta(turn, parts.join(" · ") || "已保存", message.saved === false);
    }
  }
  ui.thread.prepend(fragment);
  ui.earlier.hidden = state.boundary === "已到最早记录";
  if (state.boundary === "部分历史") setStage("部分历史：更早记录可继续加载");
}

// ---------- 认证（IF-001） ----------

async function checkKey() {
  const candidate = ui.keyInput.value.trim();
  if (!candidate) {
    ui.loginHint.textContent = "请先输入本站凭据";
    return;
  }
  sessionStorage.setItem(KEY_NAME, candidate);
  ui.keyInput.value = "";
  try {
    const response = await api("/api/auth/check", { method: "POST" });
    if (response.ok) {
      showUnlocked();
      ui.loginHint.textContent = "";
      setStage("就绪");
      if (!ui.thread.childElementCount) await loadEarlier();
      ui.earlier.hidden = state.boundary === "已到最早记录";
    } else if (response.status === 429) {
      const payload = await response.json().catch(() => ({}));
      ui.loginHint.textContent = "校验尝试过于频繁，约 " + (payload.retry_after_seconds || 60) + " 秒后重试";
    } else {
      const payload = await response.json().catch(() => ({}));
      ui.loginHint.textContent = payload.detail || "凭据校验未通过";
    }
  } catch (error) {
    if (error.message !== "unauthorized") ui.loginHint.textContent = "校验请求未完成：" + error.message;
  }
}

ui.keySubmit.addEventListener("click", checkKey);
ui.keyInput.addEventListener("keydown", (event) => { if (event.key === "Enter") checkKey(); });
ui.send.addEventListener("click", send);
ui.draft.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); send(); }
});
ui.logout.addEventListener("click", () => { sessionStorage.removeItem(KEY_NAME); showLocked(); });
ui.earlier.addEventListener("click", loadEarlier);

if (key()) {
  // 凭据只在同一标签页内复用；仍逐次由服务端校验，不在本地认定有效
  checkKey();
} else {
  showLocked();
}
