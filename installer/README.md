# installer/ — Windows 安装与打包

当前脚本版本 **1.1.0**（`#define MyAppVersion "1.1.0"`，`AppVersion` 与
`OutputBaseFilename` 均由该宏自动跟随）。

阶段 9 实现，产出：

- `CodeLabelStudio-Setup-1.1.0.exe`（Inno Setup：安装向导、目录选择、快捷方式、卸载注册、升级不覆盖用户数据、卸载时询问是否保留数据）
- `CodeLabelStudio-Portable.zip`（绿色便携版：解压即用、免管理员权限、数据保存在便携目录内）

注意：本仓库开发环境为 Linux，未安装 Inno Setup（见审计报告风险清单 R4），
需在 Windows + Inno Setup 6 环境编译；本目录仅做脚本文本级核对，不在 Linux 上执行编译。

## 编译命令（Windows + Inno Setup 6）

前置：先运行 `scripts\build_client.bat` 生成 `dist\CodeLabelStudio.exe` 与便携版压缩包，
然后在仓库根目录执行（命令本身与历史版本一致，未改动）：

```
ISCC.exe installer\codelabel.iss
```

产物输出到 `dist\installer\CodeLabelStudio-Setup-1.1.0.exe`（文件名由
`OutputBaseFilename=CodeLabelStudio-Setup-{#MyAppVersion}` 宏自动生成，无需手改）。
本仓库开发环境为 Linux，未安装 ISCC，无法在此编译；以上为可复现的 Windows 编译步骤，
脚本本身仅做文本级核对，不在 Linux 上执行编译。1.1.0 的构建状态与校验和回填要求
见 `docs/BUILD_REPORT.md`。

## 向导页面（在原「选择数据目录」页之后新增两页）

1. **打印环境检测**（只读，不安装任何驱动）：进入页面时由
   `{sys}\WindowsPowerShell\v1.0\powershell.exe` 以隐藏窗口执行
   `Get-Printer | Format-Table -AutoSize Name,DriverName,PortName,PrinterStatus`，
   结果写入 `{tmp}\printers.txt` 并在备忘录页展示；无输出显示
   “未检测到打印机”。页面文案明确：本步骤仅检测环境，不安装任何驱动；
   PowerShell 执行失败只给出手动查看提示，不阻断安装。
2. **打印机驱动（可选）**：说明驱动安装不进入主安装流程、需从厂商官方渠道
   单独下载安装程序、安装前核对厂商/型号/来源/兼容性说明、以管理员身份运行
   （UAC 提权），并给出失败恢复步骤（卸载残留驱动 → 重启 → 官方源重装 →
   检查打印队列恢复）。1.1.0 起页面说明追加两条：
   - **启动驱动安装程序成功 ≠ 驱动安装成功**：官方安装程序被成功启动（进程提权
     运行）只代表安装向导已打开，必须等待其走完安装流程；完成后返回软件点击
     「刷新打印机」重新枚举队列，核对队列与驱动状态（四种需行动状态的提示见
     `docs/PRINTER_INSTALLATION.md` 第 3 节）。
   - 本安装程序不会修改 Windows 驱动签名检查（DSE）等任何安全设置。
   底部复选框「完成后打开打印机设置向导」：勾选后在
   `[Run]` 段以 `--printer-setup` 参数启动应用（`Flags: nowait`）。

## 驱动安装边界（重要）

- 主安装程序 **不安装任何打印机驱动**，不捆绑驱动二进制，不自动下载。
- 需要管理员权限的驱动安装，由用户对自己从官方渠道下载的安装程序执行
  “以管理员身份运行”触发 UAC；脚本不做静默安装、不关闭驱动签名检查（DSE）、
  不修改组策略或任何安全设置。
- **启动成功 ≠ 安装成功**：无论由用户手动提权还是应用内 `elevate_process`
  发起，官方安装程序被启动都只代表进程已提权运行；必须等待安装完成，再回到
  软件「刷新打印机」重新枚举并核对队列/驱动状态，不能以“安装程序已打开”作为
  安装完成的依据。
- 驱动安装失败不得影响应用安装与用户数据。

## 保持不变的既有行为

- `PrivilegesRequired=lowest`（主安装免管理员权限）。
- 数据目录选择页、`ssPostInstall` 写 `data_dir.txt` 与注册表 `DataDir`、
  升级不覆盖用户数据、卸载时询问保留/删除本地数据、卸载后清理注册表项，
  全部逻辑与原版一致，未改动一行。
