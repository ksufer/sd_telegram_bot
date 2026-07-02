from telegram.error import BadRequest


def get_user_id(update) -> int:
    return update.effective_user.id


async def safe_answer(query, text: str | None = None, show_alert: bool = False):
    try:
        await query.answer(text, show_alert=show_alert)
    except Exception:
        pass


async def reply_menu(query, text: str, markup):
    try:
        await query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except BadRequest:
        await safe_answer(query)
        await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML")
