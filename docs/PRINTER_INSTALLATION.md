# 打印机驱动安装指引（PRINTER_INSTALLATION）

> 本文档对应 `client/printing/driver_installer.py` 的能力：**只做检测与指引（guidance only），
> 应用与安装包绝不捆绑、绝不自动下载、绝不自动安装任何打印机驱动。**

## 0. 版本（1.1.0）与构建说明

- 本文档对应安装器脚本 `installer/codelabel.iss` 版本 **1.1.0**。
- 1.1.0 安装包**尚未在本环境构建**：开发环境为 Linux 沙箱，无 Windows、无
  ISCC.exe。可复现的 Windows 构建步骤（`scripts\build_client.bat` →
  `ISCC.exe installer\codelabel.iss` → `dist\installer\CodeLabelStudio-Setup-1.1.0.exe`）
  与 SHA-256 回填要求见 `docs/BUILD_REPORT.md` 第 7 节；本文不重复罗列命令，
  不提供任何未构建产物的校验和。

## 1. 应用安装（无需管理员权限）

- 主安装包以 `PrivilegesRequired=lowest` 打包，普通用户双击即可安装，**不需要管理员权限**。
- 安装向导会先让你选择数据目录（默认 `%LOCALAPPDATA%\CodeLabelStudio\data`），
  升级与卸载默认保留该目录数据。
- 打印机驱动属于系统组件，**不在主安装流程内**；是否安装、何时安装由你自行决定。

## 2. 打印环境检测步骤

> 级别标注：检测逻辑（`windows_print.list_printers()` / `get_driver_info()` /
> `detect_driver_state()` 决策）为 **① 自动化通过**；在真实 Windows 队列上的
> 枚举结果展示为 **④ 尚未实测**。

安装向导「打印环境检测」页（只读，不安装任何驱动）会调用系统 PowerShell：

```powershell
Get-Printer | Format-Table -AutoSize Name,DriverName,PortName,PrinterStatus
```

- 结果按「队列名 / 驱动名 / 端口 / 状态」列出，供你核对。
- 列表为空表示当前 Windows 未检测到打印机队列；检测失败可在
  Windows「设置 → 蓝牙和设备 → 打印机」中手动查看。
- 应用内「打印机设置」页也会通过冻结接口 `windows_print.list_printers()` /
  `get_driver_info()` 做同样的只读检测，不改动系统。

## 3. 驱动状态分类与对应用户提示

> 级别标注：`detect_driver_state()` 优先级决策与提示文案为 **① 自动化通过**
> （单测覆盖）；六种状态在真实打印机上的实际判定为 **④ 尚未实测**。

检测结果由 `detect_driver_state()` 按固定优先级决策，共 6 种状态，其中 4 种需要你行动：

| 状态 | 含义 | 应用给出的提示要点 |
|---|---|---|
| DRIVER_OK | 队列存在、驱动已绑定、设备在线 | 驱动就绪：建议先打印一张**标准测试页**做**校准**，确认标签尺寸、浓度与走纸方向 |
| DRIVER_MISSING | 队列存在但未绑定驱动 | 到厂商官网按型号**官方下载** Windows 驱动 → 右键「**以管理员身份运行**」官方安装程序 → 在 **UAC** 确认框点「是」 |
| DRIVER_INSTALL_FAILED | 上次驱动安装失败被标记 | 先**卸载**残留驱动 → **重启**电脑 → 从**官方源**重新下载重装 → 检查**打印队列**是否恢复 |
| DEVICE_OFFLINE | 队列/驱动在，但设备报告离线 | 检查**电源**、连接方式与**线缆**（USB/网络）→ 在队列中**解除暂停** → 运行 **Windows 疑难解答** |
| QUEUE_MISSING | 系统里找不到该队列 | 先在 Windows 打印机设置中确认设备已连接，再从官方渠道装驱动建队列 |
| UNKNOWN | 尚无目标打印机/无法判断 | 先连接设备、确认队列存在，再按官方渠道获取对应型号驱动 |

## 4. 官方驱动下载指引表

> **安装前必须核对：厂商、完整型号、来源页面、兼容性说明（适用 Windows 版本）。**
> 下表 URL 均为厂商官方支持/下载页（2026-10 核查）；页面结构可能调整，若链接跳转，
> 请在官网内自行进入「支持 / 服务与支持 / 下载中心」按型号检索。**不要使用第三方驱动下载站。**

| 厂商 | 官方 URL | 兼容性 / 获取说明 | 已知签名状态 |
|---|---|---|---|
| Zebra（斑马） | https://support.zebra.com/ | 进入产品支持页 → Downloads → Driver，按型号（ZD420、ZT230 等）下载 ZDesigner Windows 驱动 | 未核实 |
| TSC（台半） | https://www.tscprinters.com/global/en/downloads | 官方提供 WHQL 认证 Windows 驱动（Win10/11 32/64 位），按 TDP/TE/TA/TX 系列下载 | 已知 WHQL |
| Godex（科诚） | https://www.godexintl.com/downloads | 官方下载中心按产品/文件类型筛 Driver，覆盖 G500、RT、EZ、DT 系列 | 未核实 |
| Brother（兄弟） | https://support.brother.com/ | Brother Solutions Center 输入完整型号（TD-4550DNWB、QL-1110NWB 等）取驱动 | 未核实 |
| Honeywell（霍尼韦尔） | https://automation.honeywell.com/us/en/products/productivity-solutions/printers | 找对应型号（PC/PM/PX/XD 系列）下载 InterDriver Windows 驱动；旧 honeywellaidc.com 已并入 | 未核实 |
| Star Micronics（星光） | https://starmicronics.com/support/ | 按产品（mC-Label、TSP、mPOP 等）进入支持页下载 Star Windows Software / JavaPOS 驱动 | 未核实 |
| Epson（爱普生） | https://epson.com/Support/Printers | LabelWorks / ColorWorks 按型号下载；中国大陆用户改用 epson.com.cn 服务与支持 | 未核实 |
| SATO（佐藤） | https://www.sato-global.com/drivers/redirect.html | 官方驱动统一跳转入口，按地区/型号取 Windows Gallery 驱动与 All-In-One Tool | 未核实 |
| HPRT（汉印） | https://cn.hprt.com/ | 官网顶部「下载中心 → 驱动下载」，输入完整型号（FT/N/D/X 系列）检索 | 未核实 |
| Gprinter（佳博/Gainscha） | https://www.gainscha.com/ | 按条码机系列（17/18/19/20/21、CH、CL、M、60 系列）进入支持资料页取官方驱动 | 未核实 |
| Xprinter（芯烨） | https://www.xprinter.net/Driver_Download/p-0-12.html | 官网「驱动下载」频道，按 58mm/80mm 热敏与条码系列检索 | 未核实 |

未列入上表的厂商：应用**不会编造 URL**，只提示你前往该厂商官网支持页按型号搜索。

## 5. UAC 提权流程

驱动安装需要管理员权限，全程由 Windows UAC 授权，应用不自行提权：

1. **推荐（手动）**：在文件资源管理器中右键你自己从官方渠道下载的驱动安装程序
   →「以管理员身份运行」→ UAC 弹窗点「是」→ 按官方向导完成安装。
2. **应用内辅助（`elevate_process`）**：仅当你已经把官方安装包下载到本地后，
   应用可通过 ctypes `ShellExecuteW(None, "runas", …)` 发起提权启动，UAC 弹窗仍由
   Windows 呈现：
   - `LAUNCHED`：已发起（不代表安装成功）；
   - `REQUIRES_UAC`：需要管理员/UAC 授权；
   - `CANCELLED_BY_USER`：你在 UAC 点了「否」；
   - `FAILED`：启动失败（附系统错误码）。
3. 两种方式都**只启动用户自备的官方安装程序**；应用不下载、不释放安装包。

### 5.1 驱动启动 ≠ 驱动安装成功（重要）

- **安装程序被成功启动（拿到 `LAUNCHED` / 手动提权打开向导）只代表进程已提权
  运行，绝不等于驱动已经装好。** 官方安装向导可能还要点击下一步、等待复制文件、
  选择端口并最终提示完成。
- 正确闭环：**等待官方安装程序自行走完并显示安装完成** → 回到本软件，点击
  「刷新打印机」重新枚举队列 → 核对队列是否出现、驱动是否绑定、设备是否在线
  （对照第 3 节四种需行动状态的提示）。
- 切忌看到安装窗口打开就关闭软件或判定完成；若安装中途取消/失败，检测结果会
  落到 `DRIVER_INSTALL_FAILED` 或 `DRIVER_MISSING`，按第 7 节恢复步骤处理。
- 本软件与安装包**不会修改 Windows 驱动签名检查（DSE）**、组策略或任何安全设置，
  也不提供签名旁路。

## 6. 安全红线（不可绕过）

> 级别标注：以下约束在代码与安装脚本中均已落实并有自动化/文本级核对，属
> **① 自动化通过**；"绝不在任何环境被绕过"的运行时保证属于设计承诺，
> **④ 尚未在受攻击/异常路径下实测**。

- **不捆绑驱动**：安装包与应用内不携带任何厂商驱动二进制。
- **不自动安装**：不自动下载、不静默安装、不调用 `pnputil /add-driver`。
- **不安装未验证/未签名驱动**：`allow_unsigned` 默认 `False`，且应用不提供任何签名旁路。
- **不关闭驱动签名检查（DSE）**：绝不调用 `bcdedit` 关闭测试签名/签名强制。
- **不降低安全设置**：不修改组策略、注册表安全项或任何 Windows 安全配置。
- 非 Windows 平台调用检测/提权接口直接返回 `PrintError(UNSUPPORTED_PLATFORM)`。

## 7. 驱动安装失败恢复步骤

> 级别标注：本流程为指引文案 + `DRIVER_INSTALL_FAILED` 状态提示（**① 自动化通过**）；
> 在真实失败环境（残留驱动 → 重启 → 官方源重装 → 队列恢复）的完整演练为
> **④ 尚未实测**。

1. 在 Windows「设置 → 蓝牙和设备 → 打印机」与「设备管理器」中**卸载残留驱动/设备**；
2. **重启电脑**（让残留驱动从内核卸载干净）；
3. 回到上表**官方源**重新下载对应完整型号的驱动（不要用上次失败的安装包）；
4. 右键「以管理员身份运行」重装；
5. 安装完成后**检查打印队列**是否出现、状态是否恢复就绪，再回到应用重新检测。

## 8. 常见问题（FAQ）

- **队列暂停**：打印机图标显示「已暂停」时，打开队列 →「打印机」菜单取消「暂停打印」，
  必要时取消「脱机使用打印机」。
- **设备离线**：先查电源开关与指示灯，USB 重新拔插或换口；网络打印机确认与电脑
  在同一网段、IP 未变；再运行 Windows 打印机疑难解答。
- **无默认打印机**：Windows 设置中将目标队列「设为默认」；应用无默认队列时会提示
  `NO_DEFAULT_PRINTER`，不强行选机。
- **端口占用/找不到端口**：USB 打印机先装驱动再插线；网络打印机核对端口 IP
  （`IP_xxx.xxx.xxx.xxx`）未被其他设备占用；避免多个虚拟端口并存。
- **测页正常但标签尺寸不对**：先在应用内做 1:1 尺寸校准与标签间隙传感，
  不要反复重装驱动。

## 9. 五级状态区分说明

本文档与 `docs/BUILD_REPORT.md` 统一使用以下五级标签，避免把"代码写了"误读为
"已经在真机验证过"：

| 级别 | 含义 |
|---|---|
| ① 自动化通过 | 当前环境（Linux）自动化测试已跑过该逻辑（`pytest` 通过） |
| ② Windows 构建通过 | 已在真实 Windows 由 PyInstaller / ISCC 实际产出可分发文件 |
| ③ 真实设备实测 | 已在真实打印机硬件 + 真实桌面交互下验证通过 |
| ④ 尚未实测 | 代码/脚本/指引就绪，但目标环境（Windows/真机）未验证 |
| ⑤ 尚未实现 | 功能未做，或按设计有意不做 |

### 9.1 本文档驱动安装相关项的级别

| 事项 | 级别 | 依据 / 待办 |
|---|---|---|
| 驱动状态决策 `detect_driver_state()`（6 状态优先级、4 条行动提示） | ① 自动化通过 | 单测覆盖；真机判定待验 |
| 官方驱动来源表与"不编造 URL"逻辑 | ① 自动化通过 | 来源页 2026-10 人工核查；页面结构可能调整 |
| `elevate_process` 提权启动（LAUNCHED/REQUIRES_UAC/CANCELLED/FAILED） | ① 自动化通过（分支逻辑）/ ④ 尚未实测（真机 UAC） | Linux 无法触发 Windows UAC |
| 启动成功 ≠ 安装成功的复核闭环（等待完成 → 刷新打印机 → 核对队列/驱动） | ④ 尚未实测 | 需真机走完整安装流程 |
| 真实驱动安装、队列绑定、设备在线判定 | ④ 尚未实测 | 需真实 Windows + 真实打印机 |
| 失败恢复流程（卸载残留 → 重启 → 官方源重装 → 队列恢复） | ④ 尚未实测 | 需真实失败环境演练 |
| 安全红线（不捆绑/不自动安装/不关闭 DSE/不改组策略） | ① 自动化通过（代码落实）/ ④ 异常路径未实测 | 无签名旁路、无 `bcdedit` 调用 |
| 1.1.0 Windows 安装包构建 | ④ 尚未实测（未构建） | Linux 沙箱无 ISCC；步骤见 BUILD_REPORT.md 第 7 节 |
| 数字签名、自动更新 | ⑤ 尚未实现 | 无证书 / 规格未要求 |
