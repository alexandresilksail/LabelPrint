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
