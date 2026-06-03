"""工作流导向主菜单 — /start、工作流说明页、帮助面板。"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler

from config import (
    WORKFLOW_REGISTRY,
    COMFY_WORKFLOWS,
    COMFY_VIDEO_ORIENTATIONS,
    COMFY_VIDEO_FRAMES_PRESETS,
)
from handlers import auth_callback, _user_auth_filter
from handlers.settings import (
    _ensure_settings,
    _save_settings,
    close_menu,
)
from services import credits as credits_service

logger = logging.getLogger(__name__)


# ═══ 工具函数 ═══

async def _safe_answer(query, text: str | None = None, show_alert: bool = False):
    """安全响应回调，代理不稳定时忽略网络错误。"""
    try:
        await query.answer(text, show_alert=show_alert)
    except Exception:
        pass


async def _reply_menu(query, text: str, markup):
    """用内联菜单编辑消息，失败时发送新消息。"""
    try:
        await query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except BadRequest:
        await _safe_answer(query)
        await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


def _find_workflow(key: str) -> dict | None:
    """在 WORKFLOW_REGISTRY 中按 key 查找工作流。"""
    for wf in WORKFLOW_REGISTRY:
        if wf["key"] == key:
            return wf
    return None


def _get_user_id(update) -> int:
    return update.effective_user.id


# ═══ 自动后端切换 ═══

async def _switch_to_workflow(update, context, workflow_entry: dict):
    """选择工作流时自动切换后端和 ComfyUI workflow。"""
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    comfy_key = workflow_entry["comfy_workflow"]

    settings["backend"] = workflow_entry["backend"]
    settings["comfy_workflow"] = comfy_key

    wf_config = COMFY_WORKFLOWS.get(comfy_key, {})
    if wf_config.get("model_selectable", True):
        default_model = wf_config.get("default_model", "")
        if default_model:
            settings["comfy_model"] = default_model

    _save_settings(context, user_id)


# ═══ 主菜单 ═══

def _build_main_menu() -> tuple[str, InlineKeyboardMarkup]:
    """构建工作流导向主菜单。"""
    text = (
        "<b>🎨 SD 绘图助手</b>\n\n"
        "选择你要使用的功能："
    )

    keyboard = []
    # 工作流按钮 — 一行两个
    row = []
    for wf in WORKFLOW_REGISTRY:
        btn = InlineKeyboardButton(
            f"{wf['emoji']} {wf['label']}",
            callback_data=f"workflow:{wf['key']}",
        )
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("⚙️ 参数设置", callback_data="comfy_settings"),
        InlineKeyboardButton("📖 帮助", callback_data="help_menu"),
    ])
    keyboard.append([
        InlineKeyboardButton("关闭", callback_data="close_menu"),
    ])

    return text, InlineKeyboardMarkup(keyboard)


async def show_main_menu(update, context):
    """显示工作流导向主菜单（/start 和 /help 入口）。"""
    text, markup = _build_main_menu()
    msg = update.effective_message
    if msg is None:
        return
    await msg.reply_text(text, reply_markup=markup, parse_mode="HTML")


async def main_menu_callback(update, context):
    """回调返回主菜单。"""
    query = update.callback_query
    await _safe_answer(query)
    text, markup = _build_main_menu()
    await _reply_menu(query, text, markup)


# ═══ 工作流说明页 ═══

def _build_workflow_detail(workflow_entry: dict, settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    """构建单个工作流的说明页。"""
    wf_key = workflow_entry["comfy_workflow"]
    wf_config = COMFY_WORKFLOWS.get(wf_key, {})

    text = f"<b>{workflow_entry['emoji']} {workflow_entry['label']}</b>\n\n"
    text += f"{workflow_entry['description']}\n\n"
    text += f"📋 <b>使用方法：</b>\n{workflow_entry['how_to']}\n\n"

    # 当前设置摘要
    parts = []
    if wf_config.get("model_selectable", True):
        model = settings.get("comfy_model", wf_config.get("default_model", "?"))
        parts.append(f"模型={model}")
    if not wf_config.get("is_img2img", False):
        w = settings.get("comfy_width", 768)
        h = settings.get("comfy_height", 1280)
        parts.append(f"尺寸={w}×{h}")
    if wf_config.get("output_type") == "video":
        orient = settings.get("comfy_video_orientation", "portrait")
        orient_label = COMFY_VIDEO_ORIENTATIONS.get(orient, {}).get("label", orient)
        frames_key = str(settings.get("comfy_video_frames", 81))
        frames_label = COMFY_VIDEO_FRAMES_PRESETS.get(frames_key, {}).get("label", frames_key)
        parts.append(f"方向={orient_label}")
        parts.append(f"长度={frames_label}")
    seed = settings.get("comfy_seed", -1)
    parts.append(f"种子={'随机' if seed == -1 else str(seed)}")

    text += f"<b>当前设置：</b>{' | '.join(parts)}"

    keyboard = [
        [
            InlineKeyboardButton(
                "⚡ 开始使用",
                callback_data=f"workflow_start:{workflow_entry['key']}",
            ),
            InlineKeyboardButton(
                "⚙️ 调整参数",
                callback_data="comfy_settings",
            ),
        ],
        [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
    ]

    return text, InlineKeyboardMarkup(keyboard)


async def show_workflow_detail(update, context):
    """显示工作流说明页。"""
    query = update.callback_query
    await _safe_answer(query)

    # 从 callback data 提取 workflow key：workflow:z-image-turbo
    wf_key = query.data.split(":", 1)[1]
    workflow_entry = _find_workflow(wf_key)
    if workflow_entry is None:
        await _safe_answer(query, "工作流不存在", show_alert=True)
        return

    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _build_workflow_detail(workflow_entry, settings)
    await _reply_menu(query, text, markup)


async def workflow_start(update, context):
    """「⚡ 开始使用」— 切换后端 + 引导用户。"""
    query = update.callback_query
    await _safe_answer(query)

    wf_key = query.data.split(":", 1)[1]
    workflow_entry = _find_workflow(wf_key)
    if workflow_entry is None:
        await _safe_answer(query, "工作流不存在", show_alert=True)
        return

    await _switch_to_workflow(update, context, workflow_entry)

    if workflow_entry["input_type"] == "text":
        hint = "✅ 已就绪！现在发送文字描述即可生成图片。"
    else:
        hint = "✅ 已就绪！现在发送一张图片即可开始。"

    await _safe_answer(query, hint, show_alert=True)

    # 删除菜单消息，让用户直接操作
    try:
        await query.message.delete()
    except Exception:
        pass


# ═══ 帮助面板 ═══

def _build_help_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = "<b>📖 帮助</b>"
    keyboard = [
        [
            InlineKeyboardButton("📋 使用指南", callback_data="help:guide"),
            InlineKeyboardButton("💰 额度查询", callback_data="help:credit"),
        ],
        [
            InlineKeyboardButton("🔑 命令列表", callback_data="help:commands"),
            InlineKeyboardButton("💡 使用技巧", callback_data="help:tips"),
        ],
        [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


async def show_help_menu(update, context):
    query = update.callback_query
    await _safe_answer(query)
    text, markup = _build_help_menu()
    await _reply_menu(query, text, markup)


async def help_guide(update, context):
    """使用指南 — 列出所有工作流概要，点击跳转详情。"""
    query = update.callback_query
    await _safe_answer(query)

    text = "<b>📋 使用指南</b>\n\n选择你要了解的功能："
    keyboard = []
    for wf in WORKFLOW_REGISTRY:
        keyboard.append([
            InlineKeyboardButton(
                f"{wf['emoji']} {wf['label']} — {wf['description']}",
                callback_data=f"workflow:{wf['key']}",
            ),
        ])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="help_menu")])
    await _reply_menu(query, text, InlineKeyboardMarkup(keyboard))


async def help_credit(update, context):
    query = update.callback_query
    await _safe_answer(query)

    user_id = _get_user_id(update)
    remaining = await credits_service.get_remaining(user_id)
    total_quota = (await credits_service.get_stats(user_id))["total_quota"]

    text = (
        f"<b>💰 额度查询</b>\n\n"
        f"已用：{total_quota - remaining} / {total_quota}\n"
        f"剩余：<b>{remaining}</b>\n\n"
        f"每次生成消耗 1 额度，用完后联系管理员充值。"
    )
    keyboard = [[InlineKeyboardButton("🔙 返回", callback_data="help_menu")]]
    await _reply_menu(query, text, InlineKeyboardMarkup(keyboard))


async def help_commands(update, context):
    query = update.callback_query
    await _safe_answer(query)

    text = (
        "<b>🔑 命令列表</b>\n\n"
        "/mode — 切换后端（SD WebUI / ComfyUI）\n"
        "/cancel — 取消当前等待输入\n"
        "/credit — 查看额度\n"
        "/start — 重新打开主菜单\n"
        "/help — 打开本帮助"
    )
    keyboard = [[InlineKeyboardButton("🔙 返回", callback_data="help_menu")]]
    await _reply_menu(query, text, InlineKeyboardMarkup(keyboard))


async def help_tips(update, context):
    query = update.callback_query
    await _safe_answer(query)

    text = (
        "<b>💡 使用技巧</b>\n\n"
        "• 在 ComfyUI 设置中固定种子，可以复现之前的生成结果\n"
        "• 为工作流设置自定义 Prompt，固定输出风格\n"
        "• 回复生成结果可以复用该次的种子\n"
        "• 图片编辑模式支持多轮修改，每次回复继续改"
    )
    keyboard = [[InlineKeyboardButton("🔙 返回", callback_data="help_menu")]]
    await _reply_menu(query, text, InlineKeyboardMarkup(keyboard))


# ═══ Handler 注册 ═══

def get_handlers() -> list:
    return [
        CommandHandler("start", show_main_menu, filters=_user_auth_filter()),
        CommandHandler("help", show_main_menu, filters=_user_auth_filter()),
        CallbackQueryHandler(auth_callback(main_menu_callback), pattern=r"^main_menu$"),
        CallbackQueryHandler(auth_callback(show_workflow_detail), pattern=r"^workflow:[a-z][a-z0-9-]*$"),
        CallbackQueryHandler(auth_callback(workflow_start), pattern=r"^workflow_start:[a-z][a-z0-9-]*$"),
        CallbackQueryHandler(auth_callback(show_help_menu), pattern=r"^help_menu$"),
        CallbackQueryHandler(auth_callback(help_guide), pattern=r"^help:guide$"),
        CallbackQueryHandler(auth_callback(help_credit), pattern=r"^help:credit$"),
        CallbackQueryHandler(auth_callback(help_commands), pattern=r"^help:commands$"),
        CallbackQueryHandler(auth_callback(help_tips), pattern=r"^help:tips$"),
    ]
