"""读写 workflow 配置文件，支持原子写入和软操作。"""

import json
import os
import re
import tempfile
from datetime import datetime
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
    # tmp 名唯一化，避免并发写同 key 时互相覆盖
    tmp = tempfile.NamedTemporaryFile(
        "w", dir=WORKFLOW_DIR, delete=False, suffix=".tmp", encoding="utf-8")
    try:
        with tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
        os.replace(tmp.name, path)
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        raise


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
    if not re.fullmatch(r"[a-z0-9_-]+", key):
        raise FileNotFoundError(key)
    src = WORKFLOW_DIR / f"{key}.json"
    if not src.exists():
        raise FileNotFoundError(key)
    trash = WORKFLOW_DIR / ".trash"
    trash.mkdir(exist_ok=True)
    dst = trash / f"{key}.json"
    if dst.exists():
        dst = trash / f"{key}-{datetime.now():%Y%m%d%H%M%S}.json"
    src.rename(dst)


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
            comfy[field] = _parse_json_value(val)
    return comfy


def _parse_json_value(val: str):
    """形如 JSON 数组/对象的值还原为对应类型（如 ["6","15"]），否则保留原字符串。"""
    if val[:1] in ("[", "{"):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            pass
    return val


# form.html 实际渲染的 comfy 字段（编辑时部分更新，其余字段保留原值）
FORM_COMFY_FIELDS = [
    "prompt_node", "prompt_key", "seed_node", "seed_key",
    "model_node", "model_key", "model_loader_class",
    "width_node", "width_key", "height_node", "height_key",
    "default_model",
]


def update_comfy_from_form(comfy: dict, form: dict) -> None:
    """部分更新 comfy：只覆盖表单渲染的字段，清空的字段移除，未渲染的字段保留。"""
    for field in FORM_COMFY_FIELDS:
        val = form.get(field, "").strip()
        if val:
            comfy[field] = _parse_json_value(val)
        else:
            comfy.pop(field, None)
