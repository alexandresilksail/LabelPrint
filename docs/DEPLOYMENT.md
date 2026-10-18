# CodeLabelStudio 部署文档

## 一、架构总览

```
[Windows 客户端]  ──HTTPS──▶  [Nginx :443]  ──▶  [uvicorn main:app :8000]
                                    │                    │
                                    │                    ├─ SQLite / PostgreSQL
                                    │                    └─ 授权私钥（env / PEM 600）
                                    └─ 静态资源（管理员前端，规划中）
```

- 客户端所有条码功能本地运行，服务端只负责账户、订阅、授权与订单。
- 授权验签在客户端与服务端双重进行；离线宽限 72 小时（以服务端时间为准）。

## 二、环境要求

| 组件 | 要求 |
| --- | --- |
| Python | 3.12+ |
| 服务端依赖 | `pip install -r requirements.txt` |
| 数据库 | 开发 SQLite；生产 PostgreSQL 14+（推荐） |
| 反向代理 | Nginx 1.24+（TLS 终止 + 安全响应头） |
| 客户端 | Windows 10/11（PySide6 运行时随安装包分发） |

## 三、环境变量（生产必填）

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `CLS_ENV` | 是 | `production`（缺省会触发开发模式检查） |
| `CLS_SECRET_KEY` | 是 | 应用密钥，生产缺失即拒绝启动。生成：`python -c "import secrets;print(secrets.token_hex(32))"` |
| `CLS_DATABASE_URL` | 是 | 例：`postgresql+psycopg2://user:pass@db:5432/codelabel` |
| `CLS_ADMIN_USERNAME` | 是 | 初始管理员用户名（seed 用） |
| `CLS_ADMIN_EMAIL` | 是 | 初始管理员邮箱 |
| `CLS_ADMIN_PASSWORD` | 是 | 初始管理员密码（首次登录后立即修改） |
| `CLS_LICENSE_PRIVATE_KEY` | 推荐 | Ed25519 私钥 hex；缺省时自动生成并落盘 `data/license_private.pem`（600 权限）。**生成后请备份，丢失后已签发授权无法重新验证** |
| `CLS_MAIL_BACKEND` | 是 | `smtp`（生产） |
| `CLS_SMTP_HOST/PORT/USER/PASSWORD` | 是 | SMTP 事务邮件配置 |
| `CLS_CORS_ORIGINS` | 否 | CORS 白名单（`server/app/config.py` 中按 env 细分） |
| `CLS_RL_*` | 否 | 限流配额（注册 5 / 登录 10 / 邮件 3 / 默认 120，每分钟） |

## 四、部署步骤

```bash
# 1) 部署代码
git clone <repo> /srv/codelabel && cd /srv/codelabel
python -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2) 配置环境变量（systemd EnvironmentFile 或容器 env）
export CLS_ENV=production
export CLS_SECRET_KEY=<64 位 hex>
export CLS_DATABASE_URL=postgresql+psycopg2://...
export CLS_ADMIN_USERNAME=admin CLS_ADMIN_EMAIL=admin@example.com \
       CLS_ADMIN_PASSWORD=<强密码>

# 3) 数据库迁移（Alembic）
cd server && ../.venv/bin/alembic upgrade head

# 4) 初始化订阅计划与管理员（幂等）
../.venv/bin/python -m server.seed

# 5) 启动服务（生产多 worker）
../.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2
```

## 五、Nginx 配置

参见 `deploy/nginx.admin.conf`（管理后台域名的完整示例）。要点：

- TLS 1.2+，HSTS；
- `X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy` 安全响应头；
- `/api/` 与 `/health` 反代到 uvicorn，其余按需；
- 上传/请求体大小限制（CSV 导入上限 10MB，建议 `client_max_body_size 12m`）。

## 六、健康检查与冒烟

```bash
curl -s https://your-domain/health          # {"status":"ok",...}
curl -s https://your-domain/api/plans       # 订阅计划列表
curl -s https://your-domain/api/license/public-key
```

`/health` 返回 503 表示数据库探针失败，应立即按运维手册处理。

## 七、数据库说明

- **SQLite（开发/小规模）**：WAL 模式 + foreign_keys=ON；备份即文件复制（须先 `VACUUM INTO` 或停机，见 BACKUP_RESTORE.md）。
- **PostgreSQL（生产推荐）**：金额列 `Numeric(12,2)`；多 worker 与限流共享场景必选。

## 八、客户端分发

- 客户端启动后在「账户与授权 → 服务端地址」填入部署域名；
- 首次登录 → 邮箱验证 → 自动 7 天试用 → 「激活/刷新授权」下发 Ed25519 凭证；
- 之后离线可用 72 小时，超时需联网验证。
