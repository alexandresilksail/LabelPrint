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

`CLS_ENV=production` 时，**下列任一项缺失或不合规都会让进程拒绝启动**，
并在一次报错里列出全部问题（不会带着开发默认值上线）。

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `CLS_ENV` | 是 | `production`（缺省为 development） |
| `CLS_SECRET_KEY` | 是 | ≥32 字符；缺失、过短或使用测试占位值均拒绝启动 |
| `CLS_DATABASE_URL` | 是 | 例：`postgresql+psycopg2://user:pass@db:5432/codelabel`。**生产指向 SQLite 会被拒绝**（确需单机 SQLite 须显式 `CLS_ALLOW_SQLITE_IN_PROD=1`） |
| `CLS_ADMIN_USERNAME/EMAIL/PASSWORD` | 是 | 初始管理员（seed 用），首次登录后立即改密 |
| `CLS_LICENSE_PRIVATE_KEY` | 是* | Ed25519 私钥 32 字节 hex（64 字符）。生产**不再自动生成**：既无该变量也无 PEM 文件即拒绝启动 |
| `CLS_LICENSE_KEY_FILE` | 否 | 自定义 PEM 私钥路径（容器挂载 secret 时用） |
| `CLS_MAIL_BACKEND` | 是 | 生产必须为 `smtp`；`console` 会被拒绝 |
| `CLS_SMTP_HOST/PORT/USER/PASSWORD` | 是 | SMTP 事务邮件配置，缺任一项拒绝启动 |
| `CLS_MAIL_FROM` | 是 | 发件地址，仍为 `noreply@codelabel.local` 会被拒绝 |
| `CLS_CORS_ORIGINS` | 是 | 逗号分隔白名单；含 `*`、含 localhost、为空都会拒绝启动 |
| `CLS_APP_BASE_URL` | 是 | 必须 `https://`（邮件里的验证/重置链接） |
| `CLS_BEHIND_PROXY` | 视部署 | 置于反向代理之后设为 `1`，此时必须同时配置可信代理 |
| `CLS_TRUSTED_PROXIES` | 视部署 | 逗号分隔 IP/CIDR，**只有这些地址的转发头才会被采信** |
| `CLS_RATE_LIMIT_BACKEND` | 视部署 | `redis`（多 worker 必需）或 `memory`（仅单 worker） |
| `CLS_REDIS_URL` | 视部署 | `redis://host:6379/0`；选 redis 后端时必填 |
| `CLS_WORKERS` | 是 | 进程/副本数。`memory` 限流 + `workers>1` 直接拒绝启动 |
| `CLS_RATE_LIMIT_FAIL_OPEN` | 否 | 默认 `0`：Redis 故障时限流失败关闭（拒绝请求），防止暴力破解窗口 |
| `CLS_RL_*` | 否 | 限流配额（注册 5 / 登录 10 / 邮件 3 / 默认 120，每分钟） |

> * 授权私钥是**长期身份**：丢失或更换会导致已签发授权全部验签失败。
> 请离线备份；多实例/多容器必须使用同一把密钥，启动日志与 `/health`
> 的 `license_key_fp` 可用于比对一致性。

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

# 3) 数据库迁移（Alembic）——生产启动时会校验表是否齐全
cd server && ../.venv/bin/alembic upgrade head

# 4) 初始化订阅计划与管理员（幂等；生产缺表会拒绝执行）
cd .. && .venv/bin/python -m server.seed

# 5) 启动服务
#    单 worker + 内存限流（小规模）：
.venv/bin/uvicorn server.main:app --host 127.0.0.1 --port 8000 --workers 1
#    多 worker（必须先把限流切到 Redis）：
#    export CLS_RATE_LIMIT_BACKEND=redis CLS_REDIS_URL=redis://127.0.0.1:6379/0
#    export CLS_WORKERS=4
#    .venv/bin/uvicorn server.main:app --host 127.0.0.1 --port 8000 --workers 4
```

启动日志会打印 `env / license_key_fp / key_source / limiter / workers`，
多实例部署时逐个比对 `license_key_fp` 与 `limiter` 即可发现配置漂移。

## 五、Nginx 配置

参见 `deploy/nginx.admin.conf`（管理后台域名的完整示例）。要点：

- TLS 1.2+，HSTS；
- `X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy` 安全响应头；
- `/api/` 与 `/health` 反代到 uvicorn，其余按需；
- 上传/请求体大小限制（CSV 导入上限 10MB，建议 `client_max_body_size 12m`）；
- **客户端地址**：用 `proxy_set_header X-Forwarded-For $remote_addr;` 覆盖而非
  `$proxy_add_x_forwarded_for` 追加，避免把客户端伪造的链带进后端；
- 服务端 `CLS_TRUSTED_PROXIES` 必须包含 Nginx 的出口地址，否则后端不采信
  转发头，所有请求都会被算成同一个 IP。

Docker Compose 典型组合：

```yaml
environment:
  CLS_BEHIND_PROXY: "1"
  CLS_TRUSTED_PROXIES: "172.16.0.0/12,10.0.0.0/8"
  CLS_RATE_LIMIT_BACKEND: redis
  CLS_REDIS_URL: redis://redis:6379/0
  CLS_WORKERS: "2"
```

## 六、健康检查与冒烟

```bash
curl -s https://your-domain/health
# {"status":"ok","env":"production","rate_limit_backend":"redis",
#  "workers":2,"license_key_fp":"3f2a...","outbox_pending":0}

curl -s https://your-domain/api/plans       # 订阅计划列表
curl -s https://your-domain/api/license/public-key
```

`/health` 字段说明：

| 字段 | 用途 |
| --- | --- |
| `status` | `degraded` + 503 表示数据库探针失败 |
| `rate_limit_backend` | 各实例必须一致；出现 `memory` 而 `workers>1` 属于配置错误 |
| `workers` | 与部署声明比对，防止"以为多副本"实际单进程 |
| `license_key_fp` | 各实例必须相同；不同说明某实例换了私钥，签发凭证互不认账 |
| `outbox_pending` | 未投递的通知数；持续增长说明 SMTP 故障，见运维手册 |

## 七、数据库说明

- **SQLite（开发/小规模）**：WAL 模式 + foreign_keys=ON；备份即文件复制（须先 `VACUUM INTO` 或停机，见 BACKUP_RESTORE.md）。
- **PostgreSQL（生产推荐）**：金额列 `Numeric(12,2)`；多 worker 与限流共享场景必选。

## 八、客户端分发

- 客户端启动后在「账户与授权 → 服务端地址」填入部署域名；
- 首次登录 → 邮箱验证 → 自动 7 天试用 → 「激活/刷新授权」下发 Ed25519 凭证；
- 之后离线可用 72 小时，超时需联网验证。
