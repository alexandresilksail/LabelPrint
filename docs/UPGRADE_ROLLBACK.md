# CodeLabelStudio 升级与回滚

## 一、版本策略

- 语义化版本 `MAJOR.MINOR.PATCH`；
- 客户端与服务端独立发版，授权协议保持向后兼容（载荷含 `license_id`/`user_id`/`expires_at` 等稳定字段，新增字段旧客户端忽略）；
- 数据库变更**只增不改**：新列必须有默认值或可空，禁止直接删列/改类型（用迁移 + 双写过渡）。

## 二、标准升级流程

```bash
# 0) 预告与备份（见 BACKUP_RESTORE.md）
pg_dump -Fc -d codelabel -f /backup/pre_upgrade_$(date +%F).dump

# 1) 拉取目标版本
git fetch && git checkout v<X.Y.Z>

# 2) 安装依赖（如有变化）
.venv/bin/pip install -r requirements.txt

# 3) 数据库迁移（Alembic，幂等）
cd server && ../.venv/bin/alembic upgrade head

# 4) 启动新版本
systemctl restart codelabel

# 5) 冒烟（见 DEPLOYMENT.md 第六节）
curl -s https://<domain>/health
curl -s https://<domain>/api/plans
```

## 三、回滚流程

### 应用层回滚（代码问题）

```bash
systemctl stop codelabel
git checkout v<上一版本>
.venv/bin/pip install -r requirements.txt   # 如依赖有变
systemctl start codelabel
```

**前提**：新版本未执行破坏性迁移。若已执行，先按下面的数据库回滚处理。

### 数据库回滚（Alembic downgrade）

```bash
cd server
../.venv/bin/alembic downgrade -1        # 回退一个版本
../.venv/bin/alembic downgrade base      # 或回退到空库（仅限演练环境）
```

> 已初始化的 `initial schema` 之后，downgrade 会删除全部表。生产环境**禁止**直接 downgrade 到 base；应恢复备份（见 BACKUP_RESTORE.md）。

### 数据恢复式回滚（最安全）

```bash
systemctl stop codelabel
pg_restore -d codelabel --clean --if-exists /backup/pre_upgrade_$(date +%F).dump
git checkout v<上一版本>
systemctl start codelabel
```

## 四、客户端升级

- 安装包版：Inno Setup 自动卸载旧版并保留 `data/` 目录；
- 绿色便携版：整体替换目录，保留原 `data/`；
- 客户端本地 schema 由 `migrate_1_to_2` 自动迁移（表重建，事务内完成；失败整体回滚）；
- **不要在客户端未升级时降级服务端授权载荷语义**——旧客户端可能无法解析新必填字段。

## 五、升级检查清单

- [ ] 备份完成并验证可读
- [ ] 迁移在预发环境执行过至少一次
- [ ] 冒烟脚本通过（health / plans / public-key / 注册登录试用激活全链路）
- [ ] 回滚方案明确（git 版本号 + 备份文件名）
- [ ] 低峰期执行，公告已发
- [ ] 升级后观察 30 分钟：错误率 / 登录成功率 / 授权验证成功率

## 六、已知迁移注意事项

1. **schema v1 → v2**（客户端）：GS1 记录无授权编号时降级为 `internal` 并写 `migrate_source_downgrade` 审计；GS1 范围缺授权编号则迁移整体失败回滚——升级前先补全授权编号；
2. **alembic initial**（服务端）：仅适用于空库；已有数据的库以 `alembic stamp head` 对齐版本号后走后续增量迁移；
3. 私钥轮换 = 全部历史授权失效，必须与重新签发计划同步执行。
