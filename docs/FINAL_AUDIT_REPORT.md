# CodeLabel Studio / LabelPrint — 最终审计报告

> 本报告基于**实际源码逐文件核实**，不轻信 README 声明。
> 审计时间：2026-10-18 | 审计对象：本地仓库 commit `0027109`（44 文件）
> 本文件在阶段 14 将更新为最终验收状态。

## 1. 审计范围与方法

| 项目 | 方法 |
|---|---|
| 条码算法 | 源码审查 + 与 python-barcode 0.16.1 逐模块交叉验证 + 44 项单元测试 |
| 数据库层 | 源码审查 + 约束/事务/并发测试 |
| 业务服务层 | 源码审查 + 正向/负向测试 |
| 备份恢复 | 源码审查（发现重大缺陷） |
| 测试真实性 | 实际运行 `python -m unittest discover`，记录退出码与输出 |
| 构建/部署 | 环境实测（Docker/Inno Setup 缺失） |

## 2. 审计发现（按严重程度）

### F-01【严重】`restore()` 无任何安全防护
- **文件**：`client/database/db.py` L119-125
- **问题**：恢复操作直接 `src_conn.backup(self.conn)`。无恢复前自动备份；不验证源文件存在性、完整性、schema 结构；不验证 schema 版本兼容性；不拒绝无关数据库文件；恢复失败无回退；恢复后无完整性检查；无操作日志；无二次确认界面钩子。
- **风险**：用户选错文件或备份损坏时，可能覆盖损坏本地数据库，造成不可逆数据丢失。
- **修复方案**：恢复前自动生成 `.pre_restore_<UTC>` 备份；校验源文件 `PRAGMA integrity_check`、必需表结构、`user_version <= SCHEMA_VERSION`；经临时文件 + `os.replace` 原子替换；恢复后 `quick_check`；写 JSONL 恢复日志。
- **验证**：损坏文件/错误 schema/磁盘不足/中断恢复测试（阶段 1）。

### F-02【严重】数据库层无 CHECK 约束，DAO 可绕过业务规则
- **文件**：`client/database/schema.py`
- **问题**：`barcode_type`、`source_type`、`allocation_status`、`range_status`、`rows/columns`、尺寸、编号边界均无 DB 层约束。直接调用 DAO `insert()` 可写入任意字符串状态与来源类型。
- **修复方案**：schema 升级到 v2：全部关键字段加 `CHECK`；`barcode_records.range_id` 加外键；v1→v2 表重建迁移（保留数据，旧 `import` 来源映射为 `internal` 并记录）。
- **验证**：DAO 直写非法值必须触发 `sqlite3.IntegrityError`（阶段 1 测试）。

### F-03【严重】GS1 来源校验不一致，低层入口可绕过
- **文件**：`client/barcode/service.py` L128-159, L221-236
- **问题**：`generate_single()` 显式编码路径不校验 `source_type`（白名单绕过）；`gs1_authorized` 不要求 `authorization_reference`；`import_records()` 默认写入非白名单值 `"import"`；`barcode_records` 表无 `authorization_reference` 字段，无法记录授权来源（违反规格 3.3 模式 B）。
- **修复方案**：统一 `validate_source()` 业务校验（白名单 + GS1 必须带授权编号）；记录表新增 `authorization_reference` 列（v2 迁移）；导入入口同样校验。
- **验证**：全部入口正向/负向测试，含 DAO 绕过测试（阶段 1）。

### F-04【中】`generate_batch()` 无批量上限、无取消、无进度
- **文件**：`client/barcode/service.py` L163-190
- **问题**：`count` 无上限校验；无进度回调；无取消机制；大批次在单事务内无反馈。
- **修复方案**：`MAX_BATCH_SIZE`（默认 10000，可配置）；拒绝 0/负/非整数/布尔/超限；`progress_callback` + `should_cancel`；单事务全成功或全回滚（无半成品）。
- **验证**：小批次/上限/超限/取消/范围耗尽测试（阶段 1）。

### F-05【中】schema 无迁移框架
- **文件**：`client/database/db.py` L82-90
- **问题**：仅比较 `user_version` 后整体执行 `IF NOT EXISTS` 脚本；v1→v2 需要表重建，现有逻辑无法安全迁移已有数据。
- **修复方案**：实现编号迁移步骤 `_migrate_1_to_2()`，事务内完成表重建与数据搬迁，失败回滚。
- **验证**：带数据的 v1 库升级测试（阶段 1）。

### F-06【低】`VALID_SOURCE_TYPES` 定义污染
- **文件**：`client/database/schema.py` L12
- **问题**：白名单含 `"import"`，与业务语义（internal/gs1_authorized）不符，将随 F-02/F-03 一并修正。

## 3. 核实为正常工作的部分（保留，不重写）

| 模块 | 状态 | 证据 |
|---|---|---|
| 校验位算法（UPC-A/EAN-13/EAN-8） | ✅ 正确 | 与 python-barcode 0.16.1 逐模块一致；G9 笔误已修 |
| 编码渲染（L/G/R 码表+静区） | ✅ 正确 | 三方交叉验证 |
| PNG 渲染与像素解码往返 | ✅ 通过 | 测试 |
| SQLite 事务/并发 | ✅ 通过 | 8 线程零重复分配测试 |
| 状态机（generated→allocated→used/invalidated） | ✅ 正确 | 测试 |
| 编号范围分配语义（基础编号+校验位） | ✅ 正确 | 测试 |
| 批量耗尽安全停止 | ✅ 正确 | 测试 |
| 导入四分类预检 | ✅ 正确 | 测试 |

## 4. 环境实测（2026-10-18）

| 组件 | 状态 |
|---|---|
| Windows + Python 3.13.14（venv: binaries/python/envs/default） | ✅ |
| PyPI | ✅ |
| git 2.55 | ✅ |
| Docker | ❌ 未安装（服务端编排改 docker-compose 文件交付 + 本地 SQLite 实测） |
| Inno Setup | ❌ 未安装（尝试 winget 安装；失败则交付脚本并标记阻塞） |
| 真实打印机/扫描器/支付商户/SMTP | ❌ 人工验收项，标记"待人工验证" |
| GitHub 远端 LabelPrint | ⚠️ 公开空仓库；推送受环境密钥脱敏影响阻塞（token 方案见 README） |

## 5. 阶段 1 起的修复顺序

F-03 → F-02 → F-05 → F-01 → F-04 → F-06，每项修复后立即运行相关测试。
