# CodeLabel Studio — 打印机兼容性说明（PRINTER_COMPATIBILITY）

> 本文档只描述**兼容性主张与依据**，不包含任何可执行代码；不代表任何品牌/型号已通过真实设备验证。
> 适用客户端：PySide6 桌面应用（目标运行环境 Windows 10/11）。
> 编写日期：2026-10-19。所有"理论可行/支持"均为基于官方公开文档的工程推断，**不等于已在真实设备上验证**。

---

## 1. 总览与结论

### 1.1 已实现路径与未实测路径（按当前工程事实）

| 路径 | 实现状态 | 实测状态 | 说明 |
|---|---|---|---|
| 通用 Windows 打印（Qt `QPrinter` + `QPageLayout`/`QPageSize` 精确毫米、`ExactMatch` 防自动缩放；`win32print` 枚举队列/驱动/原始数据 SpoolFile） | 已实现路径 | **未实测（待验证）** | 本开发环境为 Linux 沙箱，无 Windows、无打印机硬件，无法提交真实队列任务 [S1][S5][S6][S7][S8] |
| ZPL 专用协议适配器 | 按公开规范生成指令（已实现） | **未实测（待验证）** | 依据 Zebra 官方 ZPL II 编程指南生成；未连接任何 ZPL 设备 [S9][S10] |
| TSPL2 专用协议适配器 | 按公开规范生成指令（已实现） | **未实测（待验证）** | 依据 TSC 官方 TSPL/TSPL2 Programming Manual 生成；未连接任何 TSPL2 设备 [S12] |
| CPCL 专用协议适配器 | 按公开规范生成指令（已实现） | **未实测（待验证）** | 依据 Zebra/Honeywell(CPCL/EMZ) 公开手册生成；未连接任何 CPCL 设备 [S11][S14] |
| USB / 网络 / 蓝牙传输层 | 仅传输框架抽象 | **未实测（待验证）** | 未在任何真实连接上收发数据；不能假定所有设备具有相同传输接口 |

### 1.2 结论（硬性声明）

1. **通用 Windows 打印是本期最通用的兜底路径**：应用通过 Windows 打印队列提交，渲染与页面控制由 Windows 驱动与假脱机程序（print spooler）完成，应用只提交设备无关的绘制指令 [S1][S2]。
2. **专用协议（ZPL/TSPL2/CPCL）按公开规范生成，但全部未经真实设备实测**；"兼容型号"仅表示协议语法上可能被识别，**不代表指令序列在该型号上输出正确**。
3. **本环境无任何打印机硬件、无 Windows**，因此本文档中**没有任何品牌、任何型号可被标记为"已验证兼容"**；矩阵"是否通过实际设备测试"一列**一律为"未实测（待验证）"**。
4. 从"待验证"升级为"已验证"必须走 `docs/PRINTER_TEST_REPORT.md` 人工验收流程（见第 7 节），严禁仅凭文档或厂商宣传回填"已验证"。
5. 通用路径的真实输出尺寸受**厂商驱动自身行为**影响：应用可以用毫米单位与 `ExactMatch` 表达期望尺寸 [S7][S8]，但驱动是否缩放、是否拒绝自定义表单，应用无法单方面保证。

---

## 2. 八类兼容矩阵总表（8 行 × 8 字段）

### 2.1 阅读约定

- 表格中"理论可用/理论支持"= 基于官方文档的工程推断，**均未实测**；"未实测（待验证）"为本环境统一结论。
- 依据编号对应第 8 节来源清单。
- 图例：✅ 理论可行（待验证）｜⚠️ 视驱动/设备而定（待验证）｜— 不适用/不需要。

### 2.2 总表

| # | 类别 | ① 通用 Windows 打印是否可用 | ② 是否需要厂商驱动 | ③ 是否支持指定纸张尺寸 | ④ 是否支持真实尺寸(1:1)打印 | ⑤ 是否需要专用协议 | ⑥ 是否通过实际设备测试 | ⑦ 已知限制（摘要） | ⑧ 推荐配置（摘要） |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 普通激光打印机 | ✅ 理论可用（走 GDI/XPS 队列，未实测）[S1][S6] | 否（Windows Update/系统兼容驱动通常足够）[S3][S4] | ✅ 标准 A4/A5 等；自定义尺寸受驱动表单限制 [S7] | ⚠️ 标准纸上可做到；驱动可能缩放，未实测 | — 不需要 | 未实测（待验证） | 无连续纸/标签间隙检测；窄幅标签不适用；缩放由驱动/打印对话框决定 [S1] | 通用队列路径；`QPageLayout` 用毫米单位、`QPageSize::ExactMatch`；打印前 100% 预览核对 [S7][S8] |
| 2 | 普通喷墨打印机 | ✅ 理论可用（走队列，未实测）[S1][S6] | 否（Windows Update 或厂商安装包均可）[S3] | ✅ 标准纸张；自定义尺寸受驱动限制 [S7] | ⚠️ 取决于驱动是否缩放，未实测 | — 不需要 | 未实测（待验证） | 无边距/墨量控制与标签场景无关；不适合连续窄幅标签 | 同激光：通用队列 + 毫米精确布局 + ExactMatch [S7][S8] |
| 3 | 热敏标签打印机（直热式） | ✅ 理论可用（厂商驱动 + 队列，未实测）[S16][S3] | 是（通用路径需装厂商 Windows 驱动）[S16][S13] | ⚠️ 驱动内建/自定义表单；`ExactMatch` 防止被模糊匹配到标准纸 [S7] | ⚠️ 自定义表单 + 精确毫米；专用协议用 SIZE 指令 [S12] | 可选（ZPL/TSPL2/CPCL 非必须）[S9][S11][S12] | 未实测（待验证） | 热敏纸保存期有限；速度/密度需校准；间隙/黑标检测由设备负责 [S12] | 优先通用队列 + 厂商驱动；窄幅自定义标签表单；实测前不得宣称兼容 [S16] |
| 4 | 热转印标签打印机 | ✅ 理论可用（厂商驱动 + 队列，未实测）[S15][S16] | 是（厂商驱动）[S15][S16] | ⚠️ 同热敏：驱动表单 + ExactMatch [S7] | ⚠️ 同热敏：驱动表单或协议 SIZE 指令 [S12] | 可选 [S9][S11][S12] | 未实测（待验证） | 需匹配碳带与介质；打印浓度/速度影响 1:1 与可读性 | 通用队列兜底；协议适配器按手册生成、待实测 [S15] |
| 5 | USB 标签打印机（连接维度） | ✅ 理论可用（PnP 枚举为 USB00x 队列，未实测）[S4][S17] | 是（即插即用常自动安装，建议厂商 WHQL 驱动）[S3][S17] | ⚠️ 取决于驱动表单 [S7] | ⚠️ 取决于驱动，未实测 | 可选 | 未实测（待验证） | USB 集线器可能无法被正确识别；裸 USB 私有协议未实现/未实测 [S25] | 先走队列通用路径；私有 USB 直连不在本期范围 [S17] |
| 6 | 网络打印机（以太网/Wi-Fi） | ✅ 理论可用（Windows 添加网络打印机/共享队列或 TCP/IP 端口，未实测）[S4][S17] | 是（厂商驱动提供网络端口）[S17] | ⚠️ 取决于驱动表单 [S7] | ⚠️ 取决于驱动/协议，未实测 | 可选；9100 原始直送属未来路径 [S5] | 未实测（待验证） | 网络发现/防火墙；共享队列与 9100 直连行为不同；RAW API 本地/网络队列均可 [S5] | 通用队列优先；记录 IP/端口；直连 9100 待实测 [S17] |
| 7 | 蓝牙标签打印机 | ⚠️ 理论可配对为队列设备（未实测）[S4][S17] | 通用路径需要（安装向导含 Bluetooth Port）[S17] | ⚠️ 取决于驱动，未实测 | ⚠️ 取决于驱动，未实测 | 可选；SPP/BLE 直连未实现/未实测 | 未实测（待验证） | 配对与驱动差异大；便携机多用私有 BLE/SPP；不可假定与 USB 同接口 | 先系统配对→队列→通用路径；BLE 直连不在本期 [S4][S17] |
| 8 | 工业标签打印机（4–6 英寸/重型） | ✅ 理论可用（厂商驱动队列，未实测）[S15][S16] | 是（厂商驱动，如 InterDriver/ZDesigner）[S15][S16] | ⚠️ 驱动自定义表单；协议 SIZE 指令 [S9][S12] | ⚠️ 驱动或协议均可，未实测 | 常见（ZPL/TSPL2/DPL/IPL/ZSim）[S15] | 未实测（待验证） | 多协议仿真（如 ZSim2）≠ 原生；分辨率 203/300/600dpi 差异；大批量任务 | 队列兜底；协议按手册生成并待实测；记录 dpi 与介质规格 [S15] |

> 注：第 5/6/7 类是**连接方式维度**，与第 3/4 类（成像方式维度）存在交叉：一台 USB 直连的热敏标签机同时落入第 3 类与第 5 类。

---

## 3. 分类详细说明

每类按统一机理展开：**驱动需求 → 指定纸张尺寸 → 1:1 真实尺寸 → 限制 → 推荐配置**。

### 3.1 普通激光打印机

- **驱动需求机理**：Windows 打印体系由假脱机程序与一组打印机驱动组成；应用调用设备无关函数创建任务并送往设备，驱动的渲染组件把应用图形命令转换为设备可用格式 [S1]。激光机通常可由 Windows Update 或系统兼容驱动覆盖，不强制安装厂商驱动 [S3][S4]。
- **指定纸张尺寸**：`QPageSize` 基于 PostScript PPD 标准定义 A4/A5/Letter 等标准尺寸，单位支持毫米；自定义尺寸可保留原始单位作为计算基准 [S7]。标准纸尺寸可由 Qt 与驱动协商。
- **1:1 真实尺寸**：`QPrinter::setResolution(dpi)` 请求目标分辨率，`setPageLayout()` 告知期望页面方向与尺寸；但最终缩放由 Windows 驱动决定，应用无法强制 [S6]。
- **限制**：无标签间隙/黑标检测；不适合窄幅连续标签；打印对话框中"适合页面/无边距"等选项可能引入缩放。
- **推荐配置**：通用队列路径；`QPageLayout` 以毫米为单位 [S8]，自定义尺寸用 `QPageSize::ExactMatch` 防止被 `FuzzyMatch` 调整到邻近标准纸 [S7]；打印前以 100% 预览核对。

### 3.2 普通喷墨打印机

- **驱动需求机理**：同 3.1；驱动获取途径为 Windows Update 或制造商官网支持/驱动区按型号下载 [S3]。
- **指定纸张尺寸 / 1:1**：同激光；标准纸协商可靠，自定义窄幅尺寸受驱动表单限制 [S7]。
- **限制**：喷墨的无边距与墨量控制与标签场景无关；同样不适合连续窄幅标签。
- **推荐配置**：同 3.1。

### 3.3 热敏标签打印机（直热式，Direct Thermal）

- **驱动需求机理**：通用队列路径下必须先在 Windows 安装厂商 Windows 驱动（如 Zebra ZDesigner、TSC/国产机厂商驱动），驱动把 GDI 渲染结果转为设备数据 [S1][S16]。微软官方建议驱动优先来自 Windows Update，找不到时从制造商官网支持区按型号下载 [S3]。
- **指定纸张尺寸机理**：应用侧用毫米构造 `QPageSize`，并以 `ExactMatch` 策略避免 `FuzzyMatch`（3pt 容差）把自定义尺寸调整为邻近标准纸 [S7]；设备侧由驱动的自定义纸张表单（form）承接 [S2]。
- **1:1 机理**：通用路径依赖驱动表单按毫米输出；专用协议路径由设备指令直接定义介质（TSPL2 的 `SIZE`/`GAP` 指令、ZPL 的 `^LL`/`^PW` 等），点密度由设备 dpi 决定 [S12][S9]。
- **限制**：热敏纸随时间/温度褪色；打印速度与浓度需现场校准；标签间隙（GAP）/黑标定位由设备传感器完成，应用不参与走纸 [S12]。
- **推荐配置**：优先"通用队列 + 厂商驱动"；在驱动中建立与实物一致的窄幅自定义标签表单；任何"兼容"结论均需第 7 节验收后回填 [S16]。

### 3.4 热转印标签打印机（Thermal Transfer）

- **驱动需求机理**：同 3.3；以 Honeywell PC45T 为例，其兼容驱动明确列出 InterDriver Windows printer driver，命令语言支持 FP/DP/IPL/ZSim2(ZPL-II)/DPL 等 [S15]。
- **指定纸张尺寸 / 1:1**：同 3.3；协议路径由 SIZE 类指令定义 [S12]。
- **限制**：需碳带与介质匹配；打印浓度、速度影响可读性与实际尺寸稳定性。
- **推荐配置**：队列通用路径兜底；专用协议仅在验收后启用；记录 dpi、碳带/介质规格 [S15]。

### 3.5 USB 标签打印机（连接维度）

- **驱动需求机理**：Windows 本地/USB 打印机插入后通常自动安装；未自动安装时在"设置→蓝牙和设备→打印机和扫描仪"中添加设备 [S4]。Zebra 安装向导将通信选项分为 Network Port / USB Port / Bluetooth Port / Other(LPT/COM) [S17]。
- **指定纸张尺寸 / 1:1**：取决于所装驱动的表单，机理同 3.3 [S7]。
- **限制**：经 USB 集线器连接时设备可能无法被正确识别（Brother QL 系列快速安装指南明确提示）[S25]；私有 USB 裸协议（绕过队列）本期未实现、未实测。
- **推荐配置**：优先"系统队列 + 厂商驱动"；记录端口名（USB001 等）；不要假定可直接发私有 USB 字节流。

### 3.6 网络打印机（以太网 / Wi-Fi）

- **驱动需求机理**：安装网络/无线打印机时需与电脑处于同一网络，在"添加打印机"中发现或手动指定，可由厂商驱动创建 TCP/IP 网络端口 [S4][S17]。
- **指定纸张尺寸 / 1:1**：取决于驱动表单与所选协议，未实测 [S7]。
- **限制**：网络发现受防火墙/网段影响；Windows 共享队列与裸 9100 端口直连是两条不同路径；Win32 RAW 原始数据 API 对本地与网络队列均适用（`OpenPrinter`→`StartDocPrinter(RAW)`→`WritePrinter`），但本期未走该路径 [S5]。
- **推荐配置**：通用队列优先；记录打印机 IP、端口、队列名；9100 原始直连列为待验证未来路径 [S17][S5]。

### 3.7 蓝牙标签打印机

- **驱动需求机理**：Windows 需先完成蓝牙配对，再作为打印机设备添加；厂商驱动安装向导提供 Bluetooth Port 选项 [S4][S17]。
- **指定纸张尺寸 / 1:1**：取决于配对后所装驱动，未实测。
- **限制**：配对方式、驱动支持因机型差异很大；便携标签机常使用私有 BLE 或 SPP 协议直连，与 USB/网络队列不是同一接口，**不能假定所有设备具有相同传输接口**。
- **推荐配置**：先系统配对→装驱动→队列通用路径；BLE/SPP 私有直连不在本期范围，列为待验证。

### 3.8 工业标签打印机（4–6 英寸 / 重型）

- **驱动需求机理**：工业机普遍要求厂商 Windows 驱动（InterDriver、ZDesigner 等）[S15][S16]；部分设备提供多种命令语言仿真，如 Honeywell PC45T 支持 FP/DP/IPL/ZSim2(ZPL-II)/DPL [S15]。
- **指定纸张尺寸 / 1:1**：队列路径靠驱动自定义表单；协议路径靠设备指令（ZPL `^PW`/`^LL`、TSPL2 `SIZE`）[S9][S12]。
- **限制**：协议仿真（ZSim2 等）不等于原生支持全部命令；203/300/600 dpi 分辨率不同导致点尺寸换算差异；大批量任务需评估队列稳定性。
- **推荐配置**：队列兜底；协议适配器按官方手册生成；验收时记录 dpi、介质宽高（mm）、间隙/黑标 [S15]。

---

## 4. 协议适配器状态表

| 协议 | 规范来源（官方） | 实现状态 | 适用设备（兼容性均未逐一实测） | 未实测风险 |
|---|---|---|---|---|
| ZPL (ZPL II) | Zebra 在线 ZPL 命令手册 [S9]；ZPL II Programming Guide (P1099958) 下载入口 [S10] | 按公开规范生成指令；**未实测** | Zebra 原生 ZPL 机型；支持 ZSim2/ZSIM 仿真的第三方设备（如 Honeywell PC45T 的 ZSim2）[S15] | 仿真模式可能不支持全部命令；字体/图形指令行为未验证 |
| TSPL2 | TSC TSPL/TSPL2 Programming Manual 官方 PDF [S12] | 按公开规范生成指令；**未实测** | TSC 原生机型；声称 TSPL/TSPL2 兼容的第三方机型（含 Godex 兼容指令集、国产桌面机） | 兼容机型指令集裁剪不一致；`SIZE`/`GAP`/`DENSITY` 行为未验证 [S12] |
| CPCL | Zebra《CPCL Manual for Zebra Mobile Printers》入口 [S11]；Honeywell/Datamax-O'Neil 快速参考（EMZ 扩展 CPCL）[S14] | 按公开规范生成指令；**未实测** | Zebra 移动 CPCL 机型；Datamax-O'Neil EMZ 仿真设备；早期 Comtec CPCL 设备 | 固件版本决定可用命令；QLn 等原生 ZPL 机型可能不支持全部 CPCL 命令 [S11][S14] |

> 重要：上表"适用设备"仅为协议层面的可能性描述，**不构成兼容性承诺**；所有型号均需第 7 节验收。

---

## 5. 传输方式（USB / 网络 / 蓝牙）说明与实测状态

| 传输方式 | 本期处理方式 | 实测状态 | 关键事实依据 |
|---|---|---|---|
| 本机队列（LPT/COM/系统虚拟端口） | 通用队列枚举与提交框架 | 未实测（待验证） | 假脱机程序把任务送往串行/并行/网络端口驱动 [S1][S2] |
| USB | 仅端口推断框架（按 USB00x/usb# 前缀归类）；未实现私有 USB 直连 | 未实测（待验证） | 系统即插即用与厂商 USB Port 安装 [S4][S17]；集线器可能导致识别失败 [S25] |
| 网络（TCP/IP、共享队列、9100） | 仅端口推断框架（IP_/UNC/TCPIP）；9100 RAW 直连未启用 | 未实测（待验证） | 网络打印机安装与厂商网络端口 [S4][S17]；RAW 原始数据 API [S5] |
| 蓝牙 | 仅端口推断框架（BT/Bluetooth）；SPP/BLE 直连未实现 | 未实测（待验证） | 系统配对 + Bluetooth Port 安装 [S4][S17] |

**为何不能假定所有设备具有相同传输接口**：同一品牌内不同机型的可用接口不同（USB 必备、以太网/Wi-Fi/蓝牙为选配）；不同品牌的私有直连协议互不兼容；队列路径（驱动中介）与裸协议直连（绕过驱动）是两条独立技术路线。因此传输层必须按 USB/网络/蓝牙分别处理，不能用一个"通用连接"假设覆盖全部设备。

---

## 6. 环境限制声明

- 开发/构建环境为 Linux 沙箱，**无 Windows 操作系统、无任何打印机硬件、无真实队列**。
- 因此：① 无法枚举真实队列与驱动；② 无法验证 1:1 输出尺寸；③ 无法验证协议指令在设备上的实际走纸与打印；④ 无法验证三种传输链路的连通性。
- 本文档所有"理论支持"均不得在对外宣传中表述为"已兼容/已验证"。

---

## 7. 如何将型号从"待验证"升级为"已验证"

验收记录统一写入 `docs/PRINTER_TEST_REPORT.md`（人工验收清单；该文件由配套验收流程维护）。每一台被测设备必须登记以下字段：

1. **设备品牌与完整型号**（含硬件版本）；
2. **连接方式**（USB / 以太网 / Wi-Fi / 蓝牙 / 本机队列）及端口或 IP；
3. **驱动名称与版本**（厂商驱动/Windows Update 驱动，驱动签名情况）；
4. **标签纸规格**：宽 × 高（mm）、类型（直热/热转印）、间隙或黑标规格、碳带（如适用）；
5. **测试日期与测试人**；
6. **测试项与结果**：队列提交、指定尺寸、1:1 实测（卡尺量测）、条码可扫描性、连续走纸；
7. **失败现象与备注**（报错码、错位、缩放、缺字等）。

**回填矩阵流程**：

1. 在真实 Windows 10/11 机器 + 真实设备上完成上述验收并留档（测试页照片/卡尺记录）；
2. 在 `PRINTER_TEST_REPORT.md` 新增一行通过记录；
3. 回到本文档对应类别，把"未实测（待验证）"改为"已验证（见验收记录编号）"，并在该行标注验收记录编号；
4. 仅当存在真实验收记录时，`verified_with_hardware` 才允许为 True，严禁伪造。

---

## 8. 来源清单

> 核实状态说明：✅ = 本次用 web_fetch 实际打开并读取到正文；⚠️ = 检索可见但本次未能直接打开核实（动态页/robots 限制），仅作入口指引；❌ = 未采用。

### 8.1 Microsoft 官方文档

| 编号 | 标题 | URL | 核实状态 |
|---|---|---|---|
| S1 | Introduction to printing（Windows 打印体系：假脱机程序+驱动，GDI/XPS 路径） | https://learn.microsoft.com/en-us/windows-hardware/drivers/print/introduction-to-printing | ✅ 已核实 |
| S2 | Print Spooler Architecture（假脱机程序职责、EMF→PCL 转换、端口驱动） | https://learn.microsoft.com/en-us/windows-hardware/drivers/print/print-spooler-architecture | ✅ 已核实 |
| S3 | 下载并安装最新的打印机驱动程序（Windows Update / 制造商官网 / 添加驱动向导） | https://support.microsoft.com/zh-cn/windows/hardware/printer/download-and-install-the-latest-printer-drivers | ✅ 已核实 |
| S4 | Add or install a printer in Windows（本地 USB / 网络无线 / 蓝牙配对安装） | https://support.microsoft.com/en-US/Windows/Hardware/printer/add-or-install-a-printer-in-windows | ✅ 已核实 |
| S5 | How To: Send Data Directly to a GDI Printer（Win32 RAW：OpenPrinter/StartDocPrinter/WritePrinter） | https://learn.microsoft.com/en-us/windows/win32/printdocs/sending-data-directly-to-a-printer | ✅ 已核实 |

### 8.2 Qt 6 官方文档

| 编号 | 标题 | URL | 核实状态 |
|---|---|---|---|
| S6 | QPrinter Class（setResolution/setPageLayout，Windows 使用内建驱动） | https://doc.qt.io/qt-6/qprinter.html | ✅ 已核实 |
| S7 | QPageSize Class（ExactMatch/FuzzyMatch、毫米单位、自定义尺寸保留、windowsId） | https://doc.qt.io/qt-6/qpagesize.html | ✅ 已核实 |
| S8 | QPageLayout Class（Millimeter 单位、方向、setPageSize/minMargins） | https://doc.qt.io/qt-6/qpagelayout.html | ✅ 已核实 |

### 8.3 协议手册

| 编号 | 标题 | URL | 核实状态 |
|---|---|---|---|
| S9 | ZPL Commands（Zebra 在线 ZPL II 命令完整列表） | https://docs.zebra.com/us/en/printers/software/zpl-pg/c-zpl-zpl-commands.html | ✅ 已核实 |
| S10 | ZPLII and RFID Command Set Download（ZPL II Programming Guide P1099958 下载入口） | https://support.zebra.com/article/000021601 | ✅ 已核实 |
| S11 | CPCL Manual for Zebra Mobile Printers（适用机型与 CPCL 手册入口；QLn 原生 ZPL 说明） | https://support.zebra.com/article/CPCL-Manual-for-Zebra-Mobile-Printers | ✅ 已核实 |
| S12 | TSPL/TSPL2 Programming Manual（TSC 官方 PDF；SIZE/GAP/SPEED/DENSITY/DIRECTION/CLS/PRINT） | https://fs.tscprinters.com/system/files/31-0000001-00_tspl_tspl2_programming_h.pdf | ✅ 已核实 |
| S14 | Datamax-O'Neil Quick Reference Programming Guide（CPCL/EMZ、Easy Print 模式） | https://prod-edam.honeywell.com/content/dam/honeywell-edam/sps/ppr/en-us/public/products/printers/common/documents/sps-ppr-110200.pdf | ✅ 已核实 |

### 8.4 厂商官方驱动 / 支持入口

| 编号 | 厂商 | 标题/入口 | URL | 核实状态 |
|---|---|---|---|---|
| S15 | Honeywell | PC45T 产品页（InterDriver Windows 驱动；FP/DP/IPL/ZSim2/DPL 命令语言） | https://sps.honeywell.com/us/en/products/productivity/printers/desktop/pc45t-desktop-thermal-transfer-barcode-printer | ✅ 已核实 |
| S16 | Zebra | 打印机驱动程序页（ZDesigner v10，Windows 10/11/Server） | https://www.zebra.cn/cn/zh/support-downloads/printers/printer-drivers.html | ✅ 已核实 |
| S17 | Zebra | Install Zebra Printers Using ZDesigner Windows Driver v10（USB/Network/Bluetooth/Other 端口） | https://support.zebra.com/article/000027281?language=en_US | ✅ 已核实 |
| S13 | TSC | TSC 官网首页（Downloads/SDK 入口；按型号获取驱动与手册） | https://www.tscprinters.com/EN | ✅ 首页已核实；按型号的驱动深链为动态页，⚠️ 未逐一核实 |
| S18 | GoDEX 科诚 | Downloads（Driver/Utility/EZPL Manual 分类下载） | https://www.godexintl.com/downloads | ✅ 已核实 |
| S19 | Star Micronics | TSP100IV SK Support Page（Star Windows Software / JavaPOS 驱动下载） | https://starmicronics.com/support/products/tsp100iv-sk-support-page/ | ✅ 已核实 |
| S20 | SATO | Software and utilities（按地区跳转的驱动入口） | https://www.sato-global.com/drivers/redirect.html | ✅ 已核实 |
| S21 | Epson | Download Center（按型号/OS/地区获取驱动） | https://download-center.epson.com/ | ✅ 首页已核实；具体型号页动态生成，⚠️ 未逐一核实 |
| S22 | HPRT 汉印 | 官网首页（驱动下载入口 hprt.com.cn/XiaZai） | https://cn.hprt.com/ | ✅ 已核实 |
| S23 | Gainscha 佳博 | 官网首页（条码/票据/便携驱动与 SDK 入口） | https://cn.gainscha.com/ | ✅ 已核实 |
| S24 | Xprinter 芯烨 | 官网首页（Windows 标签/条码驱动与编辑软件下载区） | https://www.xprinter.net/ | ✅ 已核实 |
| S25 | Brother | QL-800 快速安装指南 PDF（download.brother.com；指向 install.brother.com / support.brother.com；USB 集线器提示） | https://download.brother.com/welcome/docp100286/ql800_tchn_qsg_d00rpz001.pdf | ✅ PDF 已核实；support.brother.com 主站被 robots 协议禁止自动访问，⚠️ 未直接核实 |

### 8.5 未采用/未核实说明

- Brother `support.brother.com`、TSC 按型号驱动深链、Epson 具体型号驱动页为动态渲染或访问限制页面，本次未能打开核实，故未在矩阵中作为直接依据，仅在厂商入口处提示"按型号在官网支持区下载"。
- 本文未引用任何论坛、第三方网盘或非官方镜像链接。
