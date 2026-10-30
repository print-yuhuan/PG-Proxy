# 模型目录与 API 使用说明

本文说明 **PG Proxy 0.2.0** 的在线模型目录、能力字段和接口限制，核对日期为 **2026-10-30**。19 个模型及代表性功能的历史真实测试结果来自 0.1.0，见 [Pixel 10 验证记录](MODEL_CAPABILITIES_TEST.md)；0.2.0 的参数兼容合同测试不能替代新安装包的设备与真实生成验收。

**模型列表在线获取，不是固定名单。** PG Proxy 使用已登录凭证读取 Playground `ListModels`，默认缓存最多 300 秒。新增模型符合当前生成通道的筛选条件即可进入目录；缺少可靠资料的能力保留为 `null` 或省略。

按需查阅：[接口地址](#2-接口地址) · [Token 计数](#7-token-计数) · [功能限制](#8-工具schema媒体的明确限制) · [RikkaHub 适配](#9-rikkahub-能读取哪些信息) · [验证与维护](#11-验证与维护)

## 1. 怎样理解目录里的能力

目录将上游声明与代理实现分开，主要包含三层信息：

| 层次 | 内容 | 阅读边界 |
| --- | --- | --- |
| 标准兼容字段 | 模型 ID、名称、输入／输出限额、当前代理提供的方法等 | 完整支持 Google 或 OpenAI 的所有 API |
| `pg_proxy.upstream` | 登录会话返回的模型能力、原始方法，以及有来源的公开模态资料 | 代理已经实现所有上游方法，或当前账号每项功能均可调用 |
| `pg_proxy.proxy` | 代理当前实现的能力与限制；`interfaces` 进一步区分 Gemini 与 OpenAI | 任意第三方客户端都会读取扩展字段，或已经完成真实生成验证 |

例如，支持 PDF 的模型可通过原生 Gemini 接口接收文档，OpenAI 适配器则尚不接收文档输入。上游声明的 `batchGenerateContent` 也不代表代理提供了批处理接口。调用前应同时查看模型能力和所选接口的限制。

`metadata_is_generation_verification` 固定为 `false`：能力元数据不等于生成验收结果。`supported_parameters` 表示适配器可处理的参数名称，具体取值、组合和用途仍受上游限制。

多个可用凭证返回同名模型时，公开能力采用保守合并：能力、方法和枚举取交集，输入／输出与温度上限取较小值，思考预算取共同区间。生成时再按实际选中凭证的目录校验；仅由某一个凭证返回的模型也可出现在合并列表中。

## 2. 接口地址

以下接口均使用应用配置的认证方式：Bearer、Basic、查询参数 `key` 或 `x-goog-api-key`。示例从环境变量 `PG_PROXY_KEY` 读取接口密钥；请在本地设置，不要将实际密钥提交到仓库或 Issue。

| 方法 | 路径 | 返回内容 |
| --- | --- | --- |
| GET | `/v1/models` | OpenAI 兼容模型列表，当前返回全部可用条目 |
| GET | `/v1/models/{model}` | 单个模型的 OpenAI 兼容详情 |
| GET | `/v1beta/models` | Gemini 模型列表，支持 `pageSize` 与 `pageToken` |
| GET | `/v1beta/models/{model}` | 单个模型的 Gemini 详情 |
| POST | `/v1beta/models/{model}:countTokens` | 调用上游计数，返回 `totalTokens` |
| POST | `/v1beta/models/{model}:generateContent` | Gemini 非流式生成 |
| POST | `/v1beta/models/{model}:streamGenerateContent` | Gemini 流式生成 |
| POST | `/v1/chat/completions` | OpenAI 兼容对话生成 |

Gemini 的三个 POST 操作也保留 `/v1/models/{model}:操作名` 兼容路径。注意：**GET `/v1/models/{model}` 返回 OpenAI 详情**；需要原生 Gemini 详情应使用 `/v1beta/models/{model}`。

以下示例使用 Bash 语法。Windows PowerShell 应使用 `curl.exe`、`$env:PG_PROXY_KEY` 和相应的续行语法。地址与端口以应用概览显示为准。

```bash
curl --fail-with-body http://127.0.0.1:8888/v1/models \
  -H "Authorization: Bearer $PG_PROXY_KEY"

curl --fail-with-body http://127.0.0.1:8888/v1beta/models/gemini-4-argon \
  -H "Authorization: Bearer $PG_PROXY_KEY"
```

未知模型返回 HTTP `404 / model_not_found`。目录查询也需要可用凭证；无凭证、上游认证失败和排队超时会分别返回对应错误。

## 3. OpenAI 目录字段

列表外层为 `{"object":"list","data":[...]}`。详情接口直接返回一个模型对象。

| 字段 | 含义 |
| --- | --- |
| `id` | 实际调用 ID，不带 `models/` 资源前缀 |
| `object` | 固定为 `model`，这是资源对象类型，不是聊天／图像分类 |
| `created` | 当前为 `0`，表示没有可靠的创建时间；不要当作模型发布日期 |
| `owned_by` | 当前为 `google` |
| `name`、`description` | 扩展展示字段，来自上游名称与说明 |
| `type` | 扩展分类，目前可为 `chat`、`image`、`transcription` |
| `architecture.input_modalities` | OpenRouter 风格扩展；当前 OpenAI 接口与模型可知输入能力的交集，未知时为 `null` |
| `architecture.output_modalities` | 当前 OpenAI 适配器公开的文本／图像输出模态 |
| `supported_parameters` | 扩展参数名称列表；详细取值限制仍需看下文及实际错误 |
| `context_length` | 有可靠统一上下文窗口资料时才提供；否则省略 |
| `pg_proxy` | 本项目自定义、带版本的详细能力说明 |

`name`、`type`、`architecture`、`supported_parameters`、`context_length` 和 `pg_proxy` 是扩展字段，客户端不一定读取或应用它们。

以下摘录 Argon 的关键字段，省略完整 `pg_proxy` 和部分参数；数值应以实际响应为准：

```json
{
  "id": "gemini-4-argon",
  "object": "model",
  "created": 0,
  "owned_by": "google",
  "name": "Gemini 4 Argon",
  "type": "chat",
  "architecture": {
    "input_modalities": ["text", "image", "audio"],
    "output_modalities": ["text"]
  },
  "supported_parameters": [
    "temperature", "top_p", "max_tokens", "tools",
    "tool_choice", "thinking_config", "reasoning_effort"
  ],
  "context_length": 1048576
}
```

基础参数包括采样、输出上限、停止词和 `response_format`，其余参数按模型能力列出：

- `reasoning_effort`：至少能表达一种有效思考级别或零预算时提供；具有思考能力的模型不一定支持此参数。
- `thinking_config`：同时提供 `reasoning_effort` 时，以显式 `thinking_config` 为准。
- `image_config`：图像模型允许配置比例或尺寸时提供。
- 工具、函数调用、媒体分辨率和转写参数：仅在对应能力允许时提供。

OpenRouter 的 `reasoning` 请求对象尚未实现，不会加入支持列表。

兼容层会忽略未知或无法支持的可选设置，并补全能够推断的缺省值。`pg_proxy.proxy.request_compatibility` 说明处理策略；接受请求不等于执行了被忽略的参数，详见[客户端兼容](CLIENT_COMPATIBILITY.md)。

## 4. Gemini 目录字段

列表外层为 `{"models":[...]}`，还有下一页时附带 `nextPageToken`。详情接口直接返回一个模型对象。

| 字段 | 来源与语义 |
| --- | --- |
| `name` | 上游资源名，例如 `models/gemini-4-argon` |
| `baseModelId` | 可用于当前生成请求的 ID，仅移除 `models/` 前缀；不会猜测或删除版本后缀 |
| `version` | 上游模型行的版本字段；缺失时省略 |
| `displayName`、`description` | 上游展示名称与说明 |
| `inputTokenLimit`、`outputTokenLimit` | 上游目录当前给出的输入／输出 token 上限 |
| `supportedGenerationMethods` | PG Proxy 实现的 `generateContent`、`streamGenerateContent`；上游明确列出计数方法时才增加 `countTokens` |
| `thinking` | 目录是否声明思考能力；不表示支持所有思考级别或可关闭思考 |
| `temperature`、`topP`、`topK` | 上游提供时保留的采样默认值 |
| `maxTemperature` | 上游提供时保留的温度上限 |
| `pg_proxy` | 本项目扩展，结构与 OpenAI 详情中的同名对象一致 |

`version` 不一定等于模型名字中的数字。`outputTokenLimit` 也不一定是支持续写模型的最终总输出上限；这两类限额分别见 `pg_proxy.proxy.limits`。

### 分页

- `pageSize` 默认 `50`，接受 `1` 至 `1000` 的整数；`0`、负数、非整数及超限值返回 `400`。
- 按模型 ID 稳定排序。把上一次响应的 `nextPageToken` 原样带入下一次请求。
- 分页 token 绑定目录快照。账号状态、模型能力或目录内容更新后，旧 token 可能失效，返回 `400`；此时从第一页重新获取。
- 没有 `nextPageToken` 表示已经到最后一页。分页 token 不是认证凭证。
- OpenAI `/v1/models` 当前不实现这套分页参数，仍返回完整列表。

```bash
curl --fail-with-body --get http://127.0.0.1:8888/v1beta/models \
  -H "Authorization: Bearer $PG_PROXY_KEY" \
  --data-urlencode 'pageSize=10'

# PG_MODEL_PAGE_TOKEN 保存上一页响应中的 nextPageToken。
curl --fail-with-body --get http://127.0.0.1:8888/v1beta/models \
  -H "Authorization: Bearer $PG_PROXY_KEY" \
  --data-urlencode 'pageSize=10' \
  --data-urlencode "pageToken=$PG_MODEL_PAGE_TOKEN"
```

## 5. `pg_proxy` 扩展字段

### 顶层与上游证据

| 路径 | 含义 |
| --- | --- |
| `schema_version` | 当前为整数 `1`；客户端应按版本读取 |
| `source` | 当前为 `google_ai_studio_playground` |
| `upstream.generation_methods` | 原始上游方法，可能包含代理未实现的方法 |
| `upstream.capability_codes` | 主能力码；未知编号保留原值，不猜含义 |
| `upstream.secondary_capability_codes` | 次能力码；同样不将未知值自动映射成能力 |
| `upstream.types` | 根据已知能力识别的类别列表，可同时包含多个类别 |
| `upstream.input_modalities` | 有可靠公开资料的输入模态；未知为 `null` |
| `upstream.output_modalities` | 根据已知生成能力识别的输出模态 |
| `upstream.modality_source` | 补充资料的 `source_url`、`checked_at`、`resolved_model`；无资料时为 `null` |
| `upstream.tools` | 已识别的工具名称列表 |
| `upstream.reasoning` | 思考能力，结构见下表 |
| `upstream.limits` | 输入、输出和已知统一上下文窗口 |
| `upstream.options` | 目录提供的图片比例、尺寸、音色和语言枚举 |

模态值为 `text`、`image`、`audio`、`video`、`document`。其中 `document` 的资料依据是 PDF，不代表任意文件格式。公开资料仅补充能力信息，不决定模型是否进入在线目录；`latest` 别名也只在上游明确返回目标时补充该目标的资料，不固定绑定版本。

工具名称包括 `code_execution`、`function_calling`、`google_search`、`url_context`、`image_search` 和 `google_maps`，各模型只列出自身具备的能力。

`upstream.options` 的键为 `image_aspect_ratios`、`image_output_resolutions`、`voices`、`languages`。空数组表示当前目录没有可用的已知枚举，不表示任意值都允许。图片比例、输出尺寸等参数仍需按对应模型的枚举校验。

### 思考与限额

| 字段 | 含义 |
| --- | --- |
| `reasoning.supported` | 是否声明思考能力 |
| `reasoning.budget_supported` | 是否支持预算控制 |
| `reasoning.level_supported` | 是否支持级别控制 |
| `reasoning.budget` | 条件存在；其中 `default`、`min`、`max`、`can_disable` 仅在有对应资料时提供 |
| `reasoning.levels` | 条件存在；当前模型允许的 `LOW`、`MEDIUM`、`HIGH`、`MINIMAL` 子集 |
| `reasoning.default_level` | 目录明确给出且属于允许级别时提供 |
| `limits.input_tokens` | 当前目录输入上限 |
| `limits.output_tokens` | 当前目录原始输出上限 |
| `limits.context_tokens` | 有可靠总窗口资料才提供数值，否则为 `null` |
| `proxy.limits.default_output_tokens` | 请求省略输出上限时采用的目录值 |
| `proxy.limits.max_output_tokens` | 本代理允许显式请求的最大总输出；能力码 `91` 的续写模型取目录输入／输出限额的较大值，其他模型取输出限额 |

**输入上限与输出上限不能相加当作统一上下文窗口。** 目前仅依据 Argon 和 Gemma 4 的可靠窗口资料填写 `context_length`，其他模型保留独立限额。公开资料与在线目录的数值分别记录，不用文档中的较大值覆盖会话目录默认值。

### 代理与接口能力

| 路径 | 含义 |
| --- | --- |
| `proxy.type` | 代理对当前模型的主要分类 |
| `proxy.generation_methods` | 当前代理实际提供的方法 |
| `proxy.input_modalities`、`output_modalities` | 代理整体可表达的已知模态；具体入口还需看 `interfaces` |
| `proxy.tools`、`function_calling` | 当前模型可用的已实现工具和函数调用标记 |
| `proxy.reasoning`、`limits` | 当前代理的思考设置与限额 |
| `proxy.output_continuation` | 是否处理上游长输出续写协议 |
| `proxy.unsupported_operations` | 明确未接入的缓存、批处理、Interactions、双向实时、视频生成等操作 |
| `proxy.metadata_is_generation_verification` | 固定为 `false` |
| `proxy.interfaces.gemini_generate_content` | 原生 Gemini 接口能力 |
| `proxy.interfaces.openai_chat_completions` | OpenAI 对话接口能力 |

两个接口对象分别包含 `input_modalities`、`output_modalities`、`tools`、`function_calling` 和 `client_must_preserve_signatures`。以下为原生模型详情的字段摘录：

```json
{
  "name": "models/gemini-4-argon",
  "baseModelId": "gemini-4-argon",
  "supportedGenerationMethods": [
    "generateContent", "streamGenerateContent", "countTokens"
  ],
  "pg_proxy": {
    "schema_version": 1,
    "source": "google_ai_studio_playground",
    "proxy": {
      "output_continuation": true,
      "metadata_is_generation_verification": false
    }
  }
}
```

原生 Gemini 对象还包含：

- `function_calling_modes`：能力允许时为 `AUTO`、`NONE`。
- `media_transport`：当前支持的 `inlineData` 和 HTTPS `fileData` 传递方式；不是 Files 上传服务。
- `json_schema_constraints`：`validated_subset`，只承诺已实现的 Schema 子集。
- `unsupported_features`：尚未实现的强制函数调用模式、Files 上传、Maps 坐标配置等。

OpenAI 对象还包含：

- `tool_choice_modes`：能力允许时为 `auto`、`none`。
- `parallel_tool_calls_values`：有函数能力时仅列 `true`；不宣称能强制只调用一个函数。
- `input_formats`：适配器可编码的图片／音频格式；仍应先看该模型的 `input_modalities`。
- `remote_media_download: false`：不替调用方下载远程图片或媒体。
- `unsupported_input_modalities`：当前为视频、文档。
- `nonstandard_output_fields`：`reasoning_content`、`images`、`audio_chunks`、`extra_content`。
- `standard_audio_output: false`：音频块扩展不是完整的 OpenAI 标准音频接口。
- `strict_json_schema: false`：不支持 OpenAI `strict: true` 约束承诺。
- `signature_transport`：`extra_content.google`。
- `unsupported_features`：强制／指定名称的工具选择、音频转码等。

## 6. 当前目录的 19 个模型怎样分类

2026-10-30 的在线目录快照对应网页上的 37 个选择项，经过通道筛选后保留以下 **19 个 ID**。这是有日期的观察记录，模型数量和能力应以自己账号的实时目录为准；逐模型测试结果另见[模型能力验收记录](MODEL_CAPABILITIES_TEST.md)。

| 类别 | ID | 已实现的适配范围与边界 |
| --- | --- | --- |
| 通用文本与多模态，7 个 | `gemini-4-argon`、`gemini-3.8-flash`、`gemini-3.6-flash`、`gemini-3.5-flash-lite`、`gemini-3.1-pro-preview`、`gemini-3.1-flash-lite`、`gemini-3-flash-preview` | 文本、受支持媒体、思考及模型声明的工具；原生入口可表达 PDF／视频，OpenAI 入口目前不能。Argon 还实现分轮续写；真实跨轮长输出未由短请求证明 |
| 动态别名，3 个 | `gemini-pro-latest`、`gemini-flash-latest`、`gemini-flash-lite-latest` | 使用上游当前指向的能力与文档目标，不承诺别名长期固定 |
| 图像，5 个 | `gemini-nano-banana-2.1`、`gemini-3.1-flash-image`、`gemini-3.1-flash-lite-image`、`gemini-3-pro-image`、`gemini-2.5-flash-image` | 文本／图像输出、图片比例及模型允许的输出尺寸；不是独立 `/v1/images/generations` 服务，原生模态与 OpenAI `images` 扩展需客户端支持 |
| 文件转写，1 个 | `gemini-3.5-transcribe` | 音频输入、文本及转写元数据；通过生成接口和转写配置适配，不提供实时 WebSocket 转写或独立 `/v1/audio/transcriptions` 上传接口 |
| Robotics，1 个 | `gemini-robotics-er-2-preview` | 通过普通生成接口使用模型声明的视觉／音频／视频和工具能力；不提供 Streaming Robotics 会话 |
| Gemma，2 个 | `gemma-4-26b-a4b-it`、`gemma-4-31b-it` | 官方资料确认文本／图像输入和文本输出；不把小尺寸 Gemma 的音频能力套用到这两个模型 |

目录筛选要求上游包含 `generateContent`、主能力包含聊天生成能力，并排除尚未实现的视频／实时／音乐通道及 Interactions 配置。能力码 `91` 的输出续写仍是 `GenerateContent`，不会因此被过滤；`Paid` 标签也不是过滤条件。

该快照中的五个 TTS 模型缺少聊天生成能力码，因此未进入列表；实时、音乐和 Interactions 通道也尚未接入。通用语音或媒体字段的编码支持，不代表对应通道及独立音频服务已经可用。

## 7. Token 计数

`countTokens` 由手机内后端调用 **Playground `CountTokens` RPC**，返回上游提供的 `totalTokens`；计数过程不进行本地 tokenizer 估算，也不调用生成接口。

将以下内容保存为 `count-request.json`：

```json
{
  "contents": [
    {"role": "user", "parts": [{"text": "你好，统计这段文字。"}]}
  ]
}
```

```bash
curl --fail-with-body \
  http://127.0.0.1:8888/v1beta/models/gemini-4-argon:countTokens \
  -H "Authorization: Bearer $PG_PROXY_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @count-request.json
```

响应形状为 `{"totalTokens": 数值}`，具体值取决于模型与输入内容。

需要把系统指令和函数声明一起计入时，使用 `generateContentRequest` 包装，示例如下：

```json
{
  "generateContentRequest": {
    "model": "models/gemini-4-argon",
    "systemInstruction": {
      "parts": [{"text": "用简短中文回答。"}]
    },
    "contents": [
      {"role": "user", "parts": [{"text": "请查询上海天气。"}]}
    ],
    "tools": [{
      "functionDeclarations": [{
        "name": "get_weather",
        "description": "查询指定城市的天气",
        "parameters": {
          "type": "object",
          "properties": {"city": {"type": "string"}},
          "required": ["city"]
        }
      }]
    }]
  }
}
```

顶层 `contents` 与 `generateContentRequest` 二选一。包装内填写的模型必须与路径一致；未知可选字段忽略，无法可靠编码的正文或结构仍返回 `400`，模型未声明 `countTokens` 时返回方法不支持错误。

计数与生成共享凭证操作队列，但计数不推进生成轮询位置，也不将凭证标记为“生成验证通过”。概览请求统计包含计数和模型详情接口；计数成功仅说明该次计数完成。

## 8. 工具、Schema、媒体的明确限制

### 函数、工具与结构化输出

| 功能 | 当前行为 |
| --- | --- |
| 函数调用 | 依据模型能力支持函数声明、函数调用／结果、ID 与签名回传；Google `AUTO`／`NONE`、OpenAI `auto`／`none`；原生允许在已声明函数内选择 `allowedFunctionNames` 子集 |
| 强制函数调用 | Google `ANY`／`VALIDATED`、OpenAI `required`／指定函数名称退回 `AUTO`／`auto`；不承诺强制调用，不以提示词模拟约束 |
| 并行函数参数 | 忽略无法执行的 OpenAI `parallel_tool_calls: false` 限制；`true` 配合有效函数声明使用 |
| JSON 输出 | 支持文本、JSON 对象和可编码的 JSON Schema 子集；忽略 `strict: true` 和未实现约束，无法表达的 Schema 不作为强制要求，不承诺严格校验输出 |
| 内置工具 | 根据目录能力提供搜索、图片搜索、代码执行、URL Context、Maps；Maps 自定义坐标等选项未接入；部分工具组合有显式限制 |

### 媒体与长输出

| 功能 | 当前行为 |
| --- | --- |
| 原生媒体 | `inlineData`、HTTPS `fileData` 及视频／媒体配置；同时指定 `videoMetadata` 与 AGENTIC 时保留 STATIC 配置，FPS 按 `(0,24]` 处理；没有 Drive 上传、Files API 或任意文件管理服务 |
| OpenAI 图片输入 | 仅 base64 `data:image/...`，不自动下载远程图片；忽略 `detail` 提示，保留图片正文 |
| OpenAI 音频输入 | `input_audio` 的 base64 音频，支持已列出的格式；是否能调用仍受模型输入能力限制 |
| OpenAI 文档／视频输入 | 当前拒绝，需改用原生 Gemini 接口 |
| OpenAI 媒体输出 | 图片通过 `images` 扩展；音频通过带真实 MIME 的 `audio_chunks` 返回，不提供标准 OpenAI `audio.voice`／`audio.format` 转码接口。独立的 `speech_config` 仍受模型语音能力限制，不因此开放未列出的 TTS 模型 |
| 转写配置 | 仅接受模型声明的字段和语言代码；词表、智能转写与时间戳／说话人标注存在组合限制，见下文 |
| 长输出 | 续写模型跨轮保持同一凭证、每轮新 proof、隐藏中间结束标记并累计用量；真实长输出、工具与媒体组合仍需单独验收 |

不支持的可选参数会忽略，支持的设置继续处理。缺失正文、损坏媒体、无法关联的函数历史等核心数据仍返回明确错误；认证失败、权限拒绝、限流与网络异常分别处理，生成失败不会自动重放到另一个账号。完整默认值与优先级见[客户端兼容](CLIENT_COMPATIBILITY.md)。

### 转写配置与时间戳

原生请求使用 `generationConfig.transcriptionConfig`，OpenAI 请求使用顶层 `transcription_config`。两者内部均使用 `wordTimestamps`、`speakerLabels`、`customVocabulary`、`languageCodes`、`smartTranscription`，并按模型能力分别校验；音频输入能力不等于支持全部转写选项。

代理在发送生成请求前处理下列限制。时间戳／说话人标注优先，冲突的可选设置忽略：

| 设置 | 限制 |
| --- | --- |
| 非空 `customVocabulary` | 同时启用时间戳或说话人标注时，忽略词表 |
| `smartTranscription: true` | 同时启用时间戳或说话人标注时，忽略 Smart 开关 |
| `wordTimestamps: true` 与 `speakerLabels: true` | 可以一起表达；不得再同时开启上述冲突选项 |
| 空词表或值为 `false` 的开关 | 不视为启用了对应选项；不会因键存在就自动判断冲突 |
| `languageCodes` | 目录提供非空 `pg_proxy.upstream.options.languages` 时，仅保留其中的代码，不猜测语言或改写地区后缀 |

例如，已验收的转写模型目录列出英语代码 `en`，需要指定英语时应提交 `"languageCodes": ["en"]`。`en-US` 或 `EN` 等不在枚举中的值会被忽略；若没有剩余有效值，则使用上游默认行为，不伪造语言映射。目录未提供枚举时，交由上游校验。各配置组合的历史真实测试结果见[模型能力验收记录](MODEL_CAPABILITIES_TEST.md)，兼容过滤本身的测试范围另见[客户端兼容](CLIENT_COMPATIBILITY.md)。

原生响应的文本 Part 保留上游给出的 `transcriptionMetadata.speaker` 和 `timestamps`；时间戳条目保留相应 `text`、`start`、`end`。下面只是字段形状示例，不是固定转写结果：

```json
{
  "text": "hello",
  "transcriptionMetadata": {
    "speaker": "speaker-1",
    "timestamps": [{
      "text": "hello",
      "start": {"seconds": 0, "nanos": 100000000},
      "end": {"seconds": 1, "nanos": 200000000}
    }]
  }
}
```

`start`／`end` 使用 Duration 结构，包含整数 `seconds` 和上游提供时的 `nanos`。内部 JSPB 的秒值可能是整数或十进制整数字符串，解析后统一为整数；省略秒值按 `0` 处理。上游没有给出的逐词文本、时间戳或说话人信息不会由代理编造，开启配置也不代表每个响应 Part 都必然包含所有字段。

## 9. RikkaHub 能读取哪些信息

以下结论来自 RikkaHub 修订 `a5db9c9b770f316c76136970aa01a9d790e10e51` 的源码检查，未复制其 AGPL 实现代码。客户端后续版本可能改变这些行为。

| RikkaHub 路径 | 当前源码读取行为 |
| --- | --- |
| OpenAI 提供商的模型列表 | 只读取 `data[].id`，显示名称也使用 ID |
| Google 提供商的模型列表 | 读取 `name`、`displayName`、`supportedGenerationMethods`，据方法识别聊天／嵌入类型 |
| 输入输出模态、推理／工具能力 | 添加模型时还会使用客户端本地 `ModelRegistry` 按 ID 推断；不是从上述全部扩展字段自动导入 |
| 上下文长度 | 不能据本次服务器扩展认定 RikkaHub 会自动应用正确值；需核对客户端模型设置 |

因此，服务端返回完整扩展字段后，仍需在该版本的 RikkaHub 中核对模型标签、模态、工具和上下文设置；客户端不会自动导入所有 `pg_proxy` 或 OpenRouter 风格字段。

该版本的 Google 提供商只请求 `models?pageSize=100`，不会继续读取 `nextPageToken`。19 项目录不受影响；若未来超过 100 项，客户端可能只显示第一页。

### 多轮工具调用与签名

Google 部分响应包含不透明的 `thoughtSignature`。下一轮使用这些模型输出时，客户端必须保留并原样回传相关签名、函数调用和原生 Part 结构。

- 原生 Gemini 路径保留 `thoughtSignature`；上述版本的 RikkaHub Google 提供商能够在相关消息／工具元数据中读取并回传该字段。
- PG Proxy 的 OpenAI 路径通过 `extra_content.google.thought_signature` 和 `extra_content.google.parts` 暴露签名与原生历史；函数调用也可携带相应 `extra_content`。
- 该版本的 RikkaHub OpenAI 路径未保留这套 Google 扩展，下一轮可能丢失签名。涉及签名时，优先使用原生 Google 提供商，并确认请求发往 PG Proxy 的 `/v1beta` 生成接口；单轮成功不能证明多轮工具链兼容。
- 代理不伪造签名，也不使用跨账号签名缓存补齐被客户端丢掉的字段。流式客户端需要合并并保存完整的签名扩展，而不只拼接可见文本。

上述检查未覆盖 RikkaHub 安装包的实际交互验收。该客户端也不会将 `audio_chunks` 自动作为标准 OpenAI 音频播放。

## 10. 为什么上游列出了缓存与批处理，代理却没有这些接口

`createCachedContent` 与 `batchGenerateContent` 是 Google 公开 Gemini API 能力的一部分。网页登录目录列出这些方法，不能证明 Cookie 认证的 Playground RPC 与公开 API 使用相同地址、认证和生命周期。

PG Proxy 尚未实现并验证这两条 Cookie Playground 链路，目录与接口按以下规则处理：

1. 上游原始声明可保留在 `pg_proxy.upstream.generation_methods`，便于核对。
2. 它们不会出现在代理的 `supportedGenerationMethods` 中，也不会提供虚假的成功端点。
3. 不通过额外 API Key、付费项目、Build 通道或公开 Gemini API 回退来冒充 Playground 功能。
4. 真正接入需要分别完成认证、请求编码、状态轮询、取消／清理和真实端到端验证。

以后接入时，需要以可验证的 Playground 会话协议为依据，不能仅凭目录中出现方法名就开放接口。

## 11. 验证与维护

对应实现主要位于：

- [model_catalog.py](../backend/pgproxy/model_catalog.py)：字段、模态来源、能力扩展、保守合并和分页。
- [playground_protocol.py](../backend/pgproxy/playground_protocol.py)：在线目录入口和生成协议编解码。
- [openai_transformers.py](../backend/pgproxy/openai_transformers.py)：OpenAI 请求／响应与签名映射。
- [token_protocol.py](../backend/pgproxy/token_protocol.py)：Playground token 计数编码与响应解析。
- [test_model_catalog.py](../tests/test_model_catalog.py)：目录、详情、分页、来源与多账号元数据合并回归。

字段资料取自 [Google AI Studio 模型文档](https://aistudio.google.com/docs/models)、[Gemini 模型说明](https://ai.google.dev/gemini-api/docs/models)及 [Gemma 4 模型卡](https://ai.google.dev/gemma/docs/core/model_card_4)。具体输入模态的链接和核对日期随每条已知模型的 `modality_source` 返回。Playground 私有协议参考来源见[来源说明](SOURCES.md)，客户端读取行为参考 [RikkaHub](https://github.com/rikkahub/rikkahub)。

运行本地回归：

```powershell
.venv\Scripts\python.exe -m pytest -q
```

模拟测试不访问 Google，不能提供真实生成、设备运行、功耗或长期后台的证据。请按验证层次查阅记录：

| 记录 | 范围 |
| --- | --- |
| [Pixel 10 模型能力验收](MODEL_CAPABILITIES_TEST.md) | 19 个模型及代表性工具、媒体、Schema、计数和接口组合 |
| [模型目录与生成验证](MODEL_CATALOG_TEST.md) | 此前 Argon 真机短请求及目录验证 |
| [项目验证说明](VALIDATION.md) | 构建、本地回归、设备运行与其他阶段的测试入口 |

两轮模型测试均未验证真实跨轮长输出。结果只适用于记录中的安装包、账号条件和接口，不应扩展为全部参数组合或所有设备都已通过。
