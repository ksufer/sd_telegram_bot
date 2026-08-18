# H3 全能参考模式规范（Ref2VA）

来源：MiniMax 官方 Prompting Guidance（`MiniMax-AI/MiniMax-H3` 仓库 `skills/h3-prompt-writing/references/ref-en.txt`）。

## 目录
1. 六段式骨架
2. 四类参考标签
3. summary 任务类型前缀
4. retention_analysis 关系枚举
5. detailed_description 要点
6. 官方示例骨架
7. 输出前检查清单

---

## 1. 六段式骨架（段名/顺序固定，正文英文）

```
subject_definitions:
...

summary:
...

retention_analysis:
...

detailed_description:
...

overall_soundscape:
...

non_diegetic_music:
...
```

输入上限：图片 ≤9、视频 ≤3、音频 ≤3，合计 ≤12 文件。每个上传的素材**必须**有明确角色；没标角色的素材等于浪费。

## 2. 四类参考标签

编号各自独立、全局复用、跨段落含义一致：

| 标签 | 用途 |
|---|---|
| `<Subject N>` | 从素材抽象出的可复用可见内容（人/动物/物体/场景/服装/风格/动作/表情）。一个 Subject 可由多素材合成；一个素材可提供多个 Subject。例：`<Subject 1> is the woman whose appearance comes from <Picture 1> and whose walking motion comes from <Video 1>.` |
| `<Picture N>` | 图片本身作为某镜头的首帧/关键帧/末帧/构图锚点时才单独成行；仅作为角色/风格来源的图片只写进 Subject 定义。分镜板：`<Picture 3> is a storyboard reference for [Shot 1] and [Shot 2], ...` |
| `<Video N>` | 仅用于整视频级关系：被编辑的源视频、续写起点、参考运镜/剪辑/节奏；视频里的人/物/动作仍归 `<Subject N>` |
| `<Audio N>` | 独立音频或参考视频的音轨（复制、BGM 风格、音色、台词/音效、节拍）。绑定说话人复用全局 ID：`<Audio 1> is the voice-timbre reference for <Subject 1> (S1).` |

## 3. summary 任务类型前缀

方括号开头，可用 ` + ` 组合、不重复：

`keyframe completion`、`reference generation`、`video editing`、`video continuation`、`audio reuse`、`audio reference`

- ⚠️ 参考视频只提供运镜/节奏时仍属 `reference generation`，**不是** video editing。
- 编辑任务固定开头：`The target video is an edited version of <Video 1>.`

## 4. retention_analysis 关系枚举（固定英文）

- 可见内容：`fully_preserved` / `partially_preserved` / `attribute_transfer` / `weak_reference`
  写法：`<Subject 1> (appears in [Shot 1], [Shot 3]): fully_preserved - ...`
- 音频：`fully_copy`（整轨 1:1）/ `partially_copy` / `reference` / `weak_reference`
- `retention_analysis` 中不写 `(Sx)` 说话人 ID。

## 5. detailed_description 要点

- 整体风格在 `[Shot 1]` 之前用一两句英语单独确立。
- 按播放顺序逐镜描述（生成任务通常 350–500 英文词）；分镜/时间戳/运镜/对话语法同基础模式。
- 标签在首次出现处插入：`<Subject 2> (S1) turns toward the woman and says, <d>[English] ...</d>`
- 音频关系在对应镜头/相位引用 `<Audio N>`。
- 复用参考音频台词：`<d>` 逐字保留，听不清写 `[unclear]`；**只参考音色时不得把原台词带进目标视频**。

## 6. 官方示例骨架（Samoyed 咖啡店案例节选）

```
subject_definitions:
<Subject 1> is the coffee-shop environment in <Picture 1>, featuring an exposed brick wall, an orange tufted sofa with patterned pillows, a neon sign, and a wooden coffee table.
<Subject 2> is the fluffy white Samoyed in <Picture 2>, <Picture 3>, and <Picture 4>, ...
<Subject 3> is the young blonde woman in <Video 1>, with long blonde hair and a light-pink button-down shirt...
<Subject 4> is the young man in <Video 2>, ...
<Audio 1> is the voice-timbre reference for <Subject 3> (S1), containing a spoken English vocal layer.

summary:
[reference generation + audio reference] The target video shows <Subject 3> eating a cookie in <Subject 1>. <Subject 4> enters with <Subject 2> ...

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 2], [Shot 3]): fully_preserved - the exposed brick wall, orange tufted sofa ... are retained.
...
<Audio 1>: reference - its vocal timbre guides the dialogue delivery of <Subject 3> without copying the original signal.

detailed_description:
The target video uses a realistic multi-camera sitcom style with warm indoor lighting.
[Shot 1] A medium shot establishes <Subject 1> ... <Subject 3> (S1) jerks her hand back and, using the clear youthful voice timbre referenced from <Audio 1>, exclaims with light annoyance, <d>[English] Hey! Watch your dog!</d> ...
[Shot 2] At 00:03.000, the shot cuts to a close-up of <Subject 4> (S2) ... <d>[English] He just likes cookies more than me.</d> ...
[Shot 3] At 00:05.000, ... A classic canned audience laugh begins immediately after the line...

overall_soundscape:
Soft indoor coffee-shop room tone continues throughout the scene.

non_diegetic_music:
N/A
```

## 7. 输出前检查清单

- [ ] 六段段名/顺序正确，正文英文
- [ ] 每个素材都有角色；Subject/Picture/Video/Audio 职责分清
- [ ] summary 前缀与实际任务匹配（参考运动 ≠ video editing）
- [ ] retention_analysis 使用固定枚举、不含 (Sx)
- [ ] detailed_description 风格先行、标签首现处插入、时间戳纪律遵守
- [ ] 音色参考不带入原台词；声音三层分离；无配乐 `N/A`
- [ ] 素材数量在上限内；≤7000 字符
