# CodeLabelStudio 安全审计报告

- 审计日期：2026-10-18
- 审计范围：`client/`（桌面客户端）+ `server/`（在线服务端），不含测试目录
- 工具：bandit 1.9.4 静态扫描（-r client server）+ 人工清单核对
- 代码规模：7315 行

## 一、静态扫描结果

```
$ python -m bandit -r client server -x "*/tests/*,*/tests_server/*"
No issues identified.
Total issues (by severity): Low: 0 | Medium: 0 | High: 0
```

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

## 三、已知限制与后续建议

| # | 事项 | 说明 |
| --- | --- | --- |
| L1 | 限流为进程内实现 | 多 worker 部署需改用 Redis 共享计数（`server/app/security.py` 已注明） |
| L2 | 邮箱发送为 console/smtp 双后端 | 生产需配置真实 SMTP 或事务邮件服务 |
| L3 | SQLite 单文件数据库 | 服务端生产建议 PostgreSQL（requirements 已含 psycopg2-binary） |
| L4 | 条码可扫描性依赖像素往返测试 | 正式发布前需真实扫描器 + 标签纸人工验证（审计报告 R6） |
| L5 | 客户端授权缓存为明文 JSON | 设备本地文件不可避免；已限 600 权限 + 验签兜底 |

## 四、结论

静态扫描 0 问题；人工清单全部通过。阶段 10 安全审计完成，未发现高危漏洞。
剩余事项均为部署环境相关的加固建议，已在部署文档中给出方案。
