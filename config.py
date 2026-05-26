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
DEFAULT_NEGATIVE_PROMPT = "worst quality,normal quality,anatomical nonsense,bad anatomy,interlocked fingers,extra fingers,watermark,simple background,transparent,low quality,logo,text,signature,lowres,(bad),bad hands,limb asymmetry,bad feet,text,error,fewer,extra,missing,worst quality,jpeg artifacts,low quality,watermark,unfinished,displeasing,oldest,early,chromatic aberration,signature,simple_background,artistic error,username,scan,[abstract],english text,"
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

# ---- 用户设置默认值 ----
DEFAULT_USER_SETTINGS = {
    "width": 896,
    "height": 1152,
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
    "clip_skip": 2,
}
