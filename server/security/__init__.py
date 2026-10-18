"""安全模块（兼容垫片）。

真实实现位于 server/app/security.py（密码哈希 / 令牌 / 限流）与
server/app/licensing.py（Ed25519 授权签名）。本模块仅做转发。
"""

from .app.security import *  # noqa: F401,F403
from .app.security import (  # noqa: F401
    RateLimiter,
    constant_time_eq,
    expires_at,
    generate_token,
    hash_password,
    hash_token,
    is_expired,
    needs_rehash,
    rate_limiter,
    utc_now,
    verify_password,
)
from .app.licensing import (  # noqa: F401
    canonical_payload,
    issue_credential,
    parse_credential,
    public_key_b64,
    sign_payload,
    verify_payload,
)
