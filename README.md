# CodeLabel Studio

面向 Windows 10/11 的 UPC/EAN 商品条形码生成与打印桌面软件。

系统由三部分组成：

1. **Windows 桌面客户端**（`client/`）：条码生成、管理、预览、打印、导出（核心条码功能本地运行）。
2. **在线用户及授权后台**（`server/`）：邮箱注册、登录、7 天试用、订阅管理、授权验证。
3. **管理员 Web 后台**（`admin/`）：用户管理、试用管理、订单管理、授权管理、运营统计。

## 当前进度（2026-10-18）

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 1 | 项目审计 / 风险清单 / 实施计划 | ✅ 已完成（见 `docs/phase1_audit_report.md`） |
| 2 | 条码核心：UPC-A / EAN-13 / EAN-8 校验位、编码、预览、SQLite、编号范围、批量生成、去重、状态机 | ✅ 代码完成，自动化测试通过（见 `docs/test_results_phase2.txt`） |
| 3 | 打印与导出：PDF / SVG / CSV、多尺寸标签模板、打印预览与校准 | ⏳ 待开始 |
| 4 | 用户认证：注册、邮箱验证、登录、密码哈希、会话 | ⏳ 待开始 |
| 5 | 试用与授权：7 天试用、授权校验、离线宽限、设备管理 | ⏳ 待开始 |
| 6 | 管理员 Web 后台 | ⏳ 待开始 |
| 7 | 支付接入（先人工付款确认，后接真实服务商） | ⏳ 待开始 |
| 8 | 集成测试 | ⏳ 待开始 |
| 9 | Windows 发布：安装版 / 绿色便携版 / 卸载程序 | ⏳ 待开始 |
| 10 | 最终验收 | ⏳ 待开始 |

## 快速开始（条码核心）

```bat
cd CodeLabelStudio
scripts\run_tests.bat
```

测试使用 Python 标准库（sqlite3 / zlib / unittest），无需安装第三方依赖。
默认使用 `python`，可用环境变量指定解释器：`set PYTHON=C:\path\to\python.exe`。

## 目录结构

```
CodeLabelStudio/
├── client/            # Windows 桌面客户端
│   ├── barcode/       # 条码核心：校验位 / 编码 / 渲染 / 解码（已实现）
│   ├── database/      # SQLite：schema / DAO / 事务 / 备份（已实现）
│   ├── app/ ui/ printing/ auth/ licensing/  # 阶段 3-5
│   └── assets/
├── server/            # 在线服务端 FastAPI（阶段 4 起）
├── admin/             # 管理员 Web 后台（阶段 6 起）
├── tests/             # 自动化测试（条码核心已通过）
├── deploy/            # Docker Compose / Nginx（阶段 4 起填充）
├── installer/         # Inno Setup（阶段 9）
├── scripts/           # 构建与测试脚本
└── docs/              # 审计报告 / 测试结果 / 文档
```

## 重要说明

- 条码"可扫描性"当前通过自动化 **渲染 → 像素解码往返测试** 验证；正式发布前仍需真实条码扫描器与标签纸人工验证（见审计报告风险清单 R6）。
- 本地数据库去重仅证明"本地未重复"，不等同于全球 GS1 注册状态验证（见 `client/barcode/service.py` 中 `validate_import` 的提示语）。
