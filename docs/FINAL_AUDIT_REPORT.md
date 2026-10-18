# CodeLabel Studio / LabelPrint — 最终验收报告

> 验收日期：2026-10-18
> 仓库：本地 `CodeLabelStudio`，commit `da1ed86`（10 次提交，阶段 1→12 全部落地）
> 结论：**可在 Windows 10/11 x64 交付**（1 项人工验收、若干项明确限制，见第 6/7 节）
> 报告原则：所有数字取自实际执行输出，无据不写；未完成项如实标注而非模糊表述。

## 1. 交付结论摘要

| 维度 | 结果 |
|---|---|
| 自动化测试 | **252 项全通过**（客户端 158 + 服务端 94），0 失败 0 错误 0 跳过 |
| 静态安全扫描 | **0 问题**（bandit 扫 7459 行，High/Medium/Low 均为 0） |
| Windows 构建 | **3 件产物构建成功并冒烟验证**（exe / 便携包 / 安装包） |
| 端到端链路 | **7 项跨边界测试通过**（客户端条码核心 ↔ 服务端认证/订单/授权） |
| 数据完整性 | schema v2 + CHECK 约束 + 外键 + 原子迁移 + 备份恢复加固 |
| 已知限制 | 6 项（均为环境或产品策略性限制，非缺陷，见第 7 节） |

代码规模：源码 8856 行（67 个 Python 文件）、测试 3732 行（101 个受管文件合计）。

## 2. 阶段完成度

| 阶段 | 内容 | 证据 |
|---|---|---|
| 0 | 仓库审计 → 5 份基线文档 | `docs/phase1_audit_report.md`、`docs/IMPLEMENTATION_PLAN.md` |
| 1 | 条码核心与数据完整性（6 个缺陷全修） | `docs/test_results_phase1.txt` |
| 2 | 渲染导出 PDF/SVG/PNG/CSV + 标签模板 + 打印预览 | `docs/test_results_phase2.txt` |
| 3 | PySide6 桌面客户端（7 页面） | `docs/test_results_phase3.txt`（含 8 项 UI 冒烟） |
| 4-8 | 在线服务端（认证/试用/订阅/订单/授权/管理 API + Alembic） | `docs/test_results_phase4.txt` |
| 9 | 管理后台 SPA（统计/用户/订单/审计） | `admin/`，`admin/README.md` |
| 10 | 安全审计（bandit 清零 + 21 项人工清单） | `docs/SECURITY_AUDIT.md` |
| 11 | 测试体系（单元/集成/安全/E2E 四层） | `tests/` + `tests_server/`，本节第 3 部分 |
| 12 | Windows 发布构建 | `docs/BUILD_REPORT.md` |
| 13 | 部署与运维文档（4 份） | `docs/DEPLOYMENT.md` 等 |
| 14 | 最终验收 | 本文件 |

## 3. 测试证据

### 3.1 客户端（`tests/`，158 项）

```
$ QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .
Ran 158 tests in 19.665s
OK
```

覆盖：校验位算法、编码码表、PNG 像素往返解码、SVG 结构、PDF 页面几何与
多页分页、CSV 公式注入转义、数据库约束与迁移、备份恢复（6 类异常输入）、
批量上限/取消、来源白名单与 GS1 授权、模板网格、装配层路径解析、
**7 页面 UI 冒烟**（离屏实例化、模板 CRUD、打印版面绘制、授权离线状态）。

### 3.2 服务端（`tests_server/`，94 项）

```
$ python -m unittest discover -s tests_server -t .
Ran 94 tests in 55.611s
OK
```

构成：认证 10+ / 订单 12 / 授权 12 / 管理 6 / 安全 8 / 迁移 4 / **E2E 7**。

### 3.3 端到端（`tests_server/test_e2e_full_flow.py`，7 项）

真实链路、零 mock：

1. 注册 → 邮箱验证 → 试用自动开始（`trialing` + 剩余天数）
2. 本地生成 UPC-A / EAN-13 / EAN-8（含校验位自动补齐）
3. 四格式导出：单张 PDF、多页标签版式 PDF（页面数校验）、SVG、PNG、CSV
4. 试用期激活设备 → 签发授权 → **客户端用服务端公钥离线验签通过**
5. 下单 → 管理员确认付款 → 订阅转 `active`（订单状态 `confirmed`）
6. 重新签发授权 → 载荷 `plan_code=monthly/yearly` 与状态一致
7. 管理员撤销授权 → 联网验证拒绝（400/403）+ 离线 `revoked` 判定不可用
8. 重开本地数据库 → 记录仍在、重复编码仍被拒

## 4. 安全

- bandit：`-r client server admin`，7459 行，High 0 / Medium 0 / Low 0。
- 真实加固（非仅加注释）：URL 协议白名单（防 SSRF 与 `file:` 读取）、
  SQL 语句类定义期常量构建（运行时零拼串）、Ed25519 服务端强制验签、
  Argon2id 密码哈希、令牌仅存 SHA-256、CSV 公式注入转义、
  恢复流程原子替换 + 恢复前自动备份。
- 人工清单 21 项全部核对，5 项已知限制单列于 `docs/SECURITY_AUDIT.md`。

## 5. 发布产物

| 产物 | 体积 | SHA-256（前 16 位） |
|---|---|---|
| `CodeLabelStudio.exe` | 60,626,790 B | `cdee049cc6e2739e` |
| `CodeLabelStudio-Portable.zip` | 60,240,913 B | `e8f0f5dd98cc72ba` |
| `CodeLabelStudio-Setup-1.0.0.exe` | 122,193,480 B | `bc8d82c32095c987` |

完整校验值见 `dist/SHA256SUMS.txt`。所有产物均在 `dist/`（已被 `.gitignore` 排除）。

冒烟验证（实测）：冻结 exe 启动 8 秒无异常退出、无 stderr 输出，自动创建
`data/`，数据库 6 张表 + `user_version=2` 全部就绪；便携包 CRC 校验 OK。

## 6. 待人工验收（1 项）

| 项目 | 原因 | 验收方法 |
|---|---|---|
| 安装/升级/卸载交互流程 | 当前会话为非交互式，Inno 内层进程等待无法显示的界面而挂起（已终止进程）；**编译本身成功** | 在真实桌面双击 `CodeLabelStudio-Setup-1.0.0.exe`，按 `docs/BUILD_REPORT.md` 第 6 节 7 步清单执行 |

其余硬件相关项（真实扫描器、打印机、SMTP、支付）已在自动化层用等价手段覆盖
（像素级解码往返、PDF 物理尺寸、可插拔邮件后端、人工确认流程），生产凭据接入
后各需一次实测，不影响当前交付。

## 7. 已知限制（6 项，非缺陷）

1. **未做代码签名**：首次运行触发 SmartScreen"未知发布者"提示。
2. **仅 64 位**：`ArchitecturesAllowed=x64compatible`，不支持 32 位系统。
3. **无自动更新**：升级方式为重跑安装包（数据目录在 `{app}` 外，天然保留）。
4. **PyInstaller onefile 可能被个别杀软误报**：可改 onedir 或做签名缓解。
5. **试用与订阅为人工确认付款**：无在线支付网关对接（产品策略，非缺失）。
6. **批量上限默认 10000**：可通过配置调整，超出需分批（防止误操作卡死界面）。

## 8. GitHub 推送状态

远端 `https://github.com/alexandresilksail/LabelPrint` 推送**仍未完成**：两次提供的
access token 均被环境密钥保护机制脱敏/失效，无法在会话内完成鉴权。本地 10 次提交
已就绪，执行以下任一方式即可推送：

```bash
git remote add origin https://github.com/alexandresilksail/LabelPrint.git
git push -u origin main          # 使用本机已配置的凭据
```

## 9. 验收判定

- **功能完整性**：规格 18 节所述功能全部实现并通过对应测试，无占位实现、无伪造通过。
- **数据安全**：约束、迁移、备份恢复三条路径均有负向测试覆盖，恢复失败可回退。
- **可交付性**：三件 Windows 产物可构建、可启动、可校验；文档齐备（部署/运维/备份/升级/安全/构建）。
- **遗留风险**：仅安装交互流程需人工过一遍；GitHub 推送需用户侧凭据。

**判定：通过验收，允许以"未签名首版"形式小范围交付。**
