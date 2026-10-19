"""outbox and pending-order uniqueness

审计修复对应的结构变更：

1. `outbox_messages`：事务性发件箱，付款结果通知在业务事务内落库，
   提交后投递，投递失败可重试且不影响订单状态；
2. `uq_order_user_pending`：同一用户只允许一个待付款订单的部分唯一索引，
   在数据库层拦住并发重复下单（SQLite / PostgreSQL 均支持 partial index）。

Revision ID: 9f1c2d7a4b55
Revises: 8a32c5922b78
Create Date: 2026-10-18 19:05:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9f1c2d7a4b55'
down_revision: Union[str, None] = '8a32c5922b78'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'outbox_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dedupe_key', sa.String(length=128), nullable=False),
        sa.Column('topic', sa.String(length=32), nullable=False),
        sa.Column('recipient', sa.String(length=320), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('body_text', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(), nullable=False),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','sent','failed','dead')",
            name='ck_outbox_status'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dedupe_key', name='uq_outbox_dedupe'),
    )

    # 部分唯一索引：pending 订单每用户至多一条
    op.create_index(
        'uq_order_user_pending', 'orders', ['user_id'], unique=True,
        sqlite_where=sa.text("order_status = 'pending'"),
        postgresql_where=sa.text("order_status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index('uq_order_user_pending', table_name='orders')
    op.drop_table('outbox_messages')
