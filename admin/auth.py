"""Admin 认证：密码登录 + HMAC 签名 cookie + CSRF 头校验（零额外依赖）。

- cookie 载荷: base64url(exp:csrf:signature)，httpOnly + SameSite=Lax，7 天有效
- 变更类请求要求 X-CSRF-Token 头与 cookie 内 csrf 一致（防止跨站表单提交）
- 密钥读 ADMIN_SECRET_KEY，回退旧名 FLASK_SECRET_KEY（现有 .env 无缝迁移）
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

COOKIE_NAME = "sd_admin_session"
COOKIE_MAX_AGE = 7 * 24 * 3600
CSRF_HEADER = "x-csrf-token"

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SECRET_KEY = os.getenv("ADMIN_SECRET_KEY") or os.getenv("FLASK_SECRET_KEY", "")


def check_config() -> None:
    """启动时检查必需配置，缺失即拒绝启动。"""
    if not ADMIN_PASSWORD:
        raise RuntimeError("ADMIN_PASSWORD 未配置（.env），Admin 拒绝启动")
    if not SECRET_KEY:
        raise RuntimeError("ADMIN_SECRET_KEY 未配置（.env），Admin 拒绝启动")


def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode(), payload.encode(),
                    hashlib.sha256).hexdigest()


def _b64e(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _b64d(raw: str) -> str:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()


def create_session() -> tuple[str, str]:
    """返回 (cookie_value, csrf_token)。"""
    exp = int(time.time()) + COOKIE_MAX_AGE
    csrf = secrets.token_hex(16)
    payload = f"{exp}:{csrf}"
    return f"{_b64e(payload)}.{_sign(payload)}", csrf


def verify_session(cookie_value: str | None) -> str | None:
    """校验 cookie，有效则返回 csrf_token，否则 None。"""
    if not cookie_value or "." not in cookie_value:
        return None
    b64_payload, sig = cookie_value.rsplit(".", 1)
    try:
        payload = _b64d(b64_payload)
    except Exception:
        return None
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    exp_str, _, csrf = payload.partition(":")
    if not csrf or int(exp_str) < time.time():
        return None
    return csrf


def require_auth(request: Request) -> str:
    """依赖：校验登录态，返回 csrf_token 供后续比较。"""
    csrf = verify_session(request.cookies.get(COOKIE_NAME))
    if not csrf:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return csrf


def require_csrf(request: Request) -> None:
    """依赖：登录态 + CSRF 头校验（用于变更类请求）。"""
    csrf = require_auth(request)
    header = request.headers.get(CSRF_HEADER, "")
    if not header or not hmac.compare_digest(csrf, header):
        raise HTTPException(status_code=403, detail="CSRF 校验失败")
