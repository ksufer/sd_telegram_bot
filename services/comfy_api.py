import asyncio
import copy
import json
import logging
import time
from pathlib import Path

import httpx

from config import (
    COMFY_API_BASE,
    COMFY_WORKFLOW_PATH,
    COMFY_PROMPT_NODE_ID,
    COMFY_PROMPT_INPUT_KEY,
    COMFY_SEED_NODE_ID,
    COMFY_SEED_INPUT_KEY,
    COMFY_POLL_INTERVAL,
    COMFY_TIMEOUT,
)

logger = logging.getLogger(__name__)

_workflow_cache: dict | None = None


# ── 自定义异常 ───────────────────────────────────────────

class ComfyApiError(Exception):
    """ComfyUI API 错误（提交失败、无 prompt_id、生成报错）。"""


class ComfyWorkflowError(Exception):
    """Workflow 文件缺失、无法解析或节点结构不正确。"""


class ComfyTimeoutError(Exception):
    """ComfyUI 生成超时。"""


# ── 内部工具 ─────────────────────────────────────────────

def _set_node_input(workflow: dict, node_id: str, input_key: str, value):
    """安全设置 workflow 节点输入字段，KeyError 时给出明确信息。"""
    try:
        workflow[node_id]["inputs"][input_key] = value
    except KeyError as e:
        raise ComfyWorkflowError(
            f"Workflow 节点或字段不存在: node_id={node_id}, input_key={input_key}"
        ) from e


def _load_workflow() -> dict:
    """启动时加载一次到缓存，后续 deepcopy 使用。"""
    global _workflow_cache
    if _workflow_cache is not None:
        return copy.deepcopy(_workflow_cache)
    path = Path(COMFY_WORKFLOW_PATH)
    if not path.exists():
        raise ComfyWorkflowError(f"Workflow 文件不存在: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            _workflow_cache = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise ComfyWorkflowError(f"Workflow 文件无法解析: {e}") from e
    return copy.deepcopy(_workflow_cache)


def _build_payload(workflow: dict, prompt: str, seed: int) -> dict:
    """替换 workflow 中的 prompt 文本和 seed 值。"""
    _set_node_input(workflow, COMFY_PROMPT_NODE_ID, COMFY_PROMPT_INPUT_KEY, prompt)
    _set_node_input(workflow, COMFY_SEED_NODE_ID, COMFY_SEED_INPUT_KEY, seed)
    return workflow


def validate_workflow() -> None:
    """校验 workflow 文件存在且关键节点结构正确。"""
    workflow = _load_workflow()
    _set_node_input(workflow, COMFY_PROMPT_NODE_ID, COMFY_PROMPT_INPUT_KEY, "test")
    _set_node_input(workflow, COMFY_SEED_NODE_ID, COMFY_SEED_INPUT_KEY, 1)
    logger.info("ComfyUI workflow 校验通过")


# ── API 调用 ──────────────────────────────────────────────

async def _submit_prompt(client: httpx.AsyncClient, workflow: dict) -> str:
    """POST /prompt，返回 prompt_id。"""
    resp = await client.post("/prompt", json={"prompt": workflow})
    resp.raise_for_status()
    data = resp.json()
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise ComfyApiError(f"ComfyUI 未返回 prompt_id: {data}")
    return prompt_id


async def _poll_result(client: httpx.AsyncClient, prompt_id: str) -> bytes:
    """轮询 /history/{prompt_id} 直到完成，返回图片 bytes。"""
    deadline = time.monotonic() + COMFY_TIMEOUT
    while time.monotonic() < deadline:
        resp = await client.get(f"/history/{prompt_id}")
        resp.raise_for_status()
        history = resp.json()

        item = history.get(prompt_id)
        if not item:
            await asyncio.sleep(COMFY_POLL_INTERVAL)
            continue

        # 检查执行是否出错
        status = item.get("status", {})
        if status.get("status_str") == "error":
            raise ComfyApiError(f"ComfyUI 生成失败: {status}")

        # 遍历所有 output node，找第一张图片
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

    raise ComfyTimeoutError(f"ComfyUI 生成超时 ({COMFY_TIMEOUT}s)")


async def _download_image(
    client: httpx.AsyncClient,
    filename: str,
    subfolder: str = "",
    image_type: str = "output",
) -> bytes:
    """GET /view 下载图片。"""
    resp = await client.get(
        "/view",
        params={"filename": filename, "subfolder": subfolder, "type": image_type},
    )
    resp.raise_for_status()
    return resp.content


# ── 对外入口 ──────────────────────────────────────────────

async def generate(prompt: str, seed: int) -> tuple[bytes, int]:
    workflow = _load_workflow()
    payload = _build_payload(workflow, prompt, seed)
    timeout = httpx.Timeout(connect=10, read=COMFY_TIMEOUT, write=30, pool=10)
    async with httpx.AsyncClient(base_url=COMFY_API_BASE, timeout=timeout) as client:
        prompt_id = await _submit_prompt(client, payload)
        image_bytes = await _poll_result(client, prompt_id)
    return image_bytes, seed
