"""校验工作流配置与 ComfyUI workflow JSON 的匹配性。"""

import json
from pathlib import Path

from admin.paths import COMFY_WORKFLOW_DIR

# Bot 端 comfy_api.validate_workflow() 强制要求的字段
REQUIRED_FIELDS = ("prompt_node", "prompt_key", "seed_node", "seed_key")

NODE_FIELDS = [
    "prompt_node", "seed_node", "model_node",
    "width_node", "height_node",
    "video_width_node", "video_height_node", "video_frames_node",
    "video_selector_node", "video_megapixels_node", "video_duration_node",
    "load_image_node",
    "upscale_switch_node", "pussydetailer_switch_node",
    "facedetailer_switch_node",
    "sd_upscale_node", "sd_upscale_prompt_node",
    "lora_node", "lora_enable_node", "lora_strength_node",
    "detailer_prompt_node", "face_detailer_prompt_node",
    "facedetailer_seed_node",
]


def validate_workflow_file(name: str) -> str | None:
    """校验 workflow_file 不包含路径穿越。返回错误消息或 None。"""
    if not name.endswith(".json"):
        return "workflow_file 必须以 .json 结尾"
    # 与 Bot 端 comfy_api._load_workflow 一致：仅禁止路径（允许空格、括号等）
    p = Path(name)
    if p.name != name or "/" in name or "\\" in name or ".." in p.parts:
        return "workflow_file 只能填写文件名，不允许路径"
    path = COMFY_WORKFLOW_DIR / name
    if not path.exists():
        return f"文件不存在: {name}"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return "workflow JSON 顶层必须是 dict"
    except json.JSONDecodeError as e:
        return f"workflow JSON 无效: {e}"
    except Exception as e:
        return f"读取 workflow 文件失败: {e}"
    return None


def validate_nodes(comfy_cfg: dict) -> list[dict]:
    """校验节点映射。返回校验报告列表。"""
    name = comfy_cfg.get("workflow_file", "")
    if not name:
        return [{"field": "workflow_file", "status": "error", "msg": "未设置"}]

    err = validate_workflow_file(name)
    if err:
        return [{"field": "workflow_file", "status": "error", "msg": err}]

    path = COMFY_WORKFLOW_DIR / name
    try:
        with open(path, encoding="utf-8") as f:
            wf_json = json.load(f)
    except Exception:
        return [{"field": "workflow_file", "status": "error", "msg": "无法读取"}]

    report = _check_nodes(comfy_cfg, wf_json)
    for field in REQUIRED_FIELDS:
        if not comfy_cfg.get(field):
            report.append({"field": field, "status": "error",
                           "msg": "必填字段未设置（Bot 生成依赖）"})
    _check_model_class(comfy_cfg, wf_json, report)
    _check_load_image_nodes(comfy_cfg, wf_json, report)
    return report


def _check_nodes(comfy_cfg: dict, wf_json: dict, report: list = None) -> list:
    if report is None:
        report = []

    key_suffix_map = {
        "prompt_node": "prompt_key",
        "seed_node": "seed_key",
        "model_node": "model_key",
        "width_node": "width_key",
        "height_node": "height_key",
        "video_width_node": "video_width_key",
        "video_height_node": "video_height_key",
        "video_frames_node": "video_frames_key",
        "video_selector_node": "video_selector_aspect_key",
        "video_megapixels_node": "video_megapixels_key",
        "video_duration_node": "video_duration_key",
        "load_image_node": "load_image_key",
        "upscale_switch_node": "upscale_switch_key",
        "sd_upscale_node": "sd_upscale_seed_key",
        "sd_upscale_prompt_node": "sd_upscale_prompt_key",
        "lora_strength_node": "lora_strength_key",
        "lora_enable_node": "lora_enable_key",
        "detailer_prompt_node": "detailer_prompt_key",
        "face_detailer_prompt_node": "face_detailer_prompt_key",
        "facedetailer_seed_node": "facedetailer_seed_key",
    }

    checked = set()

    for node_field in NODE_FIELDS:
        value = comfy_cfg.get(node_field)
        if value is None:
            continue

        nodes = value if isinstance(value, list) else [str(value)]
        key_field = key_suffix_map.get(node_field)
        key_value = comfy_cfg.get(key_field) if key_field else None

        for nid in nodes:
            if not nid:
                continue
            nid = str(nid)
            check_id = (node_field, str(nid) if nid else "", key_field or "", str(key_value) if key_value else "")
            if check_id in checked:
                continue
            checked.add(check_id)

            node = wf_json.get(nid)
            if node is None:
                report.append({"field": node_field, "node": nid, "status": "error",
                               "msg": f"节点 {nid} 不存在"})
                continue

            class_type = node.get("class_type", "?")
            # node 已配置但对应 key 缺失时 Bot 端生成会 KeyError
            # （prompt/seed 四元组由 validate_nodes 的必填检查覆盖，避免重复报错）
            if key_field and not key_value and key_field not in REQUIRED_FIELDS:
                report.append({"field": key_field, "node": nid, "status": "error",
                               "msg": f"已配置 {node_field} 但缺少 {key_field}"})
            elif key_value and key_value not in node.get("inputs", {}):
                report.append({"field": key_field or node_field, "node": nid,
                               "status": "error",
                               "msg": f"key '{key_value}' 不在节点 {nid} 的 inputs 中"
                                      f" (可用: {list(node.get('inputs', {}).keys())})"})
            else:
                report.append({"field": node_field, "node": nid, "status": "ok",
                               "msg": f"节点 {nid} ({class_type}) 校验通过"})

    return report


def _check_model_class(comfy_cfg: dict, wf_json: dict, report: list) -> None:
    expected = comfy_cfg.get("model_loader_class")
    if not expected:
        return

    model_node = comfy_cfg.get("model_node")
    if not model_node:
        return

    nodes = model_node if isinstance(model_node, list) else [str(model_node)]
    for nid in nodes:
        node = wf_json.get(str(nid))
        if node and node.get("class_type") != expected:
            report.append({"field": "model_loader_class", "node": nid, "status": "error",
                           "msg": f"class_type 不匹配: 期望 {expected}, 实际 {node.get('class_type')}"})


def _check_load_image_nodes(comfy_cfg: dict, wf_json: dict, report: list) -> None:
    img_nodes = comfy_cfg.get("load_image_nodes")
    if not img_nodes or not isinstance(img_nodes, dict):
        return

    for role, cfg in img_nodes.items():
        if not isinstance(cfg, dict):
            continue
        nid = str(cfg.get("node", ""))
        key = cfg.get("key", "")
        node = wf_json.get(nid)
        if not node:
            report.append({"field": "load_image_nodes", "node": nid, "status": "error",
                           "msg": f"load_image_nodes.{role}: 节点 {nid} 不存在"})
        elif key not in node.get("inputs", {}):
            report.append({"field": "load_image_nodes", "node": nid, "status": "error",
                           "msg": f"load_image_nodes.{role}: key '{key}' 不在 inputs 中"})
        else:
            report.append({"field": "load_image_nodes", "node": nid, "status": "ok",
                           "msg": f"load_image_nodes.{role}: 节点 {nid} 校验通过"})
