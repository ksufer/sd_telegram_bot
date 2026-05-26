from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler
from config import SIZE_PRESETS
from services import sd_api


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


def _settings_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    size_label = f"{settings['width']} × {settings['height']}"
    model_label = settings["model"] or "默认"
    if len(model_label) > 22:
        model_label = model_label[:19] + "..."

    hires_icon = "已启用" if settings["hires_fix"] else "已关闭"
    translate_icon = "已启用" if settings["translate"] else "已关闭"
    seed_label = str(settings["seed"]) if settings["seed"] != -1 else "随机"

    text = (
        "<b>参数设置</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"   尺寸   <code>{size_label}</code>\n"
        f"   模型   <code>{model_label}</code>\n"
        "━━━━━━━━━━━━━━\n"
        f"   高清修复   {hires_icon}\n"
        f"   中译英       {translate_icon}\n"
        f"   种子          <code>{seed_label}</code>\n"
        f"   Steps       <code>{settings['steps']}</code>\n"
        f"   CFG         <code>{settings['cfg_scale']}</code>"
    )

    keyboard = [
        [
            InlineKeyboardButton(" 尺寸", callback_data="set_size"),
            InlineKeyboardButton(" 模型", callback_data="set_model"),
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
        prefix = "" if active else ""
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
        prefix = "" if active else ""
        display = name if len(name) <= 30 else name[:27] + "..."
        keyboard.append([InlineKeyboardButton(
            f"{prefix}{display}", callback_data=f"pick_model_{name}"
        )])
    keyboard.append([InlineKeyboardButton("返回", callback_data="settings_back")])
    return text, InlineKeyboardMarkup(keyboard)


# ═══ 回调处理 ═══

async def show_main_menu(update, context):
    text, markup = _main_menu()
    await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


async def show_settings(update, context):
    query = update.callback_query
    await query.answer()
    settings = _ensure_settings(context)
    text, markup = _settings_menu(settings)
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


async def show_size_menu(update, context):
    query = update.callback_query
    await query.answer()
    settings = _ensure_settings(context)
    text, markup = _size_menu(settings)
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


async def pick_size(update, context):
    query = update.callback_query
    data = query.data
    w, h = data.replace("pick_size_", "").split("x")
    settings = _ensure_settings(context)
    settings["width"] = int(w)
    settings["height"] = int(h)
    await query.answer(f"已切换至 {w} × {h}")
    text, markup = _settings_menu(settings)
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


async def show_model_menu(update, context):
    query = update.callback_query
    await query.answer()
    settings = _ensure_settings(context)
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
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


async def pick_model(update, context):
    query = update.callback_query
    model_name = query.data.replace("pick_model_", "")
    settings = _ensure_settings(context)
    settings["model"] = model_name
    try:
        await sd_api.set_model(model_name)
        await query.answer(f"模型切换中：{model_name}")
    except Exception:
        await query.answer("模型切换失败", show_alert=True)
    text, markup = _settings_menu(settings)
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


async def toggle_hires(update, context):
    query = update.callback_query
    settings = _ensure_settings(context)
    settings["hires_fix"] = not settings["hires_fix"]
    state = "ON" if settings["hires_fix"] else "OFF"
    await query.answer(f"高清修复 · {state}")
    text, markup = _settings_menu(settings)
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


async def toggle_translate(update, context):
    query = update.callback_query
    settings = _ensure_settings(context)
    settings["translate"] = not settings["translate"]
    state = "ON" if settings["translate"] else "OFF"
    await query.answer(f"中译英 · {state}")
    text, markup = _settings_menu(settings)
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


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


def _ensure_settings(context) -> dict:
    return context.user_data.setdefault("settings", _default_settings())


def _default_settings() -> dict:
    from config import DEFAULT_USER_SETTINGS
    import copy
    return copy.deepcopy(DEFAULT_USER_SETTINGS)


# ═══ Handler 注册 ═══

def get_handlers() -> list:
    return [
        CommandHandler("start", show_main_menu),
        CommandHandler("help", show_main_menu),
        CallbackQueryHandler(show_settings, pattern="^settings_menu$|^settings_back$"),
        CallbackQueryHandler(show_size_menu, pattern="^set_size$"),
        CallbackQueryHandler(pick_size, pattern="^pick_size_"),
        CallbackQueryHandler(show_model_menu, pattern="^set_model$"),
        CallbackQueryHandler(pick_model, pattern="^pick_model_"),
        CallbackQueryHandler(toggle_hires, pattern="^toggle_hires$"),
        CallbackQueryHandler(toggle_translate, pattern="^toggle_translate$"),
        CallbackQueryHandler(start_seed_input, pattern="^set_seed$"),
        CallbackQueryHandler(close_menu, pattern="^close_menu$"),
    ]
