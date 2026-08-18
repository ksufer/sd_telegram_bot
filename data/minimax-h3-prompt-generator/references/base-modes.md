# H3 基础模式规范（T2VA / I2VA / FL2VA / L2VA）

来源：MiniMax 官方 Prompting Guidance（`MiniMax-AI/MiniMax-H3` 仓库 `skills/h3-prompt-writing/references/base-en.txt`）。

## 目录
1. 三段式骨架
2. 关键帧对齐首行（I2VA/FL2VA/L2VA）
3. 分镜与时间戳
4. 运镜词表
5. 说话人 ID 与对话标签
6. 画面可见文字
7. 各模式模板与官方示例
8. 输出前检查清单

---

## 1. 三段式骨架

字段名固定、英文、顺序固定，字段之间空一行：

```
integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

| 字段 | 内容 | 纪律 |
|---|---|---|
| `integrated_multimodal_description` | 时间线主体：风格、初始构图、主体外观与位置、动作与反应、分镜切换、运镜、对白/歌唱、同步现场声 | 按 `[Shot N]` 组织 |
| `overall_soundscape` | 贯穿全片、不绑定单一动作的环境底噪与氛围声（房间底噪、风雨、远处车流、机器低鸣等） | 1–4 句；不重复对白；仅全片静音时写 `N/A` |

**现场声 vs 环境声的划分规则**：与画面中具体动作**同步**的声响（切面包声、碰杯声、手中纸张摩擦声等 diegetic sound）写进时间线的对应镜头里；`overall_soundscape` 只放持续存在、作为背景铺底的声音。
| `non_diegetic_music` | 观众专属配乐 | 1–3 句，写配器/速度/节奏/动态，禁抽象情绪词；剧中人可听的音乐属现场声须进时间线；无配乐写 `N/A` |

## 2. 关键帧对齐首行

必须是提示词第一行，后空一行再接三字段：

- **I2VA**：`For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.`
- **FL2VA**：`How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.`（S.SS = 实际总时长，两位小数）
- **L2VA**：`How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the S.SS-second mark of the target video.`

## 3. 分镜与时间戳

- `[Shot 1]` 不写时间戳；开头先声明整体风格与初始构图。风格词：`Cinematic`、`live-action`、`2D-animated`、`3D CG`、`claymation`、`watercolor`、`vintage film`。
- 后续镜头：`[Shot 2] At 00:03.500, the camera cuts to ...`，格式 `MM:SS.mmm`，严格递增且 ≤ 总时长。
- 时间戳只标切镜，不标普通动作；事件总量匹配 4–15 秒。
- 切换动词：`the camera cuts to` / `the shot cuts/transitions/changes/switches to`；cross-dissolve/fade/wipe 仅用户明确要求时用。
- 切镜必须带来新信息；仅距离/角度微调时用运镜而非切镜。
- **节奏预算**：估算每个镜头能否容纳其内容——中文台词约 4–5 字/秒、英文约 2–3 词/秒，再加动作时间。某镜头装不下时：精简动作、缩短台词、把时间戳前移，或增加总时长，而不是把内容硬塞进剩余秒数。

## 4. 运镜词表（类型 + 幅度 + 速度，写成自然句子）

- 类型：`Zoom In/Out`、`Push In/Pull Out`、`Pan Left/Right`、`Truck Left/Right`、`Tilt Up/Down`、`Pedestal Up/Down`、`Arc Shot`、`Tracking Shot`、`Static Shot`、`Shake Slightly/Shake Strongly`、`POV`、`Roll Clockwise/Counterclockwise`
- 幅度：`with small amplitude` / `with large amplitude`；速度：`at slow speed` / `at fast speed`（中等/正常可省略）
- 例：`The camera pushes in with small amplitude at slow speed toward the folded letter in her hands.`

## 5. 说话人 ID 与对话标签

- 发声者（说话/唱歌/画外人声）分配稳定 ID `(S1)`、`(S2)`，跨镜头复用；同时说用 `(S1,S2)`；不发声者不分配。
- 首次出现给足识别信息：年龄/性别/画内外/音高/音色/语速/口音。
- 识别描述、ID、动作、语气在 `<d>` 外；`<d>` 内只有语言标签 + 逐字原话（保留原标点，不翻译）：
  `The young woman with a quiet, breathy voice (S1) says: <d>[English] I get off at the next station.</d>`
- 画外音：`The man (S1) says in an off-screen voiceover: <d>[English] ...</d> while his lips remain completely closed.`
- 跨切镜的同句台词：两部分各加 `<scenetrans>` 并注明音频跨切连续；被结尾截断用 `<cutoff>`。

## 6. 画面可见文字

横幅/招牌/标签/字幕放英文双引号、逐字原文：`A red neon sign reading "营业中" glows above the doorway.`

## 7. 各模式模板与官方示例

### T2VA 官方示例（Case 1）

```
integrated_multimodal_description: [Shot 1] Live-action, cinematic, a medium-wide shot frames a baker opening the shutters of a small street bakery before sunrise. The camera pushes in with small amplitude at slow speed as the middle-aged baker with a calm, slightly raspy voice (S1) places a fresh loaf on the wooden counter and says: <d>[English] First batch of the morning.</d> [Shot 2] At 00:05.000, the camera cuts to a close-up of steam rising from the sliced bread while the baker's final words carry over from the previous shot.

overall_soundscape: Wooden shutters scrape open over a quiet street as trays clink softly inside the bakery. The doorbell rings once, followed by light footsteps and the crisp sound of bread being sliced.

non_diegetic_music: A soft acoustic-guitar pattern at a moderate tempo, joined by sparse upright-bass notes and a gentle fade at the end.
```

### I2VA 官方示例（Case 2）

```
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, the young woman shown in <Picture 1> remains beside the rain-covered train window, preserving her appearance, clothing, seat position, and the carriage layout. The camera trucks right with small amplitude at slow speed as she lifts her gaze from the folded letter toward the passing city lights. Her reflection moves across the glass while the quiet, breathy young woman (S1) says: <d>[English] I get off at the next station.</d> She folds the letter along its existing crease.

overall_soundscape: The train wheels produce a steady metallic rhythm beneath a low ventilation hum. Rain ticks against the window while paper rustles softly in her hands.

non_diegetic_music: Sustained cello notes at a slow tempo with widely spaced piano tones, gradually decreasing in volume.
```

### 模式叙事要点

- **I2VA**：首帧锚定 → 动作开始 → 持续发展 → 结果/反应。不复述首帧已可见信息（除非必须锁定）；字数给"之后发生什么"。
- **FL2VA**：Picture 1=开头、Picture 2=结尾；写连接路径（姿态/物体/构图/光线/场景过渡），不分别复述两张图；默认单镜头连续插值；末帧由最后 `[Shot N]` 抵达。
- **L2VA**：`<Picture 1>` 是末帧；先推断合理前置状态，再逐步收敛落到末帧。

## 8. 输出前检查清单

- [ ] 字段名/顺序/空行正确；正文英文
- [ ] 关键帧模式首行对齐指令正确、时间戳与总时长一致
- [ ] `[Shot 1]` 无时间戳且声明风格+构图；后续时间戳严格递增、在时长内
- [ ] 运镜为"类型+幅度+速度"自然句
- [ ] 说话人 ID 稳定；`<d>` 内只有语言标签+原话；画外音配 lips-closed
- [ ] 声音三层分离正确（同步动作声在时间线、背景底噪在 soundscape）；无配乐写 `N/A`
- [ ] 每个镜头的台词+动作在时长预算内（中文约 4–5 字/秒、英文约 2–3 词/秒）
- [ ] 画面文字用双引号逐字引用
- [ ] 动作总量匹配时长；≤7000 字符
