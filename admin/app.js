/* CodeLabelStudio 管理后台前端逻辑（零依赖，原生 JS） */
"use strict";

const API_BASE = "";  // 同源部署（Nginx 反代 /api/）
const state = {
  token: sessionStorage.getItem("cls_admin_token") || "",
  admin: JSON.parse(sessionStorage.getItem("cls_admin") || "null"),
  users: { page: 1, size: 20, total: 0 },
  orders: { page: 1, size: 20, total: 0 },
  audit: { page: 1, size: 50, total: 0 },
  currentUser: null,
  currentOrder: null,
};

const $ = (id) => document.getElementById(id);

/* ---------------- API ---------------- */
async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;
  let resp;
  try {
    resp = await fetch(API_BASE + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new Error("网络错误：无法连接服务端");
  }
  const text = await resp.text();
  const data = text ? JSON.parse(text) : {};
  if (!resp.ok) {
    throw new Error(data.detail || `请求失败（${resp.status}）`);
  }
  return data;
}

/* ---------------- 工具 ---------------- */
let toastTimer = null;
function toast(msg, type = "") {
  const el = $("toast");
  el.textContent = msg;
  el.className = `toast ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 2600);
}

const STATUS_BADGE = {
  active: "badge-success", trialing: "badge-primary",
  expired: "badge-danger", suspended: "badge-danger",
  past_due: "badge-warning", pending: "badge-warning",
  paid: "badge-success", confirmed: "badge-success",
  failed: "badge-danger", canceled: "badge-muted", refunded: "badge-warning",
  disabled: "badge-danger",
};
const STATUS_TEXT = {
  active: "正常", trialing: "试用中", expired: "已过期",
  suspended: "已暂停", past_due: "逾期", pending: "待确认",
  paid: "已付款", confirmed: "已确认", failed: "失败",
  canceled: "已取消", refunded: "已退款", disabled: "已停用",
};
function badge(status) {
  return `<span class="badge ${STATUS_BADGE[status] || "badge-muted"}">${STATUS_TEXT[status] || status}</span>`;
}
function fmtTime(iso) {
  if (!iso) return "-";
  return iso.replace("T", " ").replace("Z", "").slice(0, 19);
}
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

/* ---------------- 登录 / 登出 ---------------- */
function showLogin() {
  $("app-view").classList.add("hidden");
  $("login-view").classList.remove("hidden");
}
function showApp() {
  $("login-view").classList.add("hidden");
  $("app-view").classList.remove("hidden");
  $("admin-name").textContent = state.admin
    ? `${state.admin.username}（${state.admin.email}）` : "";
  loadStats();
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  $("login-btn").disabled = true;
  try {
    const data = await api("POST", "/api/auth/login", {
      identifier: $("login-identifier").value.trim(),
      password: $("login-password").value,
    });
    state.token = data.access_token;
    state.admin = data.user;
    sessionStorage.setItem("cls_admin_token", state.token);
    sessionStorage.setItem("cls_admin", JSON.stringify(data.user));
    if (!data.user.is_admin) {
      state.token = "";
      sessionStorage.clear();
      throw new Error("该账号不是管理员");
    }
    $("login-password").value = "";
    showApp();
  } catch (err) {
    $("login-error").textContent = err.message;
  } finally {
    $("login-btn").disabled = false;
  }
});

$("logout-btn").addEventListener("click", () => {
  state.token = "";
  state.admin = null;
  sessionStorage.clear();
  showLogin();
});

/* ---------------- 页签 ---------------- */
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "stats") loadStats();
    if (btn.dataset.tab === "users") loadUsers();
    if (btn.dataset.tab === "orders") loadOrders();
    if (btn.dataset.tab === "audit") loadAudit();
  });
});

/* ---------------- 统计 ---------------- */
async function loadStats() {
  try {
    const s = await api("GET", "/api/admin/stats");
    const cards = [
      ["用户总数", s.users_total],
      ["已验证邮箱", s.email_verified],
      ["试用中", s.trialing],
      ["有效订阅", s.active_subscriptions],
      ["过期订阅", s.expired_subscriptions],
      ["待确认订单", s.orders_pending],
      ["已确认订单", s.orders_confirmed],
      ["确认收入（元）", s.confirmed_revenue],
    ];
    $("stats-grid").innerHTML = cards.map(
      ([label, value]) =>
        `<div class="stat-card"><div class="value">${escapeHtml(value)}</div><div class="label">${label}</div></div>`
    ).join("");
    $("stats-time").textContent = `生成时间：${fmtTime(s.generated_at)}`;
  } catch (err) {
    toast(err.message, "error");
  }
}

/* ---------------- 用户 ---------------- */
async function loadUsers() {
  const u = state.users;
  try {
    const data = await api("GET",
      `/api/admin/users?q=${encodeURIComponent($("user-q").value.trim())}` +
      `&status=${encodeURIComponent($("user-status").value)}&page=${u.page}&size=${u.size}`);
    u.total = data.total;
    $("users-page").textContent = `第 ${data.page} 页 / 共 ${Math.max(1, Math.ceil(data.total / data.size))} 页（${data.total} 人）`;
    $("users-tbody").innerHTML = data.users.map((x) => `
      <tr>
        <td>${x.id}</td>
        <td>${escapeHtml(x.username)}</td>
        <td>${escapeHtml(x.email)}</td>
        <td>${x.email_verified ? "✅" : "—"}</td>
        <td>${badge(x.account_status)}</td>
        <td>${badge(x.effective_status || "expired")}</td>
        <td><button class="btn-ghost" onclick="openUser(${x.id})">详情</button></td>
      </tr>`).join("");
  } catch (err) {
    toast(err.message, "error");
  }
}
$("user-search").addEventListener("click", () => { state.users.page = 1; loadUsers(); });
$("users-prev").addEventListener("click", () => {
  if (state.users.page > 1) { state.users.page -= 1; loadUsers(); }
});
$("users-next").addEventListener("click", () => {
  if (state.users.page * state.users.size < state.users.total) {
    state.users.page += 1; loadUsers();
  }
});

window.openUser = async function (userId) {
  try {
    const data = await api("GET", `/api/admin/users/${userId}`);
    state.currentUser = data;
    $("ud-title").textContent = `用户 #${data.user.id} ${data.user.username}`;
    $("ud-body").innerHTML = `
      <dl>
        <dt>邮箱</dt><dd>${escapeHtml(data.user.email)}</dd>
        <dt>账号状态</dt><dd>${badge(data.user.account_status)}</dd>
        <dt>授权状态</dt><dd>${badge(data.effective_status)}</dd>
        <dt>注册时间</dt><dd>${fmtTime(data.user.created_at)}</dd>
      </dl>
      <h3>试用</h3>
      <dl>
        <dt>状态</dt><dd>${data.trial ? badge(data.trial.status) : "未开始"}</dd>
        <dt>到期</dt><dd>${data.trial ? fmtTime(data.trial.expires_at) : "-"}</dd>
        <dt>延期原因</dt><dd>${escapeHtml(data.trial?.extension_reason || "-")}</dd>
      </dl>
      <h3>设备（${data.devices.length}）</h3>
      <dl>${data.devices.map((d) =>
        `<dt>#${d.id}</dt><dd>${escapeHtml(d.device_name || "未命名")} ${badge(d.status)}</dd>`
      ).join("") || "<dt></dt><dd>无</dd>"}</dl>
      <h3>订单（${data.orders.length}）</h3>
      <dl>${data.orders.map((o) =>
        `<dt>${escapeHtml(o.order_no)}</dt><dd>¥${o.amount} ${badge(o.order_status)} ${fmtTime(o.created_at)}</dd>`
      ).join("") || "<dt></dt><dd>无</dd>"}</dl>`;
    $("user-dialog").showModal();
  } catch (err) {
    toast(err.message, "error");
  }
};

$("ud-close").addEventListener("click", () => $("user-dialog").close());

$("ud-suspend").addEventListener("click", async () => {
  const u = state.currentUser?.user;
  if (!u) return;
  const reason = prompt(`停用 ${u.username} 的原因（进入审计日志）：`);
  if (!reason || !reason.trim()) return;
  try {
    await api("POST", `/api/admin/users/${u.id}/suspend`, { reason: reason.trim() });
    toast("已停用", "success");
    $("user-dialog").close();
    loadUsers();
  } catch (err) { toast(err.message, "error"); }
});

$("ud-resume").addEventListener("click", async () => {
  const u = state.currentUser?.user;
  if (!u) return;
  try {
    await api("POST", `/api/admin/users/${u.id}/resume`);
    toast("已恢复", "success");
    $("user-dialog").close();
    loadUsers();
  } catch (err) { toast(err.message, "error"); }
});

$("ud-extend").addEventListener("click", async () => {
  const u = state.currentUser?.user;
  if (!u) return;
  const days = prompt("延长试用天数：", "7");
  if (!days) return;
  const reason = prompt("延长原因（必填）：");
  if (!reason || !reason.trim()) { toast("必须填写原因", "error"); return; }
  try {
    const data = await api("POST", `/api/admin/users/${u.id}/extend-trial`, {
      days: parseInt(days, 10),
      reason: reason.trim(),
    });
    toast(`试用已延长至 ${fmtTime(data.expires_at)}`, "success");
    $("user-dialog").close();
    loadUsers();
  } catch (err) { toast(err.message, "error"); }
});

/* ---------------- 订单 ---------------- */
async function loadOrders() {
  const o = state.orders;
  try {
    const data = await api("GET",
      `/api/admin/orders?status=${encodeURIComponent($("order-status").value)}&page=${o.page}&size=${o.size}`);
    o.total = data.total;
    $("orders-page").textContent =
      `第 ${data.page} 页 / 共 ${Math.max(1, Math.ceil(data.total / data.size))} 页（${data.total} 单）`;
    $("orders-tbody").innerHTML = data.orders.map((x) => `
      <tr>
        <td>${x.id}</td>
        <td>${escapeHtml(x.order_no)}</td>
        <td>${escapeHtml(x.username || `#${x.user_id}`)}</td>
        <td>¥${x.amount}</td>
        <td>${badge(x.order_status)}</td>
        <td>${fmtTime(x.created_at)}</td>
        <td>${x.order_status === "pending"
          ? `<button class="btn" onclick="openPay(${x.id})">确认付款</button>`
          : (x.payment_reference ? `<span class="muted">${escapeHtml(x.payment_reference)}</span>` : "—")}</td>
      </tr>`).join("");
  } catch (err) {
    toast(err.message, "error");
  }
}
$("order-search").addEventListener("click", () => { state.orders.page = 1; loadOrders(); });
$("orders-prev").addEventListener("click", () => {
  if (state.orders.page > 1) { state.orders.page -= 1; loadOrders(); }
});
$("orders-next").addEventListener("click", () => {
  if (state.orders.page * state.orders.size < state.orders.total) {
    state.orders.page += 1; loadOrders();
  }
});

window.openPay = function (orderId) {
  state.currentOrder = orderId;
  $("pay-order-info").textContent = `订单 #${orderId}`;
  $("pay-reason").value = "";
  $("pay-error").textContent = "";
  $("pay-dialog").showModal();
};
$("pay-close").addEventListener("click", () => $("pay-dialog").close());

$("pay-submit").addEventListener("click", async () => {
  const reason = $("pay-reason").value.trim();
  if (!reason) {
    $("pay-error").textContent = "必须填写原因（进入审计日志）";
    return;
  }
  try {
    await api("POST", `/api/admin/orders/${state.currentOrder}/confirm-payment`, {
      approved: $("pay-approve").value === "true",
      reason,
    });
    toast("已处理", "success");
    $("pay-dialog").close();
    loadOrders();
  } catch (err) {
    $("pay-error").textContent = err.message;
  }
});

/* ---------------- 审计日志 ---------------- */
async function loadAudit() {
  const a = state.audit;
  try {
    const data = await api("GET", `/api/admin/audit-logs?page=${a.page}&size=${a.size}`);
    a.total = data.total;
    $("audit-page").textContent =
      `第 ${data.page} 页 / 共 ${Math.max(1, Math.ceil(data.total / data.size))} 页（${data.total} 条）`;
    $("audit-tbody").innerHTML = data.logs.map((l) => `
      <tr>
        <td>${l.id}</td>
        <td>${escapeHtml(l.action)}</td>
        <td>${l.operator_id ?? "-"}</td>
        <td>${l.target_user_id ?? "-"}</td>
        <td>${escapeHtml(l.reason || "-")}</td>
        <td>${escapeHtml(l.detail || "-")}</td>
        <td>${fmtTime(l.created_at)}</td>
      </tr>`).join("");
  } catch (err) {
    toast(err.message, "error");
  }
}
$("audit-prev").addEventListener("click", () => {
  if (state.audit.page > 1) { state.audit.page -= 1; loadAudit(); }
});
$("audit-next").addEventListener("click", () => {
  if (state.audit.page * state.audit.size < state.audit.total) {
    state.audit.page += 1; loadAudit();
  }
});

/* ---------------- 启动 ---------------- */
if (state.token && state.admin?.is_admin) {
  showApp();
} else {
  state.token = "";
  sessionStorage.clear();
  showLogin();
}
