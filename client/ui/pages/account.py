"""账户与授权页：服务端地址、注册/登录、邮箱验证、试用、授权激活与状态。"""

from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QLineEdit, QMessageBox, QPushButton,
    QTabWidget, QVBoxLayout, QWidget,
)

from ..widgets import (
    Card, MutedLabel, PrimaryButton, RunLater, error, info, notify_main, warn,
)
from ..api_client import ApiClient, ApiError, device_fingerprint


class AccountPage(QWidget):
    def __init__(self, ctx, main_window, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.main = main_window

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # ---- 服务端地址 ----
        server_card = Card("服务端")
        self.edit_server = QLineEdit(self.ctx.session.server_url)
        self.edit_server.setPlaceholderText("http://127.0.0.1:8000")
        server_card.body.addWidget(MutedLabel("服务端地址（可指向自建部署）"))
        server_card.body.addWidget(self.edit_server)
        btn_save_server = QPushButton("保存地址")
        btn_save_server.clicked.connect(self.save_server_url)
        server_card.body.addWidget(btn_save_server)
        layout.addWidget(server_card)

        tabs = QTabWidget()
        tabs.addTab(self._build_login_tab(), "登录 / 注册")
        tabs.addTab(self._build_license_tab(), "授权")
        layout.addWidget(tabs)
        layout.addStretch(1)
        self.refresh_all()

    # ================== 服务端地址 ==================
    def save_server_url(self):
        url = self.edit_server.text().strip()
        if not url.startswith(("http://", "https://")):
            warn(self, "地址无效", "服务端地址必须以 http:// 或 https:// 开头")
            return
        session = self.ctx.session.load()
        session["server_url"] = url
        self.ctx.session.save(session)
        info(self, "已保存", f"服务端地址已设为 {url}")

    def _client(self):
        return ApiClient(self.ctx.session.server_url)

    # ================== 登录 / 注册 ==================
    def _build_login_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        # 已登录状态
        self.logged_card = Card("当前账户")
        self.login_status = MutedLabel("")
        self.logged_card.body.addWidget(self.login_status)
        row = QHBoxLayout()
        self.btn_logout = QPushButton("退出登录")
        self.btn_logout.clicked.connect(self.do_logout)
        self.btn_verify = QPushButton("验证邮箱")
        self.btn_verify.clicked.connect(self.do_verify_email)
        self.btn_resend = QPushButton("重发验证邮件")
        self.btn_resend.clicked.connect(self.do_resend)
        row.addWidget(self.btn_verify)
        row.addWidget(self.btn_resend)
        row.addWidget(self.btn_logout)
        row.addStretch(1)
        self.logged_card.body.addLayout(row)
        self.subscription_label = MutedLabel("")
        self.logged_card.body.addWidget(self.subscription_label)
        layout.addWidget(self.logged_card)

        # 登录
        login_card = Card("登录")
        form = QFormLayout()
        self.login_id = QLineEdit()
        self.login_pwd = QLineEdit()
        self.login_pwd.setEchoMode(QLineEdit.Password)
        form.addRow("用户名 / 邮箱", self.login_id)
        form.addRow("密码", self.login_pwd)
        login_card.body.addLayout(form)
        btn_login = PrimaryButton("登录")
        btn_login.clicked.connect(self.do_login)
        login_card.body.addWidget(btn_login)
        layout.addWidget(login_card)

        # 注册
        reg_card = Card("注册")
        form2 = QFormLayout()
        self.reg_email = QLineEdit()
        self.reg_username = QLineEdit()
        self.reg_pwd = QLineEdit()
        self.reg_pwd.setEchoMode(QLineEdit.Password)
        form2.addRow("邮箱", self.reg_email)
        form2.addRow("用户名", self.reg_username)
        form2.addRow("密码", self.reg_pwd)
        reg_card.body.addLayout(form2)
        btn_reg = PrimaryButton("注册并开始 7 天试用")
        btn_reg.clicked.connect(self.do_register)
        reg_card.body.addWidget(btn_reg)
        layout.addWidget(reg_card)

        # 验证
        verify_card = Card("邮箱验证")
        form3 = QFormLayout()
        self.verify_token = QLineEdit()
        self.verify_token.setPlaceholderText("粘贴邮件中的验证令牌")
        form3.addRow("验证令牌", self.verify_token)
        verify_card.body.addLayout(form3)
        btn_verify = QPushButton("提交验证")
        btn_verify.clicked.connect(self.do_verify_email)
        verify_card.body.addWidget(btn_verify)
        layout.addWidget(verify_card)
        layout.addStretch(1)
        return page

    def do_register(self):
        email = self.reg_email.text().strip()
        username = self.reg_username.text().strip()
        password = self.reg_pwd.text()
        if not email or not username or not password:
            warn(self, "信息不完整", "请填写邮箱、用户名与密码")
            return
        client = self._client()

        def _do():
            user = client.register(email, username, password)
            # 注册后自动登录便于后续操作
            try:
                client.login(username, password)
            except ApiError:
                pass
            return user

        def _done(_user):
            info(self, "注册成功",
                 "请查收验证邮件并完成邮箱验证，验证后自动开始 7 天试用")
            self.refresh_all()

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "注册失败", m))

    def do_login(self):
        identifier = self.login_id.text().strip()
        password = self.login_pwd.text()
        if not identifier or not password:
            warn(self, "信息不完整", "请输入用户名/邮箱与密码")
            return
        client = self._client()

        def _do():
            data = client.login(identifier, password)
            session = self.ctx.session.load()
            session["access_token"] = data["access_token"]
            session["user"] = data.get("user", {})
            self.ctx.session.save(session)
            return data

        def _done(_data):
            self.login_pwd.clear()
            self.refresh_all()

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "登录失败", m))

    def do_logout(self):
        token = self.ctx.session.token

        def _do():
            if token:
                try:
                    self._client().logout(token)
                except ApiError:
                    pass
            self.ctx.session.clear()
            return True

        RunLater(self).run(_do, on_done=lambda _r: self.refresh_all(),
                           on_error=lambda m: error(self, "退出失败", m))

    def do_resend(self):
        email = self.reg_email.text().strip()
        if not email:
            user = self.ctx.session.user
            email = user.get("email", "")
        if not email:
            warn(self, "缺少邮箱", "请先填写注册邮箱")
            return

        def _do():
            return self._client().resend_verification(email)

        RunLater(self).run(_do, on_done=lambda _r: info(
            self, "已发送", "验证邮件已重新发送（请查收）"),
            on_error=lambda m: error(self, "发送失败", m))

    def do_verify_email(self):
        token = self.verify_token.text().strip()
        if not token:
            warn(self, "缺少令牌", "请粘贴邮件中的验证令牌")
            return

        def _do():
            return self._client().verify_email(token)

        def _done(data):
            info(self, "验证成功",
                 f"邮箱已验证，试用到期：{data.get('trial_expires_at', '未知')}")
            self.refresh_all()

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "验证失败", m))

    # ================== 授权 ==================
    def _build_license_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        status_card = Card("授权状态")
        self.license_status = MutedLabel("检测中…")
        status_card.body.addWidget(self.license_status)
        self.license_detail = MutedLabel("")
        status_card.body.addWidget(self.license_detail)
        row = QHBoxLayout()
        btn_activate = PrimaryButton("激活 / 刷新授权")
        btn_activate.clicked.connect(self.do_activate)
        btn_validate = QPushButton("联网验证")
        btn_validate.clicked.connect(self.do_validate)
        row.addWidget(btn_activate)
        row.addWidget(btn_validate)
        row.addStretch(1)
        status_card.body.addLayout(row)
        layout.addWidget(status_card)

        plan_card = Card("订阅计划")
        self.plans_label = MutedLabel("加载中…")
        plan_card.body.addWidget(self.plans_label)
        layout.addWidget(plan_card)

        devices_card = Card("设备")
        self.devices_label = MutedLabel("")
        devices_card.body.addWidget(self.devices_label)
        layout.addWidget(devices_card)
        layout.addStretch(1)
        return page

    def do_activate(self):
        token = self.ctx.session.token
        if not token:
            warn(self, "未登录", "请先登录后再激活授权")
            return
        client = self._client()
        fingerprint = device_fingerprint()

        def _do():
            pk = client.license_public_key()
            result = client.issue_license(token, fingerprint)
            from ..licensing.client_license import CachedCredential
            validated_at = result.get("server_time") or ""
            cached = CachedCredential(
                credential=result.get("credential", {}),
                public_key_b64=pk.get("public_key_b64", ""),
                last_validated_at=validated_at,
                revoked=False,
            )
            self.ctx.license_store.save(cached)
            return result

        def _done(_result):
            info(self, "授权已激活", "本设备已获得授权凭证（可离线宽限使用）")
            self.refresh_all()
            notify_main(self, "notify_license_changed")

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "激活失败", m))

    def do_validate(self):
        token = self.ctx.session.token
        if not token:
            warn(self, "未登录", "请先登录后再联网验证")
            return
        cached = self.ctx.license_store.load()
        if not cached.credential:
            warn(self, "未激活", "请先点击「激活 / 刷新授权」")
            return
        client = self._client()

        def _do():
            result = client.validate_license(token, cached.credential)
            if result.get("valid"):
                cached.revoked = False
                if result.get("server_time"):
                    cached.last_validated_at = result["server_time"]
                self.ctx.license_store.save(cached)
            elif result.get("revoked"):
                cached.revoked = True
                self.ctx.license_store.save(cached)
            return result

        def _done(result):
            if result.get("valid"):
                info(self, "验证通过", "授权有效")
            else:
                warn(self, "验证未通过", str(result.get("reason", "未知原因")))
            self.refresh_all()

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "验证失败", m))

    # ================== 刷新 ==================
    def refresh_all(self):
        token = self.ctx.session.token
        user = self.ctx.session.user
        if token and user:
            self.login_status.setText(
                f"已登录：{user.get('username', '')}（{user.get('email', '')}）")
            self.btn_logout.setEnabled(True)
        else:
            self.login_status.setText("未登录")
            self.btn_logout.setEnabled(False)

        # 订阅状态
        if token:
            def _load_sub():
                try:
                    return self._client().my_subscription(token)
                except ApiError as exc:
                    return {"error": str(exc)}

            def _done_sub(data):
                if "error" in data:
                    self.subscription_label.setText(f"订阅状态获取失败：{data['error']}")
                else:
                    sub = data.get("subscription") or {}
                    trial = data.get("trial") or {}
                    parts = [f"状态：{data.get('status', '未知')}"]
                    if sub.get("plan_code"):
                        parts.append(f"计划：{sub.get('plan_name') or sub['plan_code']}")
                    if sub.get("expires_at"):
                        parts.append(f"到期：{sub['expires_at']}")
                    elif trial.get("expires_at"):
                        parts.append(
                            f"试用剩余 {trial.get('days_remaining', '?')} 天"
                            f"（{trial['expires_at']} 到期）")
                    self.subscription_label.setText("；".join(parts))

            RunLater(self).run(_load_sub, on_done=_done_sub)
        else:
            self.subscription_label.setText("登录后可查看订阅状态")

        # 计划列表
        def _load_plans():
            try:
                return self._client().plans()
            except ApiError as exc:
                return {"error": str(exc)}

        def _done_plans(data):
            if "error" in data:
                self.plans_label.setText(f"计划获取失败：{data['error']}")
            else:
                lines = []
                for p in data.get("plans", []):
                    lines.append(
                        f"• {p['plan_name']}：¥{p['price']}/{p['billing_period']}")
                self.plans_label.setText("\n".join(lines) or "暂无计划")

        RunLater(self).run(_load_plans, on_done=_done_plans)

        # 设备列表
        if token:
            def _load_devices():
                try:
                    return self._client().my_devices(token)
                except ApiError as exc:
                    return {"error": str(exc)}

            def _done_devices(data):
                if "error" in data:
                    self.devices_label.setText(f"设备获取失败：{data['error']}")
                else:
                    devices = data.get("devices", [])
                    lines = [f"已绑定 {len(devices)} 台设备（上限 {data.get('max_devices', '-')}）："]
                    for d in devices:
                        lines.append(f"• #{d['id']} {d.get('device_name') or d.get('device_fingerprint_hash', '')[:8]}"
                                     f"（{d.get('status', '')}）")
                    self.devices_label.setText("\n".join(lines))

            RunLater(self).run(_load_devices, on_done=_done_devices)
        else:
            self.devices_label.setText("登录后可查看设备")

        self.refresh_license_status()

    def refresh_license_status(self):
        from ..licensing.client_license import offline_status
        cached = self.ctx.license_store.load()
        status = offline_status(cached)
        reason_text = {
            "ok": "授权有效",
            "grace": "离线宽限期内可用",
            "no_credential": "尚未激活授权",
            "invalid_signature": "凭证验签失败",
            "revoked": "授权已被撤销",
            "expired": "授权已到期",
            "grace_expired": "离线宽限已用尽，请联网验证",
            "not_yet_valid": "凭证尚未生效",
        }.get(status["reason"], status["reason"])
        mark = "✅" if status["entitled"] else "⛔"
        self.license_status.setText(f"{mark} {reason_text}")
        detail = []
        if status.get("expires_at"):
            detail.append(f"到期：{status['expires_at']}")
        if status.get("grace_deadline"):
            detail.append(f"宽限至：{status['grace_deadline']}")
        self.license_detail.setText("　".join(detail) or "联网激活后可离线使用")

    def refresh(self):
        self.refresh_all()
