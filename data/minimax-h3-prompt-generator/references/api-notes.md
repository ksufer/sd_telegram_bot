# H3 API 调用要点（v2/video_generation）

来源：MiniMax 开放平台官方文档 https://platform.minimaxi.com/docs/guides/video-generation

## 基本参数

- 模型 ID：`MiniMax-H3`；接口 `POST /v2/video_generation`（异步任务，`task_id` 轮询 `GET /v2/query/video_generation/{task_id}`，建议 10 秒间隔）
- `duration`：4–15 整数秒；`resolution`：`768P` / `2K`
- `ratio`：**T2VA 必填且不能为 `adaptive`**；图生/参考模式宽高比由输入素材决定（恒 adaptive）
- content[] 多模态结构：元素用 `type`（text / image_url / video_url / audio_url）+ `role` 区分

## role 取值

| role | 用途 |
|---|---|
| `first_frame` | 首帧图 |
| `last_frame` | 尾帧图 |
| `reference_image` | 参考图（≤9 张） |
| `reference_video` | 参考视频（≤3 段，单段 2–15 秒，总长 ≤15 秒） |
| `reference_audio` | 参考音频（≤3 段，单段 2–15 秒） |
| `base_video` | 2K 再生成时提交的唯一 base 视频 |

## 输入限制

- 混合输入总上限 12 个文件；参考图第 6 张起可能产生附加费
- 单文件：视频 ≤50MB、图片 ≤30MB、音频 ≤15MB；请求体 ≤64MB（推荐 URL 传入）
- 格式：视频 H.264/H.265（音频 AAC/MP3）；图片 JPG/JPEG/PNG/WEBP/HEIC/HEIF；音频 WAV/MP3
- 首/尾帧图片宽高范围 [256, 5760]，宽高比 5:2～2:5

## 其他

- 提示词上限 7000 字符
- 官方提示词增强：`task_type=h3_context_ir` 异步任务，从 `content.prompt` 取增强后提示词
- 简单场景可在关键描述后直接加 `[pan]`、`[zoom]`、`[static]` 等运镜简写；复杂场景用完整结构式提示词
