"""Handler 共享工具：权限判断、filter 辅助、回调包装。"""

import logging
from functools import wraps

from telegram.ext import filters

from config import ALLOWED_USER_IDS, ALLOWED_CHAT_IDS, ADMIN_USER_ID

logger = logging.getLogger(__name__)


def is_authorized(user_id: int, chat_id: int, chat_type: str) -> bool:
    """统一的权限判断，消息、命令、回调均使用此函数。

    规则：
    1. ADMIN_USER_ID 自动通过用户白名单，无需出现在 ALLOWED_USER_IDS 中
    2. ALLOWED_USER_IDS 非空时，普通用户必须在白名单中
    3. 群白名单仅用于 group/supergroup，private 不检查
    4. ALLOWED_CHAT_IDS 非空时，chat_id 必须在白名单中
    5. 管理员同样受群白名单限制
    """
    # 用户白名单
    if ADMIN_USER_ID is not None and user_id == ADMIN_USER_ID:
        pass
    elif ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        return False

    # 群白名单（仅 group/supergroup）
    if chat_type in ("group", "supergroup"):
        if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
            return False

    return True


def _user_auth_filter():
    """给 MessageHandler / CommandHandler 用的权限 filter。
    空列表返回 filters.ALL，避免 None 参与 & 组合报错。
    """
    if ALLOWED_USER_IDS:
        return filters.User(user_id=ALLOWED_USER_IDS)
    return filters.ALL


def auth_callback(func):
    """CallbackQueryHandler 回调的权限装饰器。"""

    @wraps(func)
    async def wrapped(update, context, *args, **kwargs):
        user = update.effective_user
        chat = update.effective_chat
        user_id = user.id if user else 0
        chat_id = chat.id if chat else user_id
        chat_type = chat.type if chat else "private"

        if not is_authorized(user_id, chat_id, chat_type):
            try:
                await update.callback_query.answer("⛔ 无使用权限", show_alert=True)
            except Exception:
                pass
            return
        return await func(update, context, *args, **kwargs)

    return wrapped
