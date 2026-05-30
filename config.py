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
COMFY_TIMEOUT = 300
COMFY_DEFAULT_WORKFLOW = "z-image-turbo"

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
    "image-to-real": {
        "label": "Image-to-Real（图生图）",
        "path": os.getenv("COMFY_IMG2IMG_WORKFLOW_PATH", "data/templates-image_to_real.json"),
        "is_img2img": True,
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
}
