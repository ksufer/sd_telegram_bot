---
name: minimax-h3-prompt-generator
description: 为 MiniMax H3（海螺 H3，API 模型 ID MiniMax-H3）全模态视频生成模型撰写规范提示词的技能。当用户要求生成 MiniMax H3 / Hailuo 3 / 海螺3 的文生视频（T2VA）、图生视频（I2VA）、首尾帧（FL2VA/L2VA）或全能参考（Ref2VA）提示词，或要求把一段创意描述改写为 H3 结构化提示词时使用。产出严格遵循官方 Prompting Guidance 的英文结构化提示词（integrated_multimodal_description / overall_soundscape / non_diegetic_music 三段式，或 Ref2VA 六段式），含 Shot 分镜、d 标签对话语法、说话人 ID 与运镜规范。也适用于解答 H3 提示词语法问题、审查或修复用户已有的 H3 提示词。
---

# MiniMax H3 提示词生成

为用户生成符合 MiniMax 官方规范的 H3 提示词。H3 的视频质量高度依赖提示词结构——开源版不含官方改写模块 H3-Context-IR，随意一段自然语言直跑效果会明显下降，因此**必须**按本技能的结构输出。

## 关键事实（不可违背）

- 提示词**正文一律用英文书写**；对白、歌词、画面内文字保留原语言。
- 字段名、字段顺序是硬规范，不可改写、不可乱序。
- 输出规格：4–15 整数秒、768P/2K、24FPS、32kHz 立体声；提示词上限 7000 字符。
- H3 音视频联合生成：每条提示词都必须处理声音层（对白 / 环境声 / 配乐三选一或多选，无配乐写 `N/A`）。

## 工作流程

1. **判定模式**：
   - 无参考素材 → T2VA（文生视频）
   - 只有首帧图 → I2VA；首帧+尾帧 → FL2VA；只有尾帧图 → L2VA
   - 有参考图/参考视频/参考音频（角色、动作、镜头、风格、声音参考或视频编辑）→ Ref2VA
2. **加载对应规范**：
   - 基础模式（T2VA/I2VA/FL2VA/L2VA）→ 读 `references/base-modes.md`
   - 参考模式（Ref2VA）→ 读 `references/ref2va-mode.md`
   - 用户问及 API 调用参数 → 读 `references/api-notes.md`
3. **描述补正**：凡涉及对参考图片/素材的描述（尤其用户指出模型观察不直白、或目标为能力较弱的本地部署模型），先读 `references/precision-rules.md`，按字面化规范、观察清单、原子化拆解、歧义消除四步处理；该文件末尾留有自定义补正占位区，若使用者已填写自定义规则，叠加应用。
4. **收集创作要素**：若用户描述过于简略，主动补全：主体与外观、动作序列（按发生顺序）、场景与光线、运镜、对白（含语言）、环境声、配乐有无、总时长与分镜数。要素不足且影响输出时先向用户确认。
5. **按模板撰写**：严格套用对应模式的骨架；时长匹配动作总量；时间戳严格递增且落在时长内。
6. **自检**（输出前逐条过一遍 `references/base-modes.md` 或 `ref2va-mode.md` 末尾的检查清单）。

## 输出格式

- 直接输出可直接提交给 H3 的提示词正文（英文、结构化、无 Markdown 代码块以外的多余解释）。
- 若用户要求，可附一段简短中文说明（分镜思路、可调整项），放在提示词之后，用分隔线隔开。
- 除提示词正文与可选说明外，不输出推理过程。

## 核心语法速记（细节以 references 为准）

- 三段式：`integrated_multimodal_description:` → 空行 → `overall_soundscape:` → 空行 → `non_diegetic_music:`
- 分镜：`[Shot 1]` 无时间戳且开头声明风格+构图；`[Shot 2] At 00:03.500, the camera cuts to ...`（`MM:SS.mmm`，严格递增）
- 对话：`说话人识别描述 (S1) says: <d>[English] 逐字原话</d>`；ID 跨镜头稳定；`<d>` 内只放语言标签+原话
- 运镜：`类型 + with small/large amplitude + at slow/fast speed`，写成自然句子
- 关键帧模式首行：I2VA/FL2VA/L2VA 各有固定对齐指令句（见 base-modes.md）
- 声音三层分离：对白与动作同步现场声 → 时间线对应镜头；持续背景底噪 → `overall_soundscape`；观众专属配乐 → `non_diegetic_music`（无则 `N/A`）
- 节奏预算：中文台词约 4–5 字/秒、英文约 2–3 词/秒；镜头装不下就精简内容或调整时间戳/时长
