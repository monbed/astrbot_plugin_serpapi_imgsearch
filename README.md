# astrbot_plugin_serpapi_imgsearch

通过 [SerpApi](https://serpapi.com/) 为 AstrBot 的大语言模型注册**函数工具（Function Tool / Tool Use）**，让 LLM 在对话中自动完成两件事：

- **文字搜图并发图** (`pic_search`)：根据关键词用 Google 图片（`google_images`）抓取大量候选图，再用**视觉语言模型（VLM）多轮淘汰赛**挑出最符合描述的一张，直接发送给用户。
- **以图搜图** (`reverse_image_search`)：对用户发送/引用的图片用 Google Lens（`google_lens`）反向检索，返回网页来源供 LLM 交叉印证出处、作者、作品。

> 仅通过 **LLM 函数工具** 触发，不注册任何聊天指令。文字搜图与以图搜图**共用同一组 SerpApi Key**。

## 工作原理

| 能力 | 函数工具 | 说明 |
| --- | --- | --- |
| 文字搜图 | `pic_search(query)` | `google_images` 抓候选图 → 下载拼网格图 → VLM 多轮筛选 → 下载冠军图并直接发送。需要一个支持图片输入的模型。 |
| 以图搜图 | `reverse_image_search(image_url?)` | 取当前消息/被引用消息中的图片 → 必要时上传图床转公网 URL → `google_lens` 反向检索 → 返回结果（标题/来源/链接）交由 LLM 呈现。 |

## 安装与配置

1. 将本插件放入 AstrBot 的 `data/plugins/` 目录（或通过插件市场安装），重载插件。
2. 在 **管理面板 → 插件管理 → 本插件 → 配置** 中填写：

| 配置项 | 说明 | 默认值 |
| --- | --- | --- |
| `api_keys.serpapi_keys` | **必填**。SerpApi Key 列表，搜图与以图搜图共用，支持多个 Key 负载均衡。[获取](https://serpapi.com/manage-api-key) | `[]` |
| `vlm_provider_id` | 文字搜图用于视觉筛选的 Provider ID。留空则用当前会话默认模型（需支持图片输入）。 | 空 |
| `batch_size` | 每轮淘汰赛单批处理的图片数量。 | 16 |
| `default_scrape_count` | 文字搜图默认抓取的候选图数量（建议 16 的倍数）。 | 16 |
| `region.gl` | 搜索国家/地区代码（Google `gl`），如 `us`、`cn`、`jp`。 | us |
| `region.hl` | 界面语言代码（Google `hl`），如 `en`、`zh-cn`、`ja`。 | zh-cn |
| `network.proxy_url` | HTTP/HTTPS 代理，如 `http://127.0.0.1:7890`。留空不使用。 | 空 |
| `network.allow_image_upload` | 以图搜图时允许把本地/临时图片上传到图床取公网 URL（关闭则仅支持公网直链图）。 | true |
| `network.image_host` | 上传图床：`litterbox`(临时,推荐)、`uguu`(临时)、`catbox`(永久)。临时图床更适合以图搜图，且对代理/机房 IP 更友好。 | litterbox |
| `network.litterbox_time` | 仅 `litterbox` 生效，文件保留时长：`1h`/`12h`/`24h`/`72h`。 | 1h |
| `max_results` | 以图搜图最大结果数。 | 5 |

3. 文字搜图依赖**视觉/多模态模型**：请配置 `vlm_provider_id`，或确保当前会话的默认对话模型支持图片输入。

### 关于以图搜图的“图片转公网链接”

SerpApi 服务器需要能从公网访问到待检索的图片：

- 若图片本身已是公网 http(s) URL（多数平台的图片消息）→ 直接使用。
- 否则在 `allow_image_upload=true` 时，把图片上传到所选图床（`image_host`：[Litterbox](https://litterbox.catbox.moe)/[uguu](https://uguu.se)/[Catbox](https://catbox.moe)）换取公网 URL。
- 若转链失败，工具会返回提示，模型应**降级为纯视觉辨识**继续回答，不会中断对话。

## 使用示例

- 「帮我找一张赛博朋克霓虹城市夜景的图」→ LLM 调 `pic_search(query="赛博朋克 霓虹城市夜景")` → 抓图/拼图/VLM 选择 → 直接发图。
- （发送或引用一张图片）「以图搜图」→ LLM 调 `reverse_image_search()` → `google_lens` 检索 → 结合视觉与检索结果给出作者 / 作品 / 链接。

## 依赖

无需额外安装

## 致谢

本插件的两大功能在实现思路上受到以下两个优秀项目的启发，特此感谢：

- **文字搜图**（候选图 → 拼网格 → VLM 多轮淘汰赛）灵感来自 [RC-CHN/astrbot_plugin_pic_search](https://github.com/RC-CHN/astrbot_plugin_pic_search)。
- **以图搜图**（图片转公网链接 → 反向检索）灵感来自 [FlanChanXwO/astrbot_plugin_imgexploration](https://github.com/FlanChanXwO/astrbot_plugin_imgexploration)。

同时感谢：

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) 提供的插件框架。
- [SerpApi](https://serpapi.com/) 提供的检索能力。
