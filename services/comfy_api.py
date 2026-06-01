import asyncio
import copy
import json
import logging
import time
from pathlib import Path

import httpx

from config import (
    COMFY_API_BASE,
    COMFY_WORKFLOWS,
    COMFY_DEFAULT_WORKFLOW,
    COMFY_POLL_INTERVAL,
    COMFY_TIMEOUT,
)

logger = logging.getLogger(__name__)

_workflow_cache: dict[str, dict] = {}


# ── 自定义异常 ───────────────────────────────────────────

class ComfyApiError(Exception):
    """ComfyUI API 错误（提交失败、无 prompt_id、生成报错）。"""


class ComfyWorkflowError(Exception):
    """Workflow 文件缺失、无法解析或节点结构不正确。"""


class ComfyTimeoutError(Exception):
    """ComfyUI 生成超时。"""


# ── Workflow 配置工具 ────────────────────────────────────

def _get_wf_config(settings: dict) -> dict:
    wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
    return COMFY_WORKFLOWS.get(wf_key, COMFY_WORKFLOWS[COMFY_DEFAULT_WORKFLOW])


def _set_node_input(workflow: dict, node_id: str, input_key: str, value):
    try:
        workflow[node_id]["inputs"][input_key] = value
    except KeyError as e:
        raise ComfyWorkflowError(
            f"Workflow 节点或字段不存在: node_id={node_id}, input_key={input_key}"
        ) from e


def _load_workflow(wf_key: str) -> dict:
    """按 workflow key 加载并缓存，每次返回 deepcopy。"""
    if wf_key in _workflow_cache:
        return copy.deepcopy(_workflow_cache[wf_key])
    wf_config = COMFY_WORKFLOWS.get(wf_key)
    if wf_config is None:
        raise ComfyWorkflowError(f"未知 Workflow: {wf_key}")
    path = Path(wf_config["path"])
    if not path.exists():
        raise ComfyWorkflowError(f"Workflow 文件不存在: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            _workflow_cache[wf_key] = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise ComfyWorkflowError(f"Workflow 文件无法解析: {e}") from e
    return copy.deepcopy(_workflow_cache[wf_key])


def _build_payload(workflow: dict, prompt: str, seed: int, settings: dict,
                   uploaded_image: str | None = None) -> dict:
    """根据 workflow 配置替换 prompt、seed、模型、分辨率等节点。"""
    wf = _get_wf_config(settings)
    # 优先用用户自定义 prompt，否则用传入的 prompt（文生图=用户输入，图生图=空→保留默认）
    final_prompt = settings.get("comfy_prompt", "") or prompt
    if final_prompt:
        _set_node_input(workflow, wf["prompt_node"], wf["prompt_key"], final_prompt)
    _set_node_input(workflow, wf["seed_node"], wf["seed_key"], seed)
    _set_node_input(workflow, wf["model_node"], wf["model_key"],
                    settings.get("comfy_model", wf.get("default_model", "")))
    if "width_node" in wf:
        _set_node_input(workflow, wf["width_node"], wf["width_key"],
                        settings.get("comfy_width", 768))
        _set_node_input(workflow, wf["height_node"], wf["height_key"],
                        settings.get("comfy_height", 1280))
    if uploaded_image and "load_image_node" in wf:
        _set_node_input(workflow, wf["load_image_node"], wf["load_image_key"],
                        uploaded_image)
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
        _set_node_input(workflow, wf["model_node"], wf["model_key"],
                        wf.get("default_model", ""))
        if "width_node" in wf:
            _set_node_input(workflow, wf["width_node"], wf["width_key"], 768)
            _set_node_input(workflow, wf["height_node"], wf["height_key"], 1280)
        if "load_image_node" in wf:
            _set_node_input(workflow, wf["load_image_node"], wf["load_image_key"], "test.png")

        # 校验 model_loader_class 与实际节点 class_type 一致
        model_node = wf.get("model_node")
        expected_class = wf.get("model_loader_class")
        if model_node is not None and expected_class:
            node = workflow.get(str(model_node))
            if not node:
                raise ComfyWorkflowError(
                    f"Workflow '{wf_key}': model_node '{model_node}' 不存在"
                )
            actual_class = node.get("class_type")
            if actual_class != expected_class:
                raise ComfyWorkflowError(
                    f"Workflow '{wf_key}': model_loader_class "
                    f"'{expected_class}' 与节点 {model_node} "
                    f"class_type '{actual_class}' 不匹配"
                )

    logger.info("所有 ComfyUI workflow 校验通过")


async def get_models(settings: dict) -> list[str]:
    """从 /object_info 获取当前 workflow 的可用模型列表。"""
    wf = _get_wf_config(settings)
    loader_class = wf["model_loader_class"]
    model_key = wf["model_key"]
    url = f"/object_info/{loader_class}"
    async with httpx.AsyncClient(base_url=COMFY_API_BASE, timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    node_info = data.get(loader_class, {})
    required = node_info.get("input", {}).get("required", {})
    if model_key not in required:
        logger.warning(
            "Workflow '%s': model_key '%s' 不在 %s 的 required 字段中，模型列表为空",
            settings.get("comfy_workflow", "?"), model_key, loader_class
        )
        return []
    models = required[model_key][0]
    return models if isinstance(models, list) else []


async def upload_image(image_bytes: bytes, filename: str = "input.png") -> str:
    """上传图片到 ComfyUI，返回服务器上的文件名。"""
    async with httpx.AsyncClient(base_url=COMFY_API_BASE, timeout=30) as client:
        resp = await client.post(
            "/upload/image",
            files={"image": (filename, image_bytes, "image/png")},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("name", filename)


# ── API 调用 ──────────────────────────────────────────────

async def _submit_prompt(client: httpx.AsyncClient, workflow: dict) -> str:
    resp = await client.post("/prompt", json={"prompt": workflow})
    resp.raise_for_status()
    data = resp.json()
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise ComfyApiError(f"ComfyUI 未返回 prompt_id: {data}")
    return prompt_id


async def _poll_result(client: httpx.AsyncClient, prompt_id: str, wf_key: str | None = None) -> bytes:
    deadline = time.monotonic() + COMFY_TIMEOUT
    while time.monotonic() < deadline:
        resp = await client.get(f"/history/{prompt_id}")
        resp.raise_for_status()
        history = resp.json()

        item = history.get(prompt_id)
        if not item:
            await asyncio.sleep(COMFY_POLL_INTERVAL)
            continue

        status = item.get("status", {})
        if status.get("status_str") == "error":
            raise ComfyApiError(f"ComfyUI 生成失败: {status}")

        outputs = item.get("outputs", {})
        for _node_id, node_output in outputs.items():
            images = node_output.get("images")
            if images and len(images) > 0:
                img_info = images[0]
                filename = img_info.get("filename")
                if filename:
                    return await _download_image(
                        client,
                        filename=filename,
                        subfolder=img_info.get("subfolder", ""),
                        image_type=img_info.get("type", "output"),
                    )

        await asyncio.sleep(COMFY_POLL_INTERVAL)

    logger.warning(
        "ComfyUI 生成超时 (%ss), workflow=%s, 输出节点: %s",
        COMFY_TIMEOUT,
        wf_key or "?",
        [nid for nid, node in _workflow_cache.get(wf_key, {}).items()
         if isinstance(node, dict) and node.get("class_type") in ("SaveImage", "Image Saver Simple")]
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
                   uploaded_image: str | None = None) -> tuple[bytes, int]:
    wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
    workflow = _load_workflow(wf_key)
    payload = _build_payload(workflow, prompt, seed, settings, uploaded_image)
    timeout = httpx.Timeout(connect=10, read=COMFY_TIMEOUT, write=30, pool=10)
    async with httpx.AsyncClient(base_url=COMFY_API_BASE, timeout=timeout) as client:
        prompt_id = await _submit_prompt(client, payload)
        image_bytes = await _poll_result(client, prompt_id, wf_key)
    return image_bytes, seed
