"""/credit 命令处理。"""

import logging

from telegram.ext import CommandHandler

from config import ADMIN_USER_ID
from services import credits
from handlers import is_authorized, _user_auth_filter

logger = logging.getLogger(__name__)

USAGE = (
    "<b>/credit 用法：</b>\n"
    "  <code>/credit</code> — 查看剩余额度\n"
    "管理员命令：\n"
    "  <code>/credit check &lt;user_id&gt;</code>\n"
    "  <code>/credit add &lt;user_id&gt; &lt;数量&gt;</code>\n"
    "  <code>/credit set &lt;user_id&gt; &lt;总数&gt;</code>\n"
    "  也可以回复用户消息：<code>/credit add 20</code>"
)


def _extract_target_and_arg(update, context) -> tuple[int | None, int | None]:
    """从命令参数或被回复消息中提取目标 user_id 和额外参数。
    返回 (target_user_id, arg_value_or_None)。
    """
    message = update.message
    parts = message.text.strip().split()

    # 回复消息时，目标为被回复用户
    if message.reply_to_message:
        target = message.reply_to_message.from_user.id
        arg = int(parts[2]) if len(parts) >= 3 else None
        return target, arg

    # 直接指定 user_id（parts[1] 是子命令，parts[2] 才是 user_id）
    if len(parts) >= 3:
        try:
            target = int(parts[2])
            arg = int(parts[3]) if len(parts) >= 4 else None
            return target, arg
        except ValueError:
            return None, None

    return None, None


async def handle_credit(update, context):
    message = update.message
    chat = update.effective_chat
    user_id = update.effective_user.id

    if not is_authorized(user_id, chat.id, chat.type):
        return

    parts = message.text.strip().split()
    subcommand = parts[1].lower() if len(parts) >= 2 else None

    # 没有子命令：查看自己额度
    if subcommand is None:
        stats = await credits.get_stats(user_id)
        await message.reply_text(
            f"<b>剩余额度</b>\n"
            f"已用：{stats['used']} / 总计：{stats['total_quota']} | "
            f"剩余：<b>{stats['remaining']}</b>",
            parse_mode="HTML",
        )
        return

    # 管理命令需要管理员权限
    is_admin = ADMIN_USER_ID is not None and user_id == ADMIN_USER_ID
    if not is_admin:
        await message.reply_text("无权限执行该管理命令。")
        return

    if subcommand == "check":
        target, _ = _extract_target_and_arg(update, context)
        if target is None:
            await message.reply_text("请指定用户 ID 或回复用户消息。")
            return
        stats = await credits.get_stats(target)
        await message.reply_text(
            f"用户 <code>{target}</code> 额度：\n"
            f"已用：{stats['used']} / 总计：{stats['total_quota']} | "
            f"剩余：<b>{stats['remaining']}</b>",
            parse_mode="HTML",
        )

    elif subcommand == "add":
        target, amount = _extract_target_and_arg(update, context)
        if target is None or amount is None:
            await message.reply_text(f"用法：<code>/credit add &lt;user_id&gt; &lt;数量&gt;</code>\n或回复用户消息：<code>/credit add 20</code>", parse_mode="HTML")
            return
        if amount <= 0:
            await message.reply_text("数量必须大于 0。")
            return
        await credits.add_quota(target, amount)
        stats = await credits.get_stats(target)
        await message.reply_text(
            f"已给用户 <code>{target}</code> 增加 {amount} 额度。\n"
            f"当前剩余：<b>{stats['remaining']}</b>",
            parse_mode="HTML",
        )

    elif subcommand == "set":
        target, total = _extract_target_and_arg(update, context)
        if target is None or total is None:
            await message.reply_text(f"用法：<code>/credit set &lt;user_id&gt; &lt;总数&gt;</code>\n或回复用户消息：<code>/credit set 100</code>", parse_mode="HTML")
            return
        if total < 0:
            await message.reply_text("总数不能为负。")
            return
        await credits.set_quota(target, total)
        stats = await credits.get_stats(target)
        await message.reply_text(
            f"已设置用户 <code>{target}</code> 总配额为 {total}。\n"
            f"当前剩余：<b>{stats['remaining']}</b>",
            parse_mode="HTML",
        )

    else:
        await message.reply_text(USAGE, parse_mode="HTML")


def get_handlers() -> list:
    return [
        CommandHandler("credit", handle_credit, filters=_user_auth_filter()),
    ]
