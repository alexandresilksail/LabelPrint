# CodeLabelStudio 运维手册

## 一、日常监控

| 检查项 | 命令 / 位置 | 正常值 |
| --- | --- | --- |
| 服务健康 | `curl -s https://<domain>/health` | `{"status":"ok"}`，HTTP 200 |
| 进程状态 | `systemctl status codelabel` | active (running) |
| 数据库连通 | health 探针（自动执行 `SELECT 1`） | 通过 |
| 磁盘余量 | `df -h /srv` | > 20% |
| 授权私钥在位 | `data/license_private.pem` 权限 600 | 存在且权限正确 |
| 邮件发送 | 注册/验证邮件到达率 | > 99% |

## 二、日志

| 日志 | 位置 | 内容 |
| --- | --- | --- |
| 应用日志 | systemd journal / 容器 stdout | 请求访问日志、异常堆栈 |
| 安全审计 | `audit_logs` 表 | 管理员操作、付款确认、授权撤销（含原因） |
| 迁移降级记录 | `migrate_source_downgrade` 审计项 | schema v1→v2 时 GS1 记录降级明细 |
| 客户端备份日志 | `data/*.log`（JSONL） | 本地备份/恢复事件 |

建议：应用日志接入集中式收集（journald → Loki / ELK），保留 90 天。

## 三、常见故障处理

### 1. `/health` 返回 503
1. 查看应用日志定位数据库异常；
2. PostgreSQL：检查连接数（`max_connections`）与长事务；
3. SQLite：检查 `-wal`/`-shm` 是否残留（恢复流程会自动清理）；
4. 确认后重启服务；仍失败按数据恢复流程处理。

### 2. 邮件发送失败
1. 确认 `CLS_SMTP_*` 配置与防火墙出站 465/587；
2. 查看邮件后端日志（`server/app/mailer.py` 双后端：console/smtp）；
3. 临时切回 console 后端保证注册流程可走「管理员后台手动验证」通道。

### 3. 登录被限流
- 固定窗口按 IP + 路由计数，误封后等待窗口滚动（1 分钟）或调整 `CLS_RL_LOGIN`；
- 多 worker 部署时限流不共享，属已知限制（见 SECURITY_AUDIT L1）。

### 4. 授权验证集中失败
1. 检查服务端时间（NTP）——离线宽限以服务端时间为准；
2. 核对 `CLS_LICENSE_PRIVATE_KEY` 是否与签发时一致（换钥会使全部历史凭证验签失败）；
3. 客户端「账户与授权 → 联网验证」触发重新核对。

## 四、定期任务

| 频率 | 任务 |
| --- | --- |
| 每日 | 检查 `/health`、备份成功标记、磁盘余量 |
| 每周 | 审查 `audit_logs` 异常操作；核对待确认订单 |
| 每月 | 演练一次恢复流程（BACKUP_RESTORE.md）；轮换 SMTP 密码 |
| 每季度 | 依赖漏洞扫描（`pip-audit` / bandit）；审查限流配额 |

## 五、升级窗口

- 选择低峰期；提前公告；
- 严格按 UPGRADE_ROLLBACK.md 执行：备份 → 迁移 → 冒烟 → 观察；
- 回滚决策点：迁移失败 / 冒烟失败 / 15 分钟内错误率显著上升。

## 六、联系人

- 服务端负责人：（填写）
- 数据库 DBA：（填写）
- 安全事件上报：（填写）
