# CodeLabel Studio — 发布清单

> 版本 1.0.0 · 构建日期 2026-10-18 · 详见 `docs/FINAL_AUDIT_REPORT.md`

## 1. 交付物

| 交付物 | 路径 | 状态 |
|---|---|---|
| 完整源码 | 本仓库（10 次提交） | ✅ |
| 依赖锁定 | requirements.txt | ✅ |
| 数据库迁移 | server/migrations/ (Alembic) + client schema v2 | ✅ |
| 自动化测试 | tests/（158）+ tests_server/（94） | ✅ 全通过 |
| Windows 安装版 | dist/installer/CodeLabelStudio-Setup-1.0.0.exe | ✅ 已构建 |
| Windows 便携版 | dist/CodeLabelStudio-Portable.zip | ✅ CRC 通过 |
| 单文件 exe | dist/CodeLabelStudio.exe | ✅ 冒烟通过 |
| 卸载程序 | 随安装版（Inno Setup） | ✅ 已编译 |
| SHA-256 校验 | dist/SHA256SUMS.txt | ✅ 已生成 |
| 部署文档 | docs/DEPLOYMENT.md 等四份 | ✅ |
| 构建报告 | docs/BUILD_REPORT.md | ✅ |
| 最终审计 | docs/FINAL_AUDIT_REPORT.md | ✅ |

## 2. 发布前检查项

1. [x] 全部测试通过 —— 客户端 158 / 服务端 94，0 失败 0 跳过
2. [x] bandit 无高危未处理项 —— 7459 行扫描，High/Medium/Low 全 0
3. [x] Windows 安装包真实构建 —— Inno Setup 6.7.3 在本机编译成功（非 Linux 冒充）
4. [ ] 安装/启动/升级/卸载实测 —— **启动已实测；安装交互流程需人工在桌面执行**
5. [x] 便携版独立运行通过 —— 解压即运行，数据目录随 exe 创建
6. [x] SHA-256 清单生成 —— dist/SHA256SUMS.txt（3 个产物）
7. [x] 未签名安装包已标注 —— 见 FINAL_AUDIT_REPORT 第 7 节
8. [x] 待人工验证项全部列出 —— 扫描器/打印机/SMTP/支付 + 安装交互

## 3. 版本号规范

语义化版本 `MAJOR.MINOR.PATCH`。当前一致性：

| 位置 | 值 |
|---|---|
| `client/__init__.py` `__version__` | `1.0.0` |
| `installer/codelabel.iss` `MyAppVersion` | `1.0.0` |
| `server/main.py` FastAPI version | `1.0.0` |

发布新版本时三处同改；客户端"设置 → 关于"会展示 `__version__`。

## 4. 发布步骤

```bat
REM 1) 跑全量测试（任一失败即停）
scripts\run_tests.bat

REM 2) 打包客户端
scripts\build_client.bat

REM 3) 编译安装包
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\codelabel.iss

REM 4) 生成校验值
python -c "import hashlib,os;[print(hashlib.sha256(open(f,'rb').read()).hexdigest(),os.path.basename(f)) for f in ['dist/CodeLabelStudio.exe','dist/CodeLabelStudio-Portable.zip','dist/installer/CodeLabelStudio-Setup-1.0.0.exe']]"
```

## 5. 回滚

- 客户端：保留用户数据目录（`{app}` 之外），重装旧版本即可回退
- 服务端：Alembic 提供 `downgrade`；备份恢复流程见 `docs/BACKUP_RESTORE.md`
