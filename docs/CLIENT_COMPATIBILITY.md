# 客户端请求兼容

PG Proxy **0.3.0** 的 `/v1/chat/completions` 在协议编码前清理可选参数，尽量让不同客户端使用同一个接口。原生 Gemini 生成与计数入口也执行相应的兼容处理。参数兼容自 0.2.0 引入，0.3.0 增加请求入口与 Schema 资源保护，并修复初始化失败后的状态恢复。安装包的对应提交与构建校验见该版本 Release 附件 `BUILD_INFO.json`；0.1.0 不包含这两轮兼容与保护改动。

**兼容处理不会增加模型能力。** 忽略 `logprobs` 不会产生概率结果，忽略 `strict` 不保证 Schema 严格执行，接受 `required` 工具选项也不保证模型必然调用工具。目录的 `supported_parameters` 仍只列可实际映射的参数；`pg_proxy.proxy.request_compatibility` 说明宽容接收策略。

## 如何配置客户端

选择 **OpenAI 兼容 / Chat Completions**，Base URL 填写 `http://127.0.0.1:8888/v1`，密钥使用 PG Proxy 设置中的接口密钥。其他设备使用手机局域网地址。需要自行选择模型时，先读取在线模型目录。

这不包含 `/v1/responses`、旧文本 `/v1/completions`、Embeddings、独立图片或语音服务。客户端选择了另一种接口协议时，仅忽略参数不能使该接口自动可用。Google 思考签名、多轮工具与生成图片仍需要客户端保存、回传或展示相应字段，详见[接口说明](MODEL_API.md)。

## 忽略、补全与优先级

| 输入情况 | 当前行为 |
| --- | --- |
| 未知顶层或嵌套可选字段 | 丢弃额外字段，保留可支持内容；不记录原始请求、Cookie 或参数值 |
| `presence_penalty`、`frequency_penalty`、`logit_bias`、`logprobs`、`user`、`metadata` 等未实现选项 | 忽略，不转发到 Playground，不伪造对应结果 |
| 可选参数为 `null` | 按未提供处理，例如 `stream_options: null` 不再引发内部异常 |
| 缺少或留空 `model` | 从当前在线目录选择普通文本模型，优先名称包含 `flash` 的候选；没有可选文本模型时明确报错，不写死模型 ID |
| 显式填写不存在的模型 | 保留 `404 / model_not_found`，不会偷偷换模型 |
| 缺少 `stream` / `n` | 使用 `false` / `1`；多候选请求也按单候选执行 |
| 常见布尔或数字字符串 | 能明确转换时转换；无效可选数值忽略，有界生成设置按模型允许范围处理 |
| 同时提供两种 token 上限 | 有效的 `max_completion_tokens` 优先于 `max_tokens` |
| 同时提供思考配置和 `reasoning_effort` | 显式 `thinking_config` 优先；模型不支持的预算、级别或选项忽略 |
| 消息缺少角色 | 根据工具历史推断，否则按 `user` 处理；普通消息的 `name` 作为可选标签忽略 |
| 工具调用缺少类型、参数 | 能确定是函数时补 `type: function`；缺少参数补空 JSON 对象；缺少调用 ID 仅在历史关联唯一时补全 |
| 函数声明或 Schema 的可选信息缺失 | 无参数函数使用空对象 Schema；结构化输出缺少 Schema 名称时使用 `response` |
| `strict: true`、`tool_choice: required`／指定函数、`parallel_tool_calls: false` | 忽略不能兑现的强制约束；函数选择退回 `auto`，保留有效工具声明；`none` 仍保留其禁用含义 |
| JSON Schema 扩展约束 | 仅保留可编码子集；不支持的约束、无法解析或递归引用不作强制要求；展开超出预算时整体放弃当前可选 Schema，保留正文与可用 JSON 输出设置 |
| `image_url.detail: high/low` | 忽略分辨率提示，保留原图片数据；仍不代下载远程图片 |
| 旧 `functions`、`function_call` 与 `role: function` | 转换能明确关联的输入历史；响应仍为现代 `tool_calls`，不承诺完整旧式工具客户端兼容 |

缺少输出长度、温度等设置时沿用在线模型默认值，不用固定参数覆盖所有模型。按账号选中模型后，再过滤该模型不支持的图像、语音、转写、思考及内置工具选项。

原生 Gemini 也接受常见 snake_case 别名、文本简写与可推断的 `role`。计数请求继续使用真实 `CountTokens`，不会为了计数发起生成。转写选项发生冲突时，时间戳／说话人标注优先，冲突的词表和 Smart 开关忽略；完整边界见[转写配置](MODEL_API.md#转写配置与时间戳)。

## 请求入口与资源限制

可选参数兼容不等于无限接收或无限展开。以下限制用于控制单机资源占用：

| 限制 | 当前行为 |
| --- | --- |
| API 正文大小 | 最大 **64 MiB**，按收到的完整 JSON 字节数计算，包含 base64 与其他字段；超出返回 `413`，不会截断正文后生成 |
| 完整上传时间 | 正文须在 **60 秒**内收完，超时返回 `408`；与排队时间、上游每次读写超时分别计算 |
| HTTP 并发 | Uvicorn 的连接／任务并发上限为 **16**；达到上限由服务器返回纯文本 `503 Service Unavailable`，不要假定所有错误都是 JSON |
| Schema 展开 | 一次规范化中，响应 Schema 和各函数参数共享 **4096 次节点访问、256 KiB 累计编码体积**预算；包含描述、属性名、枚举与示例，按保守开销计数 |

公开模型与生成／计数入口在读取正文前校验密钥；认证失败返回 `401`。`408`、`413` 等入口错误使用 JSON `error`，其 `code` 为对应 HTTP 状态数字。容量限制并未改变**全局串行生成**：已接收的业务请求仍需等待凭证操作队列，HTTP 并发上限不代表可并行生成 16 次。

Schema 超出预算时不会提交已展开一半的约束，也不会为放宽约束再发一次生成。响应 Schema 被忽略后，可支持的 JSON 输出格式仍保留；函数参数 Schema 被忽略时使用空对象声明，函数名称与其他有效设置保留。预算耗尽后，同次规范化的后续可选 Schema 也不再展开。需要可靠结构化输出的客户端应使用精简 Schema，并校验实际返回值。

页面初始化因网络异常或取消而未完成时，会清除本次部分初始化状态。下一次独立请求重新完成初始化，不会直接复用半完成的会话元数据，也不会自动重放失败的生成请求。

## 仍会明确报错的情况

- 非法 JSON、缺少有效 `messages`／`contents`、错误正文类型或无法推断的角色。
- 损坏的 base64、不支持的实际媒体内容、错误函数参数 JSON、无法配对的工具结果。
- 不合法的真实 Google 签名字段；代理不会伪造或从其他账号借用签名。
- 错误密钥、不可用账号、显式不存在的模型、排队超时、Google 认证／权限／配额或网络错误。
- 超出上传大小或时间限制、HTTP 服务达到并发上限；这类资源限制不能通过忽略可选字段绕过。

这些信息决定请求的真实含义或访问权限，不能当成多余选项直接删除。服务不会凭空补一条用户消息，不会把上游失败包装成成功，也不会在失败后自动重放生成或切换账号重试。

## 请求示例

以下请求省略模型和消息角色，同时携带未实现的惩罚参数；接口会选择在线文本模型、补 `user` 角色、忽略惩罚参数，并保留正文和输出上限：

```json
{
  "messages": [{"content": "请用一句话介绍你自己。"}],
  "max_tokens": 128,
  "presence_penalty": 0.5,
  "stream_options": null
}
```

需要控制模型、工具或输出格式的应用，仍建议主动填写受支持的明确参数。宽容默认值用于减少兼容错误，不应替代客户端对实际执行能力的判断。

## 参考来源与测试范围

仅核对下列项目的公开请求格式，PG Proxy 的兼容实现与测试独立编写，未复制这些项目的实现代码、资源或增加运行依赖。

| 客户端 | 固定源码与核对内容 |
| --- | --- |
| Cherry Studio | [`7643520` · customParamsFetch.ts](https://github.com/cherryhq/cherry-studio/blob/7643520c6db0695a7800edc0901b1d32235d4d50/src/main/ai/runtime/aiSdk/params/customParamsFetch.ts)：自定义平铺参数合并到 SDK 请求体；[modelParameters.ts](https://github.com/cherryhq/cherry-studio/blob/7643520c6db0695a7800edc0901b1d32235d4d50/src/main/ai/utils/modelParameters.ts) 的采样参数适配 |
| RikkaHub | [`a5db9c9` · ChatCompletionsAPI.kt](https://github.com/rikkahub/rikkahub/blob/a5db9c9b770f316c76136970aa01a9d790e10e51/ai/src/main/java/me/rerere/ai/provider/providers/openai/ChatCompletionsAPI.kt)：token 上限、思考参数与流式用量 |
| Kelivo | [`ca2721e` · chat_completions_api.dart](https://github.com/chevey339/kelivo/blob/ca2721e07b31cc8acc331c7a9f400109f8c1e298/lib/core/services/api/providers/openai/chat_completions_api.dart)：普通消息名称、函数历史与工具选择 |
| SillyTavern | [`06bde93` · chat-completions.js](https://github.com/SillyTavern/SillyTavern/blob/06bde939fb1e9c4c8d8641d810f0a916b5bce127/src/endpoints/backends/chat-completions.js)：惩罚／采样字段、工具选项与默认 `strict: true` |

`tests/test_client_compatibility.py` 使用上述字段形状构造合成请求，经过真实 FastAPI 路由、参数转换、Playground 编码及响应投影；上游为本机 MockTransport。成功用例同时检查受支持字段保留、只发送一次生成，以及非流式／SSE／usage／结束事件结构。其余规范化与异常输入覆盖见 `test_openai_compat.py`、`test_gemini_compat.py`。

0.3.0 的入口限制回归见 `tests/test_request_guard.py`；Schema 共享引用、累计体积、正常小 Schema，以及初始化失败／取消后恢复见 `tests/test_review_protocol_regressions.py`。其中 API 合同检查保留有效正文和参数，并确认成功请求只发起一次上游生成；模拟上游不等于真实 Google 服务验收。

上述 MockTransport 用例属于**源码行为核对和服务端合同测试**，不是安装四个客户端后的端到端验收，也不证明所有版本、插件或自定义参数组合均可用。

0.3.0 另在 Pixel 10／Android 16／x86_64 Debug APK 上通过真实请求验收：HTTP 检查 10 项通过，覆盖目录、四种认证、OpenAI／Gemini 生成、流式及断流后恢复；兼容探针携带 24 层共享引用 Schema（2671 字节）、缺省消息角色和未知可选字段，仍返回 200 与有效 JSON。APK 的请求头阶段拒绝、资料 IO、UI 与生命周期也有设备用例，详见[本轮验证记录](VALIDATION.md#pixel-10-新包与真实请求)。

测试通过 ADB 转发访问 APK，未运行桌面后端或 Termux，未操作物理真机；不能据此宣称 ARM64 正式包或四个客户端的全部组合已完成验收。首次测试宿主及服务前置条件失败、随后完整复测的过程保留在验证记录中。

```powershell
.venv\Scripts\python.exe -m pytest -q
```
