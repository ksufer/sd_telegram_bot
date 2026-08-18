"""Ollama Prompt 助手对话 — 主菜单「💬 Prompt 助手」。

与服务器本地 Ollama 大模型多轮对话，生成/优化生图提示词（支持文字或参考图片）。

架构要点：
- 会话存 bot_data["_prompt_chat"][user_id] = {"mode": "t2i"|"krea2"|"h3", "nsfw": bool,
  "history": [{"role", "content"}...]}（handler 可读写，queue worker 读历史并追加回复）。
- 每轮任务 backend="ollama_chat"，走串行队列与 ComfyUI/反推显存互斥；每轮扣 1 额度。
- 本轮 user 消息由本模块在入队前追加（handler 侧串行，顺序有保证）；入队失败回滚；
  worker 失败时按任务携带的 _chat_pending_index 回滚。
- 历史只存文本（单条 ≤ PROMPT_CHAT_MAX_MSG_LEN），图片仅存在于当前轮（_chat_image）。
- 等待标记 _waiting_input="prompt_chat"，由 generation.handle_text/handle_photo 顶部分发。
"""

import copy
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

from handlers import auth_callback
from handlers.common import get_user_id, reply_menu, safe_answer
from handlers.generation import (
    _check_and_charge_credit,
    _clean_caption,
    _clear_firstlast_state,
    _create_status_message,
    _download_tg_photo,
    _enqueue_and_notify,
)
from handlers.settings import _ensure_settings
from handlers.workflow_menu import (
    _build_main_menu,
    _build_workflow_detail,
    _find_workflow,
)
from services import credits
from services.queue import GenerationTask, _truncate_chat_msg

logger = logging.getLogger(__name__)

MODE_LABEL = {"t2i": "🛠 通用 T2I 优化", "krea2": "🎨 Krea 2 设计", "h3": "🎬 H3 视频提示词"}
SCALE_LABEL = {True: "🔞 NSFW", False: "🟢 SFW"}


# ═══ 会话管理 ═══

def _get_session(context, user_id: int) -> dict:
    """获取/创建用户的 Prompt 助手会话（存 bot_data，queue worker 可读写）。"""
    sessions = context.bot_data.setdefault("_prompt_chat", {})
    session = sessions.get(user_id)
    if session is None:
        session = {"mode": "t2i", "nsfw": True, "history": []}
        sessions[user_id] = session
    return session


def _session_turns(session: dict) -> int:
    return sum(1 for m in session.get("history", []) if m.get("role") == "user")


# ═══ 控制面板 ═══

def _build_menu_text(session: dict) -> tuple[str, InlineKeyboardMarkup]:
    mode = session.get("mode", "t2i")
    nsfw = session.get("nsfw", True)
    turns = _session_turns(session)

    text = (
        "<b>💬 Prompt 助手</b>\n\n"
        "与服务器本地 Ollama 大模型多轮对话，生成/优化生图提示词。\n\n"
        f"当前模式：{MODE_LABEL[mode]}"
        f"{' · ' + SCALE_LABEL[nsfw] if mode == 't2i' else ''}\n"
        f"会话轮数：{turns} 轮\n\n"
        "🛠 <b>通用 T2I 优化</b>：把你的想法扩展成一段高质量生图提示词，可连续追问调整\n"
        "🎨 <b>Krea 2 设计</b>：输出【英文提示词】+【中文释义】结构化结果\n"
        "🎬 <b>H3 视频提示词</b>：按 MiniMax H3 官方规范生成文生/图生/首尾帧视频的结构化提示词\n"
        "支持文字描述或参考图片（图片可附带文字要求）\n"
        "每轮消耗 1 额度"
    )

    rows = [
        [
            InlineKeyboardButton(
                f"{'✓ ' if mode == 't2i' else ''}🛠 通用 T2I",
                callback_data="prompt_chat:mode:t2i",
            ),
            InlineKeyboardButton(
                f"{'✓ ' if mode == 'krea2' else ''}🎨 Krea 2",
                callback_data="prompt_chat:mode:krea2",
            ),
            InlineKeyboardButton(
                f"{'✓ ' if mode == 'h3' else ''}🎬 H3 视频",
                callback_data="prompt_chat:mode:h3",
            ),
        ],
    ]
    if mode == "t2i":
        rows.append([InlineKeyboardButton(
            f"尺度：{SCALE_LABEL[nsfw]}（点按切换）",
            callback_data="prompt_chat:scale",
        )])
    rows.append([
        InlineKeyboardButton("💬 开始对话", callback_data="prompt_chat:start"),
        InlineKeyboardButton("🗑 清空会话", callback_data="prompt_chat:clear"),
    ])
    rows.append([InlineKeyboardButton("🔙 返回", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(rows)


async def prompt_chat_menu(update, context):
    """主菜单/生成结果菜单按钮：打开 Prompt 助手控制面板。"""
    query = update.callback_query
    await safe_answer(query)
    user_id = get_user_id(update)
    session = _get_session(context, user_id)
    text, markup = _build_menu_text(session)
    await reply_menu(query, text, markup)


async def prompt_chat_start(update, context):
    """开始对话：进入等待状态（与其他等待状态互斥）。"""
    query = update.callback_query
    await safe_answer(query)

    if context.user_data is None:
        await safe_answer(query, "会话状态不可用，请重新发送 /start。", show_alert=True)
        return

    # 清除 firstlast / pipeline / rev_prompt 等互斥状态（与 rev_prompt_menu 一致）
    _clear_firstlast_state(context.user_data)
    context.user_data.pop("_pipe_collect", None)
    context.user_data.pop("_pipe_edit_step", None)
    context.user_data.pop("_pipe_ready", None)
    context.user_data.pop("_rev_extra", None)
    context.user_data["_waiting_input"] = "prompt_chat"

    user_id = get_user_id(update)
    session = _get_session(context, user_id)
    mode = session.get("mode", "t2i")
    nsfw = session.get("nsfw", True)
    turns = _session_turns(session)

    text = (
        "<b>💬 Prompt 助手</b>\n\n"
        "已进入对话模式，直接发送<b>文字描述</b>或<b>参考图片</b>（可附文字）即可。\n\n"
        f"当前模式：{MODE_LABEL[mode]}"
        f"{' · ' + SCALE_LABEL[nsfw] if mode == 't2i' else ''}\n"
        + ("可继续追问上一轮结果。\n" if turns else "这是新会话。\n")
        + "\n发送 /cancel 可退出对话。"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑 清空会话", callback_data="prompt_chat:clear"),
            InlineKeyboardButton("❌ 退出对话", callback_data="prompt_chat:exit"),
        ],
    ])
    await reply_menu(query, text, keyboard)


async def prompt_chat_mode(update, context):
    """切换模式（t2i / krea2）。"""
    query = update.callback_query
    mode = query.data.split(":", 2)[2]
    user_id = get_user_id(update)
    session = _get_session(context, user_id)
    session["mode"] = mode
    await safe_answer(query, f"已切换为 {MODE_LABEL[mode]}")
    text, markup = _build_menu_text(session)
    await reply_menu(query, text, markup)


async def prompt_chat_scale(update, context):
    """切换内容尺度（NSFW / SFW，仅 t2i 模式生效）。"""
    query = update.callback_query
    user_id = get_user_id(update)
    session = _get_session(context, user_id)
    session["nsfw"] = not session.get("nsfw", True)
    await safe_answer(query, f"尺度已切换为 {SCALE_LABEL[session['nsfw']]}")
    text, markup = _build_menu_text(session)
    await reply_menu(query, text, markup)


async def prompt_chat_clear(update, context):
    """清空会话历史（保留模式/尺度与等待状态）。"""
    query = update.callback_query
    user_id = get_user_id(update)
    session = _get_session(context, user_id)
    if session.get("history"):
        session["history"] = []
        note = "已清空会话历史。"
    else:
        note = "会话历史为空。"
    await safe_answer(query, note, show_alert=True)
    text, markup = _build_menu_text(session)
    await reply_menu(query, text, markup)


async def prompt_chat_clear_quiet(update, context):
    """结果消息上的清空会话：仅清空历史并 toast，不编辑消息（保留生成内容）。"""
    query = update.callback_query
    user_id = get_user_id(update)
    session = _get_session(context, user_id)
    if session.get("history"):
        session["history"] = []
        note = "已清空会话历史。"
    else:
        note = "会话历史为空。"
    await safe_answer(query, note, show_alert=True)


async def prompt_chat_exit(update, context):
    """退出对话：删除会话并清除等待状态；内容消息保留，另发原工作流说明页。"""
    query = update.callback_query
    user_id = get_user_id(update)
    sessions = context.bot_data.get("_prompt_chat")
    if sessions:
        sessions.pop(user_id, None)
    if context.user_data is not None:
        context.user_data["_waiting_input"] = None
        _clear_firstlast_state(context.user_data)
    await safe_answer(query, "已退出 Prompt 助手。")

    # 新发一条进入 Prompt 助手前的工作流说明页（工作流不会在此期间变化）；
    # 工作流不在注册表（如 backend 为 sd）时回退主菜单
    try:
        settings = _ensure_settings(context, user_id)
        workflow_entry = _find_workflow(settings.get("comfy_workflow", ""))
        if workflow_entry is not None:
            text, markup = _build_workflow_detail(workflow_entry, settings)
        else:
            text, markup = _build_main_menu()
        await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        logger.warning("退出 Prompt 助手后发送工作流页失败", exc_info=True)


# ═══ 对话轮提交（由 generation.handle_text/handle_photo 分发调用）═══

async def _submit_turn(update, context, text: str,
                       image_bytes: bytes | None = None):
    """提交一轮对话：追加 user 消息 → 扣额度 → 入队（失败退款并回滚消息）。"""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    user_id = user.id if user else (message.sender_chat.id if message.sender_chat else 0)

    session = _get_session(context, user_id)
    content = _truncate_chat_msg(text.strip() or "请生成提示词。")

    # 额度检查 + 扣减（失败不追加历史）
    ok, credit_charged, err = await _check_and_charge_credit(user_id)
    if not ok:
        await message.reply_text(err)
        return

    # 追加本轮 user 消息到会话历史（handler 侧串行，顺序有保证）
    session["history"].append({"role": "user", "content": content})
    pending_index = len(session["history"]) - 1

    status_id = await _create_status_message(message, "正在与 Prompt 助手对话...")

    # 任务只需携带回滚索引与当前轮图片；历史/模式由 worker 从 bot_data 会话读取
    task_settings = copy.deepcopy(
        context.user_data.get("settings", {})) if context.user_data else {}
    task_settings["backend"] = "ollama_chat"
    task_settings["_chat_pending_index"] = pending_index
    if image_bytes:
        task_settings["_chat_image"] = image_bytes

    task = GenerationTask(
        user_id=user_id,
        chat_id=chat.id,
        prompt="",
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
        logger.error("用户 %s Prompt 助手入队失败", user_id, exc_info=True)
        if credit_charged:
            await credits.refund_one(user_id)
        session["history"].pop(pending_index)
        await message.reply_text("任务提交失败，请稍后重试。")
        return


async def handle_prompt_chat_text(update, context):
    """对话模式收到文字：作为本轮输入（可继续对话，不退出等待状态）。"""
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text(
            "请输入画面描述，或发送 /cancel 取消。")
        return
    await _submit_turn(update, context, text)


async def handle_prompt_chat_photo(update, context):
    """对话模式收到图片：caption 作为本轮文字，无 caption 时自动补默认要求。"""
    message = update.effective_message

    # 下载图片（失败不扣费）
    try:
        image_bytes = (await _download_tg_photo(message.photo[-1])).getvalue()
    except Exception as e:
        logger.warning("Prompt 助手图片下载失败: %s", e)
        await message.reply_text(f"图片下载失败: {e}")
        return

    user_id = get_user_id(update)
    session = _get_session(context, user_id)
    caption = _clean_caption(message, context).strip()
    if not caption:
        mode = session.get("mode", "t2i")
        if mode == "krea2":
            caption = "根据这张参考图片，生成一条适合 Krea 2 的高质量英文生图提示词。"
        elif mode == "h3":
            caption = "根据这张参考图片，生成 MiniMax H3 图生视频（I2VA）结构化提示词。"
        else:
            caption = "根据这张参考图片，生成对应的图像生成提示词。"
    await _submit_turn(update, context, caption, image_bytes)


# ═══ Handler 注册 ═══

def get_handlers() -> list:
    return [
        CallbackQueryHandler(auth_callback(prompt_chat_menu), pattern=r"^prompt_chat:menu$"),
        CallbackQueryHandler(auth_callback(prompt_chat_start), pattern=r"^prompt_chat:start$"),
        CallbackQueryHandler(auth_callback(prompt_chat_mode), pattern=r"^prompt_chat:mode:(t2i|krea2|h3)$"),
        CallbackQueryHandler(auth_callback(prompt_chat_scale), pattern=r"^prompt_chat:scale$"),
        CallbackQueryHandler(auth_callback(prompt_chat_clear), pattern=r"^prompt_chat:clear$"),
        CallbackQueryHandler(auth_callback(prompt_chat_clear_quiet), pattern=r"^prompt_chat:clear_quiet$"),
        CallbackQueryHandler(auth_callback(prompt_chat_exit), pattern=r"^prompt_chat:exit$"),
    ]
