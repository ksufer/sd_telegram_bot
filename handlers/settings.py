import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler

from config import SIZE_PRESETS, DEFAULT_USER_SETTINGS
from services import sd_api, storage
from handlers import _user_auth_filter, auth_callback

logger = logging.getLogger(__name__)


# ═══ 键盘构建 ═══

def _main_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "<b>SD 绘图助手</b>\n"
        "直接发送描述词即可生成图片。\n"
        "使用下方菜单调整参数。"
    )
    keyboard = [
        [InlineKeyboardButton("参数设置", callback_data="settings_menu")],
        [InlineKeyboardButton("关闭菜单", callback_data="close_menu")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


def _generation_menu(context_id: str) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("参数设置", callback_data="settings_menu"),
            InlineKeyboardButton("关闭菜单", callback_data="close_menu"),
        ],
        [
            InlineKeyboardButton("用本图提示词", callback_data=f"reuse_prompt_{context_id}"),
            InlineKeyboardButton("用本图种子", callback_data=f"reuse_seed_{context_id}"),
            InlineKeyboardButton("🎲", callback_data="random_seed"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def _settings_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    size_label = f"{settings['width']} × {settings['height']}"
    model_label = settings["model"] or "默认"
    if len(model_label) > 22:
        model_label = model_label[:19] + "..."

    hires_icon = "已启用" if settings["hires_fix"] else "已关闭"
    translate_icon = "已启用" if settings["translate"] else "已关闭"
    restore_icon = "已启用" if settings.get("restore_faces") else "已关闭"
    tiling_icon = "已启用" if settings.get("tiling") else "已关闭"
    seed_label = str(settings["seed"]) if settings["seed"] != -1 else "随机"
    sampler_label = settings.get("sampler", "Euler a")
    clip_skip_label = str(settings.get("clip_skip", 1))

    text = (
        "<b>参数设置</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"   尺寸       <code>{size_label}</code>\n"
        f"   模型       <code>{model_label}</code>\n"
        f"   采样器     <code>{sampler_label}</code>\n"
        "━━━━━━━━━━━━━━\n"
        f"   高清修复   {hires_icon}\n"
        f"   中译英     {translate_icon}\n"
        f"   面部修复   {restore_icon}\n"
        f"   平铺模式   {tiling_icon}\n"
        f"   CLIP Skip  <code>{clip_skip_label}</code>\n"
        f"   种子       <code>{seed_label}</code>\n"
        f"   Steps      <code>{settings['steps']}</code>\n"
        f"   CFG        <code>{settings['cfg_scale']}</code>"
    )

    keyboard = [
        [
            InlineKeyboardButton(" 尺寸", callback_data="set_size"),
            InlineKeyboardButton(" 模型", callback_data="set_model"),
        ],
        [
            InlineKeyboardButton(" 采样器", callback_data="set_sampler"),
            InlineKeyboardButton(" CLIP Skip", callback_data="set_clip_skip"),
        ],
        [
            InlineKeyboardButton(" Steps", callback_data="set_steps"),
            InlineKeyboardButton(" CFG", callback_data="set_cfg"),
        ],
        [
            InlineKeyboardButton(
                f"{' 高清修复 · ON' if settings['hires_fix'] else ' 高清修复 · OFF'}",
                callback_data="toggle_hires",
            ),
        ],
        [
            InlineKeyboardButton(
                f"{' 中译英 · ON' if settings['translate'] else ' 中译英 · OFF'}",
                callback_data="toggle_translate",
            ),
        ],
        [
            InlineKeyboardButton(
                f"{' 面部修复 · ON' if settings.get('restore_faces') else ' 面部修复 · OFF'}",
                callback_data="toggle_restore_faces",
            ),
        ],
        [
            InlineKeyboardButton(
                f"{' 平铺模式 · ON' if settings.get('tiling') else ' 平铺模式 · OFF'}",
                callback_data="toggle_tiling",
            ),
        ],
        [
            InlineKeyboardButton(" 种子", callback_data="set_seed"),
            InlineKeyboardButton(" 关闭", callback_data="close_menu"),
        ],
    ]
    return text, InlineKeyboardMarkup(keyboard)


def _size_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    current = f"{settings['width']} × {settings['height']}"
    text = f"<b> 选择尺寸</b>\n当前：<code>{current}</code>"

    keyboard = []
    for label, (w, h) in SIZE_PRESETS.items():
        active = settings["width"] == w and settings["height"] == h
        prefix = "✓ " if active else ""
        btn_label = f"{prefix}{label}"
        keyboard.append([InlineKeyboardButton(btn_label, callback_data=f"pick_size_{w}x{h}")])
    keyboard.append([InlineKeyboardButton("返回", callback_data="settings_back")])
    return text, InlineKeyboardMarkup(keyboard)


def _model_menu(settings: dict, models: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    current = settings["model"]
    text = f"<b> 选择模型</b>\n当前：<code>{current or '默认'}</code>"

    keyboard = []
    for m in models:
        name = m["model_name"]
        active = current == name
        prefix = "✓ " if active else ""
        display = name if len(name) <= 30 else name[:27] + "..."
        keyboard.append([InlineKeyboardButton(
            f"{prefix}{display}", callback_data=f"pick_model_{name}"
        )])
    keyboard.append([InlineKeyboardButton("返回", callback_data="settings_back")])
    return text, InlineKeyboardMarkup(keyboard)


def _sampler_menu(settings: dict, samplers: list[str]) -> tuple[str, InlineKeyboardMarkup]:
    current = settings.get("sampler", "Euler a")
    text = f"<b> 选择采样器</b>\n当前：<code>{current}</code>"

    keyboard = []
    for name in samplers:
        active = current == name
        prefix = "✓ " if active else ""
        display = name if len(name) <= 25 else name[:22] + "..."
        keyboard.append([InlineKeyboardButton(
            f"{prefix}{display}", callback_data=f"pick_sampler_{name}"
        )])
    keyboard.append([InlineKeyboardButton("返回", callback_data="settings_back")])
    return text, InlineKeyboardMarkup(keyboard)


def _clip_skip_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    current = settings.get("clip_skip", 1)
    text = f"<b> CLIP Skip</b>\n当前：<code>{current}</code>\n值越大，提示词影响越强。"

    keyboard = []
    row = []
    for v in range(1, 13):
        prefix = "✓ " if current == v else ""
        row.append(InlineKeyboardButton(
            f"{prefix}{v}", callback_data=f"pick_clip_skip_{v}"
        ))
        if len(row) == 4:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("返回", callback_data="settings_back")])
    return text, InlineKeyboardMarkup(keyboard)


def _steps_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    current = settings["steps"]
    text = f"<b> Steps</b>\n当前：<code>{current}</code>"
    keyboard = []
    row = []
    for v in [20, 25, 30, 35, 40]:
        prefix = "✓ " if current == v else ""
        row.append(InlineKeyboardButton(f"{prefix}{v}", callback_data=f"pick_steps_{v}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("返回", callback_data="settings_back")])
    return text, InlineKeyboardMarkup(keyboard)


def _cfg_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    current = settings["cfg_scale"]
    text = f"<b> CFG Scale</b>\n当前：<code>{current}</code>"
    keyboard = []
    row = []
    for v in [2, 3, 4, 5, 7, 9]:
        prefix = "✓ " if current == v else ""
        row.append(InlineKeyboardButton(f"{prefix}{v}", callback_data=f"pick_cfg_{v}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("返回", callback_data="settings_back")])
    return text, InlineKeyboardMarkup(keyboard)


async def _reply_menu(query, text: str, markup):
    try:
        await query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except BadRequest:
        await query.answer()
        await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


# ═══ 回调处理 ═══

async def show_main_menu(update, context):
    text, markup = _main_menu()
    await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


def _get_user_id(update) -> int:
    return update.effective_user.id


async def show_settings(update, context):
    query = update.callback_query
    await query.answer()
    settings = _ensure_settings(context, _get_user_id(update))
    text, markup = _settings_menu(settings)
    await _reply_menu(query, text, markup)


async def show_size_menu(update, context):
    query = update.callback_query
    await query.answer()
    settings = _ensure_settings(context, _get_user_id(update))
    text, markup = _size_menu(settings)
    await _reply_menu(query, text, markup)


async def pick_size(update, context):
    query = update.callback_query
    data = query.data
    w, h = data.replace("pick_size_", "").split("x")
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["width"] = int(w)
    settings["height"] = int(h)
    _save_settings(context, user_id)
    await query.answer(f"已切换至 {w} × {h}")
    text, markup = _settings_menu(settings)
    await _reply_menu(query, text, markup)


async def show_model_menu(update, context):
    query = update.callback_query
    await query.answer()
    settings = _ensure_settings(context, _get_user_id(update))
    try:
        models = await sd_api.get_models()
    except Exception:
        await query.edit_message_text(
            " 无法获取模型列表，请确认 SD 服务已启动。",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("返回", callback_data="settings_back")
            ]]),
        )
        return
    text, markup = _model_menu(settings, models)
    await _reply_menu(query, text, markup)


async def pick_model(update, context):
    query = update.callback_query
    model_name = query.data.replace("pick_model_", "")
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["model"] = model_name
    _save_settings(context, user_id)
    try:
        await sd_api.set_model(model_name)
        await query.answer(f"模型切换中：{model_name}")
    except Exception:
        await query.answer("模型切换失败", show_alert=True)
    text, markup = _settings_menu(settings)
    await _reply_menu(query, text, markup)


async def toggle_hires(update, context):
    query = update.callback_query
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["hires_fix"] = not settings["hires_fix"]
    _save_settings(context, user_id)
    state = "ON" if settings["hires_fix"] else "OFF"
    await query.answer(f"高清修复 · {state}")
    text, markup = _settings_menu(settings)
    await _reply_menu(query, text, markup)


async def toggle_translate(update, context):
    query = update.callback_query
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["translate"] = not settings["translate"]
    _save_settings(context, user_id)
    state = "ON" if settings["translate"] else "OFF"
    await query.answer(f"中译英 · {state}")
    text, markup = _settings_menu(settings)
    await _reply_menu(query, text, markup)


async def start_seed_input(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["_waiting_seed"] = True
    await query.edit_message_text(
        "请输入种子值（-1 为随机）：\n<code>/cancel</code> 取消",
        parse_mode="HTML",
    )


async def close_menu(update, context):
    query = update.callback_query
    await query.answer()
    await query.delete_message()


# ═══ Steps/CFG 回调 ═══

async def show_steps_menu(update, context):
    query = update.callback_query
    await query.answer()
    settings = _ensure_settings(context, _get_user_id(update))
    text, markup = _steps_menu(settings)
    await _reply_menu(query, text, markup)


async def pick_steps(update, context):
    query = update.callback_query
    value = int(query.data.replace("pick_steps_", ""))
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["steps"] = value
    _save_settings(context, user_id)
    await query.answer(f"Steps = {value}")
    text, markup = _settings_menu(settings)
    await _reply_menu(query, text, markup)


async def show_cfg_menu(update, context):
    query = update.callback_query
    await query.answer()
    settings = _ensure_settings(context, _get_user_id(update))
    text, markup = _cfg_menu(settings)
    await _reply_menu(query, text, markup)


async def pick_cfg(update, context):
    query = update.callback_query
    value = int(query.data.replace("pick_cfg_", ""))
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["cfg_scale"] = value
    _save_settings(context, user_id)
    await query.answer(f"CFG = {value}")
    text, markup = _settings_menu(settings)
    await _reply_menu(query, text, markup)


# ═══ 新参数回调 ═══

async def show_sampler_menu(update, context):
    query = update.callback_query
    await query.answer()
    settings = _ensure_settings(context, _get_user_id(update))
    samplers = await sd_api.get_samplers()
    text, markup = _sampler_menu(settings, samplers)
    await _reply_menu(query, text, markup)


async def pick_sampler(update, context):
    query = update.callback_query
    sampler_name = query.data.replace("pick_sampler_", "")
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["sampler"] = sampler_name
    _save_settings(context, user_id)
    await query.answer(f"采样器：{sampler_name}")
    text, markup = _settings_menu(settings)
    await _reply_menu(query, text, markup)


async def toggle_restore_faces(update, context):
    query = update.callback_query
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["restore_faces"] = not settings.get("restore_faces", False)
    _save_settings(context, user_id)
    state = "ON" if settings["restore_faces"] else "OFF"
    await query.answer(f"面部修复 · {state}")
    text, markup = _settings_menu(settings)
    await _reply_menu(query, text, markup)


async def toggle_tiling(update, context):
    query = update.callback_query
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["tiling"] = not settings.get("tiling", False)
    _save_settings(context, user_id)
    state = "ON" if settings["tiling"] else "OFF"
    await query.answer(f"平铺模式 · {state}")
    text, markup = _settings_menu(settings)
    await _reply_menu(query, text, markup)


async def show_clip_skip_menu(update, context):
    query = update.callback_query
    await query.answer()
    settings = _ensure_settings(context, _get_user_id(update))
    text, markup = _clip_skip_menu(settings)
    await _reply_menu(query, text, markup)


async def pick_clip_skip(update, context):
    query = update.callback_query
    value = int(query.data.replace("pick_clip_skip_", ""))
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["clip_skip"] = value
    _save_settings(context, user_id)
    await query.answer(f"CLIP Skip = {value}")
    text, markup = _settings_menu(settings)
    await _reply_menu(query, text, markup)


# ═══ 重用提示词/种子回调 ═══

async def reuse_prompt(update, context):
    query = update.callback_query
    context_id = query.data.replace("reuse_prompt_", "")
    ctx = context.bot_data.get("_gen_context", {}).get(context_id)
    if ctx:
        from config import DEFAULT_PROMPT_PREFIX
        full_prompt = f"{DEFAULT_PROMPT_PREFIX} {ctx['translated']}"
        await query.answer()
        await query.message.reply_text(full_prompt)
    else:
        await query.answer("上下文已过期", show_alert=True)


async def reuse_seed(update, context):
    query = update.callback_query
    context_id = query.data.replace("reuse_seed_", "")
    ctx = context.bot_data.get("_gen_context", {}).get(context_id)
    if ctx:
        user_id = _get_user_id(update)
        settings = _ensure_settings(context, user_id)
        settings["seed"] = ctx["seed"]
        _save_settings(context, user_id)
        await query.answer(f"种子已设为 {ctx['seed']}")
    else:
        await query.answer("上下文已过期", show_alert=True)


async def random_seed(update, context):
    query = update.callback_query
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["seed"] = -1
    _save_settings(context, user_id)
    await query.answer("种子已设为随机")


def _ensure_settings(context, user_id: int) -> dict:
    if "settings" not in context.user_data:
        context.user_data["settings"] = storage.load(user_id, DEFAULT_USER_SETTINGS)
    return context.user_data["settings"]


def _save_settings(context, user_id: int) -> None:
    storage.save(user_id, context.user_data.get("settings", {}))


# ═══ Handler 注册 ═══

def get_handlers() -> list:
    return [
        CommandHandler("start", show_main_menu, filters=_user_auth_filter()),
        CommandHandler("help", show_main_menu, filters=_user_auth_filter()),
        CallbackQueryHandler(auth_callback(show_settings), pattern="^settings_menu$|^settings_back$"),
        CallbackQueryHandler(auth_callback(show_size_menu), pattern="^set_size$"),
        CallbackQueryHandler(auth_callback(pick_size), pattern="^pick_size_"),
        CallbackQueryHandler(auth_callback(show_model_menu), pattern="^set_model$"),
        CallbackQueryHandler(auth_callback(pick_model), pattern="^pick_model_"),
        CallbackQueryHandler(auth_callback(show_sampler_menu), pattern="^set_sampler$"),
        CallbackQueryHandler(auth_callback(pick_sampler), pattern="^pick_sampler_"),
        CallbackQueryHandler(auth_callback(toggle_hires), pattern="^toggle_hires$"),
        CallbackQueryHandler(auth_callback(toggle_translate), pattern="^toggle_translate$"),
        CallbackQueryHandler(auth_callback(toggle_restore_faces), pattern="^toggle_restore_faces$"),
        CallbackQueryHandler(auth_callback(toggle_tiling), pattern="^toggle_tiling$"),
        CallbackQueryHandler(auth_callback(show_steps_menu), pattern="^set_steps$"),
        CallbackQueryHandler(auth_callback(pick_steps), pattern="^pick_steps_"),
        CallbackQueryHandler(auth_callback(show_cfg_menu), pattern="^set_cfg$"),
        CallbackQueryHandler(auth_callback(pick_cfg), pattern="^pick_cfg_"),
        CallbackQueryHandler(auth_callback(show_clip_skip_menu), pattern="^set_clip_skip$"),
        CallbackQueryHandler(auth_callback(pick_clip_skip), pattern="^pick_clip_skip_"),
        CallbackQueryHandler(auth_callback(start_seed_input), pattern="^set_seed$"),
        CallbackQueryHandler(auth_callback(close_menu), pattern="^close_menu$"),
        CallbackQueryHandler(auth_callback(reuse_prompt), pattern="^reuse_prompt_"),
        CallbackQueryHandler(auth_callback(reuse_seed), pattern="^reuse_seed_"),
        CallbackQueryHandler(auth_callback(random_seed), pattern="^random_seed$"),
    ]
