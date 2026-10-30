# 来源、版权与许可

本页记录 PG Proxy **0.3.0** 的代码来源、运行依赖、设计参考和许可边界。项目版本与依赖版本分别维护；完整声明随源码和安装包保留。

## 顶层许可证：MIT

PG Proxy 自有代码采用标准 [MIT 许可证](../LICENSE)，允许使用、复制、修改、合并、发布、分发、再许可和销售副本，并要求保留版权及许可声明。AIStudio2API 的改编部分继续保留其 MIT 声明。根许可证不附加发布渠道、非商业使用或禁止再分发条款。

根许可证保留 `Copyright (c) 2025 欤歡`，并补充 PG Proxy 的 2026 年贡献声明。不得删除 AIStudio2API 及 vendor 中各作者的原有版权行。MIT 不提供软件担保，也不授予 Google 等第三方商标、服务访问资格或账号使用权。

安装包的分发和自行构建不会改变第三方组件的许可证；发布时应附对应源码版本、许可与来源信息。构建、校验和发布流程见[构建与维护](MAINTENANCE.md#8-正式版本与-github-release)。

## 改编来源

| 来源 | 具体使用 | 追溯与许可 |
| --- | --- | --- |
| [AIStudio2API](https://github.com/Mag1cFall/AIStudio2API/tree/7cb5cc04676751b588146052475deec147fee35e) | `backend/pgproxy` 中的协议字段、签名、挑战与生命周期适配；`runtime/waa` 改编自其 `internal/waa` Firefox 宿主 | 固定参考提交 `7cb5cc04676751b588146052475deec147fee35e`；[MIT 原文](../THIRD_PARTY_LICENSE_AIStudio2API.txt)，Copyright (c) 2026 Mag1cFall |

协议适配和 WAA 浏览器宿主包含 AIStudio2API 的改编实现，因此保留其版权与完整许可证。运行时，PG Proxy 的 Python 后端和 Kotlin 集成直接完成请求与 Android 交互，无需部署 AIStudio2API 服务。

WAA 执行真实下载的挑战程序并生成 proof；vendor 中的浏览器接口形状和 Intl 兼容数据不是固定 proof。不要以静态 token、伪造成功响应或绕过校验代替原机制。

## 当前运行与构建依赖

| 组件 | 作用 | 许可或核对位置 |
| --- | --- | --- |
| [Mag1cFall/goja](https://github.com/Mag1cFall/goja)，以及 Go URL/文本等依赖 | JavaScript VM 和浏览器宿主基础 | `runtime/go.mod`、`runtime/vendor/modules.txt` 及各目录 LICENSE；包含 MIT/BSD/Apache 等许可和附加声明 |
| [Go](https://go.dev/) | 构建 Android WAA 可执行组件 | `app/src/main/assets/third_party/go` 中保留 Go 许可、专利声明与 vendor notices |
| [Mozilla GeckoView](https://mozilla.github.io/geckoview/) | 应用内登录浏览器 | MPL-2.0 及内含第三方许可；下节记录精确源码 |
| [Chaquopy](https://chaquo.com/chaquopy/) / [CPython](https://www.python.org/) | 将 Python 嵌入 Android | `Chaquopy-LICENSE.txt`、`CPython-LICENSE.txt`；MIT / Python 及其历史许可 |
| [FastAPI](https://fastapi.tiangolo.com/) / [Pydantic](https://docs.pydantic.dev/) | API 层、参数模型与原生校验 | MIT；版本见 `requirements-lock.txt` |
| [Uvicorn](https://www.uvicorn.org/) / [HTTPX](https://www.python-httpx.org/) / httpcore / Starlette | HTTP 服务和客户端 | BSD-3-Clause，保留 wheel 元数据中的许可证 |
| [certifi](https://certifiio.readthedocs.io/) / typing_extensions | CA 证书与 Python 类型兼容 | 分别为 MPL-2.0 / PSF-2.0；不因上层应用用 MIT 而改变 |
| [AndroidX / Compose](https://developer.android.com/jetpack/compose) | 界面、图标、生命周期 | Apache-2.0；依赖版本见 `app/build.gradle` |
| [Rust](https://www.rust-lang.org/) 与 pydantic-core crates | 构建 Python 原生 wheel | 标准库和 crates 各自许可见 `app/src/main/assets/third_party/rust`；MIT/Apache 双许可等按实际条款保留 |
| [Gradle](https://gradle.org/) | 构建引导及执行 | 仓库保留官方 wrapper，包括 `gradle-wrapper.jar`；Apache-2.0；不是本应用成品 |

此表便于查找，完整条款见 [NOTICE](../NOTICE)、`runtime/vendor`、`app/src/main/assets/third_party` 和安装包内依赖元数据。`tools/collect_licenses.py` 从本地缓存收集文本；新增依赖仍需核对其许可证、版权声明和源码提供要求。

## Gecko / MPL 的对应源码

当前引擎为 `org.mozilla.geckoview:geckoview:159.0.20261022150525`，未修改引擎自身源码。

- 对应源码提交：[`5add844c26613d7e4d84c799d6e23cf987b1240d`](https://hg.mozilla.org/releases/mozilla-release/rev/5add844c26613d7e4d84c799d6e23cf987b1240d)。
- 构建来源与 SHA-256：[source.json](../app/src/main/assets/third_party/gecko/source.json)。
- 完整引擎许可：[Mozilla-license.html](../app/src/main/assets/third_party/gecko/Mozilla-license.html)，另保留 pdf.js 字体／字符映射许可；应用内也提供“开源许可”。
- Kotlin 登录集成和会话桥扩展是独立应用代码，不是对 Mozilla 引擎文件的修改。

[Mozilla 的 MPL 说明](https://www.mozilla.org/en-US/MPL/2.0/FAQ/)解释了文件级义务和可执行形式分发时的源码告知要求。若将来修改 MPL 覆盖文件，须按其条款提供相应源码，并保持源码入口与实际引擎版本一致。GitHub Release 和其他渠道均适用这些要求；certifi 等组件也继续遵循各自许可证。

## 参考而未引入的项目

- [FlClash](https://github.com/chen08209/FlClash)：界面信息层级、卡片风格及网络查询服务资料。参考提交 `7fd417fb9a9a277c6a9339a8ac87f907df7c25f7` 和具体文件见[网络信息说明](NETWORK_INFO.md)。PG Proxy 的 Kotlin/Compose 与 Python 客户端独立实现，没有捆绑 FlClash 的 Dart 代码、图标或图片；这里的致谢不是将它声明为运行依赖。
- [RikkaHub](https://github.com/rikkahub/rikkahub/tree/a5db9c9b770f316c76136970aa01a9d790e10e51)：参考提交 `a5db9c9b770f316c76136970aa01a9d790e10e51`，项目采用 AGPL。此次仅核对 OpenAI／Google 提供商的模型目录读取、客户端本地 `ModelRegistry`、消息与 Google 思考签名的输入输出字段，PG Proxy 的对应适配独立实现，未复制其源码、资源或引入其运行依赖。服务端扩展不代表该客户端会自动读取；实际读取范围及限制见[模型与接口说明](MODEL_API.md)。如果未来需要复制或改编该项目代码，须另行评估并履行其许可证要求，不能沿用本条“仅参考”的说明。
- [Android-Node-Builder](https://github.com/print-yuhuan/Android-Node-Builder)：早期方案调研过 Android Node 运行时，**当前未使用 Node.js 或该项目的 libnode.so**。实际 WAA 引擎是 Goja，不要求下载该发布包。

## 模型能力资料来源

客户端请求兼容另核对 Cherry Studio、RikkaHub、Kelivo、SillyTavern 的公开请求格式，固定提交、文件及核对范围见[客户端兼容来源](CLIENT_COMPATIBILITY.md#参考来源与测试范围)。仅以外部 JSON 行为编写独立适配与合同测试，未复制这些客户端的源码或资源，亦未将其作为运行依赖；相关项目仍适用各自许可证。

实际模型 ID、方法、限额和能力码来自已登录会话的在线 `ListModels`。为避免猜测未明确解释的能力码，部分输入模态及统一上下文资料另参考 [Google AI Studio 模型文档](https://aistudio.google.com/docs/models)、[Gemini 模型说明](https://ai.google.dev/gemini-api/docs/models)与 [Gemma 4 模型卡](https://ai.google.dev/gemma/docs/core/model_card_4)，本轮核对日期为 **2026-10-30**。资料记录在 `model_catalog.py` 的补充元数据中，接口通过 `pg_proxy.upstream.modality_source` 返回具体页面、日期和解析后的模型目标。

这份资料注册表用于解释字段，不是模型名称允许列表；未知模型仍按在线能力筛选，缺乏可靠来源的属性保持未知。模型类型、模态、工具、限额与客户端兼容的具体区分见[模型与接口说明](MODEL_API.md)。文档资料、目录声明和私有协议字段核对不能代替真实生成验证；本轮对应结果单独见[模型能力验收记录](MODEL_CAPABILITIES_TEST.md)。

## 图标、品牌与素材边界

应用图标是原生 Android 矢量：烧瓶外形参考用户提供的 Google Labs 图示，Gemini 四角星轮廓来自用户提供的 `google-gemini.svg`，转绘后采用镂空形式并透出蓝色背景。

README 的 `docs/assets/icon.svg` 对应应用当前矢量图标。三张界面预览来自本项目 `UIInstrumentedTest` 的合成状态截图，使用虚构账号和文档测试 IP，不包含真实登录会话。

当前没有独立核验这两份参考素材的再许可文件，因此不宣称获得 Google 对标志或商标的授权，也不把 MIT 解释为第三方标志授权。Google、Google Labs、Gemini、Mozilla、Firefox 等名称与标志归各自权利人；本应用非官方产品。后续更换、商业推广或再分发图标时，应单独核对相关权利；有权利疑问可通过项目 Issues 联系维护者。

## 更新来源信息

升级依赖或替换素材时，同步修改版本锁、对应源码链接、NOTICE、完整许可证和打包校验。保留可复查的版本、提交和哈希；不要写“使用最新版”作为唯一来源记录。操作流程见[维护文档](MAINTENANCE.md)。
