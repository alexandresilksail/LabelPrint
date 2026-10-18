# CodeLabel Studio — 发布清单

## 1. 交付物

| 交付物 | 路径 | 状态 |
|---|---|---|
| 完整源码 | 本仓库 | ✅ |
| 依赖锁定 | requirements.txt | ✅ |
| 数据库迁移 | server/migrations/ (Alembic) + client schema v2 | ✅ |
| 自动化测试 | tests/ + docs/test_results_*.txt | ✅ |
| Windows 安装版 | dist/CodeLabelStudio-Setup.exe | 见阶段 12 |
| Windows 便携版 | dist/CodeLabelStudio-Portable.zip | 见阶段 12 |
| 卸载程序 | 随安装版（Inno Setup） | 见阶段 12 |
| SHA-256 校验 | dist/SHA256SUMS.txt | 构建时生成 |
| 部署文档 | docs/DEPLOYMENT.md 等四份 | 阶段 13 |
| 最终审计 | docs/FINAL_AUDIT_REPORT.md | 阶段 14 |

## 2. 发布前必须满足

1. [ ] 全部测试通过（或失败项有明确阻塞原因）
2. [ ] bandit 无高危未处理项
3. [ ] Windows 安装包真实构建（非 Linux 冒充）
4. [ ] 安装/启动/升级/卸载实测通过
5. [ ] 便携版独立运行通过
6. [ ] SHA-256 清单生成
7. [ ] 未签名安装包明确标注
8. [ ] 待人工验证项全部列出（扫描器/打印机/SMTP/支付）

## 3. 版本号规范

语义化版本 `MAJOR.MINOR.PATCH`，当前版本见 `client/__init__.py` 的 `__version__` 与安装脚本同步。

## 4. 回滚

- 客户端：保留用户数据目录，重装旧版本即可
- 服务端：数据库迁移提供 downgrade；备份恢复见 docs/BACKUP_RESTORE.md
