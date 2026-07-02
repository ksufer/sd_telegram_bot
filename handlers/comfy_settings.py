"""ComfyUI 专属设置菜单 — 模型、种子、分辨率、翻译开关。"""

import logging

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CallbackQueryHandler

from config import COMFY_SIZE_PRESETS, COMFY_WORKFLOWS, COMFY_DEFAULT_WORKFLOW
from config import COMFY_VIDEO_ASPECTS, COMFY_VIDEO_RESOLUTIONS, COMFY_VIDEO_FRAMES_PRESETS
from config import COMFY_LORA_VARIANTS, compute_video_dimensions
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

    is_video = wf_config.get("output_type") == "video"
    model_selectable = wf_config.get("model_selectable", True)

    text = (
        f"<b>🎨 ComfyUI 设置</b>\n"
        f"Workflow: {wf_config['label']}\n"
    )
    if model_selectable:
        text += f"模型: <code>{model}</code>\n"
    text += (
        f"种子: {seed_label}\n"
        f"翻译: {translate_label}\n"
        f"Prompt: {prompt_preview}"
    )

    keyboard = [
        [InlineKeyboardButton("切换 Workflow", callback_data="comfy_workflow")],
    ]
    if model_selectable:
        keyboard.append([InlineKeyboardButton("切换模型", callback_data="comfy_model")])
    keyboard.append([
        InlineKeyboardButton("种子输入", callback_data="comfy_seed"),
        InlineKeyboardButton(f"翻译 · {translate_label}", callback_data="comfy_translate"),
    ])

    # 文生图 workflow 显示尺寸选项
    if not wf_config.get("is_img2img", False):
        current_w = settings.get("comfy_width", 768)
        current_h = settings.get("comfy_height", 1280)
        text += f"\n尺寸: {current_w}×{current_h}"
        keyboard.insert(1, [InlineKeyboardButton("切换尺寸", callback_data="comfy_size")])

    # 视频 workflow 显示比例、画质和长度
    if is_video:
        aspect = settings.get("comfy_video_aspect", "9:16")
        aspect_cfg = COMFY_VIDEO_ASPECTS.get(aspect, COMFY_VIDEO_ASPECTS["9:16"])
        resolution = settings.get("comfy_video_resolution", "480p")
        resolution_cfg = COMFY_VIDEO_RESOLUTIONS.get(resolution, COMFY_VIDEO_RESOLUTIONS["480p"])
        w, h = compute_video_dimensions(aspect, resolution)
        frames_key = str(settings.get("comfy_video_frames", 81))
        frames_cfg = COMFY_VIDEO_FRAMES_PRESETS.get(frames_key, COMFY_VIDEO_FRAMES_PRESETS["81"])
        text += f"\n视频比例: {aspect_cfg['label']}"
        text += f"\n视频画质: {resolution_cfg['label']} ({w}×{h})"
        text += f"\n视频长度: {frames_cfg['label']}"
        keyboard.insert(1, [
            InlineKeyboardButton("视频比例", callback_data="comfy_video_aspect"),
            InlineKeyboardButton("视频画质", callback_data="comfy_video_resolution"),
        ])
        keyboard.insert(2, [
            InlineKeyboardButton("视频长度", callback_data="comfy_video_length"),
        ])

    # LoRA 变体（仅 zit-pussy 等有 lora_node 的 workflow 显示）
    if wf_config.get("lora_node"):
        variant_key = settings.get("comfy_lora_variant", "normal")
        variant = COMFY_LORA_VARIANTS.get(variant_key, COMFY_LORA_VARIANTS["normal"])
        text += f"\nLoRA变体: {variant['label']}"
        keyboard.insert(-2, [InlineKeyboardButton("切换 LoRA 变体", callback_data="comfy_lora_variant")])

    # 三级开关（Upscale / PussyDetailer / FaceDetailer）合并为一行
    toggle_row = []
    toggle_text_parts = []
    if wf_config.get("upscale_switch_node"):
        upscale_on = settings.get("comfy_upscale_enabled", True)
        label = "🔍" if upscale_on else "🔍✖"
        toggle_row.append(InlineKeyboardButton(label, callback_data="comfy_upscale_toggle"))
        toggle_text_parts.append(f"放大={'ON' if upscale_on else 'OFF'}")
    if wf_config.get("pussydetailer_switch_node"):
        pussydetailer_on = settings.get("comfy_pussydetailer_enabled", True)
        label = "🅿️" if pussydetailer_on else "🅿️✖"
        toggle_row.append(InlineKeyboardButton(label, callback_data="comfy_pussydetailer_toggle"))
        toggle_text_parts.append(f"精修={'ON' if pussydetailer_on else 'OFF'}")
    if wf_config.get("facedetailer_switch_node"):
        facedetailer_on = settings.get("comfy_facedetailer_enabled", True)
        label = "👤" if facedetailer_on else "👤✖"
        toggle_row.append(InlineKeyboardButton(label, callback_data="comfy_facedetailer_toggle"))
        toggle_text_parts.append(f"脸部={'ON' if facedetailer_on else 'OFF'}")
    if toggle_row:
        text += "\n" + " | ".join(toggle_text_parts)
        keyboard.insert(-2, toggle_row)

    # LoRA 开关 + 强度（krea2 等有 lora_enable_node 的 workflow 显示）
    if wf_config.get("lora_enable_node"):
        lora_on = settings.get("comfy_krea2_lora_enabled", False)
        lora_strength = settings.get("comfy_krea2_lora_strength", 5)
        lora_label = "🧬" if lora_on else "🧬✖"
        text += f"\nLoRA: {'ON' if lora_on else 'OFF'} | 强度: {lora_strength}"
        keyboard.insert(-2, [
            InlineKeyboardButton(lora_label, callback_data="comfy_krea2_lora_toggle"),
            InlineKeyboardButton(f"📊 LoRA强度({lora_strength})", callback_data="comfy_krea2_lora_strength"),
        ])

    # 脸部提示词（仅有 face_detailer_prompt_node 的 workflow 显示）
    if wf_config.get("face_detailer_prompt_node"):
        face_value = settings.get("comfy_face_prompt", "")
        if face_value:
            text += f"\n脸部提示词: {face_value[:60]}{'...' if len(face_value) > 60 else ''}"
        else:
            text += "\n脸部提示词: 🤖 自动提取"
        keyboard.insert(-2, [InlineKeyboardButton("✏️ 脸部提示词", callback_data="comfy_face_prompt_set")])
    if settings.get("comfy_face_prompt"):
        keyboard.insert(-2, [InlineKeyboardButton("🗑 清除脸部提示词", callback_data="comfy_face_prompt_clear")])

    keyboard.insert(-1, [InlineKeyboardButton("自定义 Prompt", callback_data="comfy_prompt")])
    if comfy_prompt:
        keyboard.insert(-1, [InlineKeyboardButton("🗑 清除 Prompt", callback_data="clear_comfy_prompt")])
    keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])
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

    keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])
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

    keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])
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

    keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])
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
                InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu"),
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


async def start_comfy_face_prompt_input(update, context):
    """进入脸部提示词手动输入模式。"""
    query = update.callback_query
    await _safe_answer(query)

    if context.user_data is None:
        await query.edit_message_text("当前不支持脸部提示词。")
        return

    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    current = settings.get("comfy_face_prompt", "")
    hint = f"当前: {current[:100]}" if current else "当前使用 🤖 自动提取"
    context.user_data["_waiting_input"] = "comfy_face_prompt"
    await query.edit_message_text(
        f"请输入脸部提示词（发送 /cancel 取消）\n{hint}\n\n"
        "脸部提示词用于 FaceDetailer 重绘，应只包含人物特征和画风。"
    )


async def clear_comfy_face_prompt(update, context):
    """清除手动脸部提示词，恢复自动提取。"""
    query = update.callback_query
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_face_prompt"] = ""
    _save_settings(context, user_id)

    await _safe_answer(query, "已恢复自动提取")
    text, markup = _comfy_settings_menu(settings)
    await _reply_menu(query, text, markup)


# ═══ 视频比例/画质/长度菜单 ═══

def _comfy_video_aspect_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    current = settings.get("comfy_video_aspect", "9:16")
    text = "<b>选择视频比例</b>"
    keyboard = []
    for key, preset in COMFY_VIDEO_ASPECTS.items():
        prefix = "✓ " if key == current else ""
        keyboard.append([InlineKeyboardButton(
            f"{prefix}{preset['label']}", callback_data=f"comfy_video_aspect:{key}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(keyboard)


def _comfy_video_resolution_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    current = settings.get("comfy_video_resolution", "480p")
    aspect = settings.get("comfy_video_aspect", "9:16")
    text = "<b>选择视频画质</b>"
    keyboard = []
    for key, preset in COMFY_VIDEO_RESOLUTIONS.items():
        prefix = "✓ " if key == current else ""
        w, h = compute_video_dimensions(aspect, key)
        keyboard.append([InlineKeyboardButton(
            f"{prefix}{preset['label']} ({w}×{h})", callback_data=f"comfy_video_resolution:{key}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(keyboard)


def _comfy_video_length_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    current = settings.get("comfy_video_frames", 81)
    text = f"<b>选择视频长度</b>\n当前: {current}帧"
    keyboard = []
    for key, preset in COMFY_VIDEO_FRAMES_PRESETS.items():
        active = preset["frames"] == current
        prefix = "✓ " if active else ""
        keyboard.append([InlineKeyboardButton(
            f"{prefix}{preset['label']}", callback_data=f"comfy_video_length:{key}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(keyboard)


async def show_comfy_video_aspect_menu(update, context):
    """显示视频比例选择菜单。"""
    query = update.callback_query
    await _safe_answer(query)
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _comfy_video_aspect_menu(settings)
    await _reply_menu(query, text, markup)


async def pick_comfy_video_aspect(update, context):
    """选择视频比例。"""
    query = update.callback_query
    aspect = query.data.split(":", 1)[1]
    if aspect not in COMFY_VIDEO_ASPECTS:
        await _safe_answer(query, "无效比例", show_alert=True)
        return
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_video_aspect"] = aspect
    _save_settings(context, user_id)
    label = COMFY_VIDEO_ASPECTS[aspect]["label"]
    await _safe_answer(query, f"视频比例: {label}")
    text, markup = _comfy_settings_menu(settings)
    await _reply_menu(query, text, markup)


async def show_comfy_video_resolution_menu(update, context):
    """显示视频画质选择菜单。"""
    query = update.callback_query
    await _safe_answer(query)
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _comfy_video_resolution_menu(settings)
    await _reply_menu(query, text, markup)


async def pick_comfy_video_resolution(update, context):
    """选择视频画质。"""
    query = update.callback_query
    resolution = query.data.split(":", 1)[1]
    if resolution not in COMFY_VIDEO_RESOLUTIONS:
        await _safe_answer(query, "无效画质", show_alert=True)
        return
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_video_resolution"] = resolution
    _save_settings(context, user_id)
    label = COMFY_VIDEO_RESOLUTIONS[resolution]["label"]
    await _safe_answer(query, f"视频画质: {label}")
    text, markup = _comfy_settings_menu(settings)
    await _reply_menu(query, text, markup)


async def show_comfy_video_length_menu(update, context):
    """显示视频长度选择菜单。"""
    query = update.callback_query
    await _safe_answer(query)
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _comfy_video_length_menu(settings)
    await _reply_menu(query, text, markup)


async def pick_comfy_video_length(update, context):
    """选择视频长度。"""
    query = update.callback_query
    key = query.data.split(":", 1)[1]
    preset = COMFY_VIDEO_FRAMES_PRESETS.get(key)
    if preset is None:
        await _safe_answer(query, "无效时长", show_alert=True)
        return
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_video_frames"] = preset["frames"]
    _save_settings(context, user_id)
    await _safe_answer(query, f"视频长度: {preset['label']}")
    text, markup = _comfy_settings_menu(settings)
    await _reply_menu(query, text, markup)


# ═══ LoRA 变体 ═══

def _comfy_lora_variant_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    current = settings.get("comfy_lora_variant", "normal")
    current_label = COMFY_LORA_VARIANTS.get(current, {}).get("label", "?")
    text = f"<b>选择 LoRA 变体</b>\n当前: {current_label}"

    keyboard = []
    for key, variant in COMFY_LORA_VARIANTS.items():
        prefix = "✓ " if key == current else ""
        keyboard.append([InlineKeyboardButton(
            f"{prefix}{variant['label']}", callback_data=f"comfy_lora_variant:{key}"
        )])

    keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(keyboard)


async def show_comfy_lora_variant_menu(update, context):
    """显示 LoRA 变体选择菜单。"""
    query = update.callback_query
    await _safe_answer(query)
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _comfy_lora_variant_menu(settings)
    await _reply_menu(query, text, markup)


async def pick_comfy_lora_variant(update, context):
    """选择 LoRA 变体。"""
    query = update.callback_query
    variant = query.data.split(":", 1)[1]
    if variant not in COMFY_LORA_VARIANTS:
        await _safe_answer(query, "无效变体", show_alert=True)
        return
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_lora_variant"] = variant
    _save_settings(context, user_id)
    label = COMFY_LORA_VARIANTS[variant]["label"]
    await _safe_answer(query, f"LoRA 变体: {label}")
    text, markup = _comfy_settings_menu(settings)
    await _reply_menu(query, text, markup)


async def pick_comfy_lora_variant_fast(update, context):
    """从生成后菜单快速切换 LoRA 变体，更新键盘高亮。"""
    query = update.callback_query
    variant = query.data.split(":", 1)[1]
    if variant not in COMFY_LORA_VARIANTS:
        await _safe_answer(query, "无效变体", show_alert=True)
        return
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_lora_variant"] = variant
    _save_settings(context, user_id)
    label = COMFY_LORA_VARIANTS[variant]["label"]
    await _safe_answer(query, f"LoRA 变体: {label}")
    await _update_gen_keyboard(query, settings)


# ═══ 生成后菜单键盘辅助 ═══

def _build_toggle_row(settings: dict, wf_config: dict) -> list:
    """构建三级开关行（🔍 🅿️ 👤），供生成后菜单复用。用 wf_config 判断工作流是否支持。"""
    row = []
    if wf_config.get("upscale_switch_node"):
        on = settings.get("comfy_upscale_enabled", True)
        row.append(InlineKeyboardButton("🔍" if on else "🔍✖", callback_data="comfy_upscale_toggle_gen"))
    if wf_config.get("pussydetailer_switch_node"):
        on = settings.get("comfy_pussydetailer_enabled", True)
        row.append(InlineKeyboardButton("🅿️" if on else "🅿️✖", callback_data="comfy_pussydetailer_toggle_gen"))
    if wf_config.get("facedetailer_switch_node"):
        on = settings.get("comfy_facedetailer_enabled", True)
        row.append(InlineKeyboardButton("👤" if on else "👤✖", callback_data="comfy_facedetailer_toggle_gen"))
    return row


async def _update_gen_keyboard(query, settings):
    """刷新生成后菜单键盘（各 fast handler 公用）"""
    wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
    wf_config = COMFY_WORKFLOWS.get(wf_key, COMFY_WORKFLOWS[COMFY_DEFAULT_WORKFLOW])

    if wf_config.get("lora_node"):
        # zit-pussy：LoRA 变体 + 三级开关
        current = settings.get("comfy_lora_variant", "normal")
        lora_buttons = []
        for key, variant in COMFY_LORA_VARIANTS.items():
            p = "✓ " if key == current else ""
            lora_buttons.append(InlineKeyboardButton(
                f"{p}{variant['label']}", callback_data=f"comfy_lora_var:{key}"
            ))
        toggle_row = _build_toggle_row(settings, wf_config)
        markup = InlineKeyboardMarkup([
            lora_buttons,
            toggle_row,
            [
                InlineKeyboardButton("⚙️ ComfyUI 设置", callback_data="comfy_settings"),
                InlineKeyboardButton("关闭菜单", callback_data="close_menu"),
            ],
        ])
    elif wf_config.get("lora_enable_node"):
        # krea2：脸部精修开关 + LoRA 开关
        rows = []
        toggle_row = []
        if wf_config.get("facedetailer_switch_node"):
            facedetailer_on = settings.get("comfy_facedetailer_enabled", True)
            label = "👤" if facedetailer_on else "👤✖"
            toggle_row.append(InlineKeyboardButton(label, callback_data="comfy_facedetailer_toggle_gen"))
        lora_on = settings.get("comfy_krea2_lora_enabled", False)
        lora_label = "🧬" if lora_on else "🧬✖"
        toggle_row.append(InlineKeyboardButton(lora_label, callback_data="comfy_krea2_lora_toggle_gen"))
        if toggle_row:
            rows.append(toggle_row)
        rows.append([
            InlineKeyboardButton("⚙️ ComfyUI 设置", callback_data="comfy_settings"),
            InlineKeyboardButton("关闭菜单", callback_data="close_menu"),
        ])
        markup = InlineKeyboardMarkup(rows)
    else:
        toggle_row = _build_toggle_row(settings, wf_config)
        markup = InlineKeyboardMarkup([
            toggle_row,
            [
                InlineKeyboardButton("⚙️ ComfyUI 设置", callback_data="comfy_settings"),
                InlineKeyboardButton("关闭菜单", callback_data="close_menu"),
            ],
        ])
    try:
        await query.message.edit_reply_markup(markup)
    except Exception:
        pass


# ═══ Toggle 开关（通用工厂，生成 settings 菜单和 fast 两个版本） ═══

def _make_toggle_handler(key: str, default: bool, label: str, fast: bool = False):
    """生成 toggle handler，避免 8 个重复函数。fast=True 仅刷新键盘不发消息。"""
    async def handler(update, context):
        query = update.callback_query
        user_id = _get_user_id(update)
        settings = _ensure_settings(context, user_id)
        settings[key] = not settings.get(key, default)
        _save_settings(context, user_id)
        state = "ON" if settings[key] else "OFF"
        await _safe_answer(query, f"{label} · {state}")
        if fast:
            await _update_gen_keyboard(query, settings)
        else:
            text, markup = _comfy_settings_menu(settings)
            await _reply_menu(query, text, markup)
    return handler


async def start_comfy_krea2_lora_strength(update, context):
    """进入 LoRA 强度输入模式"""
    query = update.callback_query
    if context.user_data is None:
        await _safe_answer(query, "会话已过期，请重新发送 /start")
        return
    context.user_data["_waiting_input"] = "comfy_krea2_lora_strength"
    await _safe_answer(query, "请输入 LoRA 强度")
    await query.message.reply_text(
        "请输入 LoRA 强度值（范围 -15 ~ 10，默认 5）：\n"
        "发送 /cancel 取消操作"
    )


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
        # 视频比例/画质/长度
        CallbackQueryHandler(auth_callback(show_comfy_video_aspect_menu),
                             pattern=r"^comfy_video_aspect$"),
        CallbackQueryHandler(auth_callback(pick_comfy_video_aspect),
                             pattern=r"^comfy_video_aspect:"),
        CallbackQueryHandler(auth_callback(show_comfy_video_resolution_menu),
                             pattern=r"^comfy_video_resolution$"),
        CallbackQueryHandler(auth_callback(pick_comfy_video_resolution),
                             pattern=r"^comfy_video_resolution:"),
        CallbackQueryHandler(auth_callback(show_comfy_video_length_menu),
                             pattern=r"^comfy_video_length$"),
        CallbackQueryHandler(auth_callback(pick_comfy_video_length),
                             pattern=r"^comfy_video_length:\d+$"),
        # LoRA 变体
        CallbackQueryHandler(auth_callback(show_comfy_lora_variant_menu),
                             pattern=r"^comfy_lora_variant$"),
        CallbackQueryHandler(auth_callback(pick_comfy_lora_variant),
                             pattern=r"^comfy_lora_variant:"),
        # LoRA 变体快速切换（生成后菜单）
        CallbackQueryHandler(auth_callback(pick_comfy_lora_variant_fast),
                             pattern=r"^comfy_lora_var:"),
        # 开关（通用 toggle handler 工厂）
        CallbackQueryHandler(auth_callback(_make_toggle_handler("comfy_upscale_enabled", True, "SD Upscale")),
                             pattern=r"^comfy_upscale_toggle$"),
        CallbackQueryHandler(auth_callback(_make_toggle_handler("comfy_upscale_enabled", True, "SD Upscale", fast=True)),
                             pattern=r"^comfy_upscale_toggle_gen$"),
        CallbackQueryHandler(auth_callback(_make_toggle_handler("comfy_pussydetailer_enabled", True, "PussyDetailer")),
                             pattern=r"^comfy_pussydetailer_toggle$"),
        CallbackQueryHandler(auth_callback(_make_toggle_handler("comfy_pussydetailer_enabled", True, "PussyDetailer", fast=True)),
                             pattern=r"^comfy_pussydetailer_toggle_gen$"),
        CallbackQueryHandler(auth_callback(_make_toggle_handler("comfy_facedetailer_enabled", True, "FaceDetailer")),
                             pattern=r"^comfy_facedetailer_toggle$"),
        CallbackQueryHandler(auth_callback(_make_toggle_handler("comfy_facedetailer_enabled", True, "FaceDetailer", fast=True)),
                             pattern=r"^comfy_facedetailer_toggle_gen$"),
        CallbackQueryHandler(auth_callback(_make_toggle_handler("comfy_krea2_lora_enabled", False, "Krea2 LoRA")),
                             pattern=r"^comfy_krea2_lora_toggle$"),
        CallbackQueryHandler(auth_callback(_make_toggle_handler("comfy_krea2_lora_enabled", False, "Krea2 LoRA", fast=True)),
                             pattern=r"^comfy_krea2_lora_toggle_gen$"),
        # 脸部提示词
        CallbackQueryHandler(auth_callback(start_comfy_face_prompt_input),
                             pattern=r"^comfy_face_prompt_set$"),
        CallbackQueryHandler(auth_callback(clear_comfy_face_prompt),
                             pattern=r"^comfy_face_prompt_clear$"),
        # Krea2 LoRA 强度
        CallbackQueryHandler(auth_callback(start_comfy_krea2_lora_strength),
                             pattern=r"^comfy_krea2_lora_strength$"),
    ]
