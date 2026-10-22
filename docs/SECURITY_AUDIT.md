# CodeLabelStudio 安全审计报告

- 审计日期：2026-10-18（第二轮修复后复核）
- 审计范围：`client/`（桌面客户端）+ `server/`（在线服务端）+ `admin/`（Web 后台），不含测试目录
- 工具：bandit 1.9.4 静态扫描（-r client server admin）+ 人工清单核对
- 代码规模：8111 行

## 一、静态扫描结果

```
$ python -m bandit -r client server admin -f json
扫描代码行: 8111
HIGH: 0 | MEDIUM: 0 | LOW: 0
结果条目: 0
```

> 第二轮复核（2026-10-18，本审计修复后）：新增 `server/app/netutil.py`、
> `server/services/outbox.py` 与四个测试文件后重扫，仍为 0 问题。

> 阶段 14 复核（2026-10-18）：新增 `tests_server/test_e2e_full_flow.py` 与
> settings 页版本展示后重扫，仍为 0 问题。bandit 提示若干 `# nosec` 注释
> 已不再命中（代码重构后原误报点已消失），属工具提示非问题，保留注释无副作用。

扫描过程中发现并已修复/处置的问题：

| # | 编号 | 位置 | 级别 | 处置 |
| --- | --- | --- | --- | --- |
| 1 | B310 URL 协议 | `client/auth/api_client.py`、`client/licensing/client_license.py` | Medium | **真实加固**：新增 `_validate_url()`，仅放行 http/https 且校验主机名，拒绝 file:/ftp: 等本地/明文协议（防 SSRF 与本地文件读取） |
| 2 | B608 SQL 拼接 | `client/database/dao.py` | Medium | **真实修复**：INSERT/UPDATE 语句改为类定义期由常量列名构建（`_INSERT_SQL`/`_UPDATE_SQL`），运行时零拼串，取值全部参数化绑定 |
| 3 | B110 try/except/pass | `client/app/main.py` | Low | 接受：异常钩子中弹框失败时的兜底（已打印到 stderr），加 `# nosec` 注明理由 |
| 4 | B112 try/except/continue | `client/ui/pages/print_export.py` | Low | 接受：打印预览中跳过单个非法编码继续绘制其余，加 `# nosec` 注明理由 |
| 5 | B105 硬编码字符串 | `server/api/auth.py` | Low | 误报：`"Bearer"` 为 OAuth2 令牌类型标识，非密码，加 `# nosec` |

## 二、人工安全清单核对

### 认证与会话
- [x] 密码 Argon2id 哈希，不明文存储、不入日志（`server/app/security.py`）
- [x] 会话/验证/重置令牌 URL 安全随机生成，库内仅存 SHA-256 哈希
- [x] 令牌一次性使用 + 有效期；重置密码后撤销全部会话
- [x] 登录/注册/重发验证统一防枚举文案（不泄露账号存在性）
- [x] 登录限流（进程内固定窗口，按 IP + 路由）

### 数据完整性与注入
- [x] 全参数化 SQL；DAO 层 CHECK 约束兜底绕过路径
- [x] CSV 公式注入防护（`= + - @ \t \r` 开头单元格加 `'` 前缀）
- [x] CSV 导入 10MB / 10 万行上限，utf-8-sig / gbk 双解码
- [x] SQLite PRAGMA：foreign_keys=ON、WAL、busy_timeout

### 授权与许可证
- [x] Ed25519 签名：服务端持私钥（env hex 或 600 权限 PEM），客户端持公钥
- [x] canonical JSON 载荷 + base64 签名，防止载荷篡改
- [x] 服务端 `validate_license` 双重校验：签名 + 库内记录状态/设备绑定/到期
- [x] 离线宽限 72h（以服务端时间为准，客户端改本地时间无效）
- [x] 授权缓存文件 600 权限；撤销即时生效（联网核对）

### 密钥与配置
- [x] `CLS_SECRET_KEY` 生产环境必填（缺失即拒绝启动）
- [x] 数据库连接串、CORS 白名单、限流配额全部 env 注入
- [x] `.gitignore` 排除 `.env` / `*.db` / 日志 / 安装产物
- [x] 未配置 Webhook 签名密钥时一律拒绝支付回调（防伪造）

### 备份与恢复
- [x] 恢复前自动快照（`.pre_restore_<UTC>`）
- [x] 源完整性 / schema / 版本三重校验
- [x] 临时文件 + `os.replace` 原子替换；恢复后 quick_check + JSONL 审计日志

## 三、第二轮审计修复（2026-10-18）

### 3.1 限流 IP 信任（高危，已修复）

- **问题**：`request_client_ip()` 无条件读取 `X-Forwarded-For` 首个值，
  任意客户端改一个请求头即可为每次请求伪造新 IP，注册/登录限流形同虚设。
- **修复**：新增 `server/app/netutil.py`，默认使用连接对端地址；仅当对端
  落在 `CLS_TRUSTED_PROXIES` 内才解析转发头，且**从右往左**剥离可信代理，
  取第一个不可信地址（防最左伪造）；链中含非法值失败关闭。
- **多 worker**：新增 `RedisRateLimiter`（INCR + EXPIRE 固定窗口）；生产
  `memory + workers>1` 在配置校验阶段直接拒绝启动，避免"限流配额 × worker 数"
  被误当成高可用部署。
- **证据**：`tests_server/test_trusted_proxy.py` 28 项（含伪造头端到端断言 429）。

### 3.2 订单方案复用（中危，已修复）

- **问题**：复用待付款订单时只按 `user_id + pending` 匹配，用户从月付切年付
  会拿到旧的月付订单（金额、方案都不对）。
- **修复**：复用时校验 `plan_id / amount / currency`；不一致则取消旧单
  （写 `order_canceled` 事件 + 审计日志）并新建；新增部分唯一索引
  `uq_order_user_pending`，并发撞索引后重查复用。
- **证据**：`tests_server/test_orders_plan_switch.py` 8 项（含 6 线程并发）。

### 3.3 生产启动检查（中危，已修复）

- **问题**：生产缺密钥/邮件/CORS 时仍能启动；授权私钥在文件缺失时**静默生成**
  新密钥，重启即让全部已签发授权失效，而服务仍对外"正常"。
- **修复**：`Settings.production_errors()` 一次性校验密钥强度、数据库类型、
  SMTP 完整性、CORS 白名单、HTTPS 基础地址、授权私钥、限流后端与 worker 数、
  可信代理配置；不通过即抛 `ConfigError` 拒绝启动。`licensing` 生产不再生成密钥，
  并新增启动自检（签名/验签闭环 + 篡改载荷必须验签失败）与公钥指纹。
- **证据**：`tests_server/test_production_config.py` 26 项。

### 3.4 付款确认事务与通知（中危，已修复）

- **问题**：`confirm_payment()` 里订阅激活内部 `commit()`，邮件在事务中直接
  发送——邮件失败会让订单已确认却抛异常，事务边界也不完整。
- **修复**：订阅激活支持 `commit=False` 并入外层事务；通知改为**事务性发件箱**
  （`outbox_messages`，`dedupe_key` 唯一），提交后投递，失败按指数退避重试，
  5 次后标记 `dead`；重复确认被订单状态拦下（409）。
- **证据**：`tests_server/test_payment_outbox.py` 11 项（邮件异常、提交失败回滚、
  重复确认、并发确认、去重、退避）。

## 四、已知限制与后续建议

| # | 事项 | 说明 |
| --- | --- | --- |
| L1 | ~~限流为进程内实现~~ | **已修复**：支持 Redis 共享后端；`memory + 多 worker` 被启动校验拒绝 |
| L2 | ~~邮箱发送为 console/smtp 双后端~~ | **已加固**：生产强制 `smtp` 且校验完整性；付款通知走 outbox 可重试 |
| L3 | SQLite 单文件数据库 | 服务端生产建议 PostgreSQL；生产指向 SQLite 需显式 `CLS_ALLOW_SQLITE_IN_PROD=1` |
| L4 | 条码可扫描性依赖像素往返测试 | 正式发布前需真实扫描器 + 标签纸人工验证（审计报告 R6） |
| L5 | 客户端授权缓存为明文 JSON | 设备本地文件不可避免；已限 600 权限 + 验签兜底 |
| L6 | 支付回调未接入真实服务商 | `process_webhook()` 保持一律拒绝，接入前需实现签名验证与幂等 |
| L7 | 邮箱验证/重置邮件仍为同步发送 | 付款通知已 outbox 化；认证邮件失败可由"重发验证"补偿，暂不改造 |
| L8 | Redis 故障默认失败关闭 | 可用性优先时可设 `CLS_RATE_LIMIT_FAIL_OPEN=1`，需同步监控暴力破解 |

## 五、结论

静态扫描 0 问题；人工清单全部通过；第二轮四项审计问题已修复并有对应测试
（新增 73 项服务端测试）。剩余为部署环境相关加固与未接入的外部服务。

---

## 六、凭据（Secret）专项扫描与处置（2026-10-22 第六轮）

> 本节为**凭据/令牌/私钥专项**，与前五节 bandit 通用静态扫描互补。
> 诚信红线：本报告只记录**文件路径 + 类型 + 状态 + 处置建议**，
> 不复制、不转写任何疑似凭据的字面值；仓库内未发现需要吊销的真实令牌。

### 6.1 扫描范围

- **工作树**：`admin/`、`client/`、`server/`、`scripts/`、`tests/`、
  `tests_server/`、`docs/`、`deploy/`、`installer/`、`data/`、根目录
  （`docker-compose.yml`、`.env.example`、`README.md`、`requirements.txt`）；
  二进制构建产物（`build/`、`dist/`）不做文本扫描。
- **git 历史**：`git rev-list --all` 全量提交逐一匹配（含 HEAD 之前的全部历史），
  不只扫工作树。
- **CRLF 噪音文件**（本轮不在清理/提交范围）仍逐个人工检查：
  `client/ui/pages/account.py`、`generate.py`、`records.py`、`settings.py`、
  `templates.py`，`docs/test_results_phase1/3/4/14.txt`，`server/app/licensing.py`。

### 6.2 扫描方法（grep / 模式清单）

| 类别 | 匹配模式（掩码后仅用于分类，不输出值） |
|---|---|
| GitHub PAT / 平台令牌 | `ghp_`、`gho_`、`github_pat_`、`glpat-`、`ya29.`、`sk-` 前缀；`Authorization: Bearer`、`token ` 赋值模式；AWS `AKIA[0-9A-Z]{16}` |
| 口令/密码明文 | `password=`、`passwd`、`pwd=`、`PASS*=`、`secret=`、`api_key/apikey=`、`access_key=` 的字面量赋值（排除 `os.environ.get` / `getenv` 运行时读取与占位符） |
| 私钥 | `BEGIN ... PRIVATE KEY` 块；`.pem` / `.key` / `.p12` / `.pfx` 文件 |
| 环境/配置与连接串 | `.env` / `.env.example`；`postgres(ql)://user:pass@`、`mysql://`、`redis://` 带口令 URI；JWT/SMTP/Webhook/DB 连接串 |
| 测试夹具/脚本/文档硬编码令牌 | `tests/`、`tests_server/`、`scripts/`、`docs/` 中长 hex/base64 常量与字面量令牌 |

判定口径：`CHANGE_ME`、`change_me`、`<your-token>`、`xxx`、`example.com`、
测试用假口令、运行时从环境/表单/sessionStorage 读取的值**不算泄露**；
开发兜底默认值单独标注且确认生产路径被阻断。

### 6.3 结果表（仅路径 + 类型 + 状态，无值）

| # | 路径 | 类型 | 状态 | 处置建议（本组未做 git 写操作，由集成阶段统一执行） |
|---|---|---|---|---|
| 1 | `data/license_private.pem` | Ed25519 授权签名私钥（PEM，本地开发用） | **安全：未入库**（`data/` 被 `.gitignore` 排除，`git ls-files data/` 为空；全部历史从未提交；spec/安装器不打包） | 保持本地 `600` 权限；生产改用 `CLS_LICENSE_PRIVATE_KEY`（env hex）；确认不随客户端分发；若该私钥曾外发/进聊天记录需**轮换**（新密钥对 + 重新签发授权） |
| 2 | `.env.example` | 环境变量样例（DB/JWT/SMTP/Webhook/管理员） | **安全占位符**（全部 `CHANGE_ME`） | 保持；真实 `.env` 已被 gitignore 排除，工作树内不存在 `.env` |
| 3 | `docker-compose.yml` | 数据库口令 `${DB_PASSWORD:-change_me}` | **安全占位符**（环境插值默认值，非硬编码） | 生产由 `.env`/secret 注入，禁止沿用 `change_me` |
| 4 | `scripts/push_via_api.py` | GitHub API 推送令牌 | **安全：无硬编码**（`GITHUB_TOKEN` 仅从环境变量读取；docstring 示例为 `ghp_xxx` 占位） | 保持 env 读取；禁止把真实 PAT 写进脚本/提交；历史 PAT 若曾明文出现需到 GitHub 撤销重建 |
| 5 | `server/seed.py` | 开发兜底管理员账号/口令 | **开发默认值（非泄露）**：缺 env 时仅开发使用，生产路径 `SystemExit` 阻断 | 部署文档强调生产必须显式设置 `CLS_ADMIN_USERNAME/EMAIL/PASSWORD`；开发账号不对外 |
| 6 | `tests_server/base.py` | 测试夹具注册/登录/管理员口令 | **安全假值**：仅作用于每个用例全新建的临时 SQLite 库，不入库、不对外 | 无需处置 |
| 7 | `server/app/config.py` | 会话密钥/SMTP/DB/CORS | **安全**：全部 env 注入；`test-secret-key-not-for-production` 仅为生产拒绝检查的**标记值**，不是密钥 | 保持生产 `production_errors()` 缺失即拒绝启动 |
| 8 | `admin/app.js`、`client/auth/api_client.py`、`client/licensing/client_license.py`、`server/api/deps.py` | Bearer 令牌 | **安全：运行时提取**（sessionStorage / 响应体 / 请求头解析），无字面量令牌 | 保持 |
| 9 | `server/app/licensing.py` | 授权私钥加载 | **安全**：env hex 优先，其次 PEM 文件；生产缺失即拒绝生成，开发才自动落盘 | 保持；PEM 权限 `600` |
| 10 | 10 个 CRLF 噪音文件（见 6.1） | 逐文件复查 | **安全：无泄露模式命中** | 本轮不改、不提交 |

### 6.4 git 历史处理说明

- `git rev-list --all` 全历史扫描：`ghp_/gho_/github_pat_/glpat-` 前缀、
  `BEGIN ... PRIVATE KEY` 块、带口令数据库/缓存连接串、字面量密码赋值
  **均 0 命中**；历史中从未出现 `.pem` / `.key` / `.p12` / `.pfx` / 真实 `.env`。
- 结论：**历史中无可提取的真实凭据，无需 `git filter-repo` 重写历史**；
  工作树也没有已提交的泄露文件需要 `git rm --cached`。
- `server/seed.py` 的开发兜底口令在历史中存在，但属生产阻断的开发默认值，
  不构成泄露，无需重写历史。

### 6.5 用户侧处置建议（部署前）

1. **令牌**：仓库内无真实 PAT/平台令牌，无需因本仓库吊销；推送令牌仅经
   `GITHUB_TOKEN` 环境变量注入，不落盘、不入提交。
2. **授权私钥轮换（条件触发）**：若 `data/license_private.pem` 曾被外发、
   截图或进入聊天记录，生成新 Ed25519 密钥对并重新签发全部已发授权
   （旧私钥签发的授权将验签失败）。
3. **生产密钥全部外置**：`CLS_SECRET_KEY`、`CLS_LICENSE_PRIVATE_KEY`、
   `CLS_SMTP_PASSWORD`、`DB_PASSWORD`、管理员初始口令走环境变量或 secret
   manager，禁止写入仓库/镜像层。
4. **开发默认值**：`server/seed.py` 兜底账号仅本地开发；生产启动缺
   `CLS_ADMIN_*` 直接退出。
