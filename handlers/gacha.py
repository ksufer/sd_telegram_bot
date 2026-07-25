"""灵感抽卡 — 随机词组组合，打开创作思路。

入口：主菜单「🎰 灵感抽卡」按钮 / /gacha 命令。
纯随机抽词（services/gacha.py），不做 LLM 加工。
"""

import copy
import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler

from config import COMFY_WORKFLOWS, COMFY_DEFAULT_WORKFLOW
from handlers import auth_callback, _user_auth_filter
from handlers.common import safe_answer, reply_menu, get_user_id
from handlers.settings import _ensure_settings, _save_settings
from handlers.generation import (
    _check_and_charge_credit,
    _create_status_message,
    _enqueue_and_notify,
)
from services import credits, gacha as gacha_service
from services.queue import GenerationTask

logger = logging.getLogger(__name__)

MODE_LABEL = {"sfw": "🟢 SFW", "nsfw": "🔞 NSFW"}


# ═══ 工具函数 ═══

def _get_mode(context, user_id: int) -> str:
    settings = _ensure_settings(context, user_id)
    return settings.get("gacha_mode", "sfw")


def _draw_card(context, user_id: int) -> tuple[dict, str]:
    """按用户当前模式抽一张新卡并暂存到 user_data。"""
    mode = _get_mode(context, user_id)
    pool = gacha_service.load_pool()
    card = gacha_service.draw(pool, mode)
    context.user_data["_gacha_card"] = card
    return card, mode


def _render_card(card: dict, mode: str) -> str:
    lines = [f"<b>🎰 灵感抽卡</b>（{MODE_LABEL.get(mode, mode)}）\n"]
    for w in card.values():
        lines.append(f"• {w['label']}：{html.escape(w['zh'])}")
    prompt_zh = gacha_service.build_prompt(card)
    prompt_en = gacha_service.build_prompt(card, lang="en")
    lines.append(f"\n📋 中文 Prompt（点击复制）：\n<code>{html.escape(prompt_zh)}</code>")
    lines.append(f"\n📋 English Prompt：\n<code>{html.escape(prompt_en)}</code>")
    return "\n".join(lines)


def _card_keyboard(mode: str) -> InlineKeyboardMarkup:
    toggle = "🟢 切换到 SFW" if mode == "nsfw" else "🔞 切换到 NSFW"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 再抽一次", callback_data="gacha:draw"),
            InlineKeyboardButton("🎯 重抽单项", callback_data="gacha:reroll_menu"),
        ],
        [InlineKeyboardButton(toggle, callback_data="gacha:mode")],
        [InlineKeyboardButton("🚀 用这组词生成", callback_data="gacha:gen")],
        [InlineKeyboardButton("关闭", callback_data="close_menu")],
    ])


def _reroll_keyboard(card: dict) -> InlineKeyboardMarkup:
    """重抽单项菜单：本次已抽中的维度，一行三个。"""
    rows = []
    row = []
    for key, w in card.items():
        row.append(InlineKeyboardButton(w["label"], callback_data=f"gacha:reroll:{key}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 返回卡片", callback_data="gacha:back")])
    return InlineKeyboardMarkup(rows)


# ═══ 入口 ═══

async def gacha_command(update, context):
    """/gacha 命令入口。"""
    message = update.effective_message
    if message is None:
        return
    user_id = get_user_id(update)
    try:
        card, mode = _draw_card(context, user_id)
    except Exception as e:
        logger.warning("抽卡词库不可用", exc_info=True)
        await message.reply_text(f"抽卡词库不可用：{e}")
        return
    await message.reply_text(
        _render_card(card, mode), reply_markup=_card_keyboard(mode), parse_mode="HTML",
    )


async def gacha_menu(update, context):
    """主菜单按钮入口。"""
    query = update.callback_query
    user_id = get_user_id(update)
    try:
        card, mode = _draw_card(context, user_id)
    except Exception as e:
        logger.warning("抽卡词库不可用", exc_info=True)
        await safe_answer(query, f"抽卡词库不可用：{e}", show_alert=True)
        return
    await safe_answer(query)
    await reply_menu(query, _render_card(card, mode), _card_keyboard(mode))


# ═══ 卡片操作 ═══

async def gacha_draw(update, context):
    """整卡重抽。"""
    query = update.callback_query
    user_id = get_user_id(update)
    try:
        card, mode = _draw_card(context, user_id)
    except Exception:
        logger.warning("抽卡词库不可用", exc_info=True)
        await safe_answer(query, "抽卡词库不可用", show_alert=True)
        return
    await safe_answer(query, "🎲 已重抽")
    await reply_menu(query, _render_card(card, mode), _card_keyboard(mode))


async def gacha_mode(update, context):
    """SFW/NSFW 模式切换（持久化）并重抽。"""
    query = update.callback_query
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["gacha_mode"] = "nsfw" if settings.get("gacha_mode", "sfw") == "sfw" else "sfw"
    _save_settings(context, user_id)
    try:
        card, mode = _draw_card(context, user_id)
    except Exception:
        logger.warning("抽卡词库不可用", exc_info=True)
        await safe_answer(query, "抽卡词库不可用", show_alert=True)
        return
    await safe_answer(query, f"已切换到 {MODE_LABEL[mode]}")
    await reply_menu(query, _render_card(card, mode), _card_keyboard(mode))


async def gacha_reroll_menu(update, context):
    """重抽单项选择菜单。"""
    query = update.callback_query
    card = context.user_data.get("_gacha_card") if context.user_data else None
    if not card:
        await safe_answer(query, "请先抽卡", show_alert=True)
        return
    await safe_answer(query)
    await reply_menu(query, "<b>🎯 重抽单项</b>\n\n选择要重抽的角度：", _reroll_keyboard(card))


async def gacha_reroll(update, context):
    """重抽单个维度，返回卡片。"""
    query = update.callback_query
    dim_key = query.data.split(":", 2)[2]
    user_id = get_user_id(update)
    card = context.user_data.get("_gacha_card") if context.user_data else None
    if not card or dim_key not in card:
        await safe_answer(query, "卡片已变化，请重新抽卡", show_alert=True)
        return
    mode = _get_mode(context, user_id)
    try:
        pool = gacha_service.load_pool()
    except Exception:
        logger.warning("抽卡词库不可用", exc_info=True)
        await safe_answer(query, "抽卡词库不可用", show_alert=True)
        return
    card = gacha_service.reroll(pool, mode, card, dim_key)
    context.user_data["_gacha_card"] = card
    await safe_answer(query, f"已重抽「{card[dim_key]['label']}」")
    await reply_menu(query, _render_card(card, mode), _card_keyboard(mode))


async def gacha_back(update, context):
    """从重抽菜单返回卡片。"""
    query = update.callback_query
    user_id = get_user_id(update)
    card = context.user_data.get("_gacha_card") if context.user_data else None
    if not card:
        await safe_answer(query, "请先抽卡", show_alert=True)
        return
    await safe_answer(query)
    mode = _get_mode(context, user_id)
    await reply_menu(query, _render_card(card, mode), _card_keyboard(mode))


# ═══ 直接生成 ═══

async def gacha_gen(update, context):
    """用抽中的组合 prompt 入队生成（消耗额度）。"""
    query = update.callback_query
    message = query.message
    card = context.user_data.get("_gacha_card") if context.user_data else None
    if message is None:
        await safe_answer(query)
        return
    if not card:
        await safe_answer(query, "请先抽卡", show_alert=True)
        return

    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    prompt = gacha_service.build_prompt(card)

    ok, credit_charged, err = await _check_and_charge_credit(user_id)
    if not ok:
        await safe_answer(query, err, show_alert=True)
        return

    chat = message.chat
    status_id = await _create_status_message(message)

    # ComfyUI 后端 + 图生图工作流时，回退到默认文生图工作流
    task_settings = copy.deepcopy(settings)
    fallback = False
    if task_settings.get("backend") == "comfyui":
        wf_key = task_settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
        wf_config = COMFY_WORKFLOWS.get(wf_key, {})
        if wf_config.get("is_img2img"):
            task_settings["comfy_workflow"] = COMFY_DEFAULT_WORKFLOW
            default_wf = COMFY_WORKFLOWS.get(COMFY_DEFAULT_WORKFLOW, {})
            if default_wf.get("default_model"):
                task_settings["comfy_model"] = default_wf["default_model"]
            fallback = True

    task = GenerationTask(
        user_id=user_id,
        chat_id=chat.id,
        prompt=prompt,
        settings=task_settings,
        status_message_id=status_id,
        original_message_id=message.message_id,
        reply_to_message_id=message.message_id if chat.type in ("group", "supergroup") else None,
        credit_charged=credit_charged,
    )

    try:
        queue = context.bot_data["queue"]
        await _enqueue_and_notify(task, queue, context, chat.id, status_id)
    except Exception:
        logger.error("用户 %s 抽卡生成入队失败", user_id, exc_info=True)
        if credit_charged:
            await credits.refund_one(user_id)
        await safe_answer(query, "任务提交失败，请稍后重试。", show_alert=True)
        return

    note = "🚀 已提交生成"
    if fallback:
        note += "（当前为图生图工作流，已回退到默认文生图）"
    await safe_answer(query, note)


# ═══ Handler 注册 ═══

def get_handlers() -> list:
    return [
        CommandHandler("gacha", gacha_command, filters=_user_auth_filter()),
        CallbackQueryHandler(auth_callback(gacha_menu), pattern=r"^gacha:menu$"),
        CallbackQueryHandler(auth_callback(gacha_draw), pattern=r"^gacha:draw$"),
        CallbackQueryHandler(auth_callback(gacha_mode), pattern=r"^gacha:mode$"),
        CallbackQueryHandler(auth_callback(gacha_reroll_menu), pattern=r"^gacha:reroll_menu$"),
        CallbackQueryHandler(auth_callback(gacha_reroll), pattern=r"^gacha:reroll:[a-z0-9_]+$"),
        CallbackQueryHandler(auth_callback(gacha_back), pattern=r"^gacha:back$"),
        CallbackQueryHandler(auth_callback(gacha_gen), pattern=r"^gacha:gen$"),
    ]
