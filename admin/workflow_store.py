"""读写 workflow 配置文件，支持原子写入和软操作。"""

import json
import re
from pathlib import Path

from admin.paths import WORKFLOW_DIR


def load_workflow(key: str) -> dict | None:
    if not re.fullmatch(r"[a-z0-9_-]+", key):
        return None
    path = WORKFLOW_DIR / f"{key}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_workflow(data: dict) -> None:
    key = data["key"]
    if not re.fullmatch(r"[a-z0-9_-]+", key):
        raise ValueError(f"无效的工作流 key: {key}")
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    path = WORKFLOW_DIR / f"{key}.json"
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def disable_workflow(key: str) -> None:
    data = load_workflow(key)
    if data is None:
        raise FileNotFoundError(key)
    data["enabled"] = False
    save_workflow(data)


def enable_workflow(key: str) -> None:
    data = load_workflow(key)
    if data is None:
        raise FileNotFoundError(key)
    data["enabled"] = True
    save_workflow(data)


def archive_workflow(key: str) -> None:
    src = WORKFLOW_DIR / f"{key}.json"
    if not src.exists():
        raise FileNotFoundError(key)
    trash = WORKFLOW_DIR / ".trash"
    trash.mkdir(exist_ok=True)
    src.rename(trash / f"{key}.json")


def build_comfy_from_form(form: dict) -> dict:
    """从表单数据构建 comfy 配置 dict。空值字段不包含在结果中。"""
    node_fields = [
        "prompt_node", "prompt_key", "seed_node", "seed_key",
        "model_node", "model_key", "model_loader_class",
        "width_node", "width_key", "height_node", "height_key",
        "video_width_node", "video_width_key", "video_height_node",
        "video_height_key", "video_frames_node", "video_frames_key",
        "load_image_node", "load_image_key",
        "upscale_switch_node", "upscale_switch_key",
        "upscale_switch_on", "upscale_switch_off",
        "pussydetailer_switch_node", "pussydetailer_switch_key",
        "facedetailer_switch_node", "facedetailer_switch_key",
        "facedetailer_switch_on", "facedetailer_switch_off",
        "sd_upscale_node", "sd_upscale_seed_key",
        "sd_upscale_prompt_node", "sd_upscale_prompt_key",
        "lora_node", "lora_enable_node", "lora_enable_key",
        "lora_strength_node", "lora_strength_key",
        "detailer_prompt_node", "detailer_prompt_key",
        "face_detailer_prompt_node", "face_detailer_prompt_key",
        "facedetailer_seed_node", "facedetailer_seed_key",
        "default_model",
    ]

    comfy = {}
    for field in node_fields:
        val = form.get(field, "").strip()
        if val:
            comfy[field] = val
    return comfy
