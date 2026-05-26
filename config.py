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

# ---- 默认生成参数 ----
DEFAULT_NEGATIVE_PROMPT = "lowres, bad anatomy, bad hands, text, error, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry"
DEFAULT_STEPS = 30
DEFAULT_CFG_SCALE = 7
DEFAULT_SAMPLER = "Euler a"

# ---- 预置图片尺寸 ----
SIZE_PRESETS = {
    "512×768 (竖版)":    (512, 768),
    "768×512 (横版)":    (768, 512),
    "512×512 (方形)":    (512, 512),
    "896×1152 (XL竖版)": (896, 1152),
    "1152×896 (XL横版)": (1152, 896),
}

# ---- 高清修复预置参数 ----
HIRES_FIX_PARAMS = {
    "upscaler": "R-ESRGAN 4x+",
    "upscale": 2.0,
    "denoising_strength": 0.45,
    "steps": 15,
}

# ---- 采样器静态列表（API 获取失败时的回退）----
SAMPLER_PRESETS = [
    "Euler a", "Euler", "LMS", "Heun", "DPM2", "DPM2 a",
    "DPM++ 2M", "DPM++ SDE", "DPM++ 2M SDE", "DPM fast",
    "DPM adaptive", "LMS Karras", "DPM2 Karras",
    "DPM2 a Karras", "DPM++ 2M Karras", "DPM++ SDE Karras",
    "DDIM", "PLMS", "UniPC",
]

# ---- 用户设置默认值 ----
DEFAULT_USER_SETTINGS = {
    "width": 512,
    "height": 768,
    "model": None,
    "hires_fix": False,
    "seed": -1,
    "translate": False,
    "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
    "steps": DEFAULT_STEPS,
    "cfg_scale": DEFAULT_CFG_SCALE,
    "sampler": DEFAULT_SAMPLER,
    "restore_faces": False,
    "tiling": False,
    "clip_skip": 1,
}
