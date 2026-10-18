# admin/ — 管理员 Web 后台

阶段 6 实现。独立管理员登录入口，与普通用户界面分离，服务端角色权限控制（`/api/admin/*` 路由级 `require_admin` 依赖，非管理员 403）。

## 页面（对照规格 13 章）

| 页面 | 功能 | 后端 API |
| --- | --- | --- |
| 登录 | 管理员账号登录（复用 `/api/auth/login`，校验 `is_admin`） | POST `/api/auth/login` |
| 运营统计 | 用户/验证/试用/订阅/订单/确认收入 | GET `/api/admin/stats` |
| 用户管理 | 搜索、状态筛选、详情（试用/设备/订单）、停用/恢复、延长试用（原因必填） | GET `/api/admin/users`、`/users/{id}`、POST `/users/{id}/suspend|resume|extend-trial` |
| 订单管理 | 状态筛选、确认付款（批准/驳回 + 原因必填，写审计日志） | GET `/api/admin/orders`、POST `/orders/{id}/confirm-payment` |
| 审计日志 | 全部管理员操作（操作人/目标/原因/明细/时间） | GET `/api/admin/audit-logs` |

订阅调整（extend/terminate/suspend/resume）与授权撤销（`/subscriptions/{id}/adjust`、`/licenses/{id}/revoke`）已在后端 API 就绪，前端按需接入。

## 技术形态

- 原生 HTML/CSS/JS，**零构建、零依赖**（`index.html` + `app.js` + `style.css`）；
- 令牌存 `sessionStorage`（关闭标签页即失效，不落盘）；
- 所有请求走同源相对路径 `/api/*`，由 Nginx 反代（见 `deploy/nginx.admin.conf`）；
- 所有管理操作必须填写「原因」，服务端强制并写入 `audit_logs` 表。

## 运行方式

生产（推荐）：Nginx 直接伺服本目录，`/api/` 反代到 uvicorn：

```nginx
server {
    listen 443 ssl;
    server_name admin.your-domain.com;
    root /srv/codelabel/admin;
    index index.html;
    location / { try_files $uri $uri/ /index.html; }
    location /api/ { proxy_pass http://127.0.0.1:8000; ... }
}
```

开发：服务端已自动挂载（`server/main.py` 检测到 `admin/index.html` 即挂到 `/admin`）：

```bash
cd server && python -m uvicorn main:app --reload
# 浏览器打开 http://127.0.0.1:8000/admin/
```

> 注意：`/admin` 页面本身不含权限校验（静态文件），鉴权完全由 `/api/admin/*` 强制完成；
> 生产应限制管理后台域名的网络访问（VPN / IP 白名单）。

## 测试

`tests_server/test_api_admin.py` 覆盖：管理员鉴权、用户列表/详情、停用后会话失效、
试用延长、订阅调整、统计自洽、分页边界、订单列表字段（id/username）、
确认付款写审计日志、`/admin` 静态挂载。
