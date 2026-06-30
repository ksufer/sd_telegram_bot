import os
from dotenv import load_dotenv

load_dotenv()

# ---- 日志 ----
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = "logs"
LOG_FULL_PROMPT = os.getenv("LOG_FULL_PROMPT", "false").lower() == "true"

# ---- 敏感信息（从 .env 加载）----
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
PROXY_URL = os.getenv("PROXY_URL", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# ---- Stable Diffusion WebUI API ----
SD_API_BASE = os.getenv("SD_API_BASE", "http://10.126.126.1:7860")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# ---- ComfyUI API ----
COMFY_API_BASE = os.getenv("COMFY_API_BASE", "http://10.126.126.4:8188")
COMFY_POLL_INTERVAL = 2
COMFY_TIMEOUT = 1500
COMFY_DEFAULT_WORKFLOW = "z-image-turbo"

# ---- 工作流注册表（主菜单驱动） ----
WORKFLOW_REGISTRY = [
    {
        "key": "z-image-turbo",
        "emoji": "🖼",
        "label": "文生图",
        "description": "输入文字描述，AI 生成图片",
        "how_to": (
            "直接发送描述词即可\n"
            "例如：a cat sitting on a sofa, masterpiece, best quality\n\n"
            "可选：在 ComfyUI 设置中自定义 Prompt，固定描述风格"
        ),
        "backend": "comfyui",
        "comfy_workflow": "z-image-turbo",
        "input_type": "text",
    },
    {
        "key": "zit-pussy",
        "emoji": "💦",
        "label": "ZIT Pussy",
        "description": "Z-Image-Turbo 文生图 + Pussy 精修 + SD 2x 放大",
        "how_to": (
            "直接发送描述词即可\n"
            "例如：a girl sitting on bed, spread legs\n\n"
            "自动进行 pussy 区域 FaceDetailer 精修\n"
            "最终 2x SD Upscale 放大输出"
        ),
        "backend": "comfyui",
        "comfy_workflow": "zit-pussy",
        "input_type": "text",
    },
    {
        "key": "image-to-real",
        "emoji": "📸",
        "label": "动漫转写实",
        "description": "上传动漫图片，AI 转换为写实照片风格",
        "how_to": (
            "直接发送一张动漫/二次元图片即可\n"
            "无需提示词，AI 自动转换为写实照片\n"
            "发送图片时可附带文字补充细节（如发型、瞳色等）\n\n"
            "输出图片将保持原图比例"
        ),
        "backend": "comfyui",
        "comfy_workflow": "image-to-real",
        "input_type": "photo",
    },
    {
        "key": "qwen-image-edit",
        "emoji": "✏️",
        "label": "图片编辑",
        "description": "上传图片后持续修改，支持多轮编辑",
        "how_to": (
            "第一轮：发送一张图片 → AI 编辑后返回结果\n"
            "第二轮：回复结果图 + 新指令 → 继续修改\n"
            "例如：回复图片 + 'change hair color to blue'\n\n"
            "想换底图？直接发新图片即可重新开始"
        ),
        "backend": "comfyui",
        "comfy_workflow": "qwen-image-edit",
        "input_type": "photo",
    },
    {
        "key": "image-to-video",
        "emoji": "🎬",
        "label": "图生视频",
        "description": "上传图片，AI 生成短视频",
        "how_to": (
            "发送一张图片（可附带描述词）\n"
            "例如：发一张风景照 → 生成动态视频\n\n"
            "可在 ComfyUI 设置中调整视频方向和长度"
        ),
        "backend": "comfyui",
        "comfy_workflow": "image-to-video",
        "input_type": "photo",
    },
    {
        "key": "sdxl",
        "emoji": "🎨",
        "label": "文生图（SDXL）",
        "description": "SDXL 模型文生图，高质量大图",
        "how_to": (
            "直接发送描述词即可\n"
            "例如：a cat sitting on a sofa\n\n"
            "提示词会自动添加画质前缀\n"
            "可在 ComfyUI 设置中切换模型和尺寸"
        ),
        "backend": "comfyui",
        "comfy_workflow": "sdxl",
        "input_type": "text",
    },
    {
        "key": "firstlast-video",
        "emoji": "🎞️",
        "label": "首尾帧生视频",
        "description": "上传首帧+尾帧图片，AI 生成过渡视频",
        "how_to": (
            "1. 先发送首帧图片（群聊需 @bot）\n"
            "2. 再发送尾帧图片，可附带文字描述（群聊需 @bot）\n"
            "3. 如未附带描述，再发送文字说明\n\n"
            "例如：首帧=坐着的猫，尾帧=站立的猫\n"
            "描述=cat slowly standing up"
        ),
        "backend": "comfyui",
        "comfy_workflow": "firstlast-video",
        "input_type": "photo",
    },
    {
        "key": "qwen-2pic-edit",
        "emoji": "\U0001f5bc️",
        "label": "Qwen 双图编辑",
        "description": "上传2张图片+提示词，AI 合成编辑（换脸/换装）",
        "how_to": (
            "1. 发送第一张图片（群聊需 @bot）\n"
            "2. 发送第二张图片，可附带文字描述（群聊需 @bot）\n"
            "3. 若未附带描述，再发送文字描述\n\n"
            "示例：图1=人物A，图2=人物B\n描述=将图1的脸换成图2的脸"
        ),
        "backend": "comfyui",
        "comfy_workflow": "qwen-2pic-edit",
        "input_type": "photo",
    },
]

COMFY_WORKFLOWS = {
    "z-image-turbo": {
        "label": "Z-Image-Turbo（文生图）",
        "path": os.getenv("COMFY_WORKFLOW_PATH", "data/zit-api.json"),
        "is_img2img": False,
        "prompt_node": "83:27",
        "prompt_key": "text",
        "seed_node": "83:3",
        "seed_key": "seed",
        "model_node": "83:28",
        "model_key": "unet_name",
        "model_loader_class": "UNETLoader",
        "width_node": "83:13",
        "width_key": "width",
        "height_node": "83:13",
        "height_key": "height",
        "default_model": os.getenv("COMFY_DEFAULT_MODEL", "moodyPornMix_zitV9.safetensors"),
    },
    "zit-pussy": {
        "label": "ZIT Pussy（文生图+精修+放大+脸部修复）",
        "path": "data/zit-up-pussy-face.json",
        "is_img2img": False,
        "prompt_node": "96",
        "prompt_key": "text",
        "seed_node": "97",
        "seed_key": "seed",
        "model_node": "95",
        "model_key": "unet_name",
        "model_loader_class": "UNETLoader",
        "width_node": "91",
        "width_key": "width",
        "height_node": "91",
        "height_key": "height",
        "default_model": "moodyProMix_zitV13.safetensors",
        "upscale_model_node": "98",
        "upscale_model_key": "model_name",
        "sd_upscale_node": "88",
        "sd_upscale_seed_key": "seed",
        "lora_node": "102",
        "detailer_prompt_node": "103",
        "detailer_prompt_key": "text",
        # Upscale 开关：关闭时跳过 UltimateSDUpscale，FaceDetailer 直连 VAEDecode
        "upscale_switch_node": "101",
        "upscale_switch_key": "image",
        "upscale_switch_on": ["88", 0],
        "upscale_switch_off": ["93", 0],
        # 脸部重绘 FaceDetailer（zit 模型修复人脸）
        "face_detailer_prompt_node": "115",
        "face_detailer_prompt_key": "text",
        # SD Upscale 简化提示词（避免动作/姿势产生伪影）
        "sd_upscale_prompt_node": "120",
        "sd_upscale_prompt_key": "text",
    },
    "image-to-real": {
        "label": "Image-to-Real（动漫转写实）",
        "path": os.getenv("COMFY_IMG2IMG_WORKFLOW_PATH", "data/templates-image_to_real.json"),
        "is_img2img": True,
        "use_caption_as_prompt": True,
        "append_user_prompt": True,
        "prompt_node": "17:8",
        "prompt_key": "prompt",
        "seed_node": "17:11",
        "seed_key": "seed",
        "model_node": "17:4",
        "model_key": "unet_name",
        "model_loader_class": "UNETLoader",
        "load_image_node": "14",
        "load_image_key": "image",
        "default_model": "qwen_image_edit_2509_fp8_e4m3fn.safetensors",
    },
    "qwen-image-edit": {
        "label": "Qwen Image Edit（图生图）",
        "path": os.getenv("COMFY_QWEN_EDIT_WORKFLOW_PATH", "data/Qwen Image Edit Rapid v1.0 (api).json"),
        "is_img2img": True,
        "prompt_node": "119",
        "prompt_key": "prompt",
        "seed_node": "117",
        "seed_key": "value",
        "model_node": "118",
        "model_key": "ckpt_name",
        "model_loader_class": "CheckpointLoaderSimple",
        "load_image_node": "78",
        "load_image_key": "image",
        "default_model": "Qwen-Rapid-AIO-NSFW-v11.1.safetensors",
        "use_caption_as_prompt": True,
    },
    "image-to-video": {
        "label": "Image-to-Video（图生视频）",
        "path": "data/image_to_video.json",
        "is_img2img": True,
        "use_caption_as_prompt": True,
        "model_selectable": False,
        "output_type": "video",
        "prompt_node": "129:93",
        "prompt_key": "text",
        "seed_node": "129:86",
        "seed_key": "noise_seed",
        "model_node": "129:95",
        "model_key": "unet_name",
        "model_loader_class": "UNETLoader",
        "load_image_node": "97",
        "load_image_key": "image",
        "video_width_node": "129:98",
        "video_width_key": "width",
        "video_height_node": "129:98",
        "video_height_key": "height",
        "video_frames_node": "129:98",
        "video_frames_key": "length",
        "default_model": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
    },
    "sdxl": {
        "label": "SDXL（文生图）",
        "path": "data/sdxl.json",
        "is_img2img": False,
        "prompt_node": ["6", "15"],
        "prompt_key": "text",
        "prompt_prefix": (
            "masterpiece, best quality, ultra-detailed, very aesthetic, "
            "depth of field, best lighting, detailed illustration, "
            "detailed background, cinematic, ambient occlusion, "
            "raytracing, soft lighting, blum effect, "
        ),
        "seed_node": ["10", "11"],
        "seed_key": "noise_seed",
        "model_node": ["4", "12"],
        "model_key": "ckpt_name",
        "model_loader_class": "CheckpointLoaderSimple",
        "width_node": "5",
        "width_key": "width",
        "height_node": "5",
        "height_key": "height",
        "default_model": "miaomiaoHarem_v20.safetensors",
    },
    "firstlast-video": {
        "label": "首尾帧生视频（Wan2.2）",
        "path": "data/video_wan2_2_14B_flf2v.json",
        "is_img2img": True,
        "output_type": "video",
        "model_selectable": False,
        "use_caption_as_prompt": True,
        "prompt_node": "6",
        "prompt_key": "text",
        "seed_node": "57",
        "seed_key": "noise_seed",
        "load_image_nodes": {
            "start": {"node": "68", "key": "image"},
            "end": {"node": "62", "key": "image"},
        },
        "video_width_node": "67",
        "video_width_key": "width",
        "video_height_node": "67",
        "video_height_key": "height",
        "video_frames_node": "67",
        "video_frames_key": "length",
        "default_model": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
    },
    "qwen-2pic-edit": {
        "label": "Qwen 双图编辑",
        "path": "data/qwen_2pic_edit.json",
        "is_img2img": True,
        "model_selectable": True,
        "use_caption_as_prompt": True,
        "prompt_node": "119",
        "prompt_key": "prompt",
        "seed_node": "117",
        "seed_key": "value",
        "model_node": "118",
        "model_key": "ckpt_name",
        "model_loader_class": "CheckpointLoaderSimple",
        "load_image_nodes": {
            "image1": {"node": "78", "key": "image"},
            "image2": {"node": "122", "key": "image"},
        },
        "default_model": "Qwen-Rapid-AIO-NSFW-v11.1.safetensors",
    },
}

# 兼容旧代码（从默认 workflow 取值）
_COMFY_DEFAULT_WF = COMFY_WORKFLOWS[COMFY_DEFAULT_WORKFLOW]
COMFY_WORKFLOW_PATH = _COMFY_DEFAULT_WF["path"]
COMFY_MODEL_LOADER_CLASS = _COMFY_DEFAULT_WF["model_loader_class"]
COMFY_DEFAULT_MODEL = _COMFY_DEFAULT_WF["default_model"]

# ---- 默认生成参数 ----
DEFAULT_PROMPT_PREFIX = "masterpiece, best quality, amazing quality,"
DEFAULT_NEGATIVE_PROMPT = "worst quality,normal quality,anatomical nonsense,bad anatomy,interlocked fingers,extra fingers,watermark,simple background,transparent,low quality,logo,text,signature,lowres,(bad),bad hands,limb asymmetry,bad feet,text,error,fewer,extra,missing,worst quality,jpeg artifacts,low quality,watermark,unfinished,displeasing,oldest,early,chromatic aberration,signature,simple_background,artistic error,username,scan,[abstract],english text,"
DEFAULT_STEPS = 30
DEFAULT_CFG_SCALE = 5
DEFAULT_SAMPLER = "DPM++ 2M SDE"

# ---- 预置图片尺寸 ----
SIZE_PRESETS = {
    "1024×1024 (方形)":  (1024, 1024),
    "1024 3:4 竖版": (896, 1152),
    "1024 4:3 横版": (1152, 896),
    "1024 2:3 竖版": (832, 1280),
    "1024 3:2 横版": (1280, 832),
    "1024 9:16 竖版": (768, 1344),
    "1024 16:9 横版": (1344, 768),
    "1280×1280 (方形)":  (1280, 1280),
    "1280 3:4 竖版": (1088, 1472),
    "1280 4:3 横版": (1472, 1088),
    "1280 2:3 竖版": (1024, 1536),
    "1280 3:2 横版": (1536, 1024),
    "1280 9:16 竖版": (960, 1728),
    "1280 16:9 横版": (1728, 960),

}

# ---- ComfyUI 预置图片尺寸（key 用于 callback data，无特殊字符）----
COMFY_SIZE_PRESETS = {
    "768x1280":  {"label": "768×1280（竖版）", "width": 768,  "height": 1280},
    "1280x768":  {"label": "1280×768（横版）", "width": 1280, "height": 768},
    "1024x1024": {"label": "1024×1024（方形）", "width": 1024, "height": 1024},
    "896x1152":  {"label": "896×1152（3:4）",  "width": 896,  "height": 1152},
    "1152x896":  {"label": "1152×896（4:3）",  "width": 1152, "height": 896},
}

# ---- ComfyUI 视频比例预设 ----
COMFY_VIDEO_ASPECTS = {
    "9:16": {"label": "9:16 竖版", "ratio": 9 / 16},
    "16:9": {"label": "16:9 横版", "ratio": 16 / 9},
    "4:3":  {"label": "4:3 横版",  "ratio": 4 / 3},
    "3:4":  {"label": "3:4 竖版",  "ratio": 3 / 4},
    "1:1":  {"label": "1:1 方形",  "ratio": 1 / 1},
}

# ---- ComfyUI 视频画质预设 ----
COMFY_VIDEO_RESOLUTIONS = {
    "480p": {"label": "480p", "short_side": 480},
    "720p": {"label": "720p", "short_side": 720},
}


def compute_video_dimensions(aspect_key: str, resolution_key: str) -> tuple[int, int]:
    """根据比例和画质计算视频宽高，取整到 16 的倍数。"""
    ratio = COMFY_VIDEO_ASPECTS.get(aspect_key, COMFY_VIDEO_ASPECTS["9:16"])["ratio"]
    short = COMFY_VIDEO_RESOLUTIONS.get(resolution_key, COMFY_VIDEO_RESOLUTIONS["480p"])["short_side"]

    if ratio >= 1:
        # 横版或方形：短边 = 高度
        height = short
        width = round(height * ratio / 16) * 16
    else:
        # 竖版：短边 = 宽度
        width = short
        height = round(width / ratio / 16) * 16

    return width, height

# ---- ComfyUI LoRA 变体（zit-pussy 专属）----
COMFY_LORA_VARIANTS = {
    "off": {
        "label": "关闭",
        "lora_1_on": False,
        "lora_2_on": False,
        "lora_3_on": False,
        "detailer_prompt": "",  # 空字符串 → 使用用户输入的 prompt
    },
    "normal": {
        "label": "正常",
        "lora_1_on": True,
        "lora_2_on": True,
        "lora_3_on": False,
        "detailer_prompt": (
            "A natural close-up view of a woman's genitalia. "
            "The outer labia are softly closed, showing natural contours and subtle skin folds. "
            "Soft pink skin tone with smooth, realistic texture and even lighting."
        ),
    },
    "spread": {
        "label": "Spread",
        "lora_1_on": True,
        "lora_2_on": False,
        "lora_3_on": True,
        "detailer_prompt": (
            "A girl is sitting and spreading her legs to reveal her genitalia. "
            "Pulling the outer lips wide open to show the clitoris and inner folds. "
            "The skin has a natural pink tone with a wet, glossy texture."
        ),
    },
}

# ---- NSFW 身体关键词（SD Upscale 阶段补回，避免遮挡伪影）----
NSFW_BODY_KEYWORDS = [
    # 英文
    "pussy", "clitoris", "nipples", "nipple", "breast", "breasts",
    "vagina", "vulva", "labia", "genitalia", "genitals",
    "nude", "naked", "topless", "bottomless",
    "spread pussy", "open pussy", "wet pussy",
    "areola", "clit", "penis", "testicles", "anus",
    "butt", "ass", "cleavage", "cameltoe", "upskirt",
    "underboob", "thighs",
    # 中文
    "阴部", "私处", "乳头", "乳晕", "乳房", "裸体", "裸",
]

# ---- ComfyUI 视频长度预设（帧数）----
COMFY_VIDEO_FRAMES_PRESETS = {
    "81":  {"label": "~3秒 (81帧)",   "frames": 81},
    "135": {"label": "~5秒 (135帧)",  "frames": 135},
    "189": {"label": "~7秒 (189帧)",  "frames": 189},
    "270": {"label": "~10秒 (270帧)", "frames": 270},
}

# ---- 高清修复预置参数 ----
HIRES_FIX_PARAMS = {
    "upscaler": "4x-UltraSharp",
    "upscale": 1.5,
    "denoising_strength": 0.3,
    "steps": 20,
}

# ---- 采样器静态列表（API 获取失败时的回退）----
SAMPLER_PRESETS = [
    "Euler a", "Euler", "LMS", "Heun", "DPM2", "DPM2 a",
    "DPM++ 2M", "DPM++ SDE", "DPM++ 2M SDE", "DPM fast",
    "DPM adaptive", "LMS Karras", "DPM2 Karras",
    "DPM2 a Karras", "DPM++ 2M Karras", "DPM++ SDE Karras",
    "DDIM", "PLMS", "UniPC",
]

# ---- 访问控制 ----
ALLOWED_USER_IDS: list[int] = []
ALLOWED_CHAT_IDS: list[int] = []
ADMIN_USER_ID: int | None = 7562421953

# ---- 额度系统 ----
DEFAULT_CREDIT_QUOTA = 100

# ---- 用户设置默认值 ----
DEFAULT_USER_SETTINGS = {
    "backend": "sd",
    "width": 896,
    "height": 1152,
    "model": None,
    "hires_fix": False,
    "seed": -1,
    "translate": True,
    "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
    "steps": DEFAULT_STEPS,
    "cfg_scale": DEFAULT_CFG_SCALE,
    "sampler": DEFAULT_SAMPLER,
    "restore_faces": False,
    "tiling": False,
    "clip_skip": 2,
    # ComfyUI 专属设置
    "comfy_workflow": COMFY_DEFAULT_WORKFLOW,
    "comfy_model": COMFY_DEFAULT_MODEL,
    "comfy_seed": -1,
    "comfy_width": 768,
    "comfy_height": 1280,
    "comfy_translate": False,
    "comfy_prompt": "",  # 空 = 使用 workflow 默认 prompt
    "comfy_video_aspect": "9:16",
    "comfy_video_resolution": "480p",
    "comfy_video_frames": 81,
    "comfy_lora_variant": "normal",
    "comfy_upscale_enabled": True,
    "comfy_face_prompt": "",  # 空=自动提取，非空=手动覆盖
}
