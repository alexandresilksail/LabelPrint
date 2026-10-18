"""邮件服务适配器。

- console：开发/测试用，邮件内容写入日志（不得用于生产冒充满发送）；
- smtp：真实 SMTP（SSL），凭据全部来自环境变量。
"""

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path

from .config import settings

logger = logging.getLogger("codelabel.mail")


class MailError(Exception):
    pass


def send_mail(to_address: str, subject: str, body_text: str,
              body_html: str | None = None) -> bool:
    """发送邮件。返回是否成功；失败不抛出（由调用方决定重试）。"""
    if settings.mail_backend == "smtp":
        return _send_smtp(to_address, subject, body_text, body_html)
    return _send_console(to_address, subject, body_text)


def _send_console(to_address: str, subject: str, body: str) -> bool:
    logger.info(
        "[MAIL:console] to=%s subject=%s body=%s",
        to_address, subject, body.replace("\n", " | "),
    )
    return True


def _send_smtp(to_address: str, subject: str, body_text: str,
               body_html: str | None) -> bool:
    if not settings.smtp_host or not settings.smtp_user:
        logger.error("[MAIL:smtp] SMTP 未配置（CLS_SMTP_HOST/USER）")
        return False
    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    try:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                              timeout=20) as smtp:
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("[MAIL:smtp] 发送失败: %s", exc)
        return False


# ---------- 邮件模板 ----------
def verification_email(token: str) -> tuple[str, str]:
    link = f"{settings.app_base_url}/verify-email?token={token}"
    subject = "CodeLabel Studio — 邮箱验证"
    body = (
        "感谢注册 CodeLabel Studio。\n\n"
        f"请在 24 小时内点击以下链接完成邮箱验证：\n{link}\n\n"
        "验证成功后即可开始 7 天免费试用。\n"
        "如非本人操作，请忽略本邮件。"
    )
    return subject, body


def reset_email(token: str) -> tuple[str, str]:
    link = f"{settings.app_base_url}/reset-password?token={token}"
    subject = "CodeLabel Studio — 密码重置"
    body = (
        "收到密码重置请求。\n\n"
        f"请在 2 小时内点击以下链接重置密码：\n{link}\n\n"
        "如非本人操作，请忽略本邮件，您的密码不会改变。"
    )
    return subject, body


def payment_notification(order_no: str, ok: bool) -> tuple[str, str]:
    subject = "CodeLabel Studio — 付款审核结果"
    if ok:
        body = (f"订单 {order_no} 的付款已确认，订阅已开通。\n"
                "请重新登录客户端查看授权状态。")
    else:
        body = (f"订单 {order_no} 的付款未通过审核。\n"
                "请联系管理员并提供正确的付款凭证。")
    return subject, body
