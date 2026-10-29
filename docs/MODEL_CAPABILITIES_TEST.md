# 模型目录与能力验收 · 2026-10-30

本轮使用 **Pixel_10_Playground_Test / emulator-5556**，Android 16（API 36）、x86_64。测试时间采用北京时间。服务与 WAA 均运行于模拟器中的 APK，本轮未操作真机、启动电脑替代后端或使用 Termux。

应用版本为 **0.1.0 / versionCode 10**，测试包为 x86_64 Debug APK。电脑通过 ADB 转发访问 APK 内的 Python 服务，使用应用已导入的真实 Google 会话与随包 WAA 运行时完成生成。测试没有导出 Cookie 或使用固定 proof；原始响应、真实思考签名和私有连接配置保留在本机忽略目录。

**验收结论：19 个模型均有真实计数与生成成功记录；工具、媒体和流式按代表模型验证。以下是 x86_64 测试证据，不构成新 ARM64 Release 包的真机运行证明。**

本轮经过多次“发现失败 → 修复 → 复测”。逐模型结果汇总各修复阶段；最初安装包并非一次性通过全部检查。完整功能检查阶段包的 SHA-256 为 `F2407ABAE11D2DB65805582424B3F230A57FA208816DFEFA3688753315089B2C`。最后补齐视频互斥与 FPS 校验后的测试包 SHA-256 为：

```text
BC149AEC64367FB8F2D4992949878E273C4F9216DCF213653941BADE4C9DA70C
```

该轮最终测试包重新通过目录、详情、分页、认证、STATIC / AGENTIC 视频真实生成、取消与队列恢复、转写及视频非法组合拒绝检查。对应源码的 **517 项 Python 回归全部通过**，保留 1 项既有 Starlette 弃用警告；离线 Gradle 构建成功，复用了已缓存的原生依赖。

## 验收结果

| 检查 | 结果与范围 |
| --- | --- |
| OpenAI / Gemini 目录 | 两条接口均返回 19 个模型；标准字段、扩展字段、详情查询和无认证拒绝通过 |
| Gemini 分页 | 每页 5 项，4 页合计 19 项，顺序一致、无遗漏或重复 |
| Token 计数 | 19 个模型逐一调用真实 `CountTokens` 成功；转写模型使用音频输入 |
| 逐模型生成 | 19 个模型均有真实生成成功记录，详见下表；不将只有 HTTP 200 或目录存在当作通过 |
| 原生函数调用 | 获得真实函数名称、参数和思考签名；带原样签名及工具结果完成第二轮生成 |
| OpenAI 函数调用 | `tool_calls`、工具回复、多轮签名回放通过；第二轮正确返回首轮之后才生成的随机工具结果，避免仅靠模型自行计算判定通过 |
| 工具流式输出 | 两个工具调用的索引、参数组装、结束标记、用量通过 |
| JSON / 思考 | 原生与 OpenAI 结构化 JSON 内容通过；原生思考签名或思考用量有真实返回 |
| 内置工具 | 代码执行得到计算结果；Google Search、URL Context、Maps 返回相应来源或工具元数据；图像搜索生成图片并返回来源信息 |
| 媒体输入 | 原生图片、音频、PDF、视频均识别出合成素材的已知内容；OpenAI 图片与音频输入通过 |
| 图像输出与编辑 | 五个图像模型的返回文件均可解码；OpenAI `images` 输出通过；图像编辑将蓝色方块改为绿色，像素检查确认绿色区域且没有蓝色区域 |
| 视频参数与历史 | STATIC 的截取时间、FPS、媒体分辨率通过；AGENTIC 返回两个处理工具事件，保留全部模型 Part 和真实签名的第二轮历史回放也通过 |
| 转写 | 普通转写、词级时间戳＋说话人、独立自定义词表、Smart 模式分别通过；10 个词级时间戳保留文本与起止时间 |
| SSE | 原生与 OpenAI SSE 的多个事件、结束标记、来源、签名及用量通过 |
| 错误与取消 | 8 类不支持的 OpenAI 参数明确返回 400；转写语言及冲突参数明确返回 400；取消流后下一次计数请求在 0.44 秒内成功，健康检查正常 |

工具与媒体检查主要使用 `gemini-3.8-flash`；图像编辑使用 `gemini-3.1-flash-image`，图像搜索使用 `gemini-nano-banana-2.1`，转写使用 `gemini-3.5-transcribe`。**本轮未穷举所有模型、参数值和工具组合。**

## 19 个模型的真实生成

每行均通过目录与计数；“生成通过”对应实际文本、可解码图片或可识别音频转写内容。

| 分类 | 模型 ID | 真实生成结果 |
| --- | --- | --- |
| 文本／多模态 | `gemini-4-argon` | 短文本通过 |
| 文本／多模态 | `gemini-3.8-flash` | 短文本通过，另覆盖上述多项功能 |
| 文本／多模态 | `gemini-3.5-flash-lite` | 短文本通过 |
| 文本／多模态 | `gemini-3.1-pro-preview` | 修复 JSPB 思考标记解析后通过 |
| 文本／多模态 | `gemini-3.6-flash` | 短文本通过 |
| 文本／多模态 | `gemini-3.1-flash-lite` | 短文本通过 |
| 文本／多模态 | `gemini-3-flash-preview` | 短文本通过 |
| 图像 | `gemini-nano-banana-2.1` | 图片通过，另验证图像搜索 |
| 图像 | `gemini-3.1-flash-lite-image` | 图片通过 |
| 图像 | `gemini-3.1-flash-image` | 图片通过，另验证编辑 |
| 图像 | `gemini-3-pro-image` | 图片通过 |
| 图像 | `gemini-2.5-flash-image` | 图片通过，另验证 OpenAI 图片输出 |
| 转写 | `gemini-3.5-transcribe` | 合成语音内容通过，扩展模式分别验证 |
| 动态别名 | `gemini-pro-latest` | 短文本通过 |
| 动态别名 | `gemini-flash-latest` | 短文本通过 |
| 动态别名 | `gemini-flash-lite-latest` | 短文本通过 |
| 机器人理解 | `gemini-robotics-er-2-preview` | 短文本通过；不代表真实机器人执行验证 |
| Gemma | `gemma-4-26b-a4b-it` | 修复 JSPB 思考标记解析后通过 |
| Gemma | `gemma-4-31b-it` | 修复 JSPB 思考标记解析后通过 |

模型数量与别名指向来自测试时的账号目录；本表不是模型名称允许列表，也不保证未来账号权限、额度或上游接口保持不变。

## 实测发现并修复的问题

1. **JSPB 布尔编码**：部分模型返回数字 `0/1`，原解析只接受 `true/false`。已兼容两种形式并严格拒绝其他类型；函数参数 Struct 的布尔字段采用相同规则。
2. **转写 Duration**：秒数会以十进制字符串返回。已兼容整数与字符串，保留每个词的文本、时间戳，并支持作为后续历史回传。
3. **转写组合限制**：语言代码精确按目录校验；自定义词表不能与时间戳或说话人标注组合；Smart 不能与时间戳或说话人标注组合。失败请求不会被悄悄改写成另一种配置。
4. **视频组合限制**：静态 `videoMetadata` 与 `AGENTIC` 互斥；FPS 范围为 `(0,24]`。合法的两种模式分别完成真实生成。
5. **计数协议**：包含媒体、函数或视频工具历史时使用嵌套生成请求；按账号权益发送计数请求头，不生成 WAA proof，也不消耗生成轮询位置。
6. **能力声明**：移除实际无法使用的参数声明，例如仅有思考能力却不支持该 `reasoning_effort` 取值映射的情况；详情与计数也已纳入请求统计。

测试脚本本身另修正了产品名称连写的断言：音频识别为 `PGProxy` 与 `PG Proxy` 均可表达录音中的名称，不应仅因空格差异判成音频输入失效。此项不是代理业务修复。

## 客户端兼容与尚未覆盖的边界

- **RikkaHub**：按固定源码版本的 JSON 读取行为执行合同测试，未在本轮安装其应用完成界面端到端测试。它不会自动读取全部 `pg_proxy` 扩展；能力标签依赖其本地注册表。OpenAI 通道可能丢弃 Google 思考签名，原生 Google 通道能保留对应元数据，详见[模型接口说明](MODEL_API.md#9-rikkahub-能读取哪些信息)。
- **缓存与批处理**：目录原始方法可能列出 `createCachedContent`、`batchGenerateContent`；本项目尚未实现并验证其 Cookie Playground 生命周期，因此不在代理支持方法中声明。本地缓存和循环生成不能替代上游相应操作。
- 强制工具选择、严格任意 JSON Schema、Files 上传管理、Maps 自定义坐标、OpenAI 标准音频转码等未实现项见[接口限制](MODEL_API.md#8-工具schema媒体的明确限制)。当前筛选之外的 TTS、Live、音乐、Interactions 等模型未纳入本轮 19 个模型验收。
- Argon 只验证短生成，真实跨轮长输出与极限上下文、极限图片分辨率、长音视频及所有工具组合未验证；多账号真实轮询仍不能由单账号结果推断。
- 本轮不代替 ARM64 真机、局域网直连、锁屏或长期后台验收；这些历史结果保留在[真机记录](PHYSICAL_DEVICE_TEST.md)，不得混用包哈希与测试范围。

## 复现入口

先记录目标源码提交，按[构建文档](MAINTENANCE.md)构建 x86_64 Debug 包，在指定模拟器安装并保留已有凭证。没有凭证时，由用户在应用内完成登录。以下设备序列号仅是本轮示例，执行前应确认目标设备。

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tools\build_app.py --abi x86_64 --skip-native-build --offline
adb -s emulator-5556 install -r artifacts/PG-Proxy-0.1.0-x86_64.apk
# 在应用中启动服务。8888 应替换为应用实际端口。
adb -s emulator-5556 forward tcp:18900 tcp:8888
```

使用[模型接口说明](MODEL_API.md)中的目录、详情、分页、计数和生成示例，把地址改为 `http://127.0.0.1:18900`，密钥使用应用设置中的值。媒体与工具测试应检查实际内容、签名回放、工具事件及流结束，不能仅检查 HTTP 状态。

诊断结果位于本机 `artifacts/capability-upgrade/`：`live-results.jsonl`、`feature-results.jsonl`、`openai-feature-results.jsonl`、`transcribe-results.jsonl`、`additional-results.jsonl`、`video-replay-result.json`、`lifecycle-results.json` 及测试／构建日志。初次失败和修复后的记录一并保留。该目录包含真实签名及私有连接配置，**不要整体上传**；Release 仅附正式包、经审阅的公开校验资料和测试摘要。

测试结束后已停止应用服务并移除专用转发，保留账号及原端口、密钥和设置；私有桥接清理检查通过，停止服务后 `libpgwaa.so` 工作进程数为 0。临时连接文件与设备端测试素材已清理。测试不需要重新导入会话或删除账户。

```powershell
adb -s emulator-5556 forward --remove tcp:18900
```
