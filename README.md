# CodeLabel Studio

面向 Windows 10/11（x64）的 UPC/EAN 商品条形码生成与标签打印桌面软件。

系统由三部分组成：

1. **Windows 桌面客户端**（`client/`）：条码生成、管理、预览、打印、导出（核心条码功能本地运行）。
2. **在线用户及授权后台**（`server/`）：邮箱注册、登录、7 天试用、订阅管理、授权验证。
3. **管理员 Web 后台**（`admin/`）：用户管理、试用管理、订单管理、授权管理、运营统计。

## 版本与构建（当前 1.1.0，2026-10-22）

### 版本口径矩阵（代码/文本级核对，产物未构建）

| 维度 | 当前值 | 出处 |
|---|---|---|
| 安装器版本（`MyAppVersion` / `AppVersion`） | `1.1.0` | `installer/codelabel.iss` |
| 应用显示版本（设置 → 关于） | `1.1.0` | `client/__init__.py` `__version__` 动态展示 |
| 客户端 `__version__` | `1.1.0` | `client/__init__.py` |
| 安装包文件名 | `CodeLabelStudio-Setup-1.1.0.exe` | `OutputBaseFilename` 随版本号自动拼接 |

> `server/main.py` 的 FastAPI `version` 是在线 API 服务自身口径，与桌面发布版本独立，本轮不联动。

### 历史构建 vs 新构建（务必区分）

| 类别 | 版本 | 状态 | 说明 |
|---|---|---|---|
| **历史构建**（打印专项改造前） | 1.0.0 | 仅存档 | 2026-10-18 在真实 Windows 构建的 exe / 便携包 / `CodeLabelStudio-Setup-1.0.0.exe` 属于**打印专项前产物，不反映当前源码**（打印子系统、环境检测页、驱动指引页均为改造后新增），不得当作 1.1.0 分发，哈希记录仅作存档。 |
| **新构建**（打印专项改造后） | 1.1.0 | ⏳ 待 Windows 构建 | 当前 Linux 沙箱无 Windows / Inno Setup 编译器，`dist/CodeLabelStudio.exe`、便携包与 `CodeLabelStudio-Setup-1.1.0.exe` **均尚未构建**，无体积、无冒烟结果、无 SHA-256（禁止编造）；构建与哈希回填步骤见 `docs/BUILD_REPORT.md` 7.2–7.3。 |

git 当前无任何 tag；`v1.1.0` 标签应在 Windows 构建与真机验收通过后由维护者创建并推送。

### 打印能力清单（自动化已通过 ≠ 真机已验证）

| 能力 | 代码状态 | 实测状态 |
|---|---|---|
| Windows 通用打印队列（win32print 枚举/提交/取消/查询） | ① 自动化通过（非 win32 走守卫分支） | ④ 未实测，待真实 Windows + 队列 |
| PDF 1:1 页面尺寸测量与缩放分级（block / warn / ok，warn 可取消） | ① 自动化通过 | ④ 未实测；PDF 页面正确 ≠ 物理输出正确，驱动缩放不受软件控制 |
| 打印校准（x/y 偏移、静区/模块几何 mm 换算、生效回执） | ① 自动化通过 | ④ 未实测，待卡尺复核 |
| 驱动安装指引（仅给官方渠道指引，不携带/不静默安装驱动，UAC 提权由用户发起） | ① 决策/指引逻辑通过 | ④ 未实测，待真实驱动闭环 |
| 协议链路 ZPL / TSPL / CPCL 编码 + universal（Windows 队列）适配器 | ① 自动化通过 | ④ 未实测，待真实设备出纸 |
| TCP 9100 raw 网络通道（配置/错误映射） | ① 自动化通过 | ④ 未实测，真实连通待验 |

### 未实现通道声明（有意不做，调用即报 `TRANSPORT_NOT_IMPLEMENTED`）

- **USB 私有直连**：未集成。
- **蓝牙**（SPP / BLE）：未集成。
- **LPR（RFC 1179，515 端口）**：未实现；网络通道仅支持 9100 TCP raw。

## 当前进度（2026-10-22，截至第四轮版本统一）

| 阶段 / 轮次 | 内容 | 状态 |
| --- | --- | --- |
| 1–7（历史） | 项目审计、数据完整性 schema v2、打印导出 PDF/SVG/PNG/CSV、在线服务端、PySide6 七页面客户端、管理后台 SPA、安全审计 | ✅ 已完成（历史记录见 `docs/FINAL_AUDIT_REPORT.md` 第 2–3 节） |
| 8（历史） | Windows 发布：安装版 / 绿色便携版 / 卸载程序 | ✅ 1.0.0 历史构建并冒烟（`docs/BUILD_REPORT.md` 1–6 节，仅存档） |
| 9–11（历史） | 部署文档、端到端全链路、最终验收 | ✅ 已完成 |
| 打印专项（2026-10-19） | 打印中心 UI、设置向导、安装程序改造、兼容性/安装文档、Windows 通用打印核心与协议适配器/校准/驱动检测 | ✅ 代码完成；硬件未实测（`docs/FINAL_AUDIT_REPORT.md` 第 8 节） |
| 缺陷修复轮（2026-10-20） | 份数唯一展开 / 纸张一致性 / 校准贯通 / LabelContent 协议链路 / 传输诚实性 SENT 语义 / 任务管理 | ✅ 可自动化部分通过；硬件相关未实测 |
| 第二轮打印审计修复（2026-10-22，第三轮） | P0 RunLater 线程生命周期 / P1a 静区配置传递与 unapplied / P1b 缩放 block·warn 分级 / P3a 协议四态与 verified 守卫 | ✅ 自动化闭环（新增 114 项；客户端 711 passed/2 skipped、服务端 167 passed，EXIT=0）；真机交互/硬件未实测 |
| 版本统一（2026-10-22，第四轮） | 桌面版本口径统一 1.1.0；AppId 升级路径只读审查 | ✅ 代码/文档完成；升级/卸载与 1.1.0 构建待 Windows（`docs/FINAL_AUDIT_REPORT.md` 8B） |

## 快速开始

```bat
REM 1) 安装依赖（建议虚拟环境）
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

REM 2) 运行全部测试（客户端 + 服务端）
.venv\Scripts\python -m pytest tests/ -q
.venv\Scripts\python -m pytest tests_server/ -q

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
│   ├── printing/            # 打印：队列/PDF 1:1/校准/协议适配器(ZPL·TSPL·CPCL)/驱动指引
│   ├── auth/                # 会话存储 + 服务端 API 客户端
│   ├── licensing/           # 授权缓存 + 离线宽限 + Ed25519 验签
│   ├── ui/                  # PySide6：主题/组件/页面/主窗口
│   ├── app/                 # QApplication 入口
│   └── assets/
├── server/                  # 在线服务端 FastAPI + Alembic 迁移
│   ├── app/                 # config / database / security / models / licensing / mailer
│   ├── services/            # auth / trial / subscription / license / orders
│   ├── api/                 # auth / account / license / orders / admin
│   └── migrations/          # Alembic versions
├── admin/                   # 管理员 Web 后台（零构建 SPA）
├── tests/                   # 客户端自动化测试（含 UI 冒烟）
├── tests_server/            # 服务端 API 集成 + 安全 + 迁移测试
├── deploy/                  # Docker Compose / Nginx
├── installer/               # Inno Setup 6 脚本（1.1.0）
├── scripts/                 # 构建与测试脚本
├── run_client.py            # 桌面客户端启动器
└── docs/                    # 审计报告 / 测试结果 / 构建报告 / 打印专项文档
```

## 重要说明

- 条码"可扫描性"当前通过自动化 **渲染 → 像素解码往返测试** 验证；正式发布前仍需真实条码扫描器与标签纸人工验证（见审计报告风险清单 R6）。
- 本地数据库去重仅证明"本地未重复"，不等同于全球 GS1 注册状态验证（见 `client/barcode/service.py` 中 `validate_import` 的提示语）。
- **PDF 页面正确 ≠ 物理输出正确**：第三方驱动是否缩放、设备能否按物理尺寸走纸，软件无法确认；warn 分级与取消选项已就绪，但真机交互与 1:1 物理尺寸须在 Windows 真机用卡尺复核。
- 1.0.0 历史产物仅供存档，**不反映当前 1.1.0 源码**；1.1.0 构建、SHA-256 回填、升级/卸载与真机验收均待 Windows，详见 `docs/BUILD_REPORT.md` 与 `docs/FINAL_AUDIT_REPORT.md` 8B。
