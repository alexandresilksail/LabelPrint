"""支付适配器（兼容垫片）。

当前版本实现 manual 人工付款确认流程（server/services/orders.py：
create_order / submit_payment_proof / confirm_payment / process_webhook）。
正式接入真实支付服务商前，界面必须明确标记"人工付款"。
webhook 入口在签名密钥未配置时一律拒绝，防伪造回调。
"""

from .services.orders import process_webhook  # noqa: F401
