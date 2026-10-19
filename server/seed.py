"""服务端种子数据：订阅计划 + 初始管理员。

用法：
    python -m server.seed            # 幂等：重复执行不会重复创建

计划来自服务端配置（server/app/config.py settings.plans），
客户端不得硬编码价格；管理员账号通过环境变量注入：

    CLS_ADMIN_USERNAME / CLS_ADMIN_EMAIL / CLS_ADMIN_PASSWORD

生产环境（CLS_ENV=production）三者必填；开发环境缺省使用
admin / admin@codelabel.local / ChangeMe!123 并打印警告。
"""

import sys

from sqlalchemy import select

from .app.config import settings
from .app.database import SessionLocal, create_all, missing_tables
from .app.models import Plan, User
from .app.security import hash_password


def seed_plans(db) -> int:
    """按配置同步计划（已存在则更新价格/名称，不删历史计划）。"""
    created = 0
    for spec in settings.plans:
        plan = db.scalar(select(Plan).where(Plan.plan_code == spec["plan_code"]))
        if plan is None:
            db.add(Plan(**spec))
            created += 1
        else:
            plan.plan_name = spec["plan_name"]
            plan.billing_period = spec["billing_period"]
            plan.price = spec["price"]
            plan.currency = spec["currency"]
            plan.enabled = spec["enabled"]
    # 试用占位计划（试用订阅记录引用，价格 0；enabled=False 不对外售卖）
    trial = db.scalar(select(Plan).where(Plan.plan_code == "trial"))
    if trial is None:
        db.add(Plan(plan_code="trial", plan_name="免费试用",
                    billing_period="trial", price=0, currency="CNY",
                    enabled=False))
        created += 1
    db.commit()
    return created


def seed_admin(db) -> tuple[bool, str]:
    """创建初始管理员。返回 (created, username)。"""
    username = settings_env("CLS_ADMIN_USERNAME", "")
    email = settings_env("CLS_ADMIN_EMAIL", "")
    password = settings_env("CLS_ADMIN_PASSWORD", "")
    if not (username and email and password):
        if settings.is_production:
            raise SystemExit(
                "生产环境必须设置 CLS_ADMIN_USERNAME / CLS_ADMIN_EMAIL / "
                "CLS_ADMIN_PASSWORD 后再执行 seed"
            )
        username, email, password = (
            "admin", "admin@codelabel.local", "ChangeMe!123")
        print("[seed][警告] 未设置管理员环境变量，使用开发默认账号 "
              "admin / admin@codelabel.local，生产环境必须修改！")

    existing = db.scalar(select(User).where(
        (User.username == username) | (User.email == email.lower())))
    if existing:
        if not existing.is_admin:
            existing.is_admin = True
            db.commit()
            print(f"[seed] 已存在用户 {existing.username}，提升为管理员")
        else:
            print(f"[seed] 管理员 {existing.username} 已存在，跳过")
        return False, existing.username

    admin = User(
        username=username,
        email=email.strip().lower(),
        password_hash=hash_password(password),
        account_status="active",
        email_verified_at=None,
        is_admin=True,
    )
    db.add(admin)
    db.commit()
    return True, username


def settings_env(name: str, default: str) -> str:
    import os
    return os.environ.get(name, default)


def main() -> int:
    print(f"[seed] 数据库: {settings.database_url}")
    print(f"[seed] 环境: {settings.env}")
    if settings.is_production:
        # 生产不允许用 create_all 绕过迁移：表结构必须由 alembic 管理，
        # 否则后续 ALTER 迁移会在已存在的表上失败或产生分歧。
        missing = missing_tables()
        if missing:
            print("[seed] 拒绝执行：生产库缺少表 " + "、".join(sorted(missing)))
            print("[seed] 请先执行：alembic upgrade head")
            return 2
    else:
        create_all()
    with SessionLocal() as db:
        created_plans = seed_plans(db)
        created_admin, admin_name = seed_admin(db)
    print(f"[seed] 计划同步完成（新建 {created_plans} 个）")
    print(f"[seed] 管理员: {admin_name}"
          f"（{'新建' if created_admin else '已存在'}）")
    print("[seed] 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
