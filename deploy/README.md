# deploy/ — 部署配置

- `docker-compose.yml`（仓库根目录）：PostgreSQL + API + 管理后台 Nginx
- `nginx.admin.conf`：管理后台静态托管 + /api/ 反向代理
- 生产环境必须启用 HTTPS（证书与域名配置属人工配置项，见审计报告第 8 章）
