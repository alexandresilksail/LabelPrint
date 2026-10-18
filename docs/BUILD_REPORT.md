# 阶段 12：Windows 发布构建报告

> 构建时间：2026-10-18
> 构建环境：Windows 10/11 + Python 3.13.12 + PySide6 6.12.0 + PyInstaller 6.22.3 + Inno Setup 6.7.3

## 1. 产物清单

| 产物 | 路径 | 体积 | 状态 |
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

## 6. 人工验收清单（交付方需在真实桌面执行）

1. 双击 `CodeLabelStudio-Setup-1.0.0.exe`，走完向导，确认数据目录页默认值正确。
2. 安装完成后确认开始菜单有快捷方式，启动客户端后能正常显示 7 个页面。
3. 在"生成"页生成 1 个 UPC-A，关闭客户端，重新打开确认数据仍在。
4. 再次运行安装包覆盖安装，确认数据未被清空。
5. 控制面板卸载，弹窗选择"否"（保留数据）→ 确认数据目录仍在。
6. 重新安装后卸载，弹窗选择"是"→ 确认数据目录被删除。
7. 双击 `CodeLabelStudio-Portable.zip` 解压出的 exe，确认无需安装即可运行。
