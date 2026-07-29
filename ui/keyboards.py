"""无副作用键盘构建模块。

不访问数据库、不调用 Bot API、不读写用户状态。
仅依赖 telegram 和 config，根据入参返回 InlineKeyboardMarkup。
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import (
    COMFY_WORKFLOWS,
    COMFY_DEFAULT_WORKFLOW,
    COMFY_LORA_VARIANTS,
    COMFY_PROMPT_OPTIMIZE_MODES,
)


def generation_menu(context_id: str) -> InlineKeyboardMarkup:
    """SD WebUI 生成后菜单。"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("参数设置", callback_data="settings_menu"),
            InlineKeyboardButton("关闭菜单", callback_data="close_menu"),
        ],
        [
            InlineKeyboardButton("用本图提示词",
                                 callback_data=f"reuse_prompt_{context_id}"),
            InlineKeyboardButton("用本图种子",
                                 callback_data=f"reuse_seed_{context_id}"),
            InlineKeyboardButton("🎲", callback_data="random_seed"),
        ],
    ])


def build_toggle_row(settings: dict, wf_config: dict) -> list:
    """构建快捷开关行（🔍 🎨/🅿️ 👤），按 workflow 配置出现。"""
    row = []
    if wf_config.get("upscale_switch_node"):
        on = settings.get("comfy_upscale_enabled", True)
        row.append(InlineKeyboardButton(
            "🔍" if on else "🔍✖", callback_data="comfy_upscale_toggle_gen"))
    if wf_config.get("prompt_optimize_node"):
        mode = settings.get("comfy_prompt_optimize", "nsfw")
        if isinstance(mode, bool):
            mode = "nsfw" if mode else "off"
        mode_cfg = COMFY_PROMPT_OPTIMIZE_MODES.get(
            mode, COMFY_PROMPT_OPTIMIZE_MODES["nsfw"])
        row.append(InlineKeyboardButton(
            mode_cfg["icon"], callback_data="comfy_prompt_optimize_cycle_gen"))
    if wf_config.get("sd_upscale_prompt_node"):
        on = settings.get("comfy_sd_upscale_prompt_inject", True)
        row.append(InlineKeyboardButton(
            "📝" if on else "📝✖", callback_data="comfy_sd_upscale_prompt_toggle_gen"))
    if wf_config.get("pussydetailer_switch_node"):
        on = settings.get("comfy_pussydetailer_enabled", True)
        row.append(InlineKeyboardButton(
            "🅿️" if on else "🅿️✖", callback_data="comfy_pussydetailer_toggle_gen"))
    if wf_config.get("facedetailer_switch_node"):
        on = settings.get("comfy_facedetailer_enabled", True)
        row.append(InlineKeyboardButton(
            "👤" if on else "👤✖", callback_data="comfy_facedetailer_toggle_gen"))
    return row


def comfy_generation_menu(
    context_id: str = "",
    settings: dict | None = None,
    include_seed_buttons: bool = True,
) -> InlineKeyboardMarkup:
    """ComfyUI 生成后菜单。

    Args:
        context_id: 种子按钮上下文 ID（include_seed_buttons=False 时可为空）。
        settings: 用户设置（用于判断 zit-pussy/krea2/默认三种分支）。
        include_seed_buttons: False 时不含种子复用/随机种子行（供刷新场景）。
    """
    if settings:
        wf_key = settings.get("comfy_workflow", "")
        wf_config = COMFY_WORKFLOWS.get(wf_key, {})

        if wf_config.get("lora_node"):
            # zit-pussy: LoRA 变体 + 三级开关
            current = settings.get("comfy_lora_variant", "normal")
            lora_buttons = []
            for key, variant in COMFY_LORA_VARIANTS.items():
                prefix = "✓ " if key == current else ""
                lora_buttons.append(InlineKeyboardButton(
                    f"{prefix}{variant['label']}",
                    callback_data=f"comfy_lora_var:{key}",
                ))
            rows = [lora_buttons]
            toggle_row = build_toggle_row(settings, wf_config)
            if toggle_row:
                rows.append(toggle_row)
            if include_seed_buttons:
                rows.append([
                    InlineKeyboardButton("🔁 复用本次 Seed",
                                         callback_data=f"comfy_reuse_seed_{context_id}"),
                    InlineKeyboardButton("🎲 随机 Seed",
                                         callback_data="comfy_random_seed"),
                ])
            rows.append([
                InlineKeyboardButton("⚙️ ComfyUI 设置", callback_data="comfy_settings"),
                InlineKeyboardButton("关闭菜单", callback_data="close_menu"),
            ])
            return InlineKeyboardMarkup(rows)

        if wf_config.get("lora_enable_node"):
            # krea2: 放大 + 优化 + 脸部精修 + LoRA 开关
            rows = []
            toggle_row = []
            if wf_config.get("upscale_switch_node"):
                upscale_on = settings.get("comfy_upscale_enabled", True)
                toggle_row.append(InlineKeyboardButton(
                    "🔍" if upscale_on else "🔍✖", callback_data="comfy_upscale_toggle_gen"))
            if wf_config.get("prompt_optimize_node"):
                mode = settings.get("comfy_prompt_optimize", "nsfw")
                if isinstance(mode, bool):
                    mode = "nsfw" if mode else "off"
                mode_cfg = COMFY_PROMPT_OPTIMIZE_MODES.get(
                    mode, COMFY_PROMPT_OPTIMIZE_MODES["nsfw"])
                toggle_row.append(InlineKeyboardButton(
                    mode_cfg["icon"], callback_data="comfy_prompt_optimize_cycle_gen"))
            if wf_config.get("facedetailer_switch_node"):
                facedetailer_on = settings.get("comfy_facedetailer_enabled", True)
                toggle_row.append(InlineKeyboardButton(
                    "👤" if facedetailer_on else "👤✖", callback_data="comfy_facedetailer_toggle_gen"))
            lora_on = settings.get("comfy_krea2_lora_enabled", False)
            toggle_row.append(InlineKeyboardButton(
                "🧬" if lora_on else "🧬✖", callback_data="comfy_krea2_lora_toggle_gen"))
            if toggle_row:
                rows.append(toggle_row)
            if include_seed_buttons:
                rows.append([
                    InlineKeyboardButton("🔁 复用本次 Seed",
                                         callback_data=f"comfy_reuse_seed_{context_id}"),
                    InlineKeyboardButton("🎲 随机 Seed",
                                         callback_data="comfy_random_seed"),
                ])
            rows.append([
                InlineKeyboardButton("⚙️ ComfyUI 设置", callback_data="comfy_settings"),
                InlineKeyboardButton("关闭菜单", callback_data="close_menu"),
            ])
            return InlineKeyboardMarkup(rows)

    # 默认菜单（无 lora_node 的 workflow / settings is None）
    rows = []
    if include_seed_buttons:
        rows.append([
            InlineKeyboardButton("🔁 复用本次 Seed",
                                 callback_data=f"comfy_reuse_seed_{context_id}"),
            InlineKeyboardButton("🎲 随机 Seed",
                                 callback_data="comfy_random_seed"),
        ])
    if settings:
        wf_key = settings.get("comfy_workflow", "")
        wf_config = COMFY_WORKFLOWS.get(wf_key, {})
        toggle = build_toggle_row(settings, wf_config)
        if toggle:
            rows.append(toggle)
    rows.append([
        InlineKeyboardButton("⚙️ ComfyUI 设置", callback_data="comfy_settings"),
        InlineKeyboardButton("关闭菜单", callback_data="close_menu"),
    ])
    return InlineKeyboardMarkup(rows)
