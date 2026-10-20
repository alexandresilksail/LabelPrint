# 阶段 12：Windows 发布构建报告

> 构建时间：2026-10-18
> 构建环境：Windows 10/11 + Python 3.13.12 + PySide6 6.12.0 + PyInstaller 6.22.3 + Inno Setup 6.7.3

> **历史记录声明（2026-10-20 加注）**：本报告第 1–6 节为 **1.0.0** 的构建记录，
> 属于**打印机专项改造前**的历史构建，其产物清单、体积、冒烟结果与校验信息均
> 反映当时源码，**已不反映当前源码**（当前版本为 1.1.0，打印子系统、驱动指引页、
> 环境检测页均为改造后新增）。1.1.0 的构建状态见第 7 节。

## 1. 产物清单（1.0.0 历史构建，仅供存档）

> 以下为打印机专项改造前的 1.0.0 产物记录，**不是当前 1.1.0 的产物**；
> 体积与状态仅反映 2026-10-18 的构建，不用于 1.1.0 分发校验。

| 产物 | 路径 | 体积 | 历史状态（1.0.0） |
|---|---|---|---|
| 单文件可执行 | `dist/CodeLabelStudio.exe` | 57.8 MB | ✅ 已构建并冒烟验证 |
| 绿色便携包 | `dist/CodeLabelStudio-Portable.zip` | 57.5 MB | ✅ CRC 校验通过 |
| Windows 安装包 | `dist/installer/CodeLabelStudio-Setup-1.0.0.exe` | 117 MB | ✅ 编译成功（含上面两项） |

产物均在 `.gitignore` 排除范围内（`dist/`），不入库，另行分发。

## 2. 构建命令

```bat
REM 1) 安装依赖
pip install -r requirements.txt

REM 2) 打包客户端（PyInstaller onefile + 便携 zip）
scripts\build_client.bat

REM 3) 编译安装包（需先完成第 2 步）
"C:\Program Files\Inno Setup 6\ISCC.exe" installer\codelabel.iss
```

实际使用的 PyInstaller 参数（与 `scripts\build_client.bat` 一致）：

```
--noconfirm --clean --name CodeLabelStudio --windowed --onefile
--add-data "client/assets;client/assets"
--hidden-import client.barcode.encoder|checkdigit|renderer|svg|csvio
--hidden-import client.database.db
--hidden-import client.printing.pdf
--hidden-import client.licensing.client_license
--exclude-module PySide6.QtWebEngineCore|QtWebEngineWidgets|Qt3DCore|QtQml
```

排除 QtWebEngine/Qt3D/QtQml 后体积从约 150 MB 降至 57.8 MB，条码绘制仅依赖
QtGui/QtCore/QtWidgets，功能不受影响。

## 3. 冒烟验证（实测，非声明）

### 3.1 冻结态启动

```
QT_QPA_PLATFORM=offscreen timeout 8 ./dist/CodeLabelStudio.exe
→ exit=124（timeout 主动终止，说明进程持续运行未崩溃）
→ stdout/stderr 均为空（无 ImportError、无 traceback）
```

### 3.2 数据目录与数据库初始化

首次启动自动在 exe 同级创建 `data/`：

```
dist/data/codelabel.db            4 KB
dist/data/codelabel.db-wal      144 KB
表数量: 6
表: barcode_records, numbering_ranges, operation_logs,
    print_templates, schema_meta, sqlite_sequence
schema 版本: 2
```

证明：冻结后路径解析、schema 建表、WAL 模式全部在打包环境中正常工作。

### 3.3 便携包完整性

```
CodeLabelStudio.exe  57.8 MB
CRC 校验: OK
```

### 3.4 安装包

Inno Setup 编译输出确认压缩了两项载荷：

```
Compressing: dist\CodeLabelStudio.exe
Compressing: dist\CodeLabelStudio-Portable.zip
Successful compile (35.000 sec).
→ dist\installer\CodeLabelStudio-Setup-1.0.0.exe
```

## 4. 安装包行为设计

| 项目 | 实现 |
|---|---|
| 安装范围 | `PrivilegesRequired=lowest`，按用户安装到 `%LOCALAPPDATA%\Programs\CodeLabelStudio`，无需管理员权限 |
| 数据目录选择 | `CreateInputDirPage` 自定义向导页，默认 `%LOCALAPPDATA%\CodeLabelStudio\data` |
| 客户端定位数据 | 安装时写 `{app}\data_dir.txt`；客户端 `default_data_dir()` 在 `sys.frozen` 时优先读取（已有单测 `test_frozen_mode_reads_data_dir_txt` 覆盖） |
| 升级保留数据 | 数据在 `{app}` 之外，覆盖安装天然不触碰 |
| 卸载删数据 | 卸载时弹窗询问；选择"删除"时从注册表 `HKCU\Software\CodeLabelStudio\DataDir` 读取**用户实际选择**的目录（支持自定义路径），删除后清理注册表值 |
| 语言 | 简体中文 + English |
| 快捷方式 | 开始菜单 + 可选桌面快捷方式 |

## 5. 已知限制（如实记录）

| 项目 | 状态 | 说明 |
|---|---|---|
| 静默安装实测 | ⚠️ 环境受限 | `/VERYSILENT` 在当前非交互会话中被 Inno 内层进程挂起（等待无法显示的界面），已终止进程。**安装/卸载交互流程需在真实桌面会话中人工验收** |
| 数字签名 | ❌ 未做 | 无代码签名证书；首次运行 SmartScreen 会提示"未知发布者" |
| 杀毒误报 | ⚠️ 可能 | PyInstaller onefile 产物偶被误判；如需可改用 onedir 模式或做签名 |
| 32 位系统 | ❌ 不支持 | `ArchitecturesAllowed=x64compatible`，仅 64 位 Windows 10/11 |
| 自动更新 | ❌ 未实现 | 规格未要求；升级方式为重新运行安装包（数据保留） |

## 6. 人工验收清单（1.0.0 时代，交付方需在真实桌面执行）

> 以下清单对应 1.0.0 安装包，属于历史验收记录。1.1.0 构建完成后需结合新增的
> 「打印环境检测」「打印机驱动（可选）」两页另行走一遍真实桌面验收。

1. 双击 `CodeLabelStudio-Setup-1.0.0.exe`，走完向导，确认数据目录页默认值正确。
2. 安装完成后确认开始菜单有快捷方式，启动客户端后能正常显示 7 个页面。
3. 在"生成"页生成 1 个 UPC-A，关闭客户端，重新打开确认数据仍在。
4. 再次运行安装包覆盖安装，确认数据未被清空。
5. 控制面板卸载，弹窗选择"否"（保留数据）→ 确认数据目录仍在。
6. 重新安装后卸载，弹窗选择"是"→ 确认数据目录被删除。
7. 双击 `CodeLabelStudio-Portable.zip` 解压出的 exe，确认无需安装即可运行。

---

## 7. 1.1.0 构建状态（2026-10-20）

### 7.1 如实声明：1.1.0 安装包未构建

- 当前开发环境为 **Linux 沙箱**，**无 Windows、无 Inno Setup 编译器（ISCC.exe）**，
  也无法在 Linux 上运行 PyInstaller 生成 Windows onefile 产物。
- 因此 **1.1.0 的 `dist/CodeLabelStudio.exe`、便携包与
  `dist/installer/CodeLabelStudio-Setup-1.1.0.exe` 均尚未构建**，本报告不提供
  1.1.0 的体积、冒烟结果或校验和；**任何 1.1.0 校验和在 Windows 实际构建前都
  不存在，禁止编造**。
- 当前仅完成：`installer/codelabel.iss` 版本号升至 1.1.0、驱动页文案追加两条，
  以及脚本文本级（grep / begin-end 配对 / 字符串拼接）核对，未执行编译。

### 7.2 可复现的 Windows 构建步骤

在真实 Windows 10/11（x64）上，仓库根目录执行：

```bat
REM 1) 安装依赖（含打印子系统新增的 pypdf；pywin32 仅在 win32 安装）
pip install -r requirements.txt

REM 2) PyInstaller onefile + 便携 zip（脚本已固化 spec 与 hiddenimports）
scripts\build_client.bat
REM    → 生成 dist\CodeLabelStudio.exe 与 dist\CodeLabelStudio-Portable.zip

REM 3) 编译 Inno Setup 安装包（需先完成第 2 步）
"C:\Program Files\Inno Setup 6\ISCC.exe" installer\codelabel.iss
REM    → 产物 dist\installer\CodeLabelStudio-Setup-1.1.0.exe
```

### 7.3 SHA-256 计算命令（Windows 构建后执行，校验和回填到下表）

PowerShell（推荐）：

```powershell
Get-FileHash .\dist\CodeLabelStudio.exe -Algorithm SHA256
Get-FileHash .\dist\CodeLabelStudio-Portable.zip -Algorithm SHA256
Get-FileHash .\dist\installer\CodeLabelStudio-Setup-1.1.0.exe -Algorithm SHA256
```

或 certutil：

```bat
certutil -hashfile dist\CodeLabelStudio.exe SHA256
certutil -hashfile dist\CodeLabelStudio-Portable.zip SHA256
certutil -hashfile dist\installer\CodeLabelStudio-Setup-1.1.0.exe SHA256
```

| 1.1.0 产物 | 预期路径 | SHA-256 |
|---|---|---|
| 单文件可执行 | `dist/CodeLabelStudio.exe` | 待 Windows 构建后计算并回填 |
| 绿色便携包 | `dist/CodeLabelStudio-Portable.zip` | 待 Windows 构建后计算并回填 |
| Windows 安装包 | `dist/installer/CodeLabelStudio-Setup-1.1.0.exe` | 待 Windows 构建后计算并回填 |

## 8. 打包内容核对（1.1.0，仅核对未改动 spec/requirements）

### 8.1 CodeLabelStudio.spec 已声明的打印 hiddenimports

| 类别 | spec 中已包含的模块 |
|---|---|
| 打印契约/核心 | `client.printing.capabilities`、`client.printing.errors`、`client.printing.job` |
| Windows 后端 | `client.printing.windows_print`、`win32print`、`win32api` |
| 渲染 | `client.printing.qt_print`、`client.printing.pdf_render`、`PySide6.QtPdf` |
| 校准/配置/驱动指引 | `client.printing.calibration`、`client.printing.config_store`、`client.printing.driver_installer` |
| 协议适配器 | `client.printing.adapters.zpl`、`.tspl`、`.cpcl`、`.universal`、`.registry`、`.transports` |

### 8.2 requirements.txt 打印相关依赖

| 依赖 | 用途 / 标记 |
|---|---|
| `pypdf>=5.0.0` | PDF 页面尺寸测量与缩放检测（`client/printing/pdf_render.py`），无平台标记，跨平台安装 |
| `pywin32>=306; sys_platform == 'win32'` | Windows 打印队列/驱动枚举 `win32print`，带 win32 marker，Linux 不安装 |

> 本次仅核对上述声明是否齐备，**未修改 `.spec` 与 `requirements.txt` 任何一行**。

## 9. 五级状态标注（本报告涉及项）

> 级别定义：**① 自动化通过**（当前环境自动化测试跑过）；**② Windows 构建通过**
> （已在真实 Windows 由 PyInstaller/ISCC 实际产出文件）；**③ 真实设备实测**
> （真实打印机硬件 + 真实桌面交互验证）；**④ 尚未实测**（代码/脚本就绪，目标环境
> 未验证）；**⑤ 尚未实现**（功能未做或有意不做）。

| 项目 | 级别 | 说明 |
|---|---|---|
| 打印子系统逻辑（适配器 ZPL/TSPL/CPCL/registry/transports、calibration、config_store、pdf_render、driver_installer 决策与指引） | ① 自动化通过 | Linux 环境 `pytest tests/` 456 passed, 2 skipped（skip 为 Windows 专属用例） |
| 1.0.0 单文件 exe / 便携包 / 安装包（历史） | ② Windows 构建通过 | 2026-10-18 真实 Windows 构建，属改造前产物，仅供存档 |
| 1.1.0 安装包 / exe / 便携包 | ④ 尚未实测 | Linux 沙箱无 Windows/ISCC，未构建；脚本仅文本级核对 |
| 安装向导新增「打印环境检测」页（Get-Printer 枚举展示） | ④ 尚未实测 | 代码/脚本就绪，未在真实 Windows 桌面走过 |
| 「打印机驱动（可选）」页文案与 `--printer-setup` 勾选项 | ④ 尚未实测 | 同上，待真实桌面验收 |
| win32print 队列/驱动枚举、UAC 提权启动、真实驱动安装与刷新复核 | ④ 尚未实测 | 依赖真实 Windows + 真实打印机，Linux 无法验证 |
| 驱动安装失败恢复流程 | ④ 尚未实测 | 指引与状态机就绪，未在失败真机上演练 |
| 数字签名 | ⑤ 尚未实现 | 无代码签名证书；SmartScreen 将提示"未知发布者" |
| 自动更新 | ⑤ 尚未实现 | 规格未要求；升级方式为重跑安装包（数据保留） |
| 32 位 Windows 支持 | ⑤ 尚未实现（有意不支持） | `ArchitecturesAllowed=x64compatible`，仅 x64 Win10/11 |
