# 验证总览与复现

本页汇总 **PG Proxy 0.1.0、0.2.0 与 0.3.0** 在 **2026-10-29 至 2026-10-30** 的开发与验收记录。每项结果对应具体阶段、架构和安装包；正式 Release 的源码、签名与附件校验以其公开 `BUILD_INFO.json` 和发布说明为准。

| 记录 | 环境与主要内容 | 详细报告 |
| --- | --- | --- |
| 0.3.0 · 2026-10-30 审查修复 | 本地回归与 Pixel 10 模拟器；入口保护、界面、生命周期、真实 WAA 与 API | 下节及[客户端兼容](CLIENT_COMPATIBILITY.md) |
| 0.2.0 · 2026-10-30 请求兼容 | Windows；客户端请求合同、异常输入与 ARM64 打包流程 | [客户端兼容](CLIENT_COMPATIBILITY.md)及下节 |
| 2026-10-30 模型能力适配 | x86_64 模拟器；19 个模型、计数、工具、媒体与流式 | [模型能力验收](MODEL_CAPABILITIES_TEST.md) |
| 2026-10-29 Argon 修复 | ARM64 真机；目录遗漏修复及四种真实短请求 | [Argon 验证](MODEL_CATALOG_TEST.md) |
| 2026-10-29 登录与后台 | ARM64 真机；登录、局域网、通知停止、短时锁屏 | [真机验收](PHYSICAL_DEVICE_TEST.md) |
| 构建、签名与 UI 整理 | 各阶段离线回归、APK 检查及合成界面测试 | 本页后续章节 |

这些历史记录不构成新 ARM64 发布包的安装运行证明。新的构建与签名结果、是否完成真机安装及真实生成，应在对应 Release 中分别说明。

## 0.3.0 · 2026-10-30 代码审查修复

本轮按全量审查中的 7 项问题修复代码与文档，保留原有可选参数兼容、全局串行轮询和单次生成约定。本地回归使用合成请求、替身上游及隔离文件；随后在 Pixel 10 模拟器安装新包，并使用已有凭证单独验证真实 Google 请求。各项修复与对应检查如下：

| 审查项 | 修复与检查范围 |
| --- | --- |
| 未认证请求的无界正文读取 | 先校验认证再接收 API 正文；64 MiB 上限、60 秒完整上传时限，超过限制分别返回 `413`／`408`；Uvicorn 连接／任务上限 16，满额由服务器返回纯文本 `503`；回归见 `tests/test_request_guard.py` |
| Schema 引用指数展开 | 响应与函数参数 Schema 共享节点及累计编码预算，超限整体忽略当前可选 Schema；覆盖重复引用、长描述／枚举／示例、字段名、正常小 Schema、正文保留与一次生成，见 `tests/test_review_protocol_regressions.py` |
| 页面初始化失败残留状态 | 初始化网络失败或取消时回滚部分状态；后续独立请求完整初始化，不自动重放生成；覆盖初始化各 RPC、取消、错误返回及锁释放，见同上回归文件 |
| Android 登录资料主线程 IO | 文件扫描、资料更新与清理移至串行 IO 调度，已有导入标记不重复写入；覆盖后台执行、主线程返回、互斥、页面取消与重复确认，见 `ProfileStorageIoInstrumentedTest`、`GeckoProfilesTest` |
| WAA 短延迟定时器竞态 | timer 指针赋值和注册在同一锁内完成，关闭时停止并清空；短延迟、取消与关闭回归见 `runtime/waa/loop_test.go` |
| 复现命令使用旧安装包名 | 当前构建与安装示例同步为 0.3.0；历史 0.1.0／0.2.0 的版本、测试数量和包哈希保留原始记录，不将旧结果冒充本轮结果 |
| 原生缓存缺少来源校验 | 缓存记录源码／构建输入摘要、ABI、工具链与二进制校验；复用及归档不匹配即失败，并核对 APK 内 worker 字节；对应 `tests/test_native_build_cache.py` 与发布构建回归 |

版本为 `0.3.0`、版本码 `30`，正式签名沿用 0.2.0。请求限制与 Schema 降级行为见[客户端兼容](CLIENT_COMPATIBILITY.md#请求入口与资源限制)，构建缓存约定见[维护文档](MAINTENANCE.md)。本轮已完成的本机检查：

| 检查 | 结果与边界 |
| --- | --- |
| Python 整合回归 | **853 项通过**；替身上游及本机 HTTP 入口测试，不消耗 Google 生成额度 |
| JVM 单元测试 | **3 项通过**；不能替代 Android 系统行为验收 |
| Go WAA 测试与静态检查 | **2 项测试与 `go vet` 通过**；`go test -race` 因本机缺少 GCC 未能执行，不标记为通过 |
| 原生组件重建 | 从本轮源码离线重建 Windows worker 及 Android `arm64-v8a`、`x86_64` worker，校验来源与摘要；不代表 APK 已安装运行 |

### Pixel 10 新包与真实请求

环境为 **Pixel_10_Playground_Test（emulator-5556）／Android 16／x86_64**，覆盖安装 `0.3.0` Debug APK、版本码 `30`。原有 **1 份凭证**、启用状态和六项配置保留；入口测试结束后恢复原先停止的服务状态，真实凭证测试也恢复其原启用状态。本轮未操作物理真机。

| 验证项 | 实际结果与边界 |
| --- | --- |
| Android instrumentation | **66 项不同用例通过**：入口与资料保留 1 项；UI、动画、资料 IO 等 56 项；Runtime／DeletionRecovery／BackgroundRuntime 8 项；已有 Gecko 凭证真实生成 1 项。重复执行不叠加计数 |
| 请求入口 | 保持连接但只发请求头、不上传正文时，错误密钥 **8 ms 返回 401**，超限 Content-Length **15 ms 返回 413**；坏 JSON 返回 400，之后健康检查仍通过。耗时仅描述本次测量 |
| 已有凭证与 WAA | `BrowserImportedAccountInstrumentedTest` 用 `gemini-3.8-flash` 完成两次真实生成，第二次强制更新 WAA 后仍获 Google 接受；不是重新执行 Google 交互式登录 |
| HTTP 端到端 | `tools/e2e_app.py` **10 项全部通过**：19 项实时模型目录、四种认证、OpenAI／Gemini 非流式与 SSE、原生 JSON 流，以及主动断流后目录仍可使用 |
| 真实兼容探针 | **1 项通过**：2671 字节、24 层共享引用 Schema，消息缺少 role 并携带未知可选字段；请求返回 200 和有效 JSON，确认 Schema 降级后正文仍可生成 |
| UI 人工检查 | 明色、暗色、大字号共 3 张截图检查正常；不代表全部屏幕尺寸与字体组合已覆盖 |
| 短时后台 | 约 10 秒后台窗口内 5 次健康检查通过；不能证明长期锁屏、Doze 或实际功耗 |

HTTP 测试通过 **ADB 端口转发**访问模拟器中 APK 的服务，没有运行桌面替代后端或 Termux；这不是两台物理设备间的局域网测试。目录有 19 项不代表本轮逐个模型都重新生成；本轮真实调用以 `gemini-3.8-flash` 为代表，19 个模型逐项生成的历史证据仍属于 0.1.0。

复测过程保留以下失败与前置条件：首次 UI 执行出现测试宿主 `No roots`、`UiAutomation` 重复注册，instrumentation 结束时崩溃；保留应用数据重启后，整组 56 项全部通过，未为该测试环境问题修改生产代码。首次 HTTP 执行因 instrumentation 结束后服务已停止而连接失败；在应用界面重新启动服务后，10 项全部重跑通过。首轮失败不计为通过，最终统计按通过的完整执行与不同用例计算。

本轮日志与脱敏结果保存在 Git 忽略的 `artifacts/release-0.3.0/`。以上是 **x86_64 Debug APK** 的设备结果；ARM64 正式包由最终源码另行构建，其签名、提交及哈希以 Release 清单为准，不将模拟器结果表述为 ARM64 正式包已在真机安装运行。真实双账号轮询、跨轮长输出、长期后台及 Go race 检测仍未覆盖。

## 0.2.0 · 2026-10-30 客户端请求兼容

在 OpenAI 转换前及选定账号模型后加入兼容层：过滤未知／未实现可选参数、处理 null、补全可推断默认值，保留正文、媒体、认证和工具历史校验。目录新增 `request_compatibility` 描述，避免把宽容接收解释为强制约束已经实现。

- 兼容处理开发阶段 Python 回归 780 项通过；加入 4 项签名目录保护测试后，最终回归 **784 项通过**，有 1 条已有的 Starlette 测试客户端弃用提示。
- 独立 HTTP 合同 **83 项通过**，其中包含四个客户端公开源码字段形状的流式／非流式请求，以及 **456 次**可选字段类型组合；后者包含在测试内部，不额外计为 456 个 pytest 用例。
- 验证有效参数进入 Playground wire、每个成功请求只发起一次生成、SSE 结束与用量结构、函数调用 ID／参数／签名保留，以及错误密钥、坏 JSON／媒体和错配工具历史仍拒绝。
- 开发阶段使用完整 Gradle 流程构建 ARM64 Debug 包，并确认两个新兼容模块已进入 APK。正式 0.2.0 Release 按最终源码提交单独构建，签名、版本、哈希与源码对应关系以该 Release 的公开 `BUILD_INFO.json` 为准，不能以开发包结果代替正式包校验。
- 本轮测试时没有连接中的模拟器或手机，未进行设备安装、真实 Google 生成或四个客户端安装后的端到端验证；合同测试使用 MockTransport，不消耗账户额度。

测试执行：`python -m pytest -q --junitxml=artifacts/client-compatibility/python-tests.xml`。构建使用 `tools/build_app.py --build-type debug --abi arm64-v8a --skip-native-build --offline`，复用未修改的原生组件与 wheel 缓存。报告与安装包保存在 Git 忽略的 `artifacts/`；具体默认值、固定客户端参考提交和未接入接口见[客户端兼容](CLIENT_COMPATIBILITY.md)。

## 0.1.0 · 2026-10-30 模型能力验收

目录详情、能力元数据、分页、`countTokens`、工具与媒体适配的字段约定见[模型与接口说明](MODEL_API.md)。该轮使用 0.1.0 x86_64 Debug APK，19 个模型均有真实生成与计数成功记录；工具、媒体等功能按代表模型验收，该阶段 **517 项 Python 回归通过**。包哈希、逐项结果和未覆盖范围见[模型能力验收](MODEL_CAPABILITIES_TEST.md)。

目录读取、计数、生成与客户端完整交互分别验证。RikkaHub 的列表和签名行为依据固定源码版本及 JSON 合同检查，本轮未安装其应用做界面端到端测试。上游列出的缓存／批处理方法也不等于代理已经实现这些功能。

## 2026-10-29 Argon 目录与生成

该阶段修复能力码 `91` 的模型过滤，并实现对应的 `GenerateContent` 续写协议。Python 回归 **251 项通过**（2.95 秒，1 项已有弃用警告）；正式签名 ARM64 修复包完成构建、覆盖安装和设备 APK 哈希核对。

同一 vivo 真机、同一 Google 凭证的在线目录由 18 个变为 **19 个**；`gemini-4-argon` 出现在 OpenAI 和 Gemini 目录中。通过 USB 转发访问手机服务，Argon 的 OpenAI / Gemini 流式及非流式 **4 项真实短请求全部通过**。本次不是新一轮局域网或锁屏测试，真实跨轮长输出尚未验证。请求条件、耗时、包哈希、模拟测试范围和复现方式见 [Argon 验证记录](MODEL_CATALOG_TEST.md)。

## 2026-10-29 ARM64 真机结果

本轮已在 **vivo V2505A / Android 17 / ARM64 / 4 KB 内存页**上验证正式签名修复包，电脑直接访问手机 Wi-Fi 地址，真实模型目录、四种认证、OpenAI / Gemini 生成与流式、错误处理和端口／密钥切换通过。没有用 USB 转发代替局域网测试，也没有启动电脑后端或 Termux。详细结果、包哈希和复现命令见[真机验收记录](PHYSICAL_DEVICE_TEST.md)。

登录过程中发现并修复 `accounts.youtube.com` 精确主机跳转限制，已有会话恢复导入并通过真实凭证检查；错误 `17` 仍按实际原因处理，不一概忽略。后台默认“智能控制”曾导致进程冻结与唤醒锁被系统禁用，不能把前台通知存在当作后台存活证据。厂商设置路径与后续对照结果见[后台运行说明](BACKGROUND_RUN.md#vivo-真机设置)及真机记录。

| 验收阶段 | 结果与条件 |
| --- | --- |
| 登录修复包 | 凭证恢复导入、在线目录 18 个模型、真实局域网接口、长流中断恢复、端口与密钥切换通过 |
| 默认 vivo 后台策略 | **失败**：CPU 唤醒已开启，但锁屏后的第二次局域网轮询超时；系统报告冻结 |
| 允许后台耗电后 | **通过**：电池优化豁免、CPU 唤醒和 USB 供电条件下，6 分钟内 13 次健康检查与锁屏真实生成成功 |
| 当日最终交付包 | SHA-256 `8c1dbc96…445fe5df`，覆盖安装后 **10 项真实局域网补测全部通过**，安装文件哈希一致 |
| 结束与恢复 | 通知停止及概览停止均验证清理；最终服务停止，端口 `8888`、密钥恢复初始值，局域网和 CPU 唤醒关闭，凭证与系统后台权限保留 |

当日最终包与前一修复包比较了 248 个 ZIP 条目，仅 Git 版本控制信息变化；对应哈希及测试范围见真机记录。后续修改的 APK 必须重新核对，不能仅凭显示版本同为 `0.1.0` 就沿用这次验收。

以下“未进行真机测试”等描述只对应各自历史阶段。单台 4 KB 页设备通过，不代表 16 KB 页、其他厂商、长期 Doze 或实际功耗已验证。

## 首次正式签名构建

- `arm64-v8a` Release APK 构建成功，应用版本 `0.1.0`、版本码 `10`，APK Manifest 确认未开启调试。首次 Release 缺少 Lint 缓存，补齐配置仓库中的依赖后构建成功，随后离线构建也通过。
- 使用仓库外专用发布密钥签名；`apksigner` 验证 v3 签名有效，APK 公有证书 SHA-256 与密钥库导出的证书一致。公开指纹见[维护文档](MAINTENANCE.md)，私钥与密码不提交。
- APK 运行时、Gecko、Python、WAA、许可与已知敏感文件名检查通过；`zipalign -c -P 16 4` 检查通过。段／ZIP 对齐检查不是 16 KB 页真机验收。
- Python 测试 **176 项通过**（2.93 秒，含 24 项发布构建检查），保留 1 项已有 Starlette 弃用警告。签名检查覆盖错误证书、调试证书、多签名、缺失配置、调试标记和重新归档路径；测试中的密钥和工具响应为合成数据。
- 独立 Gradle 检查确认：没有发布密钥时 `preArm64DebugBuild` 成功，`preArm64ReleaseBuild` 明确拒绝继续。Release 不支持打包 instrumentation 测试应用。
- README 的三个群号使用给定群名片链接，本地文档链接与桌面／窄屏排版检查通过。此处验证链接目标，不代表已在各类手机 QQ 中实测跳转。

首次正式签名构建阶段没有执行 ARM64 真机安装、真实 Google 登录／生成或长期后台测试；其后的真机结果见本页开头链接。源码整理阶段的模拟器结果单独记录在下方。构建、签名、公有证书和 SHA-256 清单保存在本机 `artifacts/`；对外发布时，APK 和经审阅的公开清单可作为 GitHub Release 附件，私有诊断资料继续留在本机。

本地记录：`release-python-tests.log` / `.xml`、`release-arm64-verified.log`、`debug-preflight.log`、`release-missing-key.log` 与 `pg-proxy-release-apks.json`。

## 版本码与主页更新

- 应用内部版本码改为 `10`，显示版本保持 `0.1.0`；两种 ABI 均重新离线构建，归档元数据确认 `version_code: 10`。日志：`artifacts/readme-refresh-build.log`。
- 许可资产已同步，项目名称／链接清理与本地文档链接检查通过。
- README 使用本地资源，在 1120 px 桌面和 390 px 窄屏进行 GitHub 风格的本地渲染检查：图片加载、页内导航目标和折叠区展开正常，没有页面横向溢出。此检查不是 GitHub 实际页面截图，线上字体和容器宽度可能不同。记录：`artifacts/readme-preview-check.json`。
- 文档中的应用预览来自既有合成 UI 测试截图。本阶段没有重新运行设备 UI、真实 Google 生成或真机测试。

## 源码整理阶段结果（版本码调整前）

以下保留本地源码整理阶段的原始结果。该阶段主要调整版本、分发说明、许可和文档，未重写上游协议，也未对后续版本码 `10` 重新验收。

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| Python 回归 | **152 项通过**，3.12 秒；1 项已有 Starlette 弃用警告 | HTTP 替身、ASGI 或本机临时监听，不访问 Google |
| ARM64 / x86_64 APK 与 x86_64 测试 APK | 构建通过 | 使用已有本地原生组件与 wheel，离线重新编译应用；不是在空缓存环境重新构建全部依赖 |
| APK 归档检查 | 通过 | Python、pydantic-core、WAA、Gecko、桥接与许可资产、ABI、已知敏感文件名检查 |
| x86_64 模拟器覆盖安装 | 成功 | Pixel_10_Playground_Test，Android 16；0.1.0，源码整理阶段构建（版本码调整前） |
| Android UI 回归 | **21 项通过**，93.303 秒 | 页面、表单、链接回调、深浅色、窄屏大字体、断连与后台设置；使用合成数据 |
| 文档与源码检查 | 通过 | 本地文档链接、版本一致性、许可同步、拟提交文件及自有源码的 Git 空白检查；保留第三方源码／许可文本原有格式 |

测试与构建日志位于开发机器的 Git 忽略目录：`artifacts/source-release-python.log`、`source-release-python.xml`、`source-release-build.log`、`source-release-ui.log` 和 `pg-proxy-apks.json`。这些历史诊断资料未提交到 Git。发布包及公开校验清单与原始日志分别管理；复现时应记录自己设备上的实际结果。

扫描中出现的 WAA 公共客户端 API 标识已与本地保存的上游源码核对一致，不是个人 Google 账户凭证。文件名与格式扫描只能排除部分误提交，不能替代人工审阅或安全审计。

## 复现当前检查

先按[维护文档](MAINTENANCE.md)完成首次完整构建；以下增量命令要求本地已有与当前源码匹配的原生组件及溯源清单、wheel 和完整缓存。缺少清单或原生输入改变时，先去掉 `--skip-native-build` 重建。不同设备应替换序列号与 ABI，当前命令不代表已经执行设备验收。

```powershell
.venv\Scripts\python.exe -m pytest -q --junitxml=artifacts\python-tests.xml
.venv\Scripts\python.exe tools\collect_licenses.py --check
.venv\Scripts\python.exe tools\build_app.py --skip-native-build --tests --test-abi x86_64 --offline
adb -s emulator-5556 install -r artifacts\PG-Proxy-0.3.0-x86_64.apk
adb -s emulator-5556 install -r artifacts\PG-Proxy-0.3.0-x86_64-androidTest.apk
adb -s emulator-5556 shell am instrument -w -e class 'dev.pgproxy.app.UIInstrumentedTest,dev.pgproxy.app.ProfileStorageIoInstrumentedTest,dev.pgproxy.app.ReviewRegressionInstrumentedTest' dev.pgproxy.app.test/androidx.test.runner.AndroidJUnitRunner
```

请检查 instrumentation 输出中的 `OK` / `FAILURES`，不要只看 adb 的进程退出码。不要直接运行整个测试包：`LiveAccountInstrumentedTest` 与 `BrowserImportedAccountInstrumentedTest` 涉及真实账号及生成。更多原生、进程恢复和后台用例命令见[维护文档](MAINTENANCE.md)。

## 开发阶段已有证据与边界

- 早期在 Windows 与独立 Android 应用的 x86_64 模拟器中完成过真实登录、会话自动导入、WAA 更新及生成验证；这属于既有开发证据，**本次 0.1.0 源码整理没有重新执行真实 Google 登录、模型目录或生成验收**。
- 该阶段尚未开展 ARM64 真机验收；其后的 vivo 真机结果单独记录，登录内核、宿主环境和资源管理仍分别列出，避免相互替代。
- Go WAA 的 Android ELF 段对齐、Windows worker 和后台组件在此前开发阶段验收过；本次复用其构建缓存，未把重新归档记为重新构建原生运行时。
- 后台功能已有短时锁屏、唤醒锁释放和系统设置回跳检查，详见[后台运行记录](BACKGROUND_RUN.md)。短时模拟器结果不能证明长期 Doze、厂商后台策略或实际耗电。
- 历史开发日志保留在维护者本机；公开报告通过日期、包哈希和测试范围追溯证据。新的源码提交与 Release 使用各自的构建清单。

## 真实生成的单独验收流程

由用户在应用内完成登录和网站确认，先检查凭证，再启动 API。使用设置中保存的端口，下例按新安装默认值 `8888`：

```powershell
adb -s emulator-5556 forward tcp:18080 tcp:8888
.venv\Scripts\python.exe tools\e2e_app.py --base-url http://127.0.0.1:18080 --connection-file artifacts\private-connection.json --live --output artifacts\http-e2e.json
adb -s emulator-5556 forward --remove tcp:18080
```

私有连接文件仅包含 `apiKey` 和可选的 `model`，放在 Git 忽略目录，不发到聊天或公开报告。工具访问 APK 中运行的服务；即使不加 `--live`，也会查询真实模型目录，因此不是离线测试。`--live` 会消耗生成额度。真实 proof 的服务端接受、流式内容与资源清理应一起记录，不能仅凭 `/health` 成功判断。

## 尚需独立验证

- 两个不同真实 Google 账号的轮询与会话老化。
- 16 KB 页真机与其他 Android 厂商；已测 vivo 使用 4 KB 页。
- 拔除 USB 后的长期后台、强制 Doze 和实际功耗；0.1.0 真机阶段已验证两台设备间的真实局域网 API 访问，该结果不能替代当前安装包验收。
- WAA 12 小时刷新周期、唤醒锁完整续期及其他 Google 登录分支；短时真实生成不等于这些长期机制均已验证。
- 安装 QQ 后真实打开群名片；当前 UI 用例只验证回调。
- 新发布 APK 的安装运行与真实生成，须按该附件的源码提交和哈希单独核对。
- 自动更新和字节级可复现构建目前未提供保证；正式签名与文件校验不能替代设备运行或 Google 服务验收。

源码公开、构建成功、模拟测试通过都不等于真实上游始终可用，也不等于绝对安全。报告应明确设备、版本、时间、命令、结果和未覆盖范围。
