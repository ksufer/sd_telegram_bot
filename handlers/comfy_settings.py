"""ComfyUI 专属设置菜单 — 模型、种子、分辨率、翻译开关。"""

import html
import logging

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CallbackQueryHandler

from config import COMFY_SIZE_PRESETS, COMFY_WORKFLOWS, COMFY_DEFAULT_WORKFLOW
from config import COMFY_VIDEO_ASPECTS, COMFY_VIDEO_RESOLUTIONS, COMFY_VIDEO_FRAMES_PRESETS
from config import DEFAULT_VIDEO_FRAMES_KEY
from config import COMFY_LORA_VARIANTS, compute_video_dimensions
from config import COMFY_PROMPT_OPTIMIZE_MODES, COMFY_PROMPT_OPTIMIZE_CYCLE
from handlers import auth_callback
from handlers.settings import _ensure_settings, _save_settings
from handlers.common import safe_answer, reply_menu, get_user_id
from services import comfy_api
from services.queue import _escape_and_truncate
from ui.keyboards import comfy_generation_menu, build_toggle_row

logger = logging.getLogger(__name__)


def _get_workflow_config(key: str) -> dict:
    """安全获取 workflow 配置。COMFY_WORKFLOWS 为空时返回空字典。"""
    if key in COMFY_WORKFLOWS:
        return COMFY_WORKFLOWS[key]
    if COMFY_DEFAULT_WORKFLOW in COMFY_WORKFLOWS:
        return COMFY_WORKFLOWS[COMFY_DEFAULT_WORKFLOW]
    return next(iter(COMFY_WORKFLOWS.values())) if COMFY_WORKFLOWS else {}


# ═══ 辅助函数 ═══

def _can_config_size(wf_config, uc):
    return (
        {"comfy_width", "comfy_height"}.issubset(uc)
        and wf_config.get("width_node") and wf_config.get("width_key")
        and wf_config.get("height_node") and wf_config.get("height_key")
    )

def _can_config_model(wf_config, uc):
    return (
        "comfy_model" in uc
        and wf_config.get("model_selectable", True)
        and wf_config.get("model_node") and wf_config.get("model_key")
    )

def _can_config_face_prompt(wf_config, uc):
    return (
        "comfy_face_prompt" in uc
        and wf_config.get("face_detailer_prompt_node")
    )

def _can_config_lora(wf_config, uc):
    return "comfy_lora_variant" in uc and wf_config.get("lora_node")


# ═══ 菜单渲染 ═══

def _add_dimension_rows(keyboard: list, info_lines: list,
                        wf_config: dict, settings: dict) -> None:
    """追加尺寸/视频行（原 insert(1) 位置）。"""
    uc = wf_config.get("user_configurable", [])
    is_video = wf_config.get("output_type") == "video"
    has_video_dims = (wf_config.get("video_width_node")
                      or wf_config.get("video_selector_node")
                      or wf_config.get("video_megapixels_node"))
    has_video_len = (wf_config.get("video_frames_node")
                     or wf_config.get("video_duration_node"))
    if is_video and has_video_dims and has_video_len:
        auto_aspect = bool(wf_config.get("video_megapixels_node"))
        aspect = settings.get("comfy_video_aspect", "9:16")
        aspect_cfg = COMFY_VIDEO_ASPECTS.get(aspect, COMFY_VIDEO_ASPECTS["9:16"])
        resolution = settings.get("comfy_video_resolution", "480p")
        resolution_cfg = COMFY_VIDEO_RESOLUTIONS.get(resolution,
                                                     COMFY_VIDEO_RESOLUTIONS["480p"])
        w, h = compute_video_dimensions(aspect, resolution)
        frames_key = str(settings.get("comfy_video_frames",
                                      COMFY_VIDEO_FRAMES_PRESETS[DEFAULT_VIDEO_FRAMES_KEY]["frames"]))
        frames_cfg = COMFY_VIDEO_FRAMES_PRESETS.get(frames_key,
                                                    COMFY_VIDEO_FRAMES_PRESETS[DEFAULT_VIDEO_FRAMES_KEY])
        if "comfy_video_aspect" in uc:
            info_lines.append(f"视频比例: {aspect_cfg['label']}")
        info_lines.append(
            f"视频画质: {resolution_cfg['label']}"
            f"{'（比例跟随首帧）' if auto_aspect else f' ({w}×{h})'}"
        )
        info_lines.append(f"视频长度: {frames_cfg['label']}")
        row = []
        if "comfy_video_aspect" in uc:
            row.append(InlineKeyboardButton("视频比例", callback_data="comfy_video_aspect"))
        if "comfy_video_resolution" in uc:
            row.append(InlineKeyboardButton("视频画质", callback_data="comfy_video_resolution"))
        if row:
            keyboard.append(row)
        if "comfy_video_frames" in uc:
            keyboard.append([
                InlineKeyboardButton("视频长度", callback_data="comfy_video_length"),
            ])
    elif (
        not wf_config.get("is_img2img", False)
        and _can_config_size(wf_config, uc)
    ):
        current_w = settings.get("comfy_width", 960)
        current_h = settings.get("comfy_height", 1280)
        info_lines.append(f"尺寸: {current_w}×{current_h}")
        keyboard.append([
            InlineKeyboardButton("切换尺寸", callback_data="comfy_size"),
        ])


def _add_middle_rows(keyboard: list, info_lines: list,
                     wf_config: dict, settings: dict) -> None:
    """追加 LoRA/开关/krea2/脸部提示词行（原 insert(-2) 位置）。"""
    uc = wf_config.get("user_configurable", [])

    # LoRA 变体（zit-pussy 等）
    if _can_config_lora(wf_config, uc):
        variant_key = settings.get("comfy_lora_variant", "normal")
        variant = COMFY_LORA_VARIANTS.get(variant_key, COMFY_LORA_VARIANTS["normal"])
        info_lines.append(f"LoRA变体: {variant['label']}")
        keyboard.append([
            InlineKeyboardButton("切换 LoRA 变体", callback_data="comfy_lora_variant"),
        ])

    # 三级开关
    toggle_row = []
    toggle_text_parts = []
    if wf_config.get("upscale_switch_node") and "comfy_upscale_enabled" in uc:
        upscale_on = settings.get("comfy_upscale_enabled", True)
        label = "🔍" if upscale_on else "🔍✖"
        toggle_row.append(InlineKeyboardButton(label, callback_data="comfy_upscale_toggle"))
        toggle_text_parts.append(f"放大={'ON' if upscale_on else 'OFF'}")
    if wf_config.get("pussydetailer_switch_node") and "comfy_pussydetailer_enabled" in uc:
        pussydetailer_on = settings.get("comfy_pussydetailer_enabled", True)
        label = "🅿️" if pussydetailer_on else "🅿️✖"
        toggle_row.append(InlineKeyboardButton(label, callback_data="comfy_pussydetailer_toggle"))
        toggle_text_parts.append(f"精修={'ON' if pussydetailer_on else 'OFF'}")
    if wf_config.get("facedetailer_switch_node") and "comfy_facedetailer_enabled" in uc:
        facedetailer_on = settings.get("comfy_facedetailer_enabled", True)
        label = "👤" if facedetailer_on else "👤✖"
        toggle_row.append(InlineKeyboardButton(label, callback_data="comfy_facedetailer_toggle"))
        toggle_text_parts.append(f"脸部={'ON' if facedetailer_on else 'OFF'}")
    # 提示词优化三态（关闭/NSFW/SFW）
    if wf_config.get("prompt_optimize_node") and "comfy_prompt_optimize" in uc:
        mode = _normalize_optimize_mode(settings.get("comfy_prompt_optimize", "nsfw"))
        mode_cfg = COMFY_PROMPT_OPTIMIZE_MODES[mode]
        toggle_row.append(InlineKeyboardButton(mode_cfg["icon"], callback_data="comfy_prompt_optimize_cycle"))
        toggle_text_parts.append(f"优化={mode_cfg['label']}")
    # SD Upscale 提示词注入开关
    if wf_config.get("sd_upscale_prompt_node") and "comfy_sd_upscale_prompt_inject" in uc:
        inject_on = settings.get("comfy_sd_upscale_prompt_inject", True)
        toggle_row.append(InlineKeyboardButton(
            "📝" if inject_on else "📝✖", callback_data="comfy_sd_upscale_prompt_toggle"))
        toggle_text_parts.append(f"放大词注入={'ON' if inject_on else 'OFF'}")
    if toggle_row:
        info_lines.append(" | ".join(toggle_text_parts))
        keyboard.append(toggle_row)

    # krea2 LoRA 开关 + 模型 + 触发词 + 强度
    if wf_config.get("lora_enable_node") and "comfy_krea2_lora_enabled" in uc:
        lora_on = settings.get("comfy_krea2_lora_enabled", False)
        lora_name = (settings.get("comfy_krea2_lora_name") or "").strip()
        lora_trigger = (settings.get("comfy_krea2_lora_trigger") or "").strip()
        lora_strength = settings.get("comfy_krea2_lora_strength", 5)
        name_label = _escape_and_truncate(lora_name, 20) if lora_name else "工作流默认"
        trigger_label = _escape_and_truncate(lora_trigger, 20) if lora_trigger else "无"
        info_lines.append(
            f"LoRA: {'ON' if lora_on else 'OFF'} | 模型: {name_label}"
            f" | 触发词: {trigger_label} | 强度: {lora_strength}"
        )
        keyboard.append([
            InlineKeyboardButton("🧬" if lora_on else "🧬✖",
                                 callback_data="comfy_krea2_lora_toggle"),
            InlineKeyboardButton("📖 LoRA列表", callback_data="comfy_krea2_lora_pick"),
            InlineKeyboardButton("🔤 触发词", callback_data="comfy_krea2_lora_trigger"),
            InlineKeyboardButton(f"📊 强度({lora_strength})",
                                 callback_data="comfy_krea2_lora_strength"),
        ])

    # 脸部提示词
    if _can_config_face_prompt(wf_config, uc):
        face_value = settings.get("comfy_face_prompt", "")
        if face_value:
            info_lines.append(f"脸部提示词: {_escape_and_truncate(face_value, 60)}")
        else:
            info_lines.append("脸部提示词: 🤖 自动提取")
        keyboard.append([
            InlineKeyboardButton("✏️ 脸部提示词", callback_data="comfy_face_prompt_set"),
        ])
    if _can_config_face_prompt(wf_config, uc) and settings.get("comfy_face_prompt"):
        keyboard.append([
            InlineKeyboardButton("🗑 清除脸部提示词",
                                 callback_data="comfy_face_prompt_clear"),
        ])


def _comfy_settings_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
    wf_config = _get_workflow_config(wf_key)
    uc = wf_config.get("user_configurable", [])
    model = settings.get("comfy_model", wf_config.get("default_model", "?"))
    seed = settings.get("comfy_seed", -1)
    translate = settings.get("comfy_translate", False)
    comfy_prompt = settings.get("comfy_prompt", "")
    model_selectable = _can_config_model(wf_config, uc)

    seed_label = "随机" if seed == -1 else str(seed)
    prompt_preview = _escape_and_truncate(comfy_prompt, 30) if comfy_prompt else "（使用默认）"
    translate_label = "ON" if translate else "OFF"

    # 信息文本逐行收集
    info_lines = [
        "<b>🎨 ComfyUI 设置</b>",
        f"Workflow: {wf_config.get('label', wf_key)}",
    ]
    if model_selectable:
        info_lines.append(f"模型: <code>{html.escape(str(model))}</code>")
    info_lines.append(f"种子: {seed_label}")
    info_lines.append(f"翻译: {translate_label}")
    info_lines.append(f"Prompt: {prompt_preview}")

    # 键盘用 append 按最终顺序构建（不用 insert）
    keyboard: list = []

    # Row 0: Workflow
    keyboard.append([
        InlineKeyboardButton("切换 Workflow", callback_data="comfy_workflow"),
    ])

    # Row 1+: 尺寸或视频参数（原 insert(1) 位置）
    _add_dimension_rows(keyboard, info_lines, wf_config, settings)

    # Middle rows: LoRA / 开关 / krea2 / 脸部提示词（原 insert(-2) 位置）
    _add_middle_rows(keyboard, info_lines, wf_config, settings)

    # Model（原在 keybaord[1]，被后续 insert 推到这里）
    if model_selectable:
        keyboard.append([
            InlineKeyboardButton("切换模型", callback_data="comfy_model"),
        ])

    # Custom Prompt（原 insert(-1)）
    keyboard.append([
        InlineKeyboardButton("自定义 Prompt", callback_data="comfy_prompt"),
    ])
    if comfy_prompt:
        keyboard.append([
            InlineKeyboardButton("🗑 清除 Prompt", callback_data="clear_comfy_prompt"),
        ])

    # Seed / Translate（原 keyboard[2] 即末尾位置）
    keyboard.append([
        InlineKeyboardButton("种子输入", callback_data="comfy_seed"),
        InlineKeyboardButton(f"翻译 · {translate_label}", callback_data="comfy_translate"),
    ])

    # Back
    keyboard.append([
        InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu"),
    ])

    return "\n".join(info_lines), InlineKeyboardMarkup(keyboard)


def _comfy_workflow_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    current = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
    current_label = html.escape(str(COMFY_WORKFLOWS.get(current, {}).get("label", current)))
    text = f"<b>选择 Workflow</b>\n当前: {current_label}"

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
    wf_config = _get_workflow_config(wf_key)
    current = settings.get("comfy_model", wf_config.get("default_model", "?"))
    text = f"<b>选择模型</b>\n当前: <code>{html.escape(str(current))}</code>"

    keyboard = []
    for i, name in enumerate(models):
        prefix = "✓ " if name == current else ""
        keyboard.append([InlineKeyboardButton(
            f"{prefix}{name}", callback_data=f"comfy_model:{i}"
        )])

    keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(keyboard)


def _comfy_size_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    current_w = settings.get("comfy_width", 960)
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

async def show_comfy_settings(update, context):
    """显示 ComfyUI 主设置菜单。"""
    query = update.callback_query
    await safe_answer(query)
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _comfy_settings_menu(settings)
    await reply_menu(query, text, markup)


async def show_comfy_model_menu(update, context):
    """获取模型列表并显示。"""
    query = update.callback_query
    await safe_answer(query)
    user_id = get_user_id(update)
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
    # 同时记录打开菜单时的 workflow，防止切 workflow 后旧菜单写错模型
    context.user_data["_comfy_models"] = models
    context.user_data["_comfy_models_wf"] = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)

    text, markup = _comfy_model_menu(settings, models)
    await reply_menu(query, text, markup)


async def pick_comfy_model(update, context):
    """根据索引选择模型。"""
    query = update.callback_query
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)

    # 模型列表按打开菜单时的 workflow 缓存，切换 workflow 后旧菜单作废
    current_wf = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
    if context.user_data.get("_comfy_models_wf") != current_wf:
        await safe_answer(query, "模型列表已过期，请重新打开模型菜单", show_alert=True)
        return

    models = context.user_data.get("_comfy_models", [])
    try:
        idx = int(query.data.split(":", 1)[1])
        if not 0 <= idx < len(models):
            raise IndexError
        model_name = models[idx]
    except (IndexError, ValueError):
        await safe_answer(query, "无效的模型选择", show_alert=True)
        return

    settings["comfy_model"] = model_name
    _save_settings(context, user_id)

    await safe_answer(query, f"模型: {model_name}")
    text, markup = _comfy_settings_menu(settings)
    await reply_menu(query, text, markup)


async def show_comfy_krea2_lora_menu(update, context):
    """扫描 ComfyUI 已安装 LoRA 并显示选择菜单（分页，防止键盘超限）。"""
    query = update.callback_query
    await safe_answer(query)
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)

    loras = await comfy_api.get_lora_models()
    if loras is None:
        await query.edit_message_text(
            "无法获取 ComfyUI LoRA 列表，请确认 ComfyUI 服务是否在线。",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu"),
            ]]),
        )
        return

    # 暂存列表供 pick 使用（callback data 用索引，规避 64 字节限制）
    context.user_data["_comfy_loras"] = loras

    text, markup = _build_krea2_lora_menu(settings, loras, 0)
    await reply_menu(query, text, markup)


# 每页 LoRA 按钮数（Telegram 单键盘最多 100 按钮，留出导航/返回行余量）
_KREA2_LORA_PAGE_SIZE = 10


def _build_krea2_lora_menu(settings: dict, loras: list, page: int) -> tuple[str, InlineKeyboardMarkup]:
    """构建 LoRA 选择菜单（含分页导航与当前选择标记）。"""
    current = (settings.get("comfy_krea2_lora_name") or "").strip()
    total_pages = max(1, (len(loras) + _KREA2_LORA_PAGE_SIZE - 1) // _KREA2_LORA_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * _KREA2_LORA_PAGE_SIZE
    end = min(start + _KREA2_LORA_PAGE_SIZE, len(loras))

    text = f"<b>选择 LoRA</b>（第 {page + 1}/{total_pages} 页，共 {len(loras)} 个）\n当前: "
    text += f"<code>{html.escape(current)}</code>" if current else "工作流默认"

    keyboard = []
    for i in range(start, end):
        name = loras[i]
        # 按钮文案上限 64 字符，超长截断（选择仍按索引，无功能损失）
        label = name if len(name) <= 60 else name[:57] + "…"
        prefix = "✓ " if name == current else ""
        keyboard.append([InlineKeyboardButton(
            f"{prefix}{label}", callback_data=f"comfy_krea2_lora_pick:{i}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            "⬅️ 上一页", callback_data=f"comfy_krea2_lora_page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(
            "➡️ 下一页", callback_data=f"comfy_krea2_lora_page:{page + 1}"))
    if nav:
        keyboard.append(nav)
    if current:
        keyboard.append([InlineKeyboardButton(
            "♻️ 恢复工作流默认", callback_data="comfy_krea2_lora_pick:default")])
    keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(keyboard)


async def show_comfy_krea2_lora_page(update, context):
    """LoRA 列表翻页。"""
    query = update.callback_query
    await safe_answer(query)
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    loras = context.user_data.get("_comfy_loras")
    if loras is None:
        await safe_answer(query, "LoRA 列表已过期，请重新打开列表", show_alert=True)
        return
    try:
        page = int(query.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await safe_answer(query, "无效页码", show_alert=True)
        return
    text, markup = _build_krea2_lora_menu(settings, loras, page)
    await reply_menu(query, text, markup)


async def pick_comfy_krea2_lora(update, context):
    """按索引选择 LoRA（或恢复工作流默认）。"""
    query = update.callback_query
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    payload = query.data.split(":", 2)[2]

    if payload == "default":
        settings["comfy_krea2_lora_name"] = ""
        _save_settings(context, user_id)
        await safe_answer(query, "已恢复为工作流默认 LoRA")
    else:
        loras = context.user_data.get("_comfy_loras", [])
        try:
            idx = int(payload)
            if not 0 <= idx < len(loras):
                raise IndexError
            lora_name = loras[idx]
        except (IndexError, ValueError):
            await safe_answer(query, "LoRA 列表已变化，请重新打开列表", show_alert=True)
            return
        settings["comfy_krea2_lora_name"] = lora_name
        _save_settings(context, user_id)
        await safe_answer(query, f"LoRA: {lora_name}")

    text, markup = _comfy_settings_menu(settings)
    await reply_menu(query, text, markup)


async def show_comfy_size_menu(update, context):
    """显示 ComfyUI 尺寸预设。"""
    query = update.callback_query
    await safe_answer(query)
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _comfy_size_menu(settings)
    await reply_menu(query, text, markup)


async def pick_comfy_size(update, context):
    """根据 key 设置尺寸。"""
    query = update.callback_query
    key = query.data.split(":", 1)[1]
    preset = COMFY_SIZE_PRESETS.get(key)
    if preset is None:
        await safe_answer(query, "无效的尺寸选择", show_alert=True)
        return

    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_width"] = preset["width"]
    settings["comfy_height"] = preset["height"]
    _save_settings(context, user_id)

    await safe_answer(query, f"尺寸: {preset['label']}")
    text, markup = _comfy_settings_menu(settings)
    await reply_menu(query, text, markup)


async def start_comfy_seed_input(update, context):
    """进入种子输入模式。"""
    query = update.callback_query
    await safe_answer(query)

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
    await safe_answer(query)
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _comfy_workflow_menu(settings)
    await reply_menu(query, text, markup)


async def pick_comfy_workflow(update, context):
    """选择 Workflow。"""
    query = update.callback_query
    wf_key = query.data.split(":", 1)[1]
    if wf_key not in COMFY_WORKFLOWS:
        await safe_answer(query, "无效的 Workflow 选择", show_alert=True)
        return

    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_workflow"] = wf_key
    # 切换 workflow 时同时更新默认模型
    wf_config = COMFY_WORKFLOWS[wf_key]
    settings["comfy_model"] = wf_config.get("default_model", "")
    _save_settings(context, user_id)

    # 清除 firstlast-video 多步交互状态（切换工作流时重置）
    # 注：generation 已 import 本模块，无法反向 import _clear_firstlast_state，就地清理
    if context.user_data:
        context.user_data.pop("_firstlast_start_frame", None)
        context.user_data.pop("_firstlast_end_frame", None)
        context.user_data.pop("_file_prompt", None)

    await safe_answer(query, f"Workflow: {wf_config['label']}")
    text, markup = _comfy_settings_menu(settings)
    await reply_menu(query, text, markup)


async def start_comfy_prompt_input(update, context):
    """进入自定义 Prompt 输入模式。"""
    query = update.callback_query
    await safe_answer(query)

    if context.user_data is None:
        await query.edit_message_text("当前不支持自定义 Prompt。")
        return

    user_id = get_user_id(update)
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
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_translate"] = not settings.get("comfy_translate", False)
    _save_settings(context, user_id)

    state = "ON" if settings["comfy_translate"] else "OFF"
    await safe_answer(query, f"翻译 · {state}")
    text, markup = _comfy_settings_menu(settings)
    await reply_menu(query, text, markup)


async def clear_comfy_prompt(update, context):
    """清除自定义 Prompt，恢复使用实时输入。"""
    query = update.callback_query
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_prompt"] = ""
    _save_settings(context, user_id)

    await safe_answer(query, "已清除 Prompt")
    text, markup = _comfy_settings_menu(settings)
    await reply_menu(query, text, markup)


async def start_comfy_face_prompt_input(update, context):
    """进入脸部提示词手动输入模式。"""
    query = update.callback_query
    await safe_answer(query)

    if context.user_data is None:
        await query.edit_message_text("当前不支持脸部提示词。")
        return

    user_id = get_user_id(update)
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
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_face_prompt"] = ""
    _save_settings(context, user_id)

    await safe_answer(query, "已恢复自动提取")
    text, markup = _comfy_settings_menu(settings)
    await reply_menu(query, text, markup)


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
    wf_key = settings.get("comfy_workflow", "")
    wf_config = COMFY_WORKFLOWS.get(wf_key, {})
    auto_aspect = bool(wf_config.get("video_megapixels_node"))
    text = "<b>选择视频画质</b>"
    keyboard = []
    for key, preset in COMFY_VIDEO_RESOLUTIONS.items():
        prefix = "✓ " if key == current else ""
        suffix = "" if auto_aspect else f" ({compute_video_dimensions(aspect, key)[0]}×{compute_video_dimensions(aspect, key)[1]})"
        keyboard.append([InlineKeyboardButton(
            f"{prefix}{preset['label']}{suffix}", callback_data=f"comfy_video_resolution:{key}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])
    return text, InlineKeyboardMarkup(keyboard)


def _comfy_video_length_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    default_cfg = COMFY_VIDEO_FRAMES_PRESETS[DEFAULT_VIDEO_FRAMES_KEY]
    current = settings.get("comfy_video_frames", default_cfg["frames"])
    if current not in {p["frames"] for p in COMFY_VIDEO_FRAMES_PRESETS.values()}:
        current = default_cfg["frames"]
    current_label = next(p["label"] for p in COMFY_VIDEO_FRAMES_PRESETS.values()
                         if p["frames"] == current)
    text = f"<b>选择视频长度</b>\n当前: {current_label}"
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
    await safe_answer(query)
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _comfy_video_aspect_menu(settings)
    await reply_menu(query, text, markup)


async def pick_comfy_video_aspect(update, context):
    """选择视频比例。"""
    query = update.callback_query
    aspect = query.data.split(":", 1)[1]
    if aspect not in COMFY_VIDEO_ASPECTS:
        await safe_answer(query, "无效比例", show_alert=True)
        return
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_video_aspect"] = aspect
    _save_settings(context, user_id)
    label = COMFY_VIDEO_ASPECTS[aspect]["label"]
    await safe_answer(query, f"视频比例: {label}")
    text, markup = _comfy_settings_menu(settings)
    await reply_menu(query, text, markup)


async def show_comfy_video_resolution_menu(update, context):
    """显示视频画质选择菜单。"""
    query = update.callback_query
    await safe_answer(query)
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _comfy_video_resolution_menu(settings)
    await reply_menu(query, text, markup)


async def pick_comfy_video_resolution(update, context):
    """选择视频画质。"""
    query = update.callback_query
    resolution = query.data.split(":", 1)[1]
    if resolution not in COMFY_VIDEO_RESOLUTIONS:
        await safe_answer(query, "无效画质", show_alert=True)
        return
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_video_resolution"] = resolution
    _save_settings(context, user_id)
    label = COMFY_VIDEO_RESOLUTIONS[resolution]["label"]
    await safe_answer(query, f"视频画质: {label}")
    text, markup = _comfy_settings_menu(settings)
    await reply_menu(query, text, markup)


async def show_comfy_video_length_menu(update, context):
    """显示视频长度选择菜单。"""
    query = update.callback_query
    await safe_answer(query)
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _comfy_video_length_menu(settings)
    await reply_menu(query, text, markup)


async def pick_comfy_video_length(update, context):
    """选择视频长度。"""
    query = update.callback_query
    key = query.data.split(":", 1)[1]
    preset = COMFY_VIDEO_FRAMES_PRESETS.get(key)
    if preset is None:
        await safe_answer(query, "无效时长", show_alert=True)
        return
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_video_frames"] = preset["frames"]
    _save_settings(context, user_id)
    await safe_answer(query, f"视频长度: {preset['label']}")
    text, markup = _comfy_settings_menu(settings)
    await reply_menu(query, text, markup)


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
    await safe_answer(query)
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _comfy_lora_variant_menu(settings)
    await reply_menu(query, text, markup)


async def pick_comfy_lora_variant(update, context):
    """选择 LoRA 变体。"""
    query = update.callback_query
    variant = query.data.split(":", 1)[1]
    if variant not in COMFY_LORA_VARIANTS:
        await safe_answer(query, "无效变体", show_alert=True)
        return
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_lora_variant"] = variant
    _save_settings(context, user_id)
    label = COMFY_LORA_VARIANTS[variant]["label"]
    await safe_answer(query, f"LoRA 变体: {label}")
    text, markup = _comfy_settings_menu(settings)
    await reply_menu(query, text, markup)


async def pick_comfy_lora_variant_fast(update, context):
    """从生成后菜单快速切换 LoRA 变体，更新键盘高亮。"""
    query = update.callback_query
    variant = query.data.split(":", 1)[1]
    if variant not in COMFY_LORA_VARIANTS:
        await safe_answer(query, "无效变体", show_alert=True)
        return
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_lora_variant"] = variant
    _save_settings(context, user_id)
    label = COMFY_LORA_VARIANTS[variant]["label"]
    await safe_answer(query, f"LoRA 变体: {label}")
    await _update_gen_keyboard(query, settings)



def _extract_log_context_id(query) -> str | None:
    """从当前消息键盘解析 log_gen_ 按钮携带的 context_id。"""
    markup = query.message.reply_markup
    if markup:
        for row in markup.inline_keyboard:
            for btn in row:
                data = btn.callback_data or ""
                if data.startswith("log_gen_"):
                    return data.replace("log_gen_", "")
    return None


async def _update_gen_keyboard(query, settings):
    """刷新生成后菜单键盘（各 fast handler 公用）。"""
    context_id = _extract_log_context_id(query)
    markup = comfy_generation_menu(context_id or "", settings=settings)
    try:
        await query.message.edit_reply_markup(markup)
    except Exception:
        pass


# ═══ Toggle 开关（通用工厂，生成 settings 菜单和 fast 两个版本） ═══

def _normalize_optimize_mode(value) -> str:
    """兼容旧布尔值，返回三态字符串（off/nsfw/sfw）。"""
    if isinstance(value, bool):
        return "nsfw" if value else "off"
    return value if value in COMFY_PROMPT_OPTIMIZE_MODES else "nsfw"


def _make_cycle_handler(fast: bool = False):
    """提示词优化三态循环 handler：off → nsfw → sfw → off。"""
    async def handler(update, context):
        query = update.callback_query
        user_id = get_user_id(update)
        settings = _ensure_settings(context, user_id)
        current = _normalize_optimize_mode(settings.get("comfy_prompt_optimize", "nsfw"))
        idx = COMFY_PROMPT_OPTIMIZE_CYCLE.index(current)
        nxt = COMFY_PROMPT_OPTIMIZE_CYCLE[(idx + 1) % len(COMFY_PROMPT_OPTIMIZE_CYCLE)]
        settings["comfy_prompt_optimize"] = nxt
        _save_settings(context, user_id)
        await safe_answer(query, f"提示词优化 · {COMFY_PROMPT_OPTIMIZE_MODES[nxt]['label']}")
        if fast:
            await _update_gen_keyboard(query, settings)
        else:
            text, markup = _comfy_settings_menu(settings)
            await reply_menu(query, text, markup)
    return handler


def _make_toggle_handler(key: str, default: bool, label: str, fast: bool = False):
    """生成 toggle handler，避免 8 个重复函数。fast=True 仅刷新键盘不发消息。"""
    async def handler(update, context):
        query = update.callback_query
        user_id = get_user_id(update)
        settings = _ensure_settings(context, user_id)
        settings[key] = not settings.get(key, default)
        _save_settings(context, user_id)
        state = "ON" if settings[key] else "OFF"
        await safe_answer(query, f"{label} · {state}")
        if fast:
            await _update_gen_keyboard(query, settings)
        else:
            text, markup = _comfy_settings_menu(settings)
            await reply_menu(query, text, markup)
    return handler


async def start_comfy_krea2_lora_strength(update, context):
    """进入 LoRA 强度输入模式"""
    query = update.callback_query
    if context.user_data is None:
        await safe_answer(query, "会话已过期，请重新发送 /start")
        return
    context.user_data["_waiting_input"] = "comfy_krea2_lora_strength"
    await safe_answer(query, "请输入 LoRA 强度")
    await query.message.reply_text(
        "请输入 LoRA 强度值（范围 -15 ~ 10，默认 5）：\n"
        "发送 /cancel 取消操作"
    )


async def start_comfy_krea2_lora_trigger(update, context):
    """进入 LoRA 触发词输入模式"""
    query = update.callback_query
    if context.user_data is None:
        await safe_answer(query, "会话已过期，请重新发送 /start")
        return
    context.user_data["_waiting_input"] = "comfy_krea2_lora_trigger"
    await safe_answer(query, "请输入 LoRA 触发词")
    await query.message.reply_text(
        "请输入该 LoRA 的触发词（会拼接到提示词末尾）：\n"
        "• 回复「清除」可清空已设置的触发词\n"
        "• 发送 /cancel 取消操作"
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
        CallbackQueryHandler(auth_callback(start_comfy_krea2_lora_trigger),
                             pattern=r"^comfy_krea2_lora_trigger$"),
        CallbackQueryHandler(auth_callback(show_comfy_krea2_lora_menu),
                             pattern=r"^comfy_krea2_lora_pick$"),
        CallbackQueryHandler(auth_callback(show_comfy_krea2_lora_page),
                             pattern=r"^comfy_krea2_lora_page:\d+$"),
        CallbackQueryHandler(auth_callback(pick_comfy_krea2_lora),
                             pattern=r"^comfy_krea2_lora_pick:(default|\d+)$"),
        # 提示词优化三态循环（off → nsfw → sfw）
        CallbackQueryHandler(auth_callback(_make_cycle_handler()),
                             pattern=r"^comfy_prompt_optimize_cycle$"),
        CallbackQueryHandler(auth_callback(_make_cycle_handler(fast=True)),
                             pattern=r"^comfy_prompt_optimize_cycle_gen$"),
        # SD Upscale 提示词注入开关
        CallbackQueryHandler(auth_callback(_make_toggle_handler("comfy_sd_upscale_prompt_inject", True, "放大提示词注入")),
                             pattern=r"^comfy_sd_upscale_prompt_toggle$"),
        CallbackQueryHandler(auth_callback(_make_toggle_handler("comfy_sd_upscale_prompt_inject", True, "放大提示词注入", fast=True)),
                             pattern=r"^comfy_sd_upscale_prompt_toggle_gen$"),
        # 脸部提示词
        CallbackQueryHandler(auth_callback(start_comfy_face_prompt_input),
                             pattern=r"^comfy_face_prompt_set$"),
        CallbackQueryHandler(auth_callback(clear_comfy_face_prompt),
                             pattern=r"^comfy_face_prompt_clear$"),
        # Krea2 LoRA 强度
        CallbackQueryHandler(auth_callback(start_comfy_krea2_lora_strength),
                             pattern=r"^comfy_krea2_lora_strength$"),
    ]
