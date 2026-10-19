# CodeLabel Studio — 打印机专项测试报告（PRINTER_TEST_REPORT）

> 测试日期：2026-10-19
> 执行环境：Linux 沙箱（Python 3.12.11，PySide6 6.12，pytest 8.3.4，pypdf，reportlab；alembic 1.20.0 本轮安装）
> 报告原则：所有数字取自真实命令输出，无据不写；未执行项明写"未实测/未执行"，绝不伪造。

---

## 1. 报告原则与范围

### 1.1 自动化测试能验证什么、不能验证什么

- **能验证（本报告覆盖）**：纯计算与纯逻辑——毫米↔点换算、PDF 几何测量、
  缩放偏差判定、任务规格校验（纸张/份数/DPI/模块宽度/静区/偏移）、
  驱动状态决策矩阵、配置序列化 round-trip、协议指令字节（golden）、
  平台守卫（非 Windows 抛错而非假成功）、UI 离屏实例化与表单装配。
- **不能验证（本环境一律未执行，见第 5 节）**：真实队列枚举、真实提交、
  1:1 物理输出尺寸、设备走纸与浓度、协议指令在真实设备上的实际打印、
  USB/网络/蓝牙链路连通性、驱动安装与 UAC 提权全过程。这些必须在
  Windows 10/11 真实机器 + 真实设备上由人工执行（第 4 节清单）。

### 1.2 环境硬性事实

- 本环境为 **Linux 沙箱，无 Windows 操作系统、无任何打印机硬件、无真实打印队列**。
- 因此兼容性矩阵（`docs/PRINTER_COMPATIBILITY.md`）中"是否通过实际设备测试"
  一列**全部为"未实测（待验证）"**，本报告不回填任何"已验证"型号。
- 自动化测试中所有"通过"仅代表对应**纯逻辑/守卫分支**正确，不代表设备可用。

### 1.3 执行命令与原始结论

```
$ python3 -m pytest tests/ -q
456 passed, 2 skipped in 2.13s        （客户端，收集 458 项）

$ python3 -m pytest tests_server/ -q
167 passed, 1 warning in 50.25s        （服务端，收集 167 项；0 跳过）

$ pip3 install alembic
Successfully installed Mako-1.4.3 alembic-1.20.0
```

两条命令退出码均为 0，无 failed、无 error。

**2 项跳过的真实原因（pytest `-rs` 输出）：**

| # | 位置 | 原因（原文） | 性质 |
|---|---|---|---|
| 1 | `tests/test_calibration.py:226` | `pdf_render 已就绪，缺失分支不再适用` | 旧缺失分支已被新模块覆盖，按设计 skip，非环境缺陷 |
| 2 | `tests/test_encoder.py:65` | `python-barcode 未安装，跳过第三方交叉验证` | 可选第三方库缺失导致的第三方交叉验证 skip，非代码缺陷 |

**1 条第三方告警（服务端）：** starlette `testclient.py:37` 的
`DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated`，
来自测试框架自身，与业务代码无关。

**1 条 Qt 离屏析构提示（客户端，非失败）：** 离屏冒烟测试结束后 stderr 出现
`QThread: Destroyed while thread '' is still running`，为 offscreen 平台线程
析构顺序提示；该批用例断言全部通过、退出码 0，不影响结果。

---

## 2. 自动化测试结果总表

### 2.1 客户端 `tests/`（456 passed / 2 skipped / 0 failed / 0 error）

"专项"= 本轮打印机新增测试；"既有"= 回归既有测试。

| 测试文件 | 用例数 | 覆盖点 | 结果 |
|---|---|---|---|
| `test_driver_installer.py` | 104 | 驱动状态 6 态全组合矩阵、优先级、官方指引命中/不命中（不编造 URL）、四态文案、非 Windows 守卫 | 104 passed |
| `test_windows_print.py` | 29 | 平台守卫、PRINTER_INFO_2 纯解析（端口→传输、状态位）、winerror 映射、cancel/query/spool 守卫 | 29 passed |
| `test_transports.py` | 25 | 网络配置校验、端点解析、通道构造；USB/蓝牙/裸队列在 Linux 明确不支持 | 25 passed |
| `test_print_ui_logic.py` | 25 | 能力合并去重、默认机选择、纸张匹配、表单→LabelJobSpec、摘要/issue 分组 | 25 passed |
| `test_adapters_zpl.py` | 19 | ZPL golden 指令、mm→dots、任务校验（纸张/份数/DPI/静区/模块/偏移）、submit/cancel/query 守卫 | 19 passed |
| `test_adapters_registry.py` | 19 | 协议分发、未知协议拒绝、verified 透传、后端选择、Universal 缩放 warning 不阻断 | 19 passed |
| `test_adapters_cpcl.py` | 16 | CPCL golden 指令、原点偏移、任务校验、submit/cancel/query 守卫 | 16 passed |
| `test_adapters_tspl.py` | 14 | TSPL2 golden 指令、mm→dots 边界、任务校验、submit/cancel/query 守卫 | 14 passed |
| `test_calibration.py` | 23 (+1 skipped) | 测试页精确毫米、偏移纯函数、静区/模块宽度、几何校验、verify_scale | 23 passed, 1 skipped |
| `test_config_store.py` | 12 | 配置 save/load round-trip、多机隔离、reset、损坏 JSON、文件名消毒、原子写、容错加载 | 12 passed |
| `test_pdf_render.py` | 7 | measure_pdf_pages 毫米测量、旋转交换、缩放偏差判定、多页首页基准、rasterize 平台守卫 | 7 passed |
| `test_print_ui_smoke.py` | 6 | PrintPage/PrinterSetup 离屏实例化、空能力禁用打印、注入未实测能力装配、禁用"打印成功"文案扫描 | 6 passed |
| `test_ui_smoke.py`（既有） | 8 | 7 页面离屏冒烟（含打印版面绘制回归） | 8 passed |
| `test_source_validation.py`（既有） | 21 | 来源白名单、GS1 授权 | 21 passed |
| `test_service.py`（既有） | 15 | 客户端业务服务 | 15 passed |
| `test_templates.py`（既有） | 17 | 模板网格 | 17 passed |
| `test_csvio.py`（既有） | 11 | CSV 公式注入转义 | 11 passed |
| `test_template_service.py`（既有） | 9 | 模板服务 | 9 passed |
| `test_backup_restore_hardened.py`（既有） | 9 | 备份恢复 6 类异常输入 | 9 passed |
| `test_batch_limits.py`（既有） | 9 | 批量上限/取消 | 9 passed |
| `test_checkdigit.py`（既有） | 9 | 校验位算法 | 9 passed |
| `test_database.py`（既有） | 9 | 数据库约束 | 9 passed |
| `test_pdf.py`（既有） | 10 | PDF 页面几何与多页分页（既有导出） | 10 passed |
| `test_encoder.py`（既有） | 10 (+1 skipped) | 编码码表；第三方 python-barcode 交叉验证 skip | 10 passed, 1 skipped |
| `test_svg.py`（既有） | 8 | SVG 结构 | 8 passed |
| `test_client_assembly.py`（既有） | 7 | 装配层路径解析 | 7 passed |
| `test_migration.py`（既有） | 5 | 客户端本地库迁移 | 5 passed |
| **合计** | **458 收集** | — | **456 passed, 2 skipped** |

### 2.2 服务端 `tests_server/`（167 passed / 0 skipped / 0 failed / 0 error）

服务端测试与打印专项无直接耦合，本轮全量回归保持通过。

| 测试文件 | 项数 | 说明 | 结果 |
|---|---|---|---|
| `test_trusted_proxy.py` | 28 | 可信代理、伪造头防护、限流 | 28 passed |
| `test_production_config.py` | 26 | 生产配置启动检查 | 26 passed |
| `test_api_auth.py` | 21 | 认证/JWT/密码策略 | 21 passed |
| `test_security.py` | 19 | 注入/越权/CSRF 类防护 | 19 passed |
| `test_api_admin.py` | 17 | 管理 API、审计日志 | 17 passed |
| `test_api_orders.py` | 14 | 订单状态机 | 14 passed |
| `test_api_license.py` | 12 | 授权签发/校验/撤销 | 12 passed |
| `test_payment_outbox.py` | 11 | 付款一致性/outbox | 11 passed |
| `test_orders_plan_switch.py` | 8 | 月付↔年付切换 | 8 passed |
| `test_e2e_full_flow.py` | 7 | 跨边界端到端 | 7 passed |
| `test_migrations.py` | 4 | Alembic 迁移（alembic 1.20.0 已装） | 4 passed |
| **合计** | **167** | — | **167 passed, 1 warning** |

---

## 3. 十类要求覆盖点逐一说明

> 每一项均注明：在哪里被覆盖、以什么形式、自动化覆盖到哪一层、
> **Windows 真实路径待人工验收**。

### 3.1 打印机枚举异常 = 平台守卫 + 纯解析

- **平台守卫（不可在 Linux 假成功）**：
  - `client/printing/windows_print.py`：`windows_print_available()`、`_require_win32()`，
    非 Windows / 无 pywin32 时 `list_printers()`、`get_default_printer()`、
    `get_driver_info()` 抛 `PrintError(UNSUPPORTED_PLATFORM / WIN32PRINT_UNAVAILABLE)`。
  - `client/printing/qt_print.py`：`qt_print_available()`、`_require_qt()`，
    非 Windows / 无 PySide6 时 `list_qt_printers()`、`print_pdf_via_qt()` 抛错。
- **纯解析（Linux 可测）**：`parse_printer_info_2()` 把 PRINTER_INFO_2 字段 dict
  解析为能力模型；端口名推断传输（USB00x / IP_ / UNC 共享 / LPT / 蓝牙）；
  状态位掩码解析（offline/error/paused/printing/busy，模拟位与 MS 常量双套识别）。
- **测试**：`test_windows_print.py`
  - 守卫：`test_windows_print_available_false_on_linux`、
    `test_qt_print_available_false_on_linux`、`test_list_printers_raises_on_linux`、
    `test_qt_list_printers_raises_on_linux`；
  - 解析：`test_parse_usb_port`、`test_parse_network_ip_port`、
    `test_parse_unc_share_port`、`test_parse_lpt_local_port`、
    `test_parse_bluetooth_port`、`test_parse_offline_bit`、`test_parse_error_bit`、
    `test_parse_paused_bit`、`test_parse_printing_bit_keeps_available`、
    `test_parse_busy_bit_keeps_available`、`test_parse_default_flag_and_driver_version`、
    `test_parse_win32_field_names`。
- **待人工验收**：`EnumPrinters` 真实枚举、`QPrinterInfo.availablePrinters()`
  真实列表在 Windows 上的字段完整性。

### 3.2 无默认打印机 = 守卫

- **实现**：
  - `windows_print.get_default_printer()` 调用失败返回 `None`（不抛假成功）；
  - `print_export_logic.merge_capabilities([]/None)` 返回 `None`；
  - `print_export.py` 的 `set_printers()` 在空列表时显示
    "未检测到打印机（需 Windows + 打印服务）"并禁用打印控件；
  - 错误码 `NO_DEFAULT_PRINTER` 定义于 `errors.py`（用户文案"系统中没有默认打印机"）。
- **测试**：
  - `test_print_ui_logic.py::TestMergeCapabilities::test_empty_returns_none`
    （空列表/None → None）；
  - `test_print_ui_smoke.py::test_empty_capabilities_disables_print`
    （空能力列表 → `_current_caps is None`、打印按钮禁用）；
  - `test_windows_print.py::test_get_default_printer_raises_on_linux`（平台守卫）。
- **待人工验收**：Windows 上未设默认机时 UI 的提示与可选机回退行为。

### 3.3 驱动缺失 = detect_driver_state 矩阵

- **实现**：`client/printing/driver_installer.py::detect_driver_state()` 按固定优先级
  输出 6 态：`DRIVER_OK / DRIVER_MISSING / DRIVER_INSTALL_FAILED /
  DEVICE_OFFLINE / QUEUE_MISSING / UNKNOWN`；`build_driver_guidance()` 只给
  官方渠道指引，未命中厂商**不编造 URL**；`install_guidance_for_state()` 输出
  四态行动文案。
- **测试**：`test_driver_installer.py`（104 项）
  - `test_state_matrix_all_combinations`（has_queue / driver_name / failed_flag /
    offline 全组合参数化）；
  - 优先级：`test_priority_queue_missing_beats_offline_and_failed`、
    `test_priority_offline_beats_failed_and_driver_present`、
    `test_priority_failed_flag_beats_driver_present`；
  - 边界：`test_missing_driver_when_queue_exists_but_driver_blank`、
    `test_ok_when_all_healthy`、`test_unknown_when_name_blank_even_if_queue_present`；
  - 指引：`test_guidance_hit_returns_official_url`、
    `test_guidance_miss_url_is_none_and_no_fabrication`、
    `test_guidance_blank_manufacturer_miss`；
  - 文案：`test_guidance_ok/missing/failed/offline_keywords`、
    `test_guidance_covers_remaining_states`；
  - 守卫：`test_elevate_process_raises_on_linux`、
    `test_detect_windows_raises_on_linux`。
- **待人工验收**：真实队列/驱动组合下的状态判定与 UAC 提权启动。

### 3.4 打印机离线 = 状态位解析 + 状态机

- **实现**：
  - 打印机侧：`windows_print._parse_status()` 把 pStatus 位掩码解析为
    `PrinterStatus(available/offline/error/paused/busy)`，MS 常量
    （0x80 OFFLINE、0x1000 NOT_AVAILABLE、0x4000 PROCESSING）与模拟位双识别；
  - 任务侧：`query_job()` 解析 JOB_STATUS_*（PAUSED/ERROR/DELETING/SPOOLING/
    PRINTING/PRINTED/DELETED），`is_finished` 由 PRINTED/DELETED 位判定；
    任务不存在（1801/1802）返回 `JOB_NOT_FOUND` 的 JobState 而非崩溃。
- **测试**：`test_windows_print.py::test_parse_offline_bit`、
  `test_parse_error_bit`；`test_driver_installer.py` 的 DEVICE_OFFLINE 矩阵行；
  `test_query_job_raises_on_linux`（守卫）。
- **待人工验收**：真实设备拔线/暂停/出错时状态位与 UI 提示是否一致。

### 3.5 不支持的纸张尺寸 = validate_job / check_label_geometry

- **实现**：
  - 统一校验 `adapters/_common.py::collect_common_issues()`：paper 缺失/非正 →
    `PAPER_UNSUPPORTED`；不在 `supported_papers` → `PAPER_UNSUPPORTED`；
    超出可打印区域 → `PAPER_OUT_OF_RANGE`；
  - `calibration.check_label_geometry()`：非法纸张 error、超区 error、
    无间隙传感却设 gap → `GAP_SENSOR_UNSUPPORTED` warning、未声明 1:1 →
    `TRUE_SIZE_UNSUPPORTED` warning。
- **测试**：
  - ZPL：`test_missing_paper_is_error`、`test_paper_not_in_supported_list_is_error`、
    `test_paper_out_of_printable_area`；
  - TSPL：`test_missing_paper_is_error`、`test_paper_out_of_printable_area`；
  - CPCL：`test_missing_paper_is_error`、`test_paper_not_supported_is_error`、
    `test_paper_out_of_printable_area`；
  - `test_calibration.py::TestLabelGeometry`：`test_in_range_no_out_of_range_error`、
    `test_oversized_paper_out_of_range_error`、`test_unsupported_paper_error`、
    `test_gap_without_sensor_warns`、`test_gap_with_sensor_no_warning`、
    `test_zero_gap_no_warning`。
- **待人工验收**：真实驱动表单对自定义标签尺寸的接受/拒绝。

### 3.6 PDF 输出尺寸 = measure_pdf_pages 毫米测量

- **实现**：`pdf_render.measure_pdf_pages()` 用 pypdf 读 CropBox（缺省 MediaBox），
  pt→mm（1 mm = 25.4/72 pt），/Rotate 90/270 交换宽高，返回每页
  `{page, width_mm, height_mm, rotation}`。
- **测试**：`test_pdf_render.py`
  - `test_measure_40x25_label`（40×25mm 标签实测）；
  - `test_measure_a4`（A4 标准页）；
  - `test_rotation_90_swaps_dimensions`（旋转后宽高互换）。
- **待人工验收**：测量值与物理标签纸的一致性以卡尺人工复核为准。

### 3.7 DPI 和缩放设置 = detect_scale_mismatch + DPI 映射

- **实现**：
  - `detect_scale_mismatch()`：首页实测宽/高与目标纸张偏差 ≤ tolerance_mm
    （默认 0.5mm）才 ok，否则产 `SCALE_MISMATCH` warning；多页以首页基准并注明；
  - DPI：`effective_dpi()` 任务 dpi 优先、回退能力 `resolution()`；
    `mm_to_dots()` = mm × dpi / 25.4 四舍五入；Qt 侧 `setResolution(target_dpi)`；
  - Universal 适配器把缩放偏差作为 **warning 不阻断提交**。
- **测试**：
  - `test_pdf_render.py`：`test_mismatch_detected_100x100_vs_50x30`、
    `test_match_size_ok`、`test_multipage_uses_first_page_warning`；
  - ZPL：`test_mm_to_dots_basic_and_boundaries`、`test_mm_to_dots_missing_dpi_raises`、
    `test_missing_dpi_is_error`；TSPL/CPCL 的 `test_mm_to_dots_boundaries`、
    `test_missing_dpi_is_error`；
  - `test_adapters_registry.py::test_universal_scale_mismatch_is_warning_not_error`、
    `test_universal_valid_job_no_error`；
  - `test_print_ui_logic.py::test_dpi_fallback_from_caps`；
  - `test_calibration.py::TestVerifyScale`：`test_mismatch_reported_false`、
    `test_match_reported_true`、`test_missing_module_raises_unsupported_platform`。
- **待人工验收**：驱动是否自行缩放（应用无法单方面强制，见兼容性文档 1.2.5）。

### 3.8 打印任务失败与取消 = cancel/query 守卫 + PrintResult 语义

- **实现**：
  - `cancel_job()`：OpenPrinter → SetJob(JOB_CONTROL_DELETE)；
    1801/1802/1807（队列/任务不存在）返回 False，其余经 `map_win_error` 抛出；
  - `query_job()`：GetJob(JOB_INFO_1) 解析状态位；1801/1802 返回
    `JOB_NOT_FOUND` JobState；
  - `PrintResult` 语义：QUEUED 文案固定为"已提交到打印队列，不代表物理打印完成"，
    无设备 ACK 绝不产生 PHYSICALLY_PRINTED；failed/cancelled 工厂方法语义明确；
  - 三协议适配器的 cancel/query 在无通道时返回不支持（非静默成功）。
- **测试**：
  - 守卫：`test_windows_print.py::test_cancel_job_raises_on_linux`、
    `test_query_job_raises_on_linux`、`test_spool_raw_data_raises_on_linux`；
  - 适配器：ZPL/TSPL/CPCL 各自的 `test_submit_without_channel_on_linux_unsupported`、
    `test_cancel_not_supported`、`test_query_job_not_supported`；
  - 文案红线：`test_print_ui_smoke.py::test_no_print_success_wording`
    扫描三个 UI 文件不含禁用文案"打印成功"。
- **待人工验收**：真实队列中取消是否生效、失败错误码映射是否准确。

### 3.9 配置保存和恢复 = config_store round-trip

- **实现**：`client/printing/config_store.py`
  `PrinterConfigStore.save/load/load_all/reset/list_printers`，原子写（临时文件替换），
  `from_dict` 容错（|offset|>500mm→0、dpi 非正→None、非法质量枚举→NORMAL、
  份数<1→1、负 gap→0、坏 JSON→None），文件名消毒。
- **测试**：`test_config_store.py`（12 项）
  - round-trip：`test_save_load_equals`、`test_round_trip_persists_quality_enum`；
  - 隔离：`test_two_printers_isolated`、`test_load_all`；
  - reset/工厂：`test_reset_returns_none`、`test_defaults_factory`；
  - 损坏/缺失：`test_corrupt_file_returns_none`、`test_missing_file_returns_none`；
  - 消毒：`test_forbidden_chars_sanitized`、`test_empty_name_falls_back`；
  - 原子写：`test_no_tmp_leftover_after_save`；
  - 容错：`test_invalid_values_fall_back`。
- **待人工验收**：真实 Windows 数据目录下配置文件落盘与重启恢复。

### 3.10 专用协议参数验证 = ZPL/TSPL/CPCL golden 指令测试

- **实现**：三个适配器按公开规范生成指令；`validate_job` 统一走
  `collect_common_issues`，不绕过；mm→dots 按各自 dpi（ZPL 203/300/600）。
- **测试（golden 字节比对）**：
  - ZPL：`test_golden_code128`、`test_golden_ean13`；
    参数校验 `test_invalid_copies_is_error`、`test_quiet_zone_too_small`、
    `test_module_width_out_of_range`、`test_offset_out_of_bounds_is_error`、
    `test_build_payload_rejects_invalid_job`；
  - TSPL：`test_golden_code128`、`test_golden_ean13`；
    `test_invalid_copies_is_error`、`test_quiet_zone_too_small`、
    `test_module_width_out_of_range`、`test_missing_dpi_is_error`；
  - CPCL：`test_golden_code128`、`test_golden_ean13`、
    `test_offset_shifts_origin_in_cpcl`；同类参数校验；
  - 分发：`test_adapters_registry.py::test_build_adapter_dispatch`、
    `test_unknown_protocol_unsupported`、`test_protocol_supported`、
    `test_verified_passthrough_false_by_default`、
    `test_verified_passthrough_true_when_hardware_tested`。
- **待人工验收**：golden 仅证明指令字节符合规范文本，**不等于设备输出正确**；
  三种协议均未连接任何真实设备。

---

## 4. Windows 桌面人工验收清单（可执行表格模板）

> 适用：Windows 10/11 x64，普通打印机 + 标签打印机各至少一台；
> 可选 USB / 网络 / 蓝牙各一台。每台设备完成下表并留档（测试页照片、卡尺记录）。
> 任一"失败"须在备注写明报错码/错位/缩放/缺字现象，并回到
> `docs/PRINTER_COMPATIBILITY.md` 保持"未实测"，不得回填"已验证"。

### 4.1 设备登记行（每台设备一行，可复制扩展）

| # | 设备品牌/完整型号 | 连接方式(USB/以太网/Wi-Fi/蓝牙/本地队列) | 端口/IP | 驱动名称与版本(厂商/Windows Update，签名) | 标签纸规格 宽×高(mm)/类型/间隙或黑标/碳带 | 测试日期 | 测试人 | 结果(通过/失败/未执行) | 备注(报错码/错位/缩放/缺字) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | （示例留空待填） | | | | | | | 未执行 | |
| 2 | | | | | | | | 未执行 | |
| 3 | | | | | | | | 未执行 | |

### 4.2 单台设备操作步骤（顺序执行）

1. **打印测试页**：应用内「打印机设置」或打印页选择目标机 → 打印校准测试页
   （`render_test_page_pdf`，页面物理尺寸等于目标纸张）。
2. **核对标尺**：量测测试页上水平/垂直标尺总长度，与标称毫米数比对，
   记录偏差 mm；判断是否存在整体缩放。
3. **偏移修正**：若条码相对标签边距偏移，在偏移 x/y（mm）微调，
   重新打印直至位置居中（`apply_offset` 不修改原任务）。
4. **1:1 缩放核对**：用卡尺量测输出条码的模块宽度与静区，与表单设定值比对；
   偏差 >0.5mm 视为缩放异常（对应 `detect_scale_mismatch`），回查驱动表单与
   打印对话框是否被勾选"适合页面/无边距"。
5. **静区/模块宽度**：扫码枪实测可读；静区不足、模块越界应被
   `validate_job` 提前拦截（3.5/3.10）。
6. **正式打印**：选真实批次 → 100% 预览核对 → 打印，确认 QUEUED 提示文案
   （"已提交到打印队列，不代表物理打印完成"），核对份数与方向。
7. **取消任务**：提交后立即点「取消任务」，在 Windows 队列中确认任务被移除
   （对应 `cancel_job`/SetJob DELETE）；再用 `query_job` 语义核对已结束状态。
8. **失败/离线演练（建议）**：拔线或暂停队列后提交，观察设备离线/失败提示
   是否符合 `detect_driver_state` 与状态位解析预期。

### 4.3 验收记录回填规则

1. 仅在真实设备完成 4.2 全部步骤且卡尺/扫码通过时，结果方可记"通过"；
2. 通过记录写入本表后，再回 `PRINTER_COMPATIBILITY.md` 对应类别把
   "未实测（待验证）"改为"已验证（见本表编号）"；
3. `verified_with_hardware=True` 只允许在存在真实验收记录时出现，严禁伪造。

---

## 5. 本环境限制与未执行项声明

| 项 | 状态 | 说明 |
|---|---|---|
| Windows 真实队列枚举（EnumPrinters / QPrinterInfo） | **未执行** | Linux 无系统队列 |
| 真实提交打印（QPrinter / WritePrinter RAW） | **未执行** | 无设备、无队列；成功语义仅到 QUEUED |
| 1:1 物理输出尺寸卡尺复核 | **未执行** | 自动化只测 PDF 排版尺寸，不测物理纸 |
| ZPL / TSPL2 / CPCL 真实设备出纸 | **未执行** | golden 指令仅文本/字节级比对 |
| USB / 网络(9100) / 蓝牙链路 | **未执行** | 仅配置校验与通道构造，未连通 |
| 驱动下载/安装/UAC 提权全过程 | **未执行** | 应用只检测与指引，不捆绑不自动安装 |
| 安装包（Inno Setup）重新编译 | **未执行** | 本环境无 ISCC.exe，见 `FINAL_AUDIT_REPORT.md` 打印机专项章 |
| 第三方 python-barcode 交叉验证 | **跳过(1)** | 可选库未安装，非缺陷 |
| 校准旧缺失分支 | **跳过(1)** | 已被 pdf_render 覆盖，按设计 skip |

**结论：本轮自动化 456（客户端）+ 167（服务端）全部通过，2 项跳过均为
已说明的环境/设计原因；无任何型号经真实设备测试，硬件验收以第 4 节清单
在 Windows 人工执行为准。**
