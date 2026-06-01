"""ComfyUI 专属设置菜单 — 模型、种子、分辨率、翻译开关。"""

import logging

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CallbackQueryHandler

from config import COMFY_SIZE_PRESETS, COMFY_WORKFLOWS, COMFY_DEFAULT_WORKFLOW
from handlers import auth_callback
from handlers.settings import _ensure_settings, _save_settings
from services import comfy_api

logger = logging.getLogger(__name__)


# ═══ 菜单渲染 ═══

def _comfy_settings_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
    wf_config = COMFY_WORKFLOWS.get(wf_key, COMFY_WORKFLOWS[COMFY_DEFAULT_WORKFLOW])
    model = settings.get("comfy_model", wf_config.get("default_model", "?"))
    seed = settings.get("comfy_seed", -1)
    translate = settings.get("comfy_translate", False)

    seed_label = "随机" if seed == -1 else str(seed)
    comfy_prompt = settings.get("comfy_prompt", "")
    prompt_preview = comfy_prompt[:30] + "..." if comfy_prompt else "（使用默认）"
    translate_label = "ON" if translate else "OFF"

    text = (
        f"<b>🎨 ComfyUI 设置</b>\n"
        f"Workflow: {wf_config['label']}\n"
        f"模型: <code>{model}</code>\n"
        f"种子: {seed_label}\n"
        f"翻译: {translate_label}\n"
        f"Prompt: {prompt_preview}"
    )

    keyboard = [
        [InlineKeyboardButton("切换 Workflow", callback_data="comfy_workflow")],
        [InlineKeyboardButton("切换模型", callback_data="comfy_model")],
        [
            InlineKeyboardButton("种子输入", callback_data="comfy_seed"),
            InlineKeyboardButton(f"翻译 · {translate_label}", callback_data="comfy_translate"),
        ],
    ]

    # 文生图 workflow 显示尺寸选项
    if not wf_config.get("is_img2img", False):
        current_w = settings.get("comfy_width", 768)
        current_h = settings.get("comfy_height", 1280)
        text += f"\n尺寸: {current_w}×{current_h}"
        keyboard.insert(1, [InlineKeyboardButton("切换尺寸", callback_data="comfy_size")])

    keyboard.insert(-1, [InlineKeyboardButton("自定义 Prompt", callback_data="comfy_prompt")])
    if comfy_prompt:
        keyboard.insert(-1, [InlineKeyboardButton("🗑 清除 Prompt", callback_data="clear_comfy_prompt")])
    keyboard.append([InlineKeyboardButton("关闭菜单", callback_data="close_menu")])
    return text, InlineKeyboardMarkup(keyboard)


def _comfy_workflow_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    current = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
    text = f"<b>选择 Workflow</b>\n当前: {COMFY_WORKFLOWS.get(current, {}).get('label', current)}"

    keyboard = []
    for key, wf in COMFY_WORKFLOWS.items():
        prefix = "✓ " if key == current else ""
        keyboard.append([InlineKeyboardButton(
            f"{prefix}{wf['label']}", callback_data=f"comfy_workflow:{key}"
        )])

    keyboard.append([InlineKeyboardButton("返回", callback_data="comfy_settings")])
    return text, InlineKeyboardMarkup(keyboard)


def _comfy_model_menu(settings: dict, models: list[str]) -> tuple[str, InlineKeyboardMarkup]:
    wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
    wf_config = COMFY_WORKFLOWS.get(wf_key, COMFY_WORKFLOWS[COMFY_DEFAULT_WORKFLOW])
    current = settings.get("comfy_model", wf_config.get("default_model", "?"))
    text = f"<b>选择模型</b>\n当前: <code>{current}</code>"

    keyboard = []
    for i, name in enumerate(models):
        prefix = "✓ " if name == current else ""
        keyboard.append([InlineKeyboardButton(
            f"{prefix}{name}", callback_data=f"comfy_model:{i}"
        )])

    keyboard.append([InlineKeyboardButton("返回", callback_data="comfy_settings")])
    return text, InlineKeyboardMarkup(keyboard)


def _comfy_size_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    current_w = settings.get("comfy_width", 768)
    current_h = settings.get("comfy_height", 1280)
    text = f"<b>选择尺寸</b>\n当前: {current_w}×{current_h}"

    keyboard = []
    for key, preset in COMFY_SIZE_PRESETS.items():
        active = current_w == preset["width"] and current_h == preset["height"]
        prefix = "✓ " if active else ""
        keyboard.append([InlineKeyboardButton(
            f"{prefix}{preset['label']}", callback_data=f"comfy_size:{key}"
        )])

    keyboard.append([InlineKeyboardButton("返回", callback_data="comfy_settings")])
    return text, InlineKeyboardMarkup(keyboard)


# ═══ 回调处理 ═══

async def _safe_answer(query, text: str | None = None, show_alert: bool = False):
    try:
        await query.answer(text, show_alert=show_alert)
    except Exception:
        pass


async def _reply_menu(query, text: str, markup):
    try:
        await query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


def _get_user_id(update) -> int:
    return update.effective_user.id


async def show_comfy_settings(update, context):
    """显示 ComfyUI 主设置菜单。"""
    query = update.callback_query
    await _safe_answer(query)
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _comfy_settings_menu(settings)
    await _reply_menu(query, text, markup)


async def show_comfy_model_menu(update, context):
    """获取模型列表并显示。"""
    query = update.callback_query
    await _safe_answer(query)
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)

    try:
        models = await comfy_api.get_models(settings)
    except Exception as e:
        logger.warning("获取 ComfyUI 模型列表失败: %s", e)
        await query.edit_message_text(
            "无法获取 ComfyUI 模型列表，请确认 ComfyUI 服务是否在线。",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("返回", callback_data="comfy_settings"),
            ]]),
        )
        return

    # 用 context.user_data 暂存模型列表供 pick 使用（避免 callback data 超长）
    context.user_data["_comfy_models"] = models

    text, markup = _comfy_model_menu(settings, models)
    await _reply_menu(query, text, markup)


async def pick_comfy_model(update, context):
    """根据索引选择模型。"""
    query = update.callback_query
    models = context.user_data.get("_comfy_models", [])
    try:
        idx = int(query.data.split(":", 1)[1])
        model_name = models[idx]
    except (IndexError, ValueError):
        await _safe_answer(query, "无效的模型选择", show_alert=True)
        return

    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_model"] = model_name
    _save_settings(context, user_id)

    await _safe_answer(query, f"模型: {model_name}")
    text, markup = _comfy_settings_menu(settings)
    await _reply_menu(query, text, markup)


async def show_comfy_size_menu(update, context):
    """显示 ComfyUI 尺寸预设。"""
    query = update.callback_query
    await _safe_answer(query)
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _comfy_size_menu(settings)
    await _reply_menu(query, text, markup)


async def pick_comfy_size(update, context):
    """根据 key 设置尺寸。"""
    query = update.callback_query
    key = query.data.split(":", 1)[1]
    preset = COMFY_SIZE_PRESETS.get(key)
    if preset is None:
        await _safe_answer(query, "无效的尺寸选择", show_alert=True)
        return

    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_width"] = preset["width"]
    settings["comfy_height"] = preset["height"]
    _save_settings(context, user_id)

    await _safe_answer(query, f"尺寸: {preset['label']}")
    text, markup = _comfy_settings_menu(settings)
    await _reply_menu(query, text, markup)


async def start_comfy_seed_input(update, context):
    """进入种子输入模式。"""
    query = update.callback_query
    await _safe_answer(query)

    if context.user_data is None:
        await query.edit_message_text("当前不支持种子输入。")
        return

    context.user_data["_waiting_input"] = "comfy_seed"
    await query.edit_message_text(
        "请输入种子数字（-1 表示随机）：\n发送 /cancel 取消。"
    )


async def show_comfy_workflow_menu(update, context):
    """显示 Workflow 选择菜单。"""
    query = update.callback_query
    await _safe_answer(query)
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _comfy_workflow_menu(settings)
    await _reply_menu(query, text, markup)


async def pick_comfy_workflow(update, context):
    """选择 Workflow。"""
    query = update.callback_query
    wf_key = query.data.split(":", 1)[1]
    if wf_key not in COMFY_WORKFLOWS:
        await _safe_answer(query, "无效的 Workflow 选择", show_alert=True)
        return

    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_workflow"] = wf_key
    # 切换 workflow 时同时更新默认模型
    wf_config = COMFY_WORKFLOWS[wf_key]
    settings["comfy_model"] = wf_config.get("default_model", "")
    _save_settings(context, user_id)

    await _safe_answer(query, f"Workflow: {wf_config['label']}")
    text, markup = _comfy_settings_menu(settings)
    await _reply_menu(query, text, markup)


async def start_comfy_prompt_input(update, context):
    """进入自定义 Prompt 输入模式。"""
    query = update.callback_query
    await _safe_answer(query)

    if context.user_data is None:
        await query.edit_message_text("当前不支持自定义 Prompt。")
        return

    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    current = settings.get("comfy_prompt", "")
    hint = f"当前: {current[:100]}" if current else "当前使用 workflow 默认 prompt"
    context.user_data["_waiting_input"] = "comfy_prompt"
    await query.edit_message_text(
        f"请输入自定义 Prompt（发送 /cancel 取消）\n{hint}"
    )


async def toggle_comfy_translate(update, context):
    """切换 ComfyUI 翻译开关。"""
    query = update.callback_query
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_translate"] = not settings.get("comfy_translate", False)
    _save_settings(context, user_id)

    state = "ON" if settings["comfy_translate"] else "OFF"
    await _safe_answer(query, f"翻译 · {state}")
    text, markup = _comfy_settings_menu(settings)
    await _reply_menu(query, text, markup)


async def reuse_comfy_seed(update, context):
    """从 context_id 缓存读取 actual_seed，写入 comfy_seed。"""
    query = update.callback_query
    await _safe_answer(query)

    context_id = query.data.replace("comfy_reuse_seed_", "")
    gen_ctx = context.bot_data.get("_gen_context", {}).get(context_id)
    if gen_ctx is None:
        await query.answer("未找到本次 Seed，请重新生成。", show_alert=True)
        return

    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_seed"] = gen_ctx["seed"]
    _save_settings(context, user_id)

    await query.answer(f"Seed 已固定为 {gen_ctx['seed']}，下次生成将复用。", show_alert=True)


async def random_comfy_seed(update, context):
    """恢复随机种子。"""
    query = update.callback_query
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_seed"] = -1
    _save_settings(context, user_id)

    await _safe_answer(query, "已恢复随机种子")


async def clear_comfy_prompt(update, context):
    """清除自定义 Prompt，恢复使用实时输入。"""
    query = update.callback_query
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_prompt"] = ""
    _save_settings(context, user_id)

    await _safe_answer(query, "已清除 Prompt")
    text, markup = _comfy_settings_menu(settings)
    await _reply_menu(query, text, markup)


# ═══ Handler 注册 ═══

def get_handlers() -> list:
    return [
        CallbackQueryHandler(auth_callback(show_comfy_settings), pattern=r"^comfy_settings$"),
        CallbackQueryHandler(auth_callback(show_comfy_workflow_menu), pattern=r"^comfy_workflow$"),
        CallbackQueryHandler(auth_callback(pick_comfy_workflow), pattern=r"^comfy_workflow:"),
        CallbackQueryHandler(auth_callback(show_comfy_model_menu), pattern=r"^comfy_model$"),
        CallbackQueryHandler(auth_callback(pick_comfy_model), pattern=r"^comfy_model:"),
        CallbackQueryHandler(auth_callback(show_comfy_size_menu), pattern=r"^comfy_size$"),
        CallbackQueryHandler(auth_callback(pick_comfy_size), pattern=r"^comfy_size:"),
        CallbackQueryHandler(auth_callback(start_comfy_seed_input), pattern=r"^comfy_seed$"),
        CallbackQueryHandler(auth_callback(start_comfy_prompt_input), pattern=r"^comfy_prompt$"),
        CallbackQueryHandler(auth_callback(toggle_comfy_translate), pattern=r"^comfy_translate$"),
        CallbackQueryHandler(auth_callback(reuse_comfy_seed), pattern=r"^comfy_reuse_seed_"),
        CallbackQueryHandler(auth_callback(random_comfy_seed), pattern=r"^comfy_random_seed$"),
        CallbackQueryHandler(auth_callback(clear_comfy_prompt), pattern=r"^clear_comfy_prompt$"),
    ]
