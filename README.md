<div align="center">

![:name](https://count.getloli.com/@:astrbot_plugin_memorix?name=%3Aastrbot_plugin_memorix&theme=miku&padding=7&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# Memorix

**为 AstrBot 打造的完整记忆系统插件**

图谱 + 向量混合检索 · 记忆生命周期管理 · 人物画像 · 总结导入 · 内嵌 WebUI

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16-blue)](https://github.com/Soulter/AstrBot)
[![Version](https://img.shields.io/badge/version-v0.4.0-green)]()
[![Platforms](https://img.shields.io/badge/platforms-QQ%20%7C%20Telegram%20%7C%20Discord-orange)]()

</div>

---

## 为什么选择 Memorix？

| | 传统方案 | Memorix |
|---|---|---|
| **存储** | 单一向量库或纯文本缓存 | 段落向量 + 实体图谱 + 时间线三维存储 |
| **检索** | 仅语义相似度 | 向量 + BM25 稀疏召回 + 图谱 PageRank 重排 |
| **生命周期** | 记忆只增不减 | 自动衰减 → 冻结 → 剪枝，支持保护/强化/恢复 |
| **可观测性** | 黑盒 | 内嵌 WebUI，图谱可视化、来源追溯、回收站 |
| **部署要求** | 依赖外部模型/服务 | 零外部依赖可启动，embedding 按需开启 |

## 核心特性

### 混合检索引擎

采用双路检索架构，向量路径（FAISS / Numpy 余弦）与稀疏路径（BM25 + Jieba 中文分词）并行召回，通过加权 RRF 融合排序。可选启用 Personalized PageRank 利用图谱拓扑进行二次重排，无需额外 reranker 模型。

### 记忆生命周期

记忆不是静态数据，而是有"生命"的：

- **衰减**：关系权重按半衰期（默认 24h）指数衰减
- **冻结**：低于活跃阈值进入冻结态，保留 24h 等待唤醒
- **剪枝**：权重降至阈值以下则移入回收站
- **保护** / **强化** / **恢复**：主动干预记忆命运

### 人物画像系统

自动从消息中提取发送者信息，后台定期刷新画像（默认 30 分钟周期，6 小时 TTL）。LLM 请求时自动注入发送者画像作为上下文参考。支持手动覆盖与清除。

### 总结导入

会话总结支持三种数据源：插件 transcript、AstrBot 原生会话历史、以及 hybrid 回退策略。可手动触发、定时执行，也可按消息阈值自动触发。通过 LLM 对对话生成结构化摘要并回写至记忆系统，支持叙事、事实、结构化等多种知识类型。

### 内嵌 WebUI

基于 AstrBot Plugin Pages 的 Dashboard 内嵌管理界面，提供：图谱浏览与关系编辑、记忆管理与来源追踪、回收站恢复、人物画像管理。接口经 AstrBot Dashboard 鉴权后转发到插件内 runtime。

### A_memorix 0.6.1 服务层同步

本版本已同步新版 A_memorix API-first 服务层，插件侧保留 AstrBot 生命周期、Provider、scope/source 隔离与 NapCat/OneBot 事件适配。新增能力包括：

- `/v1/query/episode`、`/v1/query/aggregate` 聚合查询链路
- 关系写入统一走 `RelationWriteService`，关系向量化与图谱边保持一致
- Episode 后台生成队列与 source 重建状态
- `/v1/readyz` 仪表盘状态中的 runtime self-check / queue 信息
- source 严格过滤 + 空结果安全回退，避免跨群记忆误注入

### 导入中心（可选启用）

新增 Dashboard 内嵌导入视图（默认关闭，需手动在配置文件处开启 `web.import.enabled=true`）。页面可进行如下三种导入：
- 上传文件导入（`.txt/.md/.json`）
- 粘贴文本导入
- 原始目录扫描导入（`raw` / `plugin_data` 别名）

导入中心支持手动选择 `knowledge_type`（`auto/factual/narrative/structured/mixed`），并提供任务级/文件级/分块级状态观察、任务取消与失败重试。
详细说明可见：`memorix/IMPORT_GUIDE.md`。

## 工作流

```
消息到达
  │
  ▼
① 作用域路由 ── 按 scope.mode 确定记忆归属
  │
  ▼
② 原始消息写入 ── 默认采用 MaiBot 风格 direct 写入：同时进入 transcript、段落向量、实体索引与 Episode 队列；可切回 transcript_only
  │
  ▼
③ 总结提炼 ── 保留 AstrBot 侧自动/手动总结能力，继续生成高质量段落、关系、Episode 并写入索引
  │
  ▼
④ 检索注入 ── LLM 请求时按 scope/source 混合检索记忆，注入当前用户消息上下文
  │
  ▼
⑤ 后台维护 ── 衰减 / 冻结 / 剪枝 / 画像刷新 / Episode 生成 / 向量持久化
```

## 本插件基于 A_Dawn 的 A_Memorix 设计理念开发，并针对 AstrBot 做了完整适配。

## 快速开始

### 安装

在 AstrBot 插件管理中搜索 `Memorix` 安装，或通过仓库地址安装：

```
https://github.com/exynos967/astrbot_plugin_memorix
```

### 最小配置（零配置即可运行）

插件安装后**无需任何配置**即可启动。默认使用本地 embedding 回退，所有功能可用。

### 推荐配置（启用独立 Embedding）

聊天模型可选指定 AstrBot 已定义 Provider；Embedding 在插件内独立配置 OpenAI-compatible 端点：

| 配置项 | 值 | 说明 |
|---|---|---|
| `provider.chat_provider_id` | AstrBot 中的聊天 Provider ID（可选） | 指定后优先使用该模型做总结/画像 |
| `embedding.enabled` | `true` | 启用远程向量化 |
| `embedding.openapi.base_url` | 你的 Embedding API 地址 | 支持不带 `/v1`，插件会自动补全 |
| `embedding.openapi.api_key` | 你的 API Key | 远程鉴权 |
| `embedding.openapi.model` | 你的 Embedding 模型名 | 如 `text-embedding-3-large` |

## 命令参考

### 通用命令

| 命令 | 说明 |
|---|---|
| `/mem status` | 查看作用域、内嵌 WebUI、调度器状态 |
| `/mem query <关键词> [top_k]` | 混合语义检索 |
| `/mem time <起始时间> [结束时间] [关键词]` | 时序检索 |
| `/mem episode [关键词] [top_k]` | Episode 检索 |
| `/mem aggregate <关键词> [top_k]` | 聚合 search/time/episode 召回 |
| `/mem profile [人物关键词] [top_k]` | 查询人物画像 |
| `/mem summary_now [上下文长度]` | 立即生成会话总结并写入记忆 |
| `/person_profile on\|off\|status` | 控制当前会话+用户的人物画像注入开关 |

### 管理员命令

| 命令 | 说明 |
|---|---|
| `/mem summary_all [上下文长度] [最大会话数]` | 对当前作用域内所有已记录会话执行批量总结导入 |
| `/mem protect <hash或关键词> [小时数]` | 保护记忆不被衰减（不填时长则永久保护） |
| `/mem reinforce <hash或关键词>` | 强化记忆热度，自动保护 24h |
| `/mem restore <hash> [relation\|entity]` | 从回收站恢复已删除的记忆 |
| `/mem delete_entity <实体名>` | 删除实体（级联删除相关关系与段落关联） |
| `/mem profile_override <人物ID> <文本>` | 手动覆盖人物画像 |
| `/mem profile_clear <人物ID>` | 清除画像覆盖，恢复自动生成 |

## 作用域模式

`scope.mode` 决定哪些会话共享同一份记忆：

| 模式 | 行为 | 适用场景 |
|---|---|---|
| `platform_global` | 同平台所有会话共享 | 希望机器人跨群/跨会话保持记忆连续性 |
| `user_global` | 同平台按用户隔离 | 需要用户级隐私隔离 |
| `group_global` | 同平台按群隔离，私聊退化为用户隔离 **（默认）** | 以群为单位沉淀独立记忆，降低串群污染 |
| `umo` | 按 `unified_msg_origin` 最细粒度隔离 | 最严格的隔离需求 |

## 存储架构

```
data/plugin_data/astrbot_plugin_memorix/scopes/<scope_key>/
├── vectors/      # FAISS / Numpy 向量索引
├── graph/        # SciPy 稀疏矩阵图谱
└── metadata/     # SQLite 元数据（段落/实体/关系/对话/画像/任务）
```

| 存储层 | 实现 | 职责 |
|---|---|---|
| 向量存储 | FAISS（降级 Numpy 余弦） | 段落和关系的语义向量索引 |
| 图谱存储 | SciPy 稀疏矩阵 | 实体节点 + 关系边权重图 |
| 元数据存储 | SQLite | 结构化数据与全文检索（FTS5） |

## 完整配置参考

<details>
<summary>点击展开全部配置项</summary>

### 作用域（scope）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `scope.mode` | string | `group_global` | 作用域模式 |

### 写入（ingest）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `ingest.record_all_events` | bool | `true` | 是否记录所有消息事件 |
| `ingest.skip_empty_text` | bool | `true` | 忽略空文本消息 |
| `ingest.skip_command_messages` | bool | `true` | 忽略命令消息（按 `command_prefixes` 判断） |
| `ingest.memory_write_mode` | string | `direct` | 写入模式：`direct`/`both` 为 MaiBot 风格直接写入长期记忆并保留 transcript；`transcript_only` 为旧的仅流水模式 |
| `ingest.direct_write_assistant` | bool | `true` | 是否将机器人回复也直接写入长期记忆 |
| `ingest.command_prefixes` | list | `["/"]` | 命令前缀列表（支持自定义前缀，如 `["/", "!", "."]`） |

### 提供商（provider）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `provider.chat_provider_id` | string | `""` | 可选指定 AstrBot 聊天 Provider；为空时回退当前会话 provider |

### Embedding

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `embedding.enabled` | bool | `false` | 启用插件内 OpenAI-compatible embedding（关闭则本地回退） |
| `embedding.dimension` | int | `1024` | 向量维度 |
| `embedding.batch_size` | int | `32` | 批量请求大小 |
| `embedding.max_concurrent` | int | `5` | 最大并发请求数 |
| `embedding.openapi.base_url` | string | `""` | Embedding API Base URL（可不带 `/v1`） |
| `embedding.openapi.api_key` | string | `""` | Embedding API Key |
| `embedding.openapi.model` | string | `""` | Embedding 模型名（空为服务端默认） |
| `embedding.openapi.timeout_seconds` | float | `30` | Embedding 请求超时（秒） |
| `embedding.openapi.max_retries` | int | `3` | Embedding 请求重试次数 |

### 检索（retrieval）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `retrieval.top_k_final` | int | `10` | 默认返回结果数 |
| `retrieval.enable_ppr` | bool | `true` | 启用 PageRank 重排 |
| `retrieval.enable_parallel` | bool | `true` | 并行检索 |
| `retrieval.temporal.enabled` | bool | `true` | 启用时序检索 |
| `retrieval.temporal.default_top_k` | int | `10` | 时序检索默认 top_k |
| `retrieval.aggregate.rrf_k` | int | `60` | 聚合检索 RRF 融合 K 值 |

### 记忆维护（memory）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `memory.enabled` | bool | `true` | 启用记忆维护 |
| `memory.half_life_hours` | float | `24.0` | 半衰期（小时） |
| `memory.base_decay_interval_hours` | float | `1.0` | 衰减执行周期（小时） |
| `memory.prune_threshold` | float | `0.1` | 剪枝阈值 |
| `memory.freeze_duration_hours` | float | `24.0` | 冻结保留时长（小时） |
| `memory.max_weight` | float | `10.0` | 关系边最大权重 |
| `memory.auto_protect_ttl_hours` | float | `24.0` | 强化自动保护时长（小时） |

### 人物画像（person_profile）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `person_profile.enabled` | bool | `true` | 启用人物画像 |
| `person_profile.profile_ttl_minutes` | int | `360` | 画像缓存 TTL（分钟） |
| `person_profile.top_k_evidence` | int | `12` | 画像生成证据数量 |

### 总结（summarization）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `summarization.enabled` | bool | `true` | 启用总结导入 |
| `summarization.source_mode` | string | `hybrid` | 总结来源模式：`transcript` / `astrbot` / `hybrid`（优先 AstrBot） |
| `summarization.context_length` | int | `50` | 总结上下文长度 |
| `summarization.default_knowledge_type` | string | `narrative` | 总结知识类型（narrative / factual / mixed / structured / auto） |

### 定时总结（schedule）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `schedule.enabled` | bool | `true` | 启用定时总结任务 |
| `schedule.import_times` | list | `["04:00"]` | 每日触发时间点（HH:MM） |

### WebUI

插件只保留 **AstrBot Dashboard 内嵌页**：AstrBot `>=4.24.2` 可在插件详情页的 `Memorix 控制台` 页面中直接打开，接口经 AstrBot Dashboard 鉴权后转发到当前 Memorix runtime。

内嵌页的 scope 选择：固定 `webui.scope` 时使用固定值；`auto/current/event` 时使用最近活跃 scope，首次打开且无活跃会话时回退到 `default`。

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `webui.enabled` | bool | `true` | 启用 Dashboard 内嵌 WebUI 接口 |
| `webui.scope` | string | `auto` | WebUI 绑定作用域，`auto` 使用最近活跃作用域 |

### 导入中心（web.import）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `web.import.enabled` | bool | `false` | 启用 Dashboard 内嵌导入视图与增强导入接口 |
| `web.import.max_queue_size` | int | `20` | 导入任务队列上限 |
| `web.import.max_files_per_task` | int | `200` | 单任务最大文件数 |
| `web.import.max_file_size_mb` | int | `20` | 单文件大小上限（MB） |
| `web.import.max_paste_chars` | int | `200000` | 粘贴导入字符上限 |
| `web.import.default_file_concurrency` | int | `2` | 默认文件并发（预留） |
| `web.import.default_chunk_concurrency` | int | `4` | 默认分块并发（预留） |
| `web.import.path_aliases.raw` | string | `raw` | 原始目录扫描别名（相对 `storage.data_dir`） |
| `web.import.path_aliases.plugin_data` | string | `.` | 插件数据目录别名（相对 `storage.data_dir`） |

</details>

## 前端目录说明

- AstrBot Dashboard 插件内嵌页读取：`pages/memorix/*`

## 依赖

| 包 | 用途 |
|---|---|
| `numpy` | 向量计算 & 降级向量存储 |
| `scipy` | 图谱稀疏矩阵 |
| `faiss-cpu` | 高性能向量索引（失败自动降级 Numpy） |
| `fastapi` | Dashboard 内嵌 WebUI API 应用 |
| `httpx` | AstrBot Dashboard 内嵌页到 FastAPI WebUI 的进程内请求转发 |
| `pydantic` | 数据校验 |
| `jieba` | 中文分词（BM25 检索） |
| `openai` | OpenAI-compatible Embedding 客户端 |

## 常见问题

<details>
<summary>没有配置 Embedding API 能用吗？</summary>

可以。`embedding.enabled=false`（默认值）时，插件使用本地确定性向量回退，所有功能正常加载。配置 `embedding.openapi` 后检索效果会显著提升。

</details>

<details>
<summary>FAISS 安装失败怎么办？</summary>

插件会自动降级到 Numpy 余弦相似度实现，功能完全可用，仅在超大规模数据时性能有差异。无需手动干预。

</details>

<details>
<summary>PageRank 重排需要额外模型吗？</summary>

不需要。`retrieval.enable_ppr` 基于图谱拓扑结构计算，是纯算法重排，不依赖任何外部模型。

</details>

<details>
<summary>如何清理旧记忆？</summary>

记忆系统自带生命周期管理：衰减 → 冻结 → 剪枝自动进行。也可通过 WebUI 手动管理，或使用 `/mem protect` 保护重要记忆不被清理。

</details>

<details>
<summary>在 group_global 模式下提示日志显示图已保存，但 WebUI 还是空白</summary>

大概率是 WebUI 当前绑定的 `scope` 和你实际有数据的对话范围不一致。

如果日志里已经出现 `graph saved`，但同时还有 `图为空，无法计算PageRank`，说明当前 WebUI 读到的那份图仍然是空的。

建议在你要查看的那个群或私聊里按顺序执行：

1. 发送一条消息或执行 `/mem status`，确认当前返回的 `scope`
2. 在 AstrBot Dashboard 插件详情页打开 `Memorix 控制台`
3. 如需固定查看范围，在插件配置里设置 `webui.scope`

</details>

## 特别感谢

- [ARC](https://github.com/A-Dawn)
- [A_memorix](https://github.com/A-Dawn/A_memorix/tree/basic)

## 许可证

本项目遵循 [AGPLv3 License](LICENSE)。
