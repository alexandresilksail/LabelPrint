# server/ — 在线用户及授权后台

FastAPI 服务端，阶段 4 起实现。目录规划：

```
server/
├── main.py            # 应用入口：create_app() / app（uvicorn server.main:app）
├── seed.py            # 种子数据：订阅计划 + 初始管理员（幂等）
├── alembic.ini        # Alembic 配置（连接串来自 CLS_DATABASE_URL）
├── app/               # 配置 / 数据库 / 安全原语 / 模型 / 授权签名 / 邮件
├── api/               # 路由：auth / account / license / orders / admin
├── services/          # 业务逻辑：auth / trial / subscription / license / orders
├── models/ security/  # 兼容垫片（转发到 app.models / app.security）
├── payments/          # 人工付款确认 + webhook 适配器（未配置签名一律拒绝）
└── migrations/        # Alembic 迁移（env.py / versions/）
```

## 快速开始

```bash
# 1) 安装依赖（在项目根目录）
pip install -r requirements.txt

# 2) 配置环境变量（开发环境可全部缺省）
export CLS_ENV=development
export CLS_DATABASE_URL=sqlite:///data/server_dev.sqlite3
# 生产环境必填：
# export CLS_SECRET_KEY=<随机长字符串>
# export CLS_ADMIN_USERNAME / CLS_ADMIN_EMAIL / CLS_ADMIN_PASSWORD
# export CLS_LICENSE_PRIVATE_KEY=<64 hex 字符 Ed25519 私钥>
# export CLS_MAIL_BACKEND=smtp + CLS_SMTP_*

# 3) 数据库迁移 + 种子数据
cd server
python -m alembic -c alembic.ini upgrade head
python -m server.seed          # 或在 server/ 目录: python -m seed

# 4) 启动
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

## 测试

```bash
# 在项目根目录（tests_server/ 含 API 集成 + 安全单测 + 迁移一致性）
python -m unittest discover -s tests_server -t .
```

## 安全设计要点

- 密码 Argon2id；会话/验证/重置令牌库内仅存 SHA-256 哈希；
- 注册/登录/验证/重置全限流（按 IP，进程内固定窗口，生产可换 Redis）；
- 登录失败统一文案、验证/重置返回统一成功（防账户枚举）；
- 授权凭证 Ed25519 签名，私钥仅存服务端；validate 服务端验签 + 核对状态；
- 人工付款确认必须管理员 + 原因；webhook 未配置签名密钥一律拒绝；
- 全部管理操作写 audit_logs（操作人/目标/原因/摘要）。
