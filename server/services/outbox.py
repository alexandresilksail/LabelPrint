"""事务性发件箱（transactional outbox）。

流程：
1. 业务事务内调用 `enqueue()` 写一条 outbox 记录（与订单、订阅、审计日志
   同一个 `commit`，要么一起成功要么一起回滚）；
2. 事务提交后调用 `dispatch_pending()` 真正投递，失败只影响 outbox 状态，
   不会把已提交的业务状态带回去；
3. 投递失败按指数退避重试，超过上限标记 `dead` 保留现场，供运维排查。

幂等：`dedupe_key` 唯一约束保证同一业务事件不会被重复投递，即使
确认接口被重复调用（重复确认本身也被订单状态拦下）。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..app import mailer
from ..app.models import OutboxMessage
from ..app.security import utc_now

logger = logging.getLogger("codelabel.outbox")

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 3600


def enqueue(db: Session, *, dedupe_key: str, topic: str, recipient: str,
            subject: str, body_text: str) -> OutboxMessage:
    """在**当前事务内**追加一条待发消息（不 commit）。"""
    existing = db.scalar(
        select(OutboxMessage).where(OutboxMessage.dedupe_key == dedupe_key))
    if existing is not None:
        return existing

    message = OutboxMessage(
        dedupe_key=dedupe_key,
        topic=topic,
        recipient=recipient,
        subject=subject,
        body_text=body_text,
        status="pending",
        attempts=0,
        next_attempt_at=utc_now(),
    )
    # 用 SAVEPOINT 包裹：并发下唯一约束冲突只会回滚这个保存点，
    # 不会把外层订单事务一起打废。
    savepoint = db.begin_nested()
    try:
        db.add(message)
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        existing = db.scalar(
            select(OutboxMessage).where(OutboxMessage.dedupe_key == dedupe_key))
        if existing is None:      # 理论上不可达，保守起见抛出
            raise
        return existing
    return message


def _backoff(attempts: int) -> timedelta:
    seconds = min(BACKOFF_BASE_SECONDS * (2 ** max(attempts - 1, 0)),
                  BACKOFF_MAX_SECONDS)
    return timedelta(seconds=seconds)


def dispatch_pending(db: Session, *, limit: int = 50,
                     now=None) -> dict[str, int]:
    """投递到期的待发消息。返回统计，便于在运维接口/日志中观察。"""
    moment = now or utc_now()
    rows = list(db.scalars(
        select(OutboxMessage)
        .where(OutboxMessage.status.in_(("pending", "failed")),
               OutboxMessage.next_attempt_at <= moment)
        .order_by(OutboxMessage.id)
        .limit(limit)
    ))
    stats = {"scanned": len(rows), "sent": 0, "retry": 0, "dead": 0}
    for message in rows:
        ok = False
        error = ""
        try:
            ok = mailer.send_mail(message.recipient, message.subject,
                                  message.body_text)
            if not ok:
                error = "邮件后端返回失败"
        except Exception as exc:                      # noqa: BLE001 - 发信失败必须兜底
            error = f"{type(exc).__name__}: {exc}"

        message.attempts += 1
        if ok:
            message.status = "sent"
            message.sent_at = utc_now()
            message.last_error = None
            stats["sent"] += 1
        else:
            message.last_error = error[:500]
            if message.attempts >= MAX_ATTEMPTS:
                message.status = "dead"
                stats["dead"] += 1
                logger.error("outbox 消息投递失败已达上限 id=%s key=%s: %s",
                             message.id, message.dedupe_key, error)
            else:
                message.status = "failed"
                message.next_attempt_at = utc_now() + _backoff(message.attempts)
                stats["retry"] += 1
                logger.warning("outbox 投递失败待重试 id=%s attempts=%s: %s",
                               message.id, message.attempts, error)
        db.commit()
    return stats


def pending_count(db: Session) -> int:
    return len(list(db.scalars(
        select(OutboxMessage.id).where(
            OutboxMessage.status.in_(("pending", "failed"))))))
