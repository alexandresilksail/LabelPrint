# CodeLabel Studio — 打印机专项测试报告（PRINTER_TEST_REPORT）

> 测试日期：第一轮 2026-10-19（打印机专项首测）；第二轮 2026-10-20（八类缺陷修复与发布验收复测）；第三轮 2026-10-22（第二轮打印审计修复：P0 线程生命周期 / P1 配置传递与缩放分级 / P3a 协议状态与 verified 守卫 + Windows 发布门禁手册）
> 执行环境：Linux 沙箱（Python 3.12.11，PySide6 6.12.0，pytest 9.1.1，pypdf 5.9.0，reportlab 4.4.1；alembic 1.20.0 已装；python-barcode 未装）
> 报告原则：所有数字取自真实命令输出，无据不写；未执行项明写"未实测/未执行"，绝不伪造。
> 第一轮历史记录（456 passed/2 skipped）完整保留于第 2.1/2.2 节；第二轮（597/167）以第 2A 章与第 2.3/2.4 节为准；**第三轮（711/167，全量退出码 0）以第 2B 章为准**。

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

**第三轮（2026-10-22，第二轮打印审计修复复测，本次为准）：**

```
$ QT_QPA_PLATFORM=offscreen python3 -m pytest tests/ -q
711 passed, 2 skipped in 3.00s        （客户端，收集 713 项；0 failed 0 error；退出码 EXIT=0）

$ python3 -m pytest tests_server/ -q
167 passed, 1 warning in 47.37s        （服务端，收集 167 项；0 skipped；退出码 EXIT=0）
```

- 客户端连续 3 次全量运行均为 `711 passed, 2 skipped` 且 **EXIT=0**；
  stderr 无 `QThread: Destroyed while thread '' is still running`、无
  Aborted/Segfault（P0 线程生命周期修复后第二轮的析构终止现象已消除，
  证据见 2B.1）。
- 服务端退出码 0，唯一 warning 仍为 starlette `testclient.py` 的 anyio
  别名 DeprecationWarning（第三方框架，与业务无关）。
- 2 项跳过与前两轮完全相同（见下表），原因未变。

**第二轮（2026-10-20，缺陷修复复测，历史保留）：**

```
$ python3 -m pytest tests/ -q
597 passed, 2 skipped in 2.20s        （客户端，收集 599 项；0 failed 0 error）

$ python3 -m pytest tests_server/ -q -rs
167 passed, 1 warning in 46.38s         （服务端，收集 167 项；0 skipped，退出码 0）
```

- 服务端命令退出码 0，无 failed、无 error、无 skipped。
- 客户端 pytest 自身在打印出 `597 passed, 2 skipped` 总结行后，于解释器
  析构阶段被 Qt offscreen 线程销毁终止（`QThread: Destroyed while thread ''
  is still running`，进程退出码 134 Aborted / 偶发 139 Segmentation fault）；
  已隔离验证：不含两个 UI 冒烟文件的全量子集退出码 0（583 passed, 2
  skipped），两个冒烟文件各自 8 passed / 6 passed 后同样在析构阶段终止。
  **该终止发生在 pytest 总结输出之后，不是失败用例、不是 error，597 项断言
  全部通过**；属 offscreen 平台线程析构顺序问题，与第一轮记录的 stderr 提示
  同源（第一轮仅打印提示未终止进程，本轮在析构阶段直接 abort，性质不变）。
- 第二轮 2 项跳过与第一轮完全相同（见下表），原因未变。

**第一轮（2026-10-19，历史保留）：**

```
$ python3 -m pytest tests/ -q
456 passed, 2 skipped in 2.13s        （客户端，收集 458 项）

$ python3 -m pytest tests_server/ -q
167 passed, 1 warning in 50.25s        （服务端，收集 167 项；0 跳过）

$ pip3 install alembic
Successfully installed Mako-1.4.3 alembic-1.20.0
```

第一轮两条命令退出码均为 0，无 failed、无 error。

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

### 2.0 三轮合计总表（以第三轮 2026-10-22 实测为准）

| 范围 | 第一轮 2026-10-19 | 第二轮 2026-10-20 | **第三轮 2026-10-22** | 三轮关系 |
|---|---|---|---|---|
| 客户端 `tests/` | 456 passed / 2 skipped（收集 458） | 597 passed / 2 skipped（收集 599），0 failed 0 error | **711 passed / 2 skipped（收集 713），0 failed 0 error，EXIT=0** | 第三轮净增 114 项 passed（4 个新测试文件）；2 项 skip 不变 |
| 服务端 `tests_server/` | 167 passed / 0 skipped（1 warning） | 167 passed / 0 skipped（1 warning），退出码 0 | **167 passed / 0 skipped（1 warning），EXIT=0** | 三轮无变动，全量回归通过 |
| 失败 / 错误 | 0 / 0 | 0 / 0 | **0 / 0** | — |
| 客户端退出码 | 0 | 总结输出后被 Qt offscreen 析构 abort（非失败） | **0（P0 修复后连跑稳定，无 Destroyed/abort）** | 见 2B.1 |

> 第二轮客户端进程在总结输出后被 Qt offscreen 析构终止（非失败，见 1.3 与
> 第 6 章说明）；该现象在第三轮 P0 修复后消除。逐文件明细见 2.3（第二轮）
> 与 2B（第三轮），第一轮历史明细见 2.1/2.2（原样保留）。

### 2.1 【第一轮历史】客户端 `tests/`（456 passed / 2 skipped / 0 failed / 0 error）

"专项"= 第一轮打印机新增测试；"既有"= 回归既有测试。下表为 2026-10-19
原始记录，第二轮文件数已变化，请勿以本表作为当前数字。

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

### 2.2 【第一轮历史】服务端 `tests_server/`（167 passed / 0 skipped / 0 failed / 0 error）

服务端测试与打印专项无直接耦合，第二轮全量回归仍为 167 passed（见 2.3）。
下表为第一轮原始记录。

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

### 2.3 第二轮（2026-10-20）客户端逐文件实测（597 passed / 2 skipped / 收集 599）

> 数字来自 `pytest --collect-only -q` 收集计数与逐文件 `pytest <file> -q`
> 实测结果；"新增"= 本轮新文件，"更新"= 本轮改写/扩展，"未变"= 第一轮文件本轮未改。

| 测试文件 | 状态 | 收集 | 结果 | 本轮覆盖点 |
|---|---|---|---|---|
| `test_print_pdf_semantics.py` | 新增 | 24 | 24 passed | 份数唯一展开（任务份数不进 PDF）、跨页/dropped/MAX_SHEET_PAGES、偏移叠加、静区模块数、target_module_mm、paper_override 放不下拒绝、landscape 旋转交换 |
| `test_adapters_content.py` | 新增 | 31 | 31 passed | LabelContent 完整布局驱动 ZPL/TSPL/CPCL；template_copies 展开、job.copies 唯一写入、偏移进指令、缺 content→INVALID_JOB、显式 content 覆盖、show_text/GAP/模块宽 |
| `test_print_ui_logic.py` | 更新（25→46） | 46 | 46 passed | 能力合并去重、pick_paper 命中/旋转命中/未命中、build_job_from_form 偏移/默认/非法回退/份数钳制、摘要、issue 分组 |
| `test_transports.py` | 更新（25→33） | 33 | 33 passed | 网络配置校验/端点解析/通道 kind；USB/蓝牙一律 TRANSPORT_NOT_IMPLEMENTED；SpoolChannel Linux 守卫 |
| `test_adapters_registry.py` | 更新（19→22） | 22 | 22 passed | 协议分发、未知协议拒绝、verified 透传、**不按品牌名推断协议**、Universal 后端选择纯函数 |
| `test_driver_installer.py` | 未变 | 104 | 104 passed | 驱动 6 态矩阵、优先级、官方指引不编造 URL、四态文案、提权分支（LAUNCHED≠安装成功）、非 Windows 守卫 |
| `test_qt_print_semantics.py` | 新增 | 16 | 16 passed | resolve_effective_page 三态/容差边界/旋转互换、单位换算、平台守卫（Linux 抛 UNSUPPORTED_PLATFORM） |
| `test_windows_print.py` | 未变 | 29 | 29 passed | 平台守卫、PRINTER_INFO_2 纯解析、winerror 映射、cancel/query/spool 守卫 |
| `test_universal_semantics.py` | 新增 | 8 | 8 passed | PAPER_SIZE_MISMATCH 拒绝 / allow_paper_conversion 确认转换降级 warning、单轴 SCALE_MISMATCH、checker 缺失跳过、submit 守卫 |
| `test_print_job_session.py` | 新增 | 15 | 15 passed | 同机并发锁 DEVICE_BUSY、按打印机句柄独立 ActiveJob、临时文件 keep_recent/cleanup_all、cancel/query 无任务 JOB_NOT_FOUND |
| `test_transport_honesty.py` | 新增 | 15 | 15 passed | SENT（TCP 已发无设备确认）/QUEUED 语义；USB/蓝牙/LPR=TRANSPORT_NOT_IMPLEMENTED；注入失败 socket 的错误映射，不真实发包 |
| `test_adapters_zpl.py` | 更新（内容改写，19 项未变） | 19 | 19 passed | golden 字节、mm→dots、统一校验、build_payload(job, content=)、submit 守卫 |
| `test_adapters_cpcl.py` | 更新（内容改写，16 项未变） | 16 | 16 passed | golden、原点偏移、校验、build_payload(job, content=)、submit 守卫 |
| `test_adapters_tspl.py` | 更新（内容改写，14 项未变） | 14 | 14 passed | golden、mm→dots 边界、校验、build_payload(job, content=)、submit 守卫 |
| `test_calibration.py` | 未变 | 24 | 23 passed, 1 skipped | 测试页毫米、偏移纯函数、静区/模块宽、几何校验、verify_scale |
| `test_encoder.py` | 未变 | 11 | 10 passed, 1 skipped | 码表；python-barcode 第三方交叉验证 skip |
| `test_service.py`（既有） | 未变 | 15 | 15 passed | 客户端业务服务 |
| `test_source_validation.py`（既有） | 未变 | 21 | 21 passed | 来源白名单、GS1 授权 |
| `test_templates.py`（既有） | 未变 | 17 | 17 passed | 模板网格 |
| `test_config_store.py` | 未变 | 12 | 12 passed | 配置 round-trip、多机隔离、reset、损坏 JSON、原子写、容错 |
| `test_print_ui_smoke.py` | 未变 | 6 | 6 passed | PrintPage/PrinterSetup 离屏实例化、空能力禁用、禁用"打印成功"文案扫描 |
| `test_ui_smoke.py`（既有） | 未变 | 8 | 8 passed | 7 页面离屏冒烟 |
| `test_pdf_render.py` | 未变 | 7 | 7 passed | measure_pdf_pages、旋转交换、缩放偏差、rasterize 守卫 |
| `test_pdf.py`（既有） | 未变 | 10 | 10 passed | PDF 几何与多页分页（既有导出） |
| `test_csvio.py`（既有） | 未变 | 11 | 11 passed | CSV 公式注入转义 |
| `test_template_service.py`（既有） | 未变 | 9 | 9 passed | 模板服务 |
| `test_backup_restore_hardened.py`（既有） | 未变 | 9 | 9 passed | 备份恢复 6 类异常输入 |
| `test_batch_limits.py`（既有） | 未变 | 9 | 9 passed | 批量上限/取消 |
| `test_checkdigit.py`（既有） | 未变 | 9 | 9 passed | 校验位算法 |
| `test_database.py`（既有） | 未变 | 9 | 9 passed | 数据库约束 |
| `test_svg.py`（既有） | 未变 | 8 | 8 passed | SVG 结构 |
| `test_client_assembly.py`（既有） | 未变 | 7 | 7 passed | 装配层路径解析 |
| `test_migration.py`（既有） | 未变 | 5 | 5 passed | 客户端本地库迁移 |
| **合计** | — | **599** | **597 passed, 2 skipped** | 0 failed / 0 error |

### 2.4 第二轮服务端逐文件（167 passed / 0 skipped，与第一轮一致）

| 测试文件 | 项数 | 结果 |
|---|---|---|
| `test_trusted_proxy.py` | 28 | 28 passed |
| `test_production_config.py` | 26 | 26 passed |
| `test_api_auth.py` | 21 | 21 passed |
| `test_security.py` | 19 | 19 passed |
| `test_api_admin.py` | 17 | 17 passed |
| `test_api_orders.py` | 14 | 14 passed |
| `test_api_license.py` | 12 | 12 passed |
| `test_payment_outbox.py` | 11 | 11 passed |
| `test_orders_plan_switch.py` | 8 | 8 passed |
| `test_e2e_full_flow.py` | 7 | 7 passed |
| `test_migrations.py` | 4 | 4 passed |
| **合计** | **167** | **167 passed, 1 warning** |

---

## 2A. 第二轮：缺陷修复与发布验收（2026-10-20）

> 本章为八类缺陷修复轮的验收记录。修复已全部落地，自动化复测
> 597 passed / 2 skipped（客户端）、167 passed（服务端）；硬件、Windows
> 构建、UAC 等非自动化项**一律未实测/未实现，按五级标签如实标注**，不把
> "代码写了/自动化通过"写成"已在真机验证"。

### 2A.1 八类缺陷 → 修复方式 → 验证

| # | 缺陷 | 修复方式（落地事实） | 验证：测试文件（用例数）/ 结果 |
|---|---|---|---|
| 1 | **份数双重放大**：模板份数与任务份数在 PDF 渲染与 UI 两侧重复展开，导致出纸翻倍 | `render_label_sheet_pdf` 成为模板份数 `template.copies` 的**唯一展开点**，签名层面不接受任务份数；`print_export` 导出/打印路径向渲染器传**未展开原始 items**；任务份数 `job.copies` 不进入 PDF，由 Qt `setCopyCount` / 协议打印数量唯一应用 | `test_print_pdf_semantics.py`（24，1/2/3 模板份 × 1/2/5 任务份断言任务份不进 PDF）；`test_adapters_content.py`（31，job.copies 唯一写入协议打印数量）；`test_print_ui_logic.py`（46，raw items 不展开/任务份不在 content 重复）；均 passed |
| 2 | **纸张一致性**：整体纸尺寸超容差时被静默缩放或放行 | `UniversalWindowsAdapter.validate_job`：两轴均超容差且 `allow_paper_conversion=False` → `PAPER_SIZE_MISMATCH` **error 阻止提交**；`allow_paper_conversion=True`（用户确认）→ 降级 warning 并注明已确认转换；单轴偏差保持 `SCALE_MISMATCH` warning；网格 `paper_override` 放不下 → ValueError"无法容纳"拒绝；`resolve_effective_page` 三态/容差边界/旋转互换 | `test_universal_semantics.py`（8，三态注入式 scale_checker）；`test_print_pdf_semantics.py`（24，paper_override 放不下拒绝/放得下 pypdf 实测尺寸）；`test_qt_print_semantics.py`（16，页面三态/容差/旋转/守卫）；均 passed |
| 3 | **校准参数未贯通**：偏移/静区/模块宽度停留在配置，未进入渲染与协议指令 | 偏移（正/负/零、与 template.offset 叠加）进入 PDF 渲染与 ZPL/TSPL/CPCL 指令；静区、`target_module_mm` 模块宽度进入 fit 容量与编码；渲染器新增可选校准参数且向后兼容 | `test_print_pdf_semantics.py`（24，偏移叠加/静区模块数/模块宽生效）；`test_adapters_content.py`（31，偏移进指令/静区不足 error/模块宽/GAP）；均 passed |
| 4 | **协议内容链路断裂**：适配器靠假设切片而非完整布局，`build_payload` 内容来源不明 | 引入 `LabelContent` 完整布局模型；`build_payload(job, *, content=None)` 显式内容参数；缺 `label_content` → `INVALID_JOB`；显式 content 覆盖；show_text/GAP/模块宽进入三协议指令；三协议测试改写走冻结接口 | `test_adapters_content.py`（31，新增）；`test_adapters_zpl.py`（19，改写）、`test_adapters_tspl.py`（14，改写）、`test_adapters_cpcl.py`（16，改写）；均 passed |
| 5 | **传输层不诚实 / 结果语义混淆**：未集成通道伪装可用；发送成功被当作打印成功 | TCP 直接通道发送成功 = `SENT`（无设备确认，≠物理打印）；spool 队列 = `QUEUED`；`PHYSICALLY_PRINTED` 无设备 ACK 绝不产生；USB/蓝牙/LPR 一律抛 `TRANSPORT_NOT_IMPLEMENTED`（UI 相应禁用）；registry **不按厂商名/驱动名推断协议**（见到 Zebra 不认 ZPL） | `test_transport_honesty.py`（15，新增，注入失败 socket/fake channel）；`test_transports.py`（33，更新）；`test_adapters_registry.py`（22，更新，无品牌推断）；`test_print_ui_logic.py`（46，USB/蓝牙/LPR 禁用、TCP 诚实 warning、mode_basis 不按品牌）；均 passed |
| 6 | **任务会话管理缺失**：重复点击并发提交、切机取消错任务、临时 PDF 提前删除 | 新增 `print_session.PrintJobSession`：同打印机 `begin_submit` 忙锁（`DEVICE_BUSY`）、按打印机句柄独立 `ActiveJob`（正确队列名+任务 ID）、临时 PDF `keep_recent` 保留最近 K 个、`cleanup_all` 兜底清理；提交完成前绝不删临时文件 | `test_print_job_session.py`（15，新增，纯逻辑无 Qt/pywin32）；passed |
| 7 | **驱动启动被当作安装成功** | `elevate_process` 结果四态 `LAUNCHED/REQUIRES_UAC/CANCELLED_BY_USER/FAILED`：LAUNCHED 仅表示 ShellExecute 发起，**不代表安装成功**；安装器驱动页文案新增"启动成功≠安装成功，等待完成后返回软件点『刷新打印机』复核队列/驱动" | 自动化仅覆盖非 Windows 守卫（`test_driver_installer.py::test_elevate_process_raises_on_linux`，该文件 104 项全过）；**四态分支、真机 UAC 与刷新闭环无 Linux 单测，属 ④尚未实测**；安装器文案为脚本文本级核对 |
| 8 | **安装器版本与状态标注缺失**：版本号停留 1.0.0，"代码写了"易被误读为"已验证" | `installer/codelabel.iss` 版本号 1.0.0→**1.1.0**，驱动可选页补第 6/7 条文案；全套文档统一五级状态标签（①自动化通过/②Windows 构建通过/③真实设备实测/④尚未实测/⑤尚未实现） | 脚本文本级核对（版本号、begin/end 配对、字符串拼接 grep）；**ISCC 编译未执行**（Linux 无 ISCC），见五级表 |

**新增/更新测试文件清单（第二轮）：**

| 类型 | 文件 | 用例数 | 结果 |
|---|---|---|---|
| 新增 | `test_print_pdf_semantics.py` | 24 | 24 passed |
| 新增 | `test_qt_print_semantics.py` | 16 | 16 passed |
| 新增 | `test_universal_semantics.py` | 8 | 8 passed |
| 新增 | `test_adapters_content.py` | 31 | 31 passed |
| 新增 | `test_transport_honesty.py` | 15 | 15 passed |
| 新增 | `test_print_job_session.py` | 15 | 15 passed |
| 更新 | `test_print_ui_logic.py` | 46（第一轮 25） | 46 passed |
| 更新 | `test_adapters_zpl.py` | 19（内容改写） | 19 passed |
| 更新 | `test_adapters_tspl.py` | 14（内容改写） | 14 passed |
| 更新 | `test_adapters_cpcl.py` | 16（内容改写） | 16 passed |
| 更新 | `test_adapters_registry.py` | 22（第一轮 19） | 22 passed |
| 更新 | `test_transports.py` | 33（第一轮 25） | 33 passed |

新增 6 文件合计 109 项；更新文件净增 32 项（46-25=21、22-19=3、33-25=8；
zpl/tspl/cpcl 计数未变仅内容改写）；合计净增 141 项 passed（456→597）。

### 2A.2 五级状态区分表（第二轮逐项落实）

> 五级定义：① 自动化通过（Linux pytest 已跑过该逻辑）；② Windows 构建通过
> （真实 Windows 由 PyInstaller/ISCC 产出可分发文件）；③ 真实设备实测（真实
> 打印机硬件+真实桌面交互验证通过）；④ 尚未实测（代码/脚本/指引就绪，目标
> 环境未验证）；⑤ 尚未实现（功能未做或按设计有意不做）。

| 事项 | 本轮实际级别 | 依据 / 待办 |
|---|---|---|
| 份数唯一展开、任务份数不进 PDF（纯逻辑） | **① 自动化通过** | `test_print_pdf_semantics` 24 项断言通过 |
| PAPER_SIZE_MISMATCH 拒绝 / 确认转换 / 网格超纸拒绝 / 页面三态 | **① 自动化通过** | `test_universal_semantics` 8、`test_print_pdf_semantics` 24、`test_qt_print_semantics` 16 |
| 偏移/静区/模块宽进渲染与协议指令 | **① 自动化通过** | `test_print_pdf_semantics` 24、`test_adapters_content` 31、`test_print_ui_logic`（offsets/gap 进 content） |
| LabelContent 完整布局 + `build_payload(job, content=)` 三协议 | **① 自动化通过** | `test_adapters_content` 31 + zpl/tspl/cpcl 改写 |
| SENT/QUEUED 语义、USB/蓝牙/LPR 禁用、registry 无品牌推断（逻辑分支） | **① 自动化通过** | `test_transport_honesty` 15、`test_adapters_registry` 22、`test_print_ui_logic` 46、`test_transports` 33 |
| PrintJobSession 并发锁/句柄/临时文件生命周期 | **① 自动化通过** | `test_print_job_session` 15 |
| 驱动提权：非 Windows 守卫 | **① 自动化通过** | `test_driver_installer::test_elevate_process_raises_on_linux`（104 项全过） |
| 驱动提权四态（LAUNCHED/REQUIRES_UAC/CANCELLED_BY_USER/FAILED）、真机 UAC、安装→刷新闭环 | **④ 尚未实测** | Linux 无法触发 ShellExecuteW/UAC，无单测覆盖四态 |
| **TCP 9100 网络通道**（配置校验/端点解析/错误映射） | **① 自动化通过 + ④ 尚未实测** | 自动化仅校验与错误分支；真实连通与出纸未做 |
| **1.1.0 Windows 构建**（exe/便携包/安装包） | **⑤ 尚未实现（未构建）+ ④ 尚未实测** | Linux 无 ISCC、无法产出 Windows 产物；脚本已改但未编译 |
| **USB 私有直连 / 蓝牙 SPP·BLE / LPR 传输集成** | **⑤ 尚未实现（按设计未做）** | 一律抛 `TRANSPORT_NOT_IMPLEMENTED`，不伪装可用 |
| 真实队列枚举/提交/取消/查询（EnumPrinters/OpenPrinter/SetJob/GetJob） | **④ 尚未实测** | 无 Windows 队列 |
| 1:1 物理出纸尺寸卡尺复核 | **④ 尚未实测** | 自动化只测 PDF 排版尺寸 |
| ZPL/TSPL2/CPCL 真实设备出纸 | **④ 尚未实测** | golden 仅字节级比对，未连设备 |
| UAC 提权弹窗、驱动安装→刷新打印机闭环 | **④ 尚未实测** | Linux 无法触发 UAC |
| 安装器 1.1.0 交互（检测页/驱动可选页/`--printer-setup`） | **④ 尚未实测** | 须先 Windows 编译后人工走向导 |
| 静态安全扫描 | **① 自动化通过**（2026-10-18 轮记录，本轮未重扫） | bandit 0 问题 |
| ③ 真实设备实测 | **本轮 0 项达到** | 无任何型号经真实设备测试 |
| ② Windows 构建通过 | **本轮 0 项达到** | 1.1.0 未构建 |

### 2A.3 本轮明确"尚未实测"清单（禁止写成已验证）

1. 真实队列**提交 / 取消 / 查询**全流程（真实 Windows 打印队列，任务 ID 移除与
   终态核对）；
2. **1:1 物理出纸**：卡尺复核输出模块宽度/静区/整体尺寸与标称一致；
3. **协议设备实测**：ZPL / TSPL2 / CPCL 在真实标签打印机上实际出纸；
4. **UAC**：真实提权弹窗、用户自备官方驱动包安装全过程；
5. **驱动安装刷新闭环**：等待安装完成 → 应用内「刷新打印机」→ 队列/驱动状态
   由 MISSING 转 OK 的完整闭环；
6. 安装器 1.1.0 向导交互（打印环境检测页、驱动可选页、`--printer-setup` 勾选
   启动），须先按 `docs/BUILD_REPORT.md` 第 7 节在 Windows 编译后执行。

---

## 2B. 第三轮：第二轮打印审计修复与发布门禁（2026-10-22）

> 本章为第二轮审计（P0 / P1a / P1b / P3a）修复轮的验收记录。修复已全部
> 落地，自动化复测 **711 passed / 2 skipped（客户端，EXIT=0）+ 167 passed
> （服务端，EXIT=0）**；硬件、Windows 构建、UAC 等非自动化项**一律未执行/
> 未实现，按五级标签如实标注**，不把"代码写了/自动化通过"写成"已在真机
> 验证"，不写入任何虚构哈希或实测结果。

### 2B.1 P0：RunLater 后台线程生命周期重写（`client/ui/widgets.py`）

**根因（第二轮遗留现象）**：裸 `QThread` 启动后进入 `exec()` 事件循环，
`fn` 执行完线程并不退出，必须等主线程把排队的 done/failed 信号派发出去、
再由槽调用 `quit()`。当主线程事件循环不再被泵动（自动化测试末尾、应用
退出前）时，`quit()` 永不被调用，线程停留在 running；解释器/QApplication
析构时 Qt 强制删除仍在运行的 QThread，触发
`QThread: Destroyed while thread '' is still running` 并 Aborted/Segfault
（退出码 134/偶发 139）。即便 `fn` 已完成，未被释放的 QThread 对象连同
排队信号进入 C++ 析构也会造成 use-after-free。这正是第二轮客户端全量
pytest 在打印总结行后被析构终止的来源（第二轮已隔离定位但未修）。

**修复（落地事实）**：

1. 重写 `_TaskThread.run()`：`fn` 执行完毕（成功/异常）即发信号并返回，
   线程自然 `finished`，**不进入 `exec()` 空转循环**，从根上消除"fn 完了
   线程还活着"；
2. 模块级注册表（锁保护 `_ACTIVE_TASKS` / `_ALL_TASKS`）持有每条任务：
   线程**强引用** + 父控件弱引用 + 回调，避免临时 RunLater 对象被 GC 后
   线程无人回收、重复调用造成线程泄漏；
3. 同时绑定 `QApplication.aboutToQuit` 与解释器 `atexit`：`shutdown_all()`
   对所有活跃线程 `requestInterruption + quit + wait(限时 2000ms)`，超时仅
   告警不阻塞不崩溃；回收时 `_disconnect` 断开信号，杜绝退出后迟到的排队
   回调触达已析构对象；
4. 回调经信号自动切回主线程；父控件已销毁（弱引用失效）或已进入退出流程
   则不再回调；`fn` 异常在 `run()` 的 try/finally 语义下保证发出 failed 并
   结束线程，回调自身异常被捕获记录，不影响线程收尾。

**证据（第三轮实测）**：

- 客户端全量 `pytest tests/ -q` 连续 3 次均为 `711 passed, 2 skipped`，
  **EXIT=0**；stderr 无 `QThread: Destroyed`、无 Aborted/Segfault（第二轮
  的析构终止现象已消除）；
- 新增 `tests/test_runlater_lifecycle.py`（2 项）：回调完成后任务从活跃集合
  释放且线程 finished；重复发起多任务各自独立结束、不泄漏活跃线程
  （不调用 `shutdown_all`，避免置全局退出标志影响同进程其他用例）。

### 2B.2 P1a：配置传递——静区单位转换、间隙语义隔离、unapplied 诚实标记

**修复（落地事实）**：

1. **静区 mm ↔ 模块数唯一换算入口（`client/printing/units.py`）**：
   `quiet_zone_mm_to_modules(quiet_zone_mm, module_mm) = round(qz_mm /
   module_mm)` 与互逆的 `quiet_modules_to_mm(modules, module_mm)`；
   `module_mm` 非正、`quiet_zone_mm` 为 None/负数均**显式抛 ValueError**，
   绝不静默回退成错误静区。`calibration.check_quiet_zone_mm()` 毫米版本
   校验先经 units 换算再委托既有 `check_quiet_zone`，既有模块数版本行为
   不变。
2. **静区与标签间隙语义隔离（冻结）**：条码**静区 quiet zone**（识读保护带，
   UI/配置层以 `quiet_zone_mm` 毫米保存，渲染/编码层以模块数工作）与
   标签纸**物理间隙 `label_gap_mm`**（相邻标签走纸/定位硬件参数，仅专用
   协议 TSPL GAP 等与设备相关）**严禁混用**：`fit_barcode` 不接受
   `label_gap_mm`；`render_label_sheet_pdf` 只把间隙原值记入 meta
   `label_gap_mm_passthrough` + `label_gap_mm_note`，**不加入静区或条码
   布局**（`gap=3` 与 `gap=0` 渲染几何一致）。
3. **渲染参数显式化（`pdf.py` meta.unapplied_params）**：
   `render_label_sheet_pdf` 新增 `quiet_zone_mm` / `label_gap_mm` /
   `hardware_params` 显式参数，并在 meta 回写配置生效回执：
   `quiet_modules_applied` / `quiet_zone_mm_applied` / `module_mm_applied`
   为真实生效值；走纸（feed_mm）、间隙传感（gap_sensor_mode）、打印浓度
   （print_darkness）等软件无法控制的硬件参数逐项进入 `unapplied_params`
   并标注"不由软件直接控制"，**禁止静默丢弃、禁止显示"已应用"**。
4. **`resolve_render_params` / `hardware_controlled_notice`
  （`print_export_logic.py`，纯函数）**：配置 → 渲染参数映射（静区 mm 换算、
   module 解析、gap 直通、硬件参数列入 unapplied）；逐项声明"走纸与间隙
   定位由打印机硬件控制、浓度由驱动/设备面板设置，不由软件直接控制"，
   设备未声明 `supports_gap_sensor` 时追加"间隙参数仅对支持设备生效"。
   `print_export.py` 接线：通用路径先 `resolve_render_params(cfg,
   template, caps)`，再把 `quiet_modules/quiet_zone_mm/target_module_mm/
   label_gap_mm/hardware_params` 显式传入渲染器。

**证据**：新增 `tests/test_print_config_semantics.py`（29 项，pypdf 实测几何
+ meta 断言；覆盖 mm↔模块换算、非法输入抛错、gap 不进布局、硬件参数如实
标注、UI dict → build_job_from_form → resolve_render_params →
render_label_sheet_pdf 全链路），全部 passed。

### 2B.3 P1b：缩放风险严格分级（block / warn + 取消选项）

**诚信核心（`client/printing/scale_policy.py`，纯函数无 Qt/无硬件依赖）**：
**PDF 页面尺寸正确 ≠ 物理标签输出尺寸正确。** 软件按精确毫米把页面尺寸
下发给系统队列，但第三方驱动是否再缩放、设备能否按该物理尺寸走纸，软件
无法控制也无法代为确认。`classify_scale_risk()` 分级互斥主干：

| 条件 | 级别 | 原因码 / 映射码 |
|---|---|---|
| 纸张无效（缺尺寸 mm） | **block** | INVALID_PAPER / INVALID_JOB |
| 尺寸超容差 + `allow_paper_conversion=False` | **block** | PAPER_SIZE_MISMATCH（error 阻止提交，禁止静默拉伸/压缩） |
| 尺寸超容差 + 用户已确认转换 | **warn** | PAPER_CONVERTED（按 PDF 实际尺寸 1:1，仍附驱动风险提示） |
| 容差内 + `supports_true_size is True` | **ok** | — |
| 容差内 + `supports_true_size is False` | **warn** | TRUE_SIZE_NOT_DECLARED / SCALE_MISMATCH |
| 容差内 + caps 缺失 / `supports_true_size is None` | **warn** | TRUE_SIZE_UNKNOWN / SCALE_MISMATCH（目标物理尺寸无法由软件确认） |

**UI 接线（`print_export.py`）**：

- `ScaleRiskConfirmDialog`：warn 级风险逐条列出原因，提供【取消（中止打印）】
  与【继续打印】两个选项；**block 级直接 `error` 中止，不进入对话框**；
  用户取消则释放锁、停止提交，不预览不打印；
- `_classify_preview_risk()`：预检报告可用时按实测页尺寸分级；预检不可用
  （模块缺失/未落盘）时**不静默放行**，以纸张自身尺寸兜底，能力未声明/
  未知 → warn，由确认对话框给取消选项；
- `adapters/universal.py` 的 `_classify_scale_report` 与 scale_policy 对齐：
  超容差未许可 → error(block)，已确认转换 → warning(warn)，单轴/局部偏差
  保持 SCALE_MISMATCH warning；`qt_print.resolve_effective_page` 转换注记
  补诚信提示"PDF 页面尺寸正确不代表物理标签输出尺寸正确；第三方驱动缩放
  不受本软件控制，请先以测试页复核"。

**证据**：新增 `tests/test_scale_risk.py`（32 项）：分级矩阵、边界
（A4 210×297、40×25、57×40、横纵互换、容差 0.1/0.5/2.0、DPI
203/300/600/None 不改变分级）、universal block→error / warn→warning、
qt_print 三态保持、任何 message 不得出现"已应用"，全部 passed。

### 2B.4 P3a：协议状态四态区分 + verified 守卫修复

**四态（`job.JobOutcome`，不得拔高）**：

| outcome | 触发条件 | 如实含义 |
|---|---|---|
| `QUEUED` | Windows 系统队列提交成功 | 任务进入队列，**不代表物理打印完成** |
| `SENT` | TCP 9100 等直接通道发送成功（无设备确认回执） | 指令送达设备网络栈，**不等于物理打印完成** |
| `PHYSICALLY_PRINTED` | 仅当设备/系统提供可验证完成证据 | 当前**无任何通道达到此级**，严禁由 QUEUED/SENT 拔高 |
| `FAILED` / `CANCELLED` | 失败 / 用户取消 | 携带登记错误码，不吞异常、不改写为成功 |

**verified 守卫修复（`capabilities.from_dict` / `_truthy_verified`）**：
原 `bool()` 裸转会把非空字符串（"false"、"0"）误当已实测；现仅明确为真
（`True` / `1` / `"true"` `"1"` `"yes"` `"y"` `"on"`）才置
`verified_with_hardware=True`；缺省 / None / False / `"false"` / `"0"` /
`"no"` / `""` 一切未知值一律 **False**。另有源码级守卫：全仓库 `client/`
不得出现把 `verified_with_hardware` 赋为 True 的字面量。

**证据**：新增 `tests/test_protocol_state_audit.py`（51 项）：四态语义、
USB/蓝牙 open/write 与 LPR `TRANSPORT_NOT_IMPLEMENTED`、非 Windows
`UNSUPPORTED_PLATFORM`、verified 严格解析参数化、源码无 True 赋值扫描、
协议只按显式 `protocol` 分发（不按品牌/驱动名推断）、OSError 映射
SPOOL_FAILED、不真实发包（只注入 fake channel），全部 passed。

### 2B.5 第三轮新增测试清单（114 项，全部 passed）

| 类型 | 文件 | 收集项 | 结果 | 覆盖点 |
|---|---|---|---|---|
| 新增 | `tests/test_runlater_lifecycle.py` | 2 | 2 passed | RunLater 回调释放/线程 finished/多任务不泄漏 |
| 新增 | `tests/test_print_config_semantics.py` | 29 | 29 passed | 静区 mm↔模块换算、gap 隔离、unapplied、全链路 meta |
| 新增 | `tests/test_scale_risk.py` | 32 | 32 passed | block/warn/ok 分级矩阵、边界、适配器/qt 对齐、诚信措辞 |
| 新增 | `tests/test_protocol_state_audit.py` | 51 | 51 passed | 四态语义、未集成通道报错、verified 守卫、协议分发、错误映射 |
| **合计** | 4 文件 | **114** | **114 passed** | 0 failed / 0 error |

> 第三轮客户端收集 713 = 第二轮 599 + 本轮新增 114；711 passed = 713 −
> 2 skipped（与前两轮相同的 2 项环境/设计 skip，见 1.3）。

### 2B.6 第三轮五级状态区分表

> 五级定义同 2A.2：① 自动化通过（Linux pytest 已跑过该逻辑）；② Windows
> 构建通过（真实 Windows 由 PyInstaller/ISCC 产出可分发文件）；③ 真实设备
> 实测（真实打印机硬件 + 真实桌面交互验证通过并留档）；④ 尚未实测
> （代码/脚本/指引就绪，目标环境未验证）；⑤ 尚未实现（功能未做或按设计
> 有意不做）。

| 事项 | 本轮实际级别 | 依据 / 待办 |
|---|---|---|
| RunLater 线程生命周期（run 自然结束 + 强引用注册表 + aboutToQuit/atexit 统一 join） | **① 自动化通过** | `test_runlater_lifecycle` 2 项；全量 EXIT=0、无 Destroyed、连跑稳定 |
| 静区 mm↔模块唯一换算、check_quiet_zone_mm、gap 语义隔离、unapplied 诚实标记、resolve_render_params | **① 自动化通过** | `test_print_config_semantics` 29 项 |
| scale_policy 分级 block/warn/ok + UI 取消选项 + universal/qt 对齐 | **① 自动化通过** | `test_scale_risk` 32 项；对话框离屏默认继续，真机交互 ④ |
| 四态 outcome 语义、USB/蓝牙/LPR 未集成报错、verified 严格守卫、协议显式分发 | **① 自动化通过** | `test_protocol_state_audit` 51 项 |
| warn 确认对话框真实桌面交互（取消/继续点击、阻断行为） | **④ 尚未实测** | 离屏测试走默认分支，未在真实桌面点击 |
| 真实队列枚举/提交/取消/查询（EnumPrinters/OpenPrinter/SetJob/GetJob） | **④ 尚未实测** | 无 Windows 队列 |
| 1:1 物理出纸尺寸卡尺复核、驱动是否缩放 | **④ 尚未实测** | 自动化只测 PDF 排版尺寸；PDF 正确 ≠ 物理正确 |
| ZPL/TSPL2/CPCL 真实设备出纸、TCP 9100 真实连通与出纸 | **④ 尚未实测** | golden/配置校验通过，未连设备 |
| UAC 提权、驱动安装→刷新闭环、安装器 1.1.0 向导交互 | **④ 尚未实测** | 须先 Windows 构建后人工走 |
| **1.1.0 Windows 构建（exe/便携包/安装包）** | **⑤ 尚未实现（未构建）+ ④ 尚未实测** | Linux 无 ISCC/PyInstaller win 产物；步骤见 2B.7 与 BUILD_REPORT.md 第 7 节 |
| **USB 私有直连 / 蓝牙 SPP·BLE / LPR** | **⑤ 尚未实现（按设计未做）** | 一律 `TRANSPORT_NOT_IMPLEMENTED`，不伪装可用 |
| ③ 真实设备实测 | **本轮 0 项达到** | 无任何型号经真实设备测试 |
| ② Windows 构建通过 | **本轮 0 项达到** | 1.1.0 未构建 |

### 2B.7 Windows 发布门禁运行手册（精确、可复制执行；本环境未执行）

> 以下步骤**必须在真实 Windows 10/11 x64 机器上由人工执行**；当前 Linux
> 沙箱无 Windows、无 ISCC.exe、无真实打印机，**全部未执行**。命令逐条
> 复制即可；任何产出的体积/校验和在实际构建前都不存在，禁止编造回填。

#### a) 环境准备

1. 安装 **Windows 10/11 x64**（`ArchitecturesAllowed=x64compatible`，不支持
   32 位）；安装 **Python 3.12**（勾选 Add python.exe to PATH）。
2. 仓库根目录打开命令提示符（CMD）或 PowerShell，安装依赖：

```bat
python -m pip install --upgrade pip
pip install -r requirements.txt
```

   关键依赖：`PySide6>=6.6.0`、`ReportLab>=4.0.0`、`pypdf>=5.0.0`、
   `pyinstaller>=6.3.0`；`pywin32>=306` 仅在 win32 安装（`sys_platform
   == 'win32'` 标记）；测试 `pytest>=8.0.0`。
3. 构建安装包另需安装 **Inno Setup 6**（默认装到
   `C:\Program Files\Inno Setup 6\`，提供 `ISCC.exe`）。

#### b) 完整自动化测试（期望退出码 0）

```bat
python -m pytest tests/ -q
python -m pytest tests_server/ -q
```

- 期望：客户端 `711 passed, 2 skipped`（0 failed 0 error，退出码 0）；
  服务端 `167 passed, 1 warning`（0 skipped，退出码 0）。
- 2 项 skip：`test_calibration.py:226`（旧缺失分支按设计 skip）、
  `test_encoder.py:65`（python-barcode 未装，可选第三方交叉验证）。
- 任一命令退出码非 0 或出现 failed/error，**停止发布**，先修复再复测。

#### c) 构建（PyInstaller 单文件 + 便携 zip → Inno Setup 安装包）

```bat
REM 1) PyInstaller onefile + 便携 zip（脚本已固化 spec 与 hiddenimports）
scripts\build_client.bat
REM    → dist\CodeLabelStudio.exe
REM    → dist\CodeLabelStudio-Portable.zip

REM 2) 编译 Inno Setup 安装包（须先完成第 1 步）
"C:\Program Files\Inno Setup 6\ISCC.exe" installer\codelabel.iss
REM    → dist\installer\CodeLabelStudio-Setup-1.1.0.exe
```

- 冒烟：双击/命令行启动 `dist\CodeLabelStudio.exe`，确认 7 个页面可打开、
  首次启动自动建 `data\codelabel.db`、stdout/stderr 无 ImportError/traceback；
  便携 zip 解压后直接运行同样通过。

#### d) SHA-256 回填（构建后执行；当前为"待计算"，不得编造）

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

把三个哈希逐行回填 `docs/BUILD_REPORT.md` 7.3 表（当前均为
"待 Windows 构建后计算并回填"）。

#### e) 升级与卸载验证（1.0.0 → 1.1.0 数据保留 / 卸载询问与清理）

1. 在已装 1.0.0（或任意旧版）且有业务数据的机器上运行
   `CodeLabelStudio-Setup-1.1.0.exe` 覆盖安装：确认数据目录（默认
   `%LOCALAPPDATA%\CodeLabelStudio\data`）**未被清空**，重启应用数据仍在；
2. 走完安装向导：核对数据目录页默认值、开始菜单快捷方式、可选桌面图标、
   「打印环境检测」只读页、「打印机驱动（可选）」页、`--printer-setup` 勾选
   启动；
3. 控制面板卸载：弹窗询问是否删除数据目录——选"否"→ 确认数据目录仍在；
   重装后再卸载选"是"→ 确认数据目录被删除、注册表
   `HKCU\Software\CodeLabelStudio\DataDir` 清理；
4. 便携版：解压 zip 直接运行，确认无需安装可用。

#### f) 真实设备人工验收（至少 1 台普通打印机 + 1 台标签打印机）

按 2B.8 空表逐台填写，步骤同第 4.2 节（测试页 → 标尺 → 偏移 → 1:1 核对 →
静区/模块宽扫码 → 正式打印核对份数/方向 → 取消任务 → 失败/离线演练）。
**只有真实测试通过的型号才可在 `PRINTER_COMPATIBILITY.md` 标记 L3 已验证。**

### 2B.8 真实设备人工验收记录表（空表模板，待用户填写）

> 每台设备一行，可复制扩展；至少 **1 台普通打印机（激光/喷墨）+ 1 台标签
> 打印机（直热/热转印）**。任何"未执行"保持空白/未通过，严禁预填通过。
> 标尺测量：四边留边 mm、5mm 网格格差、100mm 基准尺偏差 mm；结论只许填
> 通过 / 未通过 / 备注。

| # | 日期 | 打印机完整型号（含硬件版本） | 驱动名称与版本（厂商/Windows Update，签名） | 连接方式（USB/以太网/Wi-Fi/蓝牙/本机队列）及端口/IP | 标签规格 宽×高(mm)/类型/间隙或黑标/碳带 | 标尺测量：四边留边(mm) / 5mm 网格格差 / 100mm 基准偏差(mm) | 偏移 x/y(mm) | 份数（设定/实出） | 扫码结果（可读/不可读） | 取消任务结果（队列移除/未移除） | 结论（通过/未通过） | 备注（报错码/错位/缩放/缺字） |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1（普通打印机） | | | | | | 四边：／网格：／100mm： | | / | | | 未执行 | |
| 2（标签打印机） | | | | | | 四边：／网格：／100mm： | | / | | | 未执行 | |
| 3（可选：网络/蓝牙） | | | | | | 四边：／网格：／100mm： | | / | | | 未执行 | |

回填规则：仅当本行全部测试项通过且卡尺/扫码留档后，结论方可改"通过"，
再回 `PRINTER_COMPATIBILITY.md` 把对应类别 L4 升级为 L3 并标注本表编号；
`verified_with_hardware=True` 只允许在存在真实验收记录时出现。

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
- **第三轮 P1b 细化（2026-10-22）**：缩放判定升级为 `scale_policy` 三级
  分级（block/warn/ok，见 2B.3）：超容差未许可 → block 阻止提交；已确认
  转换 / 设备未声明或未知支持 1:1 → warn 弹确认框给【取消/继续打印】；
  匹配且声明支持 1:1 → ok。核心诚信口径：**PDF 页面尺寸正确 ≠ 物理标签
  输出尺寸正确**，第三方驱动缩放不受软件控制。

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

| 项 | 状态（五级） | 说明 |
|---|---|---|
| Windows 真实队列枚举（EnumPrinters / QPrinterInfo） | **④ 尚未实测（未执行）** | Linux 无系统队列 |
| 真实队列提交 / 取消 / 查询（QPrinter / WritePrinter RAW / SetJob / GetJob） | **④ 尚未实测（未执行）** | 无设备、无队列；成功语义仅到 QUEUED/SENT |
| 1:1 物理输出尺寸卡尺复核 | **④ 尚未实测（未执行）** | 自动化只测 PDF 排版尺寸，不测物理纸 |
| ZPL / TSPL2 / CPCL 真实设备出纸 | **④ 尚未实测（未执行）** | golden 指令仅文本/字节级比对，未连设备 |
| TCP 9100 网络链路连通与出纸 | **① 自动化通过（校验/错误映射）+ ④ 尚未实测** | 仅配置校验与错误分支，未真实连通 |
| USB 私有直连 / 蓝牙 SPP·BLE / LPR | **⑤ 尚未实现（按设计未做）** | 一律抛 TRANSPORT_NOT_IMPLEMENTED，不伪装可用 |
| 驱动下载/安装/UAC 提权全过程 | **④ 尚未实测（未执行）** | 应用只检测与指引，不捆绑不自动安装 |
| 驱动安装→刷新打印机状态闭环 | **④ 尚未实测（未执行）** | LAUNCHED≠安装成功，需真机走完复核 |
| 1.1.0 安装包（Inno Setup）编译 / exe / 便携包 | **⑤ 尚未实现（未构建）+ ④ 尚未实测** | Linux 无 ISCC.exe、无法产出 Windows 产物；步骤见 2B.7 与 `BUILD_REPORT.md` 第 7 节 |
| RunLater 线程生命周期（P0） | **① 自动化通过** | `test_runlater_lifecycle` 2 项；全量 EXIT=0、无 Destroyed、连跑稳定 |
| 静区 mm↔模块换算 / gap 隔离 / unapplied 诚实标记（P1a） | **① 自动化通过** | `test_print_config_semantics` 29 项 |
| 缩放风险 block/warn 分级 + 取消选项（P1b） | **① 自动化通过（分级逻辑）+ ④ 尚未实测（对话框真机交互）** | `test_scale_risk` 32 项；PDF 正确 ≠ 物理正确 |
| 四态 outcome / verified 严格守卫（P3a） | **① 自动化通过** | `test_protocol_state_audit` 51 项 |
| 第三方 python-barcode 交叉验证 | **跳过(1)** | 可选库未安装，非缺陷 |
| 校准旧缺失分支 | **跳过(1)** | 已被 pdf_render 覆盖，按设计 skip |

**结论（第三轮 2026-10-22，以此为准）：自动化 711 passed / 2 skipped
（客户端，收集 713，EXIT=0，连续 3 次稳定，无 QThread Destroyed/abort）+
167 passed（服务端，EXIT=0，1 条第三方 anyio 告警）全部通过，0 failed
0 error；2 项跳过均为已说明的环境/设计原因。P0 线程生命周期修复消除了
第二轮的析构终止现象；P1a 静区单位转换/间隙隔离/unapplied、P1b 缩放
block/warn 分级与取消选项、P3a 四态与 verified 守卫均有自动化闭环。
无任何型号经真实设备测试，②Windows 构建通过与 ③真实设备实测本轮均为
0 项；Windows 发布门禁按 2B.7 手册执行，硬件验收按 2B.8 空表在真机填写。
第二轮 597/167 结论与第一轮 456/167 历史记录见 2A / 2.1/2.2。**

**结论（第二轮 2026-10-20，历史保留）：自动化 597 passed / 2 skipped（客户端）+
167 passed（服务端）全部通过，0 failed 0 error；2 项跳过均为已说明的
环境/设计原因。客户端进程在 pytest 总结输出后被 Qt offscreen 析构终止
（非失败，见 1.3）。无任何型号经真实设备测试，②Windows 构建通过与
③真实设备实测本轮均为 0 项；硬件验收以第 4 节清单在 Windows 人工执行为准。
第一轮 456/167 历史记录见 2.1/2.2。**
