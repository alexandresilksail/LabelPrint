# server/ — 在线用户及授权后台

FastAPI 服务端，阶段 4 起实现。目录规划：

```
server/
├── app/          # FastAPI 应用装配、启动入口
├── api/          # 路由：auth / account / license / plans / orders / payments / admin
├── models/       # SQLAlchemy 模型（users/trials/plans/subscriptions/orders/devices/sessions/audit_logs）
├── schemas/      # Pydantic 请求/响应模型
├── services/     # 业务逻辑：试用、订阅、授权、邮件、统计
├── security/     # Argon2id 密码哈希、JWT、授权签名私钥
├── payments/     # 可替换支付适配器（开发阶段：manual 人工确认）
└── migrations/   # Alembic 数据库迁移
```

数据库表设计、API 清单、试用规则见 `docs/phase1_audit_report.md` 第 6 章
与需求文档第十四、十五章。开发环境用 SQLite，生产用 PostgreSQL（见根目录 docker-compose.yml）。
