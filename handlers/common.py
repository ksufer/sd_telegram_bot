import logging

from telegram.error import BadRequest

from config import maybe_reload_workflows

logger = logging.getLogger(__name__)


def get_user_id(update) -> int:
    return update.effective_user.id


def refresh_workflows() -> None:
    """热重载 workflows 配置（管理面板改动后无需重启 Bot），失败仅告警。"""
    try:
        maybe_reload_workflows()
    except Exception:
        logger.warning("workflows 热重载失败", exc_info=True)


async def safe_answer(query, text: str | None = None, show_alert: bool = False):
    try:
        await query.answer(text, show_alert=show_alert)
    except Exception:
        pass


async def reply_menu(query, text: str, markup):
    try:
        await query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except BadRequest as e:
        # 内容未变化（如点选已选中项）不算错误，直接忽略，避免重复发菜单
        if "not modified" in str(e).lower():
            return
        await safe_answer(query)
        await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML")
