import asyncio
import copy
import fnmatch
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

from config import (
    COMFY_API_BASE,
    COMFY_WORKFLOWS,
    COMFY_DEFAULT_WORKFLOW,
    COMFY_POLL_INTERVAL,
    COMFY_TIMEOUT,
    COMFY_PROGRESS_HEARTBEAT_INTERVAL,
    COMFY_SIZE_PRESETS,
    COMFY_VIDEO_ASPECTS,
    COMFY_VIDEO_RESOLUTIONS,
    COMFY_VIDEO_FRAMES_PRESETS,
    DEFAULT_VIDEO_FRAMES_KEY,
    COMFY_LORA_VARIANTS,
    COMFY_PROMPT_OPTIMIZE_MODES,
    NSFW_BODY_KEYWORDS,
    compute_video_dimensions,
)

logger = logging.getLogger(__name__)

_workflow_cache: dict[str, dict] = {}
_workflow_cache_mtime: dict[str, int] = {}


# ── 输出类型 ─────────────────────────────────────────────

@dataclass
class ComfyOutput:
    data: bytes
    filename: str
    kind: str  # "image" | "video" | "gif" | "file"


def _detect_output_kind(filename: str) -> str:
    name = filename.lower()
    if name.endswith((".mp4", ".mov", ".webm", ".mkv")):
        return "video"
    if name.endswith(".gif"):
        return "gif"
    if name.endswith((".png", ".jpg", ".jpeg", ".webp")):
        return "image"
    return "file"


# ── 自定义异常 ───────────────────────────────────────────

class ComfyApiError(Exception):
    """ComfyUI API 错误（提交失败、无 prompt_id、生成报错）。"""


class ComfyWorkflowError(Exception):
    """Workflow 文件缺失、无法解析或节点结构不正确。"""


class ComfyTimeoutError(Exception):
    """ComfyUI 生成超时。"""


# ── Workflow 配置工具 ────────────────────────────────────

def _get_wf_config(settings: dict) -> tuple[str, dict]:
    """解析用户 workflow 设置，返回 (实际 wf_key, wf_config)。

    用户保存的 key 可能已被管理面板删除：依次回退 默认 workflow → 第一个
    workflow，避免旧设置锁死所有生成。COMFY_WORKFLOWS 为空时返回 (原 key, {})。
    """
    wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
    if wf_key in COMFY_WORKFLOWS:
        return wf_key, COMFY_WORKFLOWS[wf_key]
    if COMFY_DEFAULT_WORKFLOW in COMFY_WORKFLOWS:
        logger.warning("Workflow '%s' 不存在，回退到默认 '%s'",
                       wf_key, COMFY_DEFAULT_WORKFLOW)
        return COMFY_DEFAULT_WORKFLOW, COMFY_WORKFLOWS[COMFY_DEFAULT_WORKFLOW]
    if COMFY_WORKFLOWS:
        first_key = next(iter(COMFY_WORKFLOWS))
        logger.warning("Workflow '%s' 不存在，回退到 '%s'", wf_key, first_key)
        return first_key, COMFY_WORKFLOWS[first_key]
    return wf_key, {}


def _set_node_input(workflow: dict, node_id: str | list[str], input_key: str, value):
    """向一个或多个 workflow 节点注入值。

    支持单节点 (str) 和多节点 (list[str])，后者将同一值注入所有节点。
    input_key 支持点号分隔的嵌套路径，如 "lora_2.on"。
    """
    ids = node_id if isinstance(node_id, list) else [node_id]
    for nid in ids:
        try:
            keys = input_key.split(".")
            target = workflow[nid]["inputs"]
            for k in keys[:-1]:
                target = target[k]
            target[keys[-1]] = value
        except KeyError as e:
            raise ComfyWorkflowError(
                f"Workflow 节点或字段不存在: node_id={nid}, input_key={input_key}"
            ) from e


def _load_workflow(wf_key: str) -> dict:
    """按 workflow key 加载并缓存，每次返回 deepcopy。

    缓存按文件 mtime 失效：管理面板上传新 workflow JSON 后无需重启 Bot。
    """
    wf_config = COMFY_WORKFLOWS.get(wf_key)
    if wf_config is None:
        raise ComfyWorkflowError(f"未知 Workflow: {wf_key}")
    if wf_config.get("workflow_file"):
        wf_file = wf_config["workflow_file"]
        if Path(wf_file).name != wf_file or "/" in wf_file or "\\" in wf_file or ".." in wf_file:
            raise ComfyWorkflowError("workflow_file 只能是文件名")
        path = Path("data/comfy_workflows") / wf_file
    elif wf_config.get("path"):
        path = Path(wf_config["path"])
    else:
        raise ComfyWorkflowError(f"Workflow '{wf_key}' 缺少 workflow_file/path")
    if not path.exists():
        raise ComfyWorkflowError(f"Workflow 文件不存在: {path}")
    mtime = path.stat().st_mtime_ns
    if wf_key in _workflow_cache and _workflow_cache_mtime.get(wf_key) == mtime:
        return copy.deepcopy(_workflow_cache[wf_key])
    try:
        with open(path, "r", encoding="utf-8") as f:
            _workflow_cache[wf_key] = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise ComfyWorkflowError(f"Workflow 文件无法解析: {e}") from e
    _workflow_cache_mtime[wf_key] = mtime
    return copy.deepcopy(_workflow_cache[wf_key])


def _apply_prompt_and_seed(workflow: dict, wf_config: dict, full_prompt: str,
                          seed: int) -> None:
    """注入 prompt 和所有 seed 相关节点。"""
    if full_prompt:
        _set_node_input(workflow, wf_config["prompt_node"],
                        wf_config["prompt_key"], full_prompt)
    _set_node_input(workflow, wf_config["seed_node"], wf_config["seed_key"], seed)
    if "sd_upscale_node" in wf_config:
        _set_node_input(workflow, wf_config["sd_upscale_node"],
                        wf_config.get("sd_upscale_seed_key", "seed"), seed)
    if "facedetailer_seed_node" in wf_config:
        _set_node_input(workflow, wf_config["facedetailer_seed_node"],
                        wf_config["facedetailer_seed_key"], seed)


def _apply_model(workflow: dict, wf_config: dict, settings: dict) -> None:
    """注入模型节点。model_selectable=False 时保留 workflow 默认模型。"""
    if wf_config.get("model_selectable", True):
        _set_node_input(workflow, wf_config["model_node"], wf_config["model_key"],
                        settings.get("comfy_model") or wf_config.get("default_model", ""))


def _apply_dimensions(workflow: dict, wf_config: dict, settings: dict) -> None:
    """注入图片尺寸、视频宽高和帧数。"""
    _apply_image_dimensions(workflow, wf_config, settings)
    _apply_resolution_selector(workflow, wf_config, settings)
    if "video_width_node" in wf_config:
        aspect = settings.get("comfy_video_aspect", "9:16")
        resolution = settings.get("comfy_video_resolution", "480p")
        w, h = compute_video_dimensions(aspect, resolution)
        mult = wf_config.get("video_dim_multiple")
        if mult:
            w = (w + mult // 2) // mult * mult
            h = (h + mult // 2) // mult * mult
        _set_node_input(workflow, wf_config["video_width_node"],
                        wf_config["video_width_key"], w)
        _set_node_input(workflow, wf_config["video_height_node"],
                        wf_config["video_height_key"], h)
    if "video_selector_node" in wf_config:
        # ResolutionSelector（t2v 无图场景）：注入比例枚举 + megapixels
        aspect = settings.get("comfy_video_aspect", "9:16")
        resolution = settings.get("comfy_video_resolution", "480p")
        rs_aspect = COMFY_VIDEO_ASPECTS.get(aspect, COMFY_VIDEO_ASPECTS["9:16"])["rs_aspect"]
        megapixels = COMFY_VIDEO_RESOLUTIONS.get(
            resolution, COMFY_VIDEO_RESOLUTIONS["480p"])["megapixels"]
        _set_node_input(workflow, wf_config["video_selector_node"],
                        wf_config["video_selector_aspect_key"], rs_aspect)
        _set_node_input(workflow, wf_config["video_selector_node"],
                        wf_config["video_selector_mp_key"], megapixels)
    if "video_megapixels_node" in wf_config:
        # ImageScaleToTotalPixels（i2v/flf2v 自动比例链）：只注入画质
        resolution = settings.get("comfy_video_resolution", "480p")
        megapixels = COMFY_VIDEO_RESOLUTIONS.get(
            resolution, COMFY_VIDEO_RESOLUTIONS["480p"])["megapixels"]
        _set_node_input(workflow, wf_config["video_megapixels_node"],
                        wf_config["video_megapixels_key"], megapixels)
    if "video_frames_node" in wf_config:
        frames_key = str(settings.get("comfy_video_frames",
                                      COMFY_VIDEO_FRAMES_PRESETS[DEFAULT_VIDEO_FRAMES_KEY]["frames"]))
        cfg = COMFY_VIDEO_FRAMES_PRESETS.get(frames_key,
                                             COMFY_VIDEO_FRAMES_PRESETS[DEFAULT_VIDEO_FRAMES_KEY])
        _set_node_input(workflow, wf_config["video_frames_node"],
                        wf_config["video_frames_key"], cfg["frames"])
    if "video_duration_node" in wf_config:
        # MiniMax H3 时长链（105:111 秒数 → 表达式 round(a*24) 还原帧数）
        frames_key = str(settings.get("comfy_video_frames",
                                      COMFY_VIDEO_FRAMES_PRESETS[DEFAULT_VIDEO_FRAMES_KEY]["frames"]))
        cfg = COMFY_VIDEO_FRAMES_PRESETS.get(frames_key,
                                             COMFY_VIDEO_FRAMES_PRESETS[DEFAULT_VIDEO_FRAMES_KEY])
        _set_node_input(workflow, wf_config["video_duration_node"],
                        wf_config["video_duration_key"], round(cfg["frames"] / 24, 2))


def _apply_image_dimensions(workflow: dict, wf_config: dict, settings: dict) -> None:
    """注入精确宽高到 EmptyLatentImage 节点。"""
    if "width_node" in wf_config:
        _set_node_input(workflow, wf_config["width_node"], wf_config["width_key"],
                        settings.get("comfy_width", 960))
        _set_node_input(workflow, wf_config["height_node"], wf_config["height_key"],
                        settings.get("comfy_height", 1280))


def _apply_resolution_selector(workflow: dict, wf_config: dict, settings: dict) -> None:
    """注入 aspect_ratio + megapixels 到 ResolutionSelector 节点，
    确保 tile 计算节点读取到与主图一致的尺寸参数。
    当 preset lookup 失败（旧用户尺寸未迁移）时，跳过注入并记录 warning。"""
    rs_node = wf_config.get("resolution_selector_node")
    if not rs_node:
        return
    w = settings.get("comfy_width", 960)
    h = settings.get("comfy_height", 1280)
    for preset in COMFY_SIZE_PRESETS.values():
        if preset["width"] == w and preset["height"] == h:
            _set_node_input(workflow, rs_node,
                            wf_config["resolution_selector_aspect_key"],
                            preset["rs_ar"])
            _set_node_input(workflow, rs_node,
                            wf_config["resolution_selector_mp_key"],
                            preset["rs_mp"])
            return
    logger.warning("ResolutionSelector lookup 失败: %dx%d 不在预设中，跳过 RS 注入", w, h)


def _apply_images(workflow: dict, wf_config: dict,
                  uploaded_image: str | None = None,
                  uploaded_images: dict[str, str] | None = None) -> None:
    """注入上传图片路径至 load_image 节点（支持单图和多图角色映射）。"""
    if uploaded_images and "load_image_nodes" in wf_config:
        image_nodes = wf_config["load_image_nodes"]
        for role, filename in uploaded_images.items():
            cfg = image_nodes.get(role)
            if cfg and filename:
                _set_node_input(workflow, cfg["node"], cfg["key"], filename)
    elif uploaded_image and "load_image_node" in wf_config:
        _set_node_input(workflow, wf_config["load_image_node"],
                        wf_config["load_image_key"], uploaded_image)


def _apply_switches(workflow: dict, wf_config: dict, settings: dict) -> None:
    """三级级联开关 reroute（Upscale / PussyDetailer / FaceDetailer）。

    每个 OFF 开关将上游源直连到下游节点，跳过对应处理环节。
    """
    pre_pussy_source = None
    pre_face_source = None

    if "upscale_switch_node" in wf_config:
        upscale_on = settings.get("comfy_upscale_enabled", True)
        pre_pussy_source = (
            wf_config["upscale_switch_on"] if upscale_on
            else wf_config["upscale_switch_off"]
        )
        _set_node_input(workflow, wf_config["upscale_switch_node"],
                        wf_config["upscale_switch_key"], pre_pussy_source)

    if "pussydetailer_switch_node" in wf_config:
        if "upscale_switch_node" not in wf_config:
            # 缺少上游开关时级联源为 None，写入节点 input 会导致 ComfyUI 400
            raise ComfyWorkflowError(
                "级联开关配置错误: 'pussydetailer_switch_node' 依赖上游 "
                "'upscale_switch_node'"
            )
        pussydetailer_on = settings.get("comfy_pussydetailer_enabled", True)
        pre_face_source = (
            [wf_config["upscale_switch_node"], 0] if pussydetailer_on
            else pre_pussy_source
        )
        _set_node_input(workflow, wf_config["pussydetailer_switch_node"],
                        wf_config["pussydetailer_switch_key"], pre_face_source)

    if "facedetailer_switch_node" in wf_config:
        facedetailer_on = settings.get("comfy_facedetailer_enabled", True)
        if "facedetailer_switch_on" in wf_config:
            off_target = wf_config["facedetailer_switch_off"]
            if (not facedetailer_on
                    and "facedetailer_switch_off_no_upscale" in wf_config):
                upscale_on = settings.get("comfy_upscale_enabled", True)
                if not upscale_on:
                    off_target = wf_config["facedetailer_switch_off_no_upscale"]
            save_source = (
                wf_config["facedetailer_switch_on"] if facedetailer_on
                else off_target
            )
        else:
            if "pussydetailer_switch_node" not in wf_config:
                # 同上：缺少上游开关时级联源为 None
                raise ComfyWorkflowError(
                    "级联开关配置错误: 'facedetailer_switch_node' 未配置 "
                    "'facedetailer_switch_on' 时依赖上游 'pussydetailer_switch_node'"
                )
            save_source = (
                [wf_config["pussydetailer_switch_node"], 0] if facedetailer_on
                else pre_face_source
            )
        _set_node_input(workflow, wf_config["facedetailer_switch_node"],
                        wf_config["facedetailer_switch_key"], save_source)


def _apply_lora(workflow: dict, wf_config: dict, settings: dict,
                prompt_fallback: str) -> None:
    """注入 LoRA 相关配置（变体 + detailer 提示词 + krea2 开关/强度）。"""
    if "lora_node" in wf_config:
        variant_key = settings.get("comfy_lora_variant", "normal")
        variant = COMFY_LORA_VARIANTS.get(variant_key, COMFY_LORA_VARIANTS["normal"])
        _set_node_input(workflow, wf_config["lora_node"], "lora_1.on",
                        variant.get("lora_1_on", True))
        _set_node_input(workflow, wf_config["lora_node"], "lora_2.on",
                        variant["lora_2_on"])
        _set_node_input(workflow, wf_config["lora_node"], "lora_3.on",
                        variant["lora_3_on"])
    if "detailer_prompt_node" in wf_config:
        variant_key = settings.get("comfy_lora_variant", "normal")
        variant = COMFY_LORA_VARIANTS.get(variant_key, COMFY_LORA_VARIANTS["normal"])
        detailer_prompt = variant["detailer_prompt"] or prompt_fallback
        _set_node_input(workflow, wf_config["detailer_prompt_node"],
                        wf_config["detailer_prompt_key"], detailer_prompt)
    if "lora_enable_node" in wf_config:
        lora_enabled = settings.get("comfy_krea2_lora_enabled", False)
        _set_node_input(workflow, wf_config["lora_enable_node"],
                        wf_config["lora_enable_key"], lora_enabled)
    if "lora_strength_node" in wf_config:
        strength = max(-15, min(10, settings.get("comfy_krea2_lora_strength", 5)))
        _set_node_input(workflow, wf_config["lora_strength_node"],
                        wf_config["lora_strength_key"], strength)


def _apply_prompt_optimize(workflow: dict, wf_config: dict, settings: dict) -> None:
    """注入提示词优化三态（关闭/NSFW/SFW）。

    - 节点 82（Refine Prompt? Boolean）：nsfw/sfw 时为 True，off 时 False。
    - 节点 91（System Prompt）：按模式注入对应文本。
    """
    if "prompt_optimize_node" in wf_config:
        mode = settings.get("comfy_prompt_optimize", "nsfw")
        if isinstance(mode, bool):
            mode = "nsfw" if mode else "off"
        mode_cfg = COMFY_PROMPT_OPTIMIZE_MODES.get(mode, COMFY_PROMPT_OPTIMIZE_MODES["nsfw"])
        enabled = mode_cfg["system"] is not None
        _set_node_input(workflow, wf_config["prompt_optimize_node"],
                        wf_config["prompt_optimize_key"], enabled)
        if enabled and "prompt_system_node" in wf_config:
            _set_node_input(workflow, wf_config["prompt_system_node"],
                            wf_config["prompt_system_key"], mode_cfg["system"])
        # 优化开启时注入随机 seed，避免 ComfyUI 缓存导致 PreviewAny 输出缺失
        if enabled and "prompt_optimize_seed_node" in wf_config:
            seed_node = wf_config["prompt_optimize_seed_node"]
            seed_key = wf_config["prompt_optimize_seed_key"]
            # seed_key 是 "sampling_mode.seed" 这类扁平带点的 key，必须直接赋值，
            # 不能走 _set_node_input（会按点号分割成嵌套路径）
            try:
                workflow[seed_node]["inputs"][seed_key] = random.randint(0, 2**32 - 1)
            except KeyError:
                logger.warning(
                    "prompt_optimize seed 注入失败: 节点 '%s' 或字段 '%s' 不存在",
                    seed_node, seed_key,
                )


def _apply_face_prompt(workflow: dict, wf_config: dict, face_prompt: str | None,
                       settings: dict) -> None:
    """注入脸部重绘提示词（FaceDetailer 节点）。"""
    if "face_detailer_prompt_node" in wf_config:
        face_text = face_prompt or settings.get("comfy_face_prompt", "")
        if face_text:
            _set_node_input(workflow, wf_config["face_detailer_prompt_node"],
                            wf_config["face_detailer_prompt_key"], face_text)


def _apply_upscale_prompts(workflow: dict, wf_config: dict,
                           prompt_fallback: str, raw_prompt: str,
                           settings: dict, face_prompt: str | None) -> None:
    """注入 SD Upscale 提示词（脸部提示词 + NSFW 身体关键词）。

    comfy_sd_upscale_prompt_inject=False 时跳过注入，节点保留 workflow 内置文本。
    """
    if ("sd_upscale_prompt_node" in wf_config
            and settings.get("comfy_sd_upscale_prompt_inject", True)):
        base = (face_prompt or settings.get("comfy_face_prompt", "")
                or prompt_fallback)
        found = [kw for kw in NSFW_BODY_KEYWORDS
                 if kw.lower() in raw_prompt.lower()]
        upscale_text = f"{base}, {', '.join(found)}" if found else base
        _set_node_input(workflow, wf_config["sd_upscale_prompt_node"],
                        wf_config["sd_upscale_prompt_key"], upscale_text)


def _build_payload(workflow: dict, prompt: str, seed: int, settings: dict,
                   uploaded_image: str | None = None,
                   uploaded_images: dict[str, str] | None = None,
                   face_prompt: str | None = None) -> dict:
    """根据 workflow 配置替换 prompt、seed、模型、分辨率等节点。"""
    _, wf = _get_wf_config(settings)

    # 计算完整最终提示词（含 prefix / append）
    full_prompt = settings.get("comfy_prompt", "") or prompt
    if wf.get("ignore_user_prompt"):
        full_prompt = prompt
    if full_prompt:
        prefix = wf.get("prompt_prefix", "")
        if prefix:
            full_prompt = prefix + full_prompt
        if wf.get("append_user_prompt"):
            node_id = wf["prompt_node"]
            nid = node_id[0] if isinstance(node_id, list) else node_id
            default_prompt = workflow[nid]["inputs"].get(wf["prompt_key"], "")
            if default_prompt:
                full_prompt = default_prompt + ", " + full_prompt

    _apply_prompt_and_seed(workflow, wf, full_prompt, seed)
    _apply_model(workflow, wf, settings)
    _apply_dimensions(workflow, wf, settings)
    _apply_images(workflow, wf, uploaded_image, uploaded_images)
    _apply_switches(workflow, wf, settings)
    _apply_lora(workflow, wf, settings, full_prompt)
    _apply_prompt_optimize(workflow, wf, settings)
    _apply_face_prompt(workflow, wf, face_prompt, settings)
    _apply_upscale_prompts(workflow, wf, full_prompt, prompt, settings, face_prompt)

    return workflow


def validate_workflow() -> None:
    """校验所有配置的 workflow 文件存在且关键节点正确。"""
    for wf_key, wf in COMFY_WORKFLOWS.items():
        workflow = _load_workflow(wf_key)

        # 强制要求 is_img2img 字段
        if "is_img2img" not in wf:
            raise ComfyWorkflowError(
                f"Workflow '{wf_key}': 缺少 'is_img2img' 字段（必须显式指定 True/False）"
            )

        _set_node_input(workflow, wf["prompt_node"], wf["prompt_key"], "test")
        _set_node_input(workflow, wf["seed_node"], wf["seed_key"], 1)

        # model_selectable=False 时跳过 model 校验（不注入 model）
        if wf.get("model_selectable", True):
            _set_node_input(workflow, wf["model_node"], wf["model_key"],
                            wf.get("default_model", ""))

            # 校验 model_loader_class 与实际节点 class_type 一致
            model_node = wf.get("model_node")
            expected_class = wf.get("model_loader_class")
            if model_node is not None and expected_class:
                node_ids = model_node if isinstance(model_node, list) else [model_node]
                for nid in node_ids:
                    node = workflow.get(str(nid))
                    if not node:
                        raise ComfyWorkflowError(
                            f"Workflow '{wf_key}': model_node '{nid}' 不存在"
                        )
                    actual_class = node.get("class_type")
                    if actual_class != expected_class:
                        raise ComfyWorkflowError(
                            f"Workflow '{wf_key}': model_loader_class "
                            f"'{expected_class}' 与节点 {nid} "
                            f"class_type '{actual_class}' 不匹配"
                        )

        if "width_node" in wf:
            _set_node_input(workflow, wf["width_node"], wf["width_key"], 768)
            _set_node_input(workflow, wf["height_node"], wf["height_key"], 1280)
        if "load_image_nodes" in wf:
            for role, cfg in wf["load_image_nodes"].items():
                _set_node_input(workflow, cfg["node"], cfg["key"], f"test_{role}.png")
        elif "load_image_node" in wf:
            _set_node_input(workflow, wf["load_image_node"], wf["load_image_key"], "test.png")
        if "video_width_node" in wf:
            _validate_node_input(workflow, wf, wf_key, "video_width_node",
                                 "video_width_key", 480)
            _validate_node_input(workflow, wf, wf_key, "video_height_node",
                                 "video_height_key", 848)
        if "video_selector_node" in wf:
            _validate_node_input(workflow, wf, wf_key, "video_selector_node",
                                 "video_selector_aspect_key", "9:16 (Portrait Widescreen)")
            _validate_node_input(workflow, wf, wf_key, "video_selector_node",
                                 "video_selector_mp_key", 0.4)
        if "video_megapixels_node" in wf:
            _validate_node_input(workflow, wf, wf_key, "video_megapixels_node",
                                 "video_megapixels_key", 0.4)
        if "video_frames_node" in wf:
            _validate_node_input(workflow, wf, wf_key, "video_frames_node",
                                 "video_frames_key", 81)
        if "video_duration_node" in wf:
            _validate_node_input(workflow, wf, wf_key, "video_duration_node",
                                 "video_duration_key", 5.17)

    logger.info("所有 ComfyUI workflow 校验通过")


def _validate_node_input(workflow: dict, wf: dict, wf_key: str,
                         node_field: str, key_field: str, value) -> None:
    """注入校验：key 必须已存在于节点 inputs。

    _set_node_input 对不存在的 key 是静默创建（dict 赋值），需显式检查，
    否则配置错误只能在真实生成时暴露。
    """
    actual_key = wf[key_field]
    node_ids = wf[node_field] if isinstance(wf[node_field], list) else [wf[node_field]]
    for nid in node_ids:
        node = workflow.get(str(nid))
        if node is None:
            raise ComfyWorkflowError(
                f"Workflow '{wf_key}': {node_field} '{nid}' 不存在"
            )
        if actual_key not in node.get("inputs", {}):
            raise ComfyWorkflowError(
                f"Workflow '{wf_key}': {key_field} '{actual_key}' "
                f"不在节点 {nid} 的 inputs 中"
            )
    _set_node_input(workflow, wf[node_field], actual_key, value)


# ── 模型列表缓存与解析 ─────────────────────────────────────

_MODELS_CACHE_TTL = 60   # 成功缓存秒数
_MODELS_FAIL_TTL = 15    # 失败缓存秒数（避免服务器宕机时每次请求都卡超时）
# key: (loader_class, model_key) -> (过期时间戳, 模型列表；None 表示拉取失败)
_models_cache: dict[tuple[str, str], tuple[float, list[str] | None]] = {}


async def _fetch_models(loader_class: str, model_key: str) -> list[str] | None:
    """从 /object_info 拉取模型列表（带 TTL 缓存）。失败返回 None，不抛异常。"""
    cache_key = (loader_class, model_key)
    now = time.monotonic()
    cached = _models_cache.get(cache_key)
    if cached and now < cached[0]:
        return cached[1]
    try:
        async with httpx.AsyncClient(base_url=COMFY_API_BASE, timeout=10) as client:
            resp = await client.get(f"/object_info/{loader_class}")
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("拉取模型列表失败（%s）: %s", loader_class, e)
        _models_cache[cache_key] = (now + _MODELS_FAIL_TTL, None)
        return None
    required = data.get(loader_class, {}).get("input", {}).get("required", {})
    if model_key not in required:
        logger.warning(
            "model_key '%s' 不在 %s 的 required 字段中，模型列表为空",
            model_key, loader_class,
        )
        models: list[str] = []
    else:
        raw = required[model_key][0]
        models = raw if isinstance(raw, list) else []
    _models_cache[cache_key] = (now + _MODELS_CACHE_TTL, models)
    return models


async def get_models(settings: dict) -> list[str]:
    """从 /object_info 获取当前 workflow 的可用模型列表（带 TTL 缓存）。

    网络失败时抛 ComfyApiError（菜单调用方据此提示服务离线）。
    """
    wf_key, wf = _get_wf_config(settings)
    loader_class = wf.get("model_loader_class")
    if not loader_class:
        logger.warning("Workflow '%s' 缺少 model_loader_class，模型列表为空", wf_key)
        return []
    models = await _fetch_models(loader_class, wf["model_key"])
    if models is None:
        raise ComfyApiError("无法从 ComfyUI 获取模型列表（详见上方 warning）")
    return models


_VERSION_SUFFIX_RE = re.compile(r"(?:[_-]?[Vv]?\d[\d.]*)+$")
_MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf")


def _derive_model_prefix(model_name: str) -> str:
    """从模型文件名推导家族前缀：去扩展名，再剥离尾部版本号。

    例：moodyKrea2Mix_v50.safetensors → moodyKrea2Mix
        moodyPornMix_zitV9.safetensors → moodyPornMix_zit
    无法剥离时返回去扩展名后的全名（即只做精确匹配）。
    """
    stem = model_name
    for ext in _MODEL_EXTS:
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    return _VERSION_SUFFIX_RE.sub("", stem) or stem


def _natural_sort_key(name: str) -> list:
    """数字分段比较的自然排序 key，避免 'V13' < 'V9' 的字符串序问题。

    分段带类型标记（数字段 0 / 文本段 1），防止异构名字比较时 int/str 碰撞。
    """
    return [(0, int(p)) if p.isdigit() else (1, p)
            for p in re.split(r"(\d+)", name)]


async def resolve_model(wf_key: str, wf_config: dict, settings: dict) -> str | None:
    """对照 ComfyUI 实时模型列表解析实际应注入的模型。

    解析链：用户 comfy_model → default_model → 家族最新
    （default_model_pattern glob，或从 default_model 推导前缀）→ 列表第一个。
    列表拉取失败返回 None（调用方保持未解析的原行为，无回归）。
    """
    loader_class = wf_config.get("model_loader_class")
    model_key = wf_config.get("model_key")
    if not loader_class or not model_key:
        return None
    models = await _fetch_models(loader_class, model_key)
    if not models:
        return None

    user_model = settings.get("comfy_model")
    default_model = wf_config.get("default_model", "")
    if user_model in models:
        return user_model
    if default_model in models:
        if user_model:
            logger.info("Workflow '%s': 用户模型 '%s' 已失效，回退默认 '%s'",
                        wf_key, user_model, default_model)
        return default_model

    # 家族匹配：显式 glob 优先，否则从 default_model 推导前缀
    pattern = wf_config.get("default_model_pattern")
    if pattern:
        family = [m for m in models if fnmatch.fnmatchcase(m, pattern)]
    else:
        prefix = _derive_model_prefix(default_model)
        family = [m for m in models if m.startswith(prefix)] if prefix else []
    if family:
        picked = max(family, key=_natural_sort_key)
        logger.info("Workflow '%s': 默认模型 '%s' 已失效，跟随家族最新 '%s'",
                    wf_key, default_model or user_model, picked)
        return picked

    logger.warning(
        "Workflow '%s': 模型 '%s' 及家族均无匹配，兜底列表第一个 '%s'",
        wf_key, user_model or default_model, models[0])
    return models[0]


async def upload_image(image_bytes: bytes, filename: str | None = None) -> str:
    """上传图片到 ComfyUI，返回服务器上的文件名。默认生成唯一文件名避免互相覆盖。"""
    if filename is None:
        filename = f"tg_{uuid.uuid4().hex[:12]}.png"
    async with httpx.AsyncClient(base_url=COMFY_API_BASE, timeout=30) as client:
        resp = await client.post(
            "/upload/image",
            files={"image": (filename, image_bytes, "image/png")},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("name", filename)


async def free_memory() -> None:
    """卸载 ComfyUI 已加载的模型并释放显存（供 Ollama 等共享 GPU 服务使用）。

    失败抛异常，由调用方决定降级策略。
    """
    async with httpx.AsyncClient(base_url=COMFY_API_BASE, timeout=30) as client:
        resp = await client.post(
            "/free",
            json={"unload_models": True, "free_memory": True},
        )
        resp.raise_for_status()


# ── API 调用 ──────────────────────────────────────────────

async def _submit_prompt(client: httpx.AsyncClient, workflow: dict) -> str:
    resp = await client.post("/prompt", json={"prompt": workflow})
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        # ComfyUI 400 会返回节点校验细节: {"error": {"type","message"}, "node_errors": {...}}
        detail = ""
        try:
            err = e.response.json()
            parts = []
            error = err.get("error") or {}
            if isinstance(error, dict) and error.get("message"):
                parts.append(str(error["message"]))
            node_errors = err.get("node_errors") or {}
            for nid, nerr in list(node_errors.items())[:3]:
                msgs = [m.get("message", "") for m in nerr.get("errors", [])
                        if isinstance(m, dict)]
                parts.append(f"节点 {nid}: {'; '.join(m for m in msgs if m)[:100]}")
            detail = "; ".join(p for p in parts if p)
        except Exception:
            pass
        raise ComfyApiError(
            f"ComfyUI 拒绝 prompt 提交 ({e.response.status_code}): "
            f"{detail or e.response.text[:300]}"
        ) from e
    data = resp.json()
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise ComfyApiError(f"ComfyUI 未返回 prompt_id: {data}")
    return prompt_id


async def _poll_result(client: httpx.AsyncClient, prompt_id: str,
                     wf_config: dict | None = None,
                     wf_key: str | None = None,
                     progress_callback=None) -> tuple[ComfyOutput, str | None]:
    deadline = time.monotonic() + COMFY_TIMEOUT
    start = time.monotonic()
    last_beat = start
    output_node_classes = {"SaveImage", "SaveImageAdvanced", "Image Saver Simple", "SaveVideo", "VHS_VideoCombine"}
    while time.monotonic() < deadline:
        # 心跳：长任务（视频可达 20+ 分钟）期间定期汇报已用时间，避免状态看似卡死
        if progress_callback:
            now = time.monotonic()
            if now - last_beat >= COMFY_PROGRESS_HEARTBEAT_INTERVAL:
                last_beat = now
                try:
                    await progress_callback(int(now - start))
                except Exception:
                    logger.debug("进度回调异常", exc_info=True)
        try:
            resp = await client.get(f"/history/{prompt_id}")
            resp.raise_for_status()
        except httpx.TransportError as e:
            # 长任务需轮询数百次，瞬时网络错误不应终结整个生成（deadline 兜底）
            logger.warning("轮询 /history 网络错误，继续等待: %s", e)
            await asyncio.sleep(COMFY_POLL_INTERVAL)
            continue
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                logger.warning("轮询 /history 返回 %s，继续等待",
                               e.response.status_code)
                await asyncio.sleep(COMFY_POLL_INTERVAL)
                continue
            raise  # 4xx 属于永久错误，立即失败
        history = resp.json()

        item = history.get(prompt_id)
        if not item:
            await asyncio.sleep(COMFY_POLL_INTERVAL)
            continue

        status = item.get("status", {})
        if status.get("status_str") == "error":
            raise ComfyApiError(f"ComfyUI 生成失败: {status}")

        outputs = item.get("outputs", {})
        logger.debug("ComfyUI outputs: %s", list(outputs.keys()))

        # 捕获优化后的提示词文本（PreviewAny 节点的 UI 输出）
        optimized_prompt = None
        if wf_config and "prompt_output_node" in wf_config:
            prompt_nid = wf_config["prompt_output_node"]
            node_out = outputs.get(prompt_nid, {})
            for text_key in ("text", "string", "value"):
                text_vals = node_out.get(text_key)
                if text_vals and isinstance(text_vals, list) and len(text_vals) > 0:
                    optimized_prompt = str(text_vals[0])
                    logger.info("ComfyUI 捕获优化提示词 (node=%s): %s",
                                prompt_nid, optimized_prompt[:80])
                    break

        # 收集所有候选输出，优先返回 Save 类节点（避免取到 PreviewImage 中间结果）
        candidates = []
        for _node_id, node_output in outputs.items():
            if wf_config and wf_config.get("output_type") == "video":
                file_keys = ("videos", "gifs", "images")
            else:
                file_keys = ("images", "gifs", "videos")
            for file_type_rank, file_key in enumerate(file_keys):
                files = node_output.get(file_key)
                if files and len(files) > 0:
                    file_info = files[0]
                    filename = file_info.get("filename")
                    logger.debug("ComfyUI 取图候选: node=%s, file_key=%s, filename=%s", _node_id, file_key, filename)
                    if filename:
                        # Save 类节点优先级 0，其他节点（PreviewImage 等）优先级 1；
                        # 同级再按 file_key 序位（视频任务 videos/gifs 优先于 images）
                        cached_wf = _workflow_cache.get(wf_key or "", {})
                        node = cached_wf.get(_node_id, {})
                        class_type = node.get("class_type", "") if isinstance(node, dict) else ""
                        priority = 0 if class_type in output_node_classes else 1
                        candidates.append((priority, file_type_rank, _node_id, file_info, node_output, file_key))
        if candidates:
            candidates.sort(key=lambda x: (x[0], x[1]))
            priority, _, _node_id, file_info, _, file_key = candidates[0]
            filename = file_info.get("filename")
            logger.info(f"ComfyUI 取图: node={_node_id}, file_key={file_key}, filename={filename}, priority={priority}")
            data = await _download_image(
                client,
                filename=filename,
                subfolder=file_info.get("subfolder", ""),
                image_type=file_info.get("type", "output"),
            )
            return ComfyOutput(
                data=data,
                filename=filename,
                kind=_detect_output_kind(filename),
            ), optimized_prompt

        await asyncio.sleep(COMFY_POLL_INTERVAL)

    logger.warning(
        "ComfyUI 生成超时 (%ss), workflow=%s, 输出节点: %s",
        COMFY_TIMEOUT,
        wf_config.get("label", "?") if wf_config else (wf_key or "?"),
        [nid for nid, node in _workflow_cache.get(
            wf_key or "", {}
        ).items() if isinstance(node, dict) and node.get("class_type") in output_node_classes],
    )
    raise ComfyTimeoutError(f"ComfyUI 生成超时 ({COMFY_TIMEOUT}s)")


async def _download_image(
    client: httpx.AsyncClient,
    filename: str,
    subfolder: str = "",
    image_type: str = "output",
) -> bytes:
    resp = await client.get(
        "/view",
        params={"filename": filename, "subfolder": subfolder, "type": image_type},
    )
    resp.raise_for_status()
    return resp.content


# ── 对外入口 ──────────────────────────────────────────────

async def generate(prompt: str, settings: dict, seed: int,
                   uploaded_image: str | None = None,
                   uploaded_images: dict[str, str] | None = None,
                   face_prompt: str | None = None,
                   progress_callback=None) -> tuple[ComfyOutput, int, str | None]:
    """提交 workflow 并轮询结果。progress_callback(elapsed_seconds) 在长任务期间定期调用。"""
    # 解析为实际生效的 wf_key（用户旧 key 已被删除时回退），缓存键随之正确
    wf_key, wf_config = _get_wf_config(settings)
    workflow = _load_workflow(wf_key)
    # 对照服务器实时模型列表解析模型：默认值/用户保存的模型被删后自动跟随家族最新
    if wf_config.get("model_selectable", True) and wf_config.get("model_node"):
        resolved = await resolve_model(wf_key, wf_config, settings)
        if resolved:
            settings = {**settings, "comfy_model": resolved}
    payload = _build_payload(workflow, prompt, seed, settings,
                             uploaded_image=uploaded_image,
                             uploaded_images=uploaded_images,
                             face_prompt=face_prompt)
    timeout = httpx.Timeout(connect=10, read=COMFY_TIMEOUT, write=30, pool=10)
    async with httpx.AsyncClient(base_url=COMFY_API_BASE, timeout=timeout) as client:
        prompt_id = await _submit_prompt(client, payload)
        output, optimized_prompt = await _poll_result(
            client, prompt_id, wf_config, wf_key, progress_callback)
    return output, seed, optimized_prompt
