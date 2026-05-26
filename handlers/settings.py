from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ConversationHandler, CommandHandler, MessageHandler, filters
from config import SIZE_PRESETS
from services import sd_api

# 用于种子输入的状态
WAITING_SEED = 1

# ---- 键盘构建 ----

def _settings_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    """构建设置菜单消息和键盘。"""
    size_label = f"{settings['width']}×{settings['height']}"
    model_label = settings["model"] or "(当前模型)"
    hires_label = "开" if settings["hires_fix"] else "关"
    seed_label = str(settings["seed"]) if settings["seed"] != -1 else "随机"
    translate_label = "开" if settings["translate"] else "关"

    text = (
        "<b>当前参数</b>\n\n"
        f"尺寸: {size_label}\n"
        f"模型: {model_label}\n"
        f"高清修复: {hires_label}\n"
        f"种子: {seed_label}\n"
        f"中译英: {translate_label}\n"
        f"Steps: {settings['steps']}\n"
        f"CFG Scale: {settings['cfg_scale']}\n"
    )

    keyboard = [
        [InlineKeyboardButton(f"尺寸: {size_label}", callback_data="set_size")],
        [InlineKeyboardButton(f"模型: {model_label}", callback_data="set_model")],
        [
            InlineKeyboardButton(f"高清修复: {hires_label}", callback_data="toggle_hires"),
            InlineKeyboardButton(f"中译英: {translate_label}", callback_data="toggle_translate"),
        ],
        [InlineKeyboardButton(f"种子: {seed_label}", callback_data="set_seed")],
        [InlineKeyboardButton("关闭菜单", callback_data="close_menu")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


def _size_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    """构建尺寸选择菜单。"""
    current_size = f"{settings['width']}×{settings['height']}"
    text = f"<b>选择尺寸</b> (当前: {current_size})"

    keyboard = []
    for label, (w, h) in SIZE_PRESETS.items():
        mark = "✓ " if settings["width"] == w and settings["height"] == h else ""
        keyboard.append([InlineKeyboardButton(
            f"{mark}{label}", callback_data=f"pick_size_{w}x{h}"
        )])
    keyboard.append([InlineKeyboardButton("« 返回", callback_data="settings_back")])
    return text, InlineKeyboardMarkup(keyboard)


def _model_menu(settings: dict, models: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    """构建模型选择菜单。"""
    current = settings["model"]
    text = f"<b>选择模型</b> (当前: {current or '未指定'})"

    keyboard = []
    for m in models:
        name = m["model_name"]
        mark = "✓ " if current == name else ""
        # 截断过长的模型名
        display = name if len(name) <= 35 else name[:32] + "..."
        keyboard.append([InlineKeyboardButton(
            f"{mark}{display}", callback_data=f"pick_model_{name}"
        )])
    keyboard.append([InlineKeyboardButton("« 返回", callback_data="settings_back")])
    return text, InlineKeyboardMarkup(keyboard)


def _main_keyboard() -> InlineKeyboardMarkup:
    """构建主菜单键盘（/start 时显示）。"""
    keyboard = [
        [InlineKeyboardButton("参数设置", callback_data="settings_menu")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ---- 回调处理 ----

async def show_main_menu(update, context):
    """显示主欢迎菜单。"""
    await update.message.reply_text(
        "欢迎使用 SD 绘图 Bot！\n\n"
        "直接发送提示词即可生成图片。\n"
        "点击下方按钮配置参数。",
        reply_markup=_main_keyboard(),
    )


async def show_settings(update, context):
    """显示参数设置菜单。"""
    query = update.callback_query
    await query.answer()
    settings = context.user_data.setdefault("settings", _default_settings())
    text, markup = _settings_menu(settings)
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


async def show_size_menu(update, context):
    """显示尺寸选择菜单。"""
    query = update.callback_query
    await query.answer()
    settings = context.user_data.setdefault("settings", _default_settings())
    text, markup = _size_menu(settings)
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


async def pick_size(update, context):
    """选择尺寸。"""
    query = update.callback_query
    data = query.data  # pick_size_512x768
    w, h = data.replace("pick_size_", "").split("x")
    settings = context.user_data.setdefault("settings", _default_settings())
    settings["width"] = int(w)
    settings["height"] = int(h)
    await query.answer(f"尺寸已设为 {w}×{h}")
    text, markup = _settings_menu(settings)
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


async def show_model_menu(update, context):
    """显示模型选择菜单。"""
    query = update.callback_query
    await query.answer()
    settings = context.user_data.setdefault("settings", _default_settings())
    try:
        models = await sd_api.get_models()
    except Exception:
        await query.edit_message_text(
            "无法获取模型列表，请检查 SD 服务是否运行。",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« 返回", callback_data="settings_back")
            ]]),
        )
        return
    text, markup = _model_menu(settings, models)
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


async def pick_model(update, context):
    """选择模型。"""
    query = update.callback_query
    model_name = query.data.replace("pick_model_", "")
    settings = context.user_data.setdefault("settings", _default_settings())
    settings["model"] = model_name
    try:
        await sd_api.set_model(model_name)
        await query.answer(f"正在切换模型: {model_name}")
    except Exception:
        await query.answer("切换模型失败", show_alert=True)
    text, markup = _settings_menu(settings)
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


async def toggle_hires(update, context):
    """切换高清修复开关。"""
    query = update.callback_query
    settings = context.user_data.setdefault("settings", _default_settings())
    settings["hires_fix"] = not settings["hires_fix"]
    state = "开" if settings["hires_fix"] else "关"
    await query.answer(f"高清修复已设为: {state}")
    text, markup = _settings_menu(settings)
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


async def toggle_translate(update, context):
    """切换中译英开关。"""
    query = update.callback_query
    settings = context.user_data.setdefault("settings", _default_settings())
    settings["translate"] = not settings["translate"]
    state = "开" if settings["translate"] else "关"
    await query.answer(f"中译英已设为: {state}")
    text, markup = _settings_menu(settings)
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


async def start_seed_input(update, context):
    """开始输入种子。"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "请输入种子值（数字，-1 表示随机）：\n\n发送 /cancel 取消",
    )
    return WAITING_SEED


async def receive_seed(update, context):
    """接收种子值。"""
    settings = context.user_data.setdefault("settings", _default_settings())
    try:
        seed = int(update.message.text.strip())
        settings["seed"] = seed
    except ValueError:
        await update.message.reply_text("请输入有效的数字。")
        return WAITING_SEED

    label = "随机" if seed == -1 else str(seed)
    await update.message.reply_text(f"种子已设为: {label}")
    text, markup = _settings_menu(settings)
    await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")
    return ConversationHandler.END


async def cancel_seed_input(update, context):
    """取消种子输入。"""
    settings = context.user_data.setdefault("settings", _default_settings())
    await update.message.reply_text("已取消。")
    text, markup = _settings_menu(settings)
    await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")
    return ConversationHandler.END


async def close_menu(update, context):
    """关闭设置菜单。"""
    query = update.callback_query
    await query.answer()
    await query.delete_message()


def _default_settings() -> dict:
    from config import DEFAULT_USER_SETTINGS
    import copy
    return copy.deepcopy(DEFAULT_USER_SETTINGS)


# ---- Handler 注册 ----

def get_handlers() -> list:
    """返回所有设置相关的 handlers。"""
    seed_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_seed_input, pattern="^set_seed$")],
        states={
            WAITING_SEED: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_seed),
                CommandHandler("cancel", cancel_seed_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_seed_input)],
        per_message=False,
    )

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
        CallbackQueryHandler(close_menu, pattern="^close_menu$"),
        seed_handler,
    ]
