"""批量生成中国女明星肖像 — 用于检查模型训练数据包含情况。

直接调用 services/comfy_api（不经过 Bot/队列/翻译），工作流固定 z-image-turbo：
模型 z_image_turbo_bf16.safetensors、种子随机、尺寸 1024×1024、使用用户输入 prompt
（comfy_prompt 为空 = 不覆盖）。输出保存到 data/celebrity_check/。

用法（项目根目录）：
    uv run python scripts/batch_celebrity_portraits.py              # 全部名单各 1 张
    uv run python scripts/batch_celebrity_portraits.py --per 2      # 每人 2 张
    uv run python scripts/batch_celebrity_portraits.py --limit 5    # 只跑前 5 个
    uv run python scripts/batch_celebrity_portraits.py --names 杨幂,刘亦菲
"""

import argparse
import asyncio
import copy
import logging
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEFAULT_USER_SETTINGS  # noqa: E402
from services import comfy_api  # noqa: E402

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# ── 名单（按知名度分层，便于观察模型数据覆盖边界；可自行增删） ──
NAMES = [
    # 一线 / 高曝光
    "杨幂", "赵丽颖", "刘亦菲", "迪丽热巴", "古力娜扎", "杨颖",
    "唐嫣", "刘诗诗", "杨紫", "范冰冰", "章子怡", "周迅",
    "巩俐", "李冰冰", "孙俪", "高圆圆", "汤唯", "舒淇",
    # 中坚力量
    "倪妮", "周冬雨", "李沁", "景甜", "江疏影", "佟丽娅",
    "张雨绮", "宋茜", "马思纯", "金晨", "钟楚曦", "谭松韵",
    # 新生代 / 流量
    "关晓彤", "赵露思", "白鹿", "虞书欣", "鞠婧祎", "杨超越",
    "欧阳娜娜", "张子枫", "刘浩存", "周也", "张婧仪", "王楚然",
    "田曦薇", "陈都灵", "张天爱", "吴谨言",
]

PROMPT_TEMPLATE = "{name}，明星唯美写真照片，正面上半身特写，证件照，皮肤白皙"

WORKFLOW = "z-image-turbo"
MODEL = "z_image_turbo_bf16.safetensors"
SIZE = 1024
OUT_DIR = Path("data/celebrity_check")


def _build_settings() -> dict:
    settings = copy.deepcopy(DEFAULT_USER_SETTINGS)
    settings.update({
        "backend": "comfyui",
        "comfy_workflow": WORKFLOW,
        "comfy_model": MODEL,
        "comfy_width": SIZE,
        "comfy_height": SIZE,
        "comfy_translate": False,  # 翻译 OFF（本脚本也不经过翻译层）
        "comfy_prompt": "",        # 使用传入的 prompt，不覆盖
        "comfy_seed": -1,          # 随机（实际 seed 在调用处生成）
    })
    return settings


async def generate_one(settings: dict, name: str, out_dir: Path) -> tuple[bool, str]:
    """生成单张并保存。返回 (成功与否, 描述信息)。"""
    prompt = PROMPT_TEMPLATE.format(name=name)
    seed = random.randint(0, 2 ** 50)
    try:
        output, actual_seed, _ = await comfy_api.generate(
            prompt, settings, seed,
        )
        if output.kind != "image":
            return False, f"产出类型异常: {output.kind}"
        out_path = out_dir / f"{name}_{actual_seed}.png"
        out_path.write_bytes(output.data)
        return True, f"seed={actual_seed} -> {out_path}"
    except Exception as e:
        return False, f"失败: {e}"


async def main_async(args) -> None:
    names = args.names.split(",") if args.names else NAMES
    if args.limit:
        names = names[:args.limit]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    settings = _build_settings()
    total = len(names) * args.per
    print(f"工作流={WORKFLOW} 模型={MODEL} 尺寸={SIZE}x{SIZE} "
          f"| {len(names)} 人 x {args.per} 张 = {total} 张 | 输出={out_dir}")

    done = ok_count = 0
    start = time.monotonic()
    for name in names:
        name = name.strip()
        if not name:
            continue
        for _ in range(args.per):
            done += 1
            ok, info = await generate_one(settings, name, out_dir)
            ok_count += ok
            elapsed = time.monotonic() - start
            print(f"[{done}/{total}] {name}: {'✅' if ok else '❌'} {info} "
                  f"(已用 {elapsed:.0f}s)", flush=True)

    print(f"\n完成: {ok_count}/{total} 成功, 耗时 {time.monotonic() - start:.0f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="批量生成女明星肖像（模型数据检查）")
    parser.add_argument("--per", type=int, default=1, help="每人生成张数（默认 1）")
    parser.add_argument("--limit", type=int, default=0, help="只跑名单前 N 个")
    parser.add_argument("--names", type=str, default="",
                        help="逗号分隔的自定义名单（覆盖内置名单）")
    parser.add_argument("--out", type=str, default=str(OUT_DIR), help="输出目录")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
