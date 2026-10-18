# CodeLabelStudio 备份与恢复

## 一、备份对象

| 对象 | 位置 | 说明 |
| --- | --- | --- |
| 服务端数据库 | `CLS_DATABASE_URL` 指向的库 | 用户/订阅/订单/授权/审计 |
| 授权私钥 | `data/license_private.pem` 或 `CLS_LICENSE_PRIVATE_KEY` | **最高优先级**：丢失后无法重新验证历史授权 |
| 客户端本地库 | 用户本机 `data/codelabel.db` | 用户编码数据，由客户端「设置 → 备份数据库」自助备份 |

## 二、服务端备份

### PostgreSQL（生产）

```bash
# 每日定时（crontab 示例：0 3 * * *）
pg_dump -Fc -d codelabel -f /backup/codelabel_$(date +%F).dump
# 保留 30 天
find /backup -name 'codelabel_*.dump' -mtime +30 -delete
```

### SQLite（开发/小规模）

```bash
# 一致性快照（无需停机）
sqlite3 data/server_dev.sqlite3 "VACUUM INTO '/backup/server_$(date +%F).sqlite3'"
```

### 授权私钥

```bash
install -m 600 data/license_private.pem /backup/license_private.pem
```

> 私钥与数据库分开存放；私钥丢失 = 所有离线授权在宽限期后失效，只能重新签发。

## 三、服务端恢复（PostgreSQL）

```bash
# 1) 停止应用（避免写入）
systemctl stop codelabel

# 2) 恢复
pg_restore -d codelabel --clean --if-exists /backup/codelabel_2026-10-18.dump

# 3) 核对表数量与管理员账号
psql -d codelabel -c "select count(*) from users;"

# 4) 启动并冒烟
systemctl start codelabel
curl -s https://<domain>/health
```

## 四、客户端备份与恢复（用户侧）

客户端「设置 → 数据」提供：

1. **备份数据库**：选择目标路径导出 `.db` 文件；
2. **完整性校验**：执行 `quick_check` + 统计编码数与活跃范围；
3. **从备份恢复**：安全恢复流程——
   - 恢复前自动创建快照 `<db>.pre_restore_<UTC>`；
   - 源文件三重校验：完整性 / schema 版本 / 版本号；
   - 临时文件 + `os.replace` 原子替换（失败不影响现有数据）；
   - 恢复后自动 `quick_check`，并写 JSONL 审计日志；
   - 恢复成功后全部页面自动刷新。

> 恢复前仍建议先手动备份；自动快照是兜底而非替代。

## 五、灾难恢复演练（每季度）

1. 从备份恢复到一个隔离环境；
2. 执行 `alembic upgrade head` 确认迁移链路完好；
3. 用测试账号跑通：注册 → 验证 → 试用 → 激活授权 → 联网验证；
4. 记录 RTO/RWR 并更新本手册。

## 六、备份保留策略

| 类型 | 频率 | 保留 |
| --- | --- | --- |
| 数据库全量 | 每日 | 30 天 |
| 数据库全量 | 每月 | 12 个月 |
| 授权私钥 | 变更时 | 永久（多副本） |
| 客户端用户库 | 用户自助 | 用户自定 |
