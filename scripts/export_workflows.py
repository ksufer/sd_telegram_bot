"""将 config.py 中硬编码的工作流配置导出为 data/workflows/*.json 文件。"""

import json
import os
import shutil
import sys
from pathlib import Path

from config import WORKFLOW_REGISTRY, COMFY_WORKFLOWS

WORKFLOW_DIR = Path("data/workflows")
COMFY_DIR = Path("data/comfy_workflows")
DATA_DIR = Path("data")


def export(dry_run: bool = False, force: bool = False) -> None:
    """导出所有工作流配置。"""
    if not dry_run:
        WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
        COMFY_DIR.mkdir(parents=True, exist_ok=True)

    for entry in WORKFLOW_REGISTRY:
        key = entry["key"]
        comfy_key = entry["comfy_workflow"]
        comfy_cfg = COMFY_WORKFLOWS.get(comfy_key, {})

        # 复制 ComfyUI workflow JSON 到新目录
        workflow_path = comfy_cfg.get("path", "")
        src = Path(workflow_path)
        dst = COMFY_DIR / src.name
        if src.exists() and not dry_run:
            shutil.copy2(src, dst)

        data = {
            "schema_version": 1,
            "key": key,
            "enabled": True,
            "menu": {
                "emoji": entry.get("emoji", ""),
                "label": entry["label"],
                "description": entry["description"],
                "how_to": entry.get("how_to", ""),
                "input_type": entry.get("input_type", "text"),
                "backend": entry.get("backend", "comfyui"),
            },
            "comfy": {
                **comfy_cfg,
                "workflow_file": src.name,
            },
            "user_configurable": _default_user_configurable(comfy_cfg),
        }
        # 移除旧 path 字段（已替换为 workflow_file）
        data["comfy"].pop("path", None)

        if dry_run:
            print(f"\n=== {key}.json ===")
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            out_path = WORKFLOW_DIR / f"{key}.json"
            if out_path.exists() and not force:
                print(f"SKIP {key}.json (已存在，使用 --force 覆盖)")
                continue
            tmp = out_path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(out_path)
            print(f"Wrote {out_path}")

    print(f"\nDry-run: {dry_run}. Files count: {len(WORKFLOW_REGISTRY)}")


def _default_user_configurable(comfy: dict) -> list[str]:
    """根据 comfy 配置推断默认的用户可编辑项。"""
    items = ["comfy_seed", "comfy_translate", "comfy_prompt"]
    if comfy.get("model_selectable", True):
        items.append("comfy_model")
    if comfy.get("width_node"):
        items.extend(["comfy_width", "comfy_height"])
    if comfy.get("output_type") == "video":
        items.extend(["comfy_video_aspect", "comfy_video_resolution", "comfy_video_frames"])
    if comfy.get("upscale_switch_node"):
        items.append("comfy_upscale_enabled")
    if comfy.get("pussydetailer_switch_node"):
        items.append("comfy_pussydetailer_enabled")
    if comfy.get("facedetailer_switch_node"):
        items.append("comfy_facedetailer_enabled")
    if comfy.get("lora_node"):
        items.append("comfy_lora_variant")
    if comfy.get("face_detailer_prompt_node"):
        items.append("comfy_face_prompt")
    if comfy.get("lora_enable_node"):
        items.extend(["comfy_krea2_lora_enabled", "comfy_krea2_lora_strength"])
    return items


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    export(dry_run=dry_run, force=force)
