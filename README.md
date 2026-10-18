# CodeLabel Studio

面向 Windows 10/11 的 UPC/EAN 商品条形码生成与打印桌面软件。

系统由三部分组成：

1. **Windows 桌面客户端**（`client/`）：条码生成、管理、预览、打印、导出（核心条码功能本地运行）。
2. **在线用户及授权后台**（`server/`）：邮箱注册、登录、7 天试用、订阅管理、授权验证。
3. **管理员 Web 后台**（`admin/`）：用户管理、试用管理、订单管理、授权管理、运营统计。

## 当前进度（2026-10-18）

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 1 | 项目审计 / 风险清单 / 实施计划 | ✅ 已完成（见 `docs/FINAL_AUDIT_REPORT.md`） |
| 2 | 数据完整性：schema v2 迁移、CHECK 约束、GS1 来源统一校验、批量上限/取消/进度、备份恢复加固 | ✅ 已完成（`docs/test_results_phase1.txt`） |
| 3 | 打印与导出：PDF / SVG / PNG / CSV、4+ 套标签模板、打印预览与越界警告 | ✅ 已完成（`docs/test_results_phase2.txt`） |
| 4 | 在线服务端：注册/邮箱验证/登录、7 天试用、订阅、订单、人工付款确认、管理后台 API、Alembic 迁移 | ✅ 已完成（`docs/test_results_phase4.txt`） |
| 5 | PySide6 桌面客户端：7 大页面、授权缓存与离线宽限、服务端 API 客户端 | ✅ 已完成（UI 冒烟 8 项通过，`docs/test_results_phase3.txt`） |
| 6 | 管理员 Web 后台（前端页面） | ✅ 已完成（`admin/` 零构建 SPA，统计/用户/订单/审计 4 页签） |
| 7 | 安全审计：bandit 静态扫描 + 人工清单 | ✅ 已完成（`docs/SECURITY_AUDIT.md`，0 问题） |
| 8 | Windows 发布：安装版 / 绿色便携版 / 卸载程序 | ✅ 已构建并冒烟验证（`docs/BUILD_REPORT.md`） |
| 9 | 部署文档：DEPLOYMENT / OPERATIONS / BACKUP_RESTORE / UPGRADE_ROLLBACK | ✅ 已完成 |
| 10 | 端到端测试：跨客户端/服务端全链路 | ✅ 已完成（7 项，`tests_server/test_e2e_full_flow.py`） |
| 11 | 最终验收 | 🔄 进行中 |

## 快速开始

```bat
REM 1) 安装依赖（建议虚拟环境）
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

REM 2) 运行全部测试（客户端 + 服务端）
.venv\Scripts\python -m unittest discover -s tests -t .
.venv\Scripts\python -m unittest discover -s tests_server -t .

REM 3) 启动桌面客户端
.venv\Scripts\python run_client.py

REM 4) 启动服务端（开发模式）
cd server && ..\.venv\Scripts\python -m uvicorn main:app --reload
```

条码编码核心（`client/barcode/`）仅依赖 Python 标准库，可独立测试。

## 目录结构

```
CodeLabelStudio/
├── client/                  # Windows 桌面客户端
│   ├── barcode/             # 条码核心：校验位/编码/渲染/解码/CSV/SVG
│   ├── database/            # SQLite：schema v2 / DAO / 事务 / 备份恢复
│   ├── printing/            # 标签模板 / 版面几何 / PDF
│   ├── auth/                # 会话存储 + 服务端 API 客户端
│   ├── licensing/           # 授权缓存 + 离线宽限 + Ed25519 验签
│   ├── ui/                  # PySide6：主题/组件/7 页面/主窗口
│   ├── app/                 # QApplication 入口
│   └── assets/
├── server/                  # 在线服务端 FastAPI + Alembic 迁移
│   ├── app/                 # config / database / security / models / licensing / mailer
│   ├── services/            # auth / trial / subscription / license / orders
│   ├── api/                 # auth / account / license / orders / admin
│   └── migrations/          # Alembic versions
├── admin/                   # 管理员 Web 后台（前端页面，规划中）
├── tests/                   # 客户端自动化测试（含 UI 冒烟）
├── tests_server/            # 服务端 API 集成 + 安全 + 迁移测试
├── deploy/                  # Docker Compose / Nginx（规划中）
├── installer/               # Inno Setup（阶段 8）
├── scripts/                 # 构建与测试脚本
├── run_client.py            # 桌面客户端启动器
└── docs/                    # 审计报告 / 测试结果 / 文档
```

## 重要说明

- 条码"可扫描性"当前通过自动化 **渲染 → 像素解码往返测试** 验证；正式发布前仍需真实条码扫描器与标签纸人工验证（见审计报告风险清单 R6）。
- 本地数据库去重仅证明"本地未重复"，不等同于全球 GS1 注册状态验证（见 `client/barcode/service.py` 中 `validate_import` 的提示语）。
