# CodeLabel Studio — 实施计划（14 阶段）

依据 `docs/FINAL_AUDIT_REPORT.md` 的审计发现制定。每个阶段完成后运行测试再进入下一阶段。

## 阶段 0：仓库审计与基线 ✅
- 五份基线文档：FINAL_AUDIT_REPORT / IMPLEMENTATION_PLAN / TEST_PLAN / SECURITY_CHECKLIST / RELEASE_CHECKLIST
- 6 项审计发现（3 严重 / 2 中 / 1 低）已定位到文件与行号

## 阶段 1：修复条码核心与数据完整性
1. **F-03**：统一来源校验 `validate_source()`（白名单 internal/gs1_authorized；GS1 必须带授权编号）；所有入口（generate_single 显式路径 / import_records / create_range）强制走业务校验；记录表新增 `authorization_reference`
2. **F-02**：schema v2——全字段 CHECK 约束 + range_id 外键；v1→v2 表重建迁移（保留数据，`import`→`internal`）
3. **F-05**：编号迁移框架 `_migrate_1_to_2()`，事务内搬迁，失败回滚
4. **F-01**：`restore()` 加固（恢复前自动备份 / 源校验 / 原子替换 / 完整性检查 / 日志）
5. **F-04**：`generate_batch()` 上限 + 进度回调 + 取消 + 全成功或全回滚
6. 测试：绕过防护、DAO 直写非法值、迁移保数据、恢复各失败场景、批量边界

## 阶段 2：渲染导出与打印
- ReportLab 矢量 PDF（真实 PDF 文档）、真实 SVG、PNG、CSV（公式注入防护 `= + - @` 前缀转义）
- 标签模板服务：尺寸/边距/间距/行列/横竖排/条高/文本开关/字体/对齐/偏移
- 打印预览几何计算：页面边界、标签边界、静区、越界警告、100% 原始尺寸选项
- 打印校准说明 + 人工验收表

## 阶段 3：PySide6 桌面客户端
- 页面：登录前/注册登录/首页/单个生成/批量生成/编码管理/批量导入/打印中心/导出中心/账号订阅/设置
- QThread 后台任务、表单验证、异常日志脱敏、offscreen 可测试

## 阶段 4-8：在线服务端
- FastAPI + SQLAlchemy + Alembic；11 张表；Argon2id；邮箱验证/密码重置令牌（哈希存储、一次性、有效期）；速率限制
- 7 天试用（服务端时间、幂等创建、管理员延长留痕）
- 订阅状态机 trialing/active/past_due/expired/canceled/suspended
- 授权签名：Ed25519，服务端持私钥，客户端内置公钥；离线宽限 72h 可配置
- 订单 + 人工付款确认流程（管理员核实后才开通，全程审计）
- 管理员 API：权限分离、二次确认、原因必填、审计日志

## 阶段 9：API 与数据库完整性
- 路由统一 `/api/v1/`；金额 Decimal；UTC 存储；外键与唯一约束；迁移脚本

## 阶段 10：安全审计
- bandit 静态扫描 + 依赖漏洞检查 + 人工清单（SQL 注入/路径穿越/CSV 注入/SVG/越权/伪造/重放/日志泄露/密钥提交）

## 阶段 11：测试体系
- 单元（算法/来源/范围/状态/事务/备份/导出/模板/金额/订阅/签名/试用）
- 集成（注册→验证→试用→订单→确认→订阅→到期）
- 安全（越权/伪造 webhook/重复回调/过期授权/无效签名/CSV 注入/路径穿越/频率超限）
- E2E（13 条用户旅程）
- pytest 统一运行，记录命令/退出码/通过数

## 阶段 12：Windows 构建
- PyInstaller spec + build_windows.bat；Inno Setup 脚本（缺失则 winget 尝试安装，否则标记阻塞并交付脚本）
- Setup.exe + Portable.zip + SHA-256 清单

## 阶段 13：部署运维
- DEPLOYMENT / OPERATIONS / BACKUP_RESTORE / UPGRADE_ROLLBACK 四份文档 + .env.example + 管理员初始化脚本 + 备份恢复实测

## 阶段 14：最终验收
- 独立复核对 20 条验收标准；更新 FINAL_AUDIT_REPORT.md；发布建议（通过/有条件通过/不通过）
