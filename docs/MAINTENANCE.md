# 构建与维护

本页介绍 PG Proxy **0.3.0** 的构建、测试、签名与发布流程。应用由 Android/Kotlin 界面、GeckoView 登录浏览器、Chaquopy Python 服务和 Goja WAA 工作进程组成；安装后可在手机上独立运行。

源码采用标准 [MIT 许可证](../LICENSE)。维护者可通过 [GitHub Releases](https://github.com/print-yuhuan/PG-Proxy/releases) 提供正式签名安装包，使用者也可自行构建或依适用许可证再分发。APK、签名材料和本机构建产物不提交到 Git；经核验的 APK 与公开校验清单可作为 Release 附件。

模型目录的标准字段、能力扩展、详情、分页与 token 计数约定见[模型与接口说明](MODEL_API.md)；新增能力的真实结果见[模型能力验收记录](MODEL_CAPABILITIES_TEST.md)。构建成功、元数据完整和真实生成成功应分别报告。

快速定位：[环境](#1-环境与版本) · [构建](#2-从源码完整构建) · [签名](#3-安装与签名) · [测试](#4-测试分层) · [发布](#8-正式版本与-github-release)

## 1. 环境与版本

完整源码构建流程目前以 **Windows x64** 为支持主机。Go 工作进程脚本包含 Linux/macOS 的工具路径支持，但 Android Python wheel 脚本目前只实现 Windows 流程，因此不能把单个组件可跨平台构建表述为完整工程已跨平台验证。

| 项目 | 当前配置／开发阶段使用版本 | 配置位置 |
| --- | --- | --- |
| JDK | 建议使用已验证的 JDK 21；Java 源码目标为 17 | `app/build.gradle` |
| Android SDK | compile SDK 37.2；min SDK 28，target SDK 35 | `app/build.gradle` |
| Android NDK | `28.2.13676358`（r28c） | Python wheel 构建脚本固定校验 |
| Python 构建主机 | Python 3.14 | `requirements-lock.txt` |
| Go | `1.27.3`；模块声明要求 1.27 | `runtime/go.mod` |
| Rust | `1.99.1` | `tools/build_python_wheels.py` |
| Gradle | `9.6.0`，使用仓库 Wrapper | `gradle/wrapper/gradle-wrapper.properties` |
| Android Gradle Plugin | `9.4.1` | `build.gradle` |
| Kotlin／Compose 插件 | `2.4.20` | `build.gradle` |
| Chaquopy | `17.2.0`；CPython target `3.14.8-0` | Gradle 与 wheel 构建脚本 |
| GeckoView | `159.0.20261022150525` | `app/build.gradle` |
| Pydantic／pydantic-core | `2.14.1`／`2.50.1` | requirements、Gradle 与 wheel 构建脚本 |
| Android ABI | `arm64-v8a`、`x86_64` | `app/build.gradle` |
| PG Proxy 版本 | `versionName 0.3.0`、`versionCode 30` | `app/build.gradle` |

以上是仓库当前锁定或开发阶段使用的版本，不代表任意更新版本均兼容。Android 设备最低为 Android 9 / API 28，目前没有 32 位 ARM 安装包。

当前显示版本为 `0.3.0`，内部版本码为 `30`；后端 `VERSION` 与 Gecko 桥接扩展版本同步为 `0.3.0`。今后的应用更新应递增内部版本码，并保持正式签名身份一致。

Windows 还需要安装 Visual Studio 或 Build Tools 的 **“使用 C++ 的桌面开发”** 工作负载、Windows SDK，以及 Android SDK Platform Tools。首次使用 Android SDK 前，请通过 Android Studio 或 `sdkmanager --licenses` 自行阅读并接受所需许可。安装 Android SDK 37.2 和指定 NDK，避免让脚本误选另一版 NDK。

## 2. 从源码完整构建

先取得本仓库源码，在仓库根目录执行。以下示例使用 `D:\PG-Proxy`，路径可自行调整。本机凭证、签名材料和导出的浏览器会话不得进入受跟踪源码；本地签名目录的放置和忽略要求见第 3 节。

```powershell
cd D:\PG-Proxy
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -c requirements-lock.txt -r requirements-dev.txt
.venv\Scripts\python.exe tools\build_app.py --build-wheels --bootstrap-rust --tests --test-abi x86_64
```

上述命令会：

1. 保留并收集本地可用的第三方许可。
2. 从 `runtime/vendor/` 离线编译两个 ABI 的 Go WAA 工作进程。
3. 在 `data/python-wheels/` 初始化固定 Rust 工具链，构建两个 ABI 的 `pydantic-core` wheel。
4. 调用 Gradle Wrapper 构建两个 Debug 主 APK 和 x86_64 instrumentation APK。
5. 检查 APK 中的运行时、浏览器桥接、许可证、ABI 及已知敏感文件名，归档 APK 和 SHA-256 清单。

首次构建需要联网访问 Gradle、Google/Mozilla/Maven 仓库、PyPI、Rust 官方下载服务与 crates.io。Go WAA 使用已包含的 vendor 源码，构建时关闭 Go 模块联网下载。完整构建不需要 Google 账户、手机 Cookie 或另行部署的反代服务。

默认从环境变量、标准安装目录和 `local.properties` 发现工具。路径未被识别或安装了多个版本时，明确指定：

```powershell
.venv\Scripts\python.exe tools\build_app.py `
  --java 'C:\BuildTools\jdk-21\bin\java.exe' `
  --sdk 'C:\Android\Sdk' `
  --ndk 'C:\Android\Sdk\ndk\28.2.13676358' `
  --go 'C:\BuildTools\go\bin\go.exe' `
  --build-wheels --bootstrap-rust --tests --test-abi x86_64
```

请将示例路径替换为实际安装位置。可用 `--cargo-home`、`--rustup-home` 或 `CARGO_HOME`、`RUSTUP_HOME` 复用现有 Rust 工具链；工具链版本与 Android target 仍需匹配。完整参数以各脚本的 `--help` 为准。

只构建手机 ARM64：添加 `--abi arm64-v8a` 并省略 `--tests --test-abi x86_64`。只构建模拟器：添加 `--abi x86_64`。`--test-abi` 必须包含在所选主 APK 架构内。

已有原生组件、wheel 和构建缓存时，可执行增量构建：

```powershell
.venv\Scripts\python.exe tools\build_app.py --skip-native-build --tests --test-abi x86_64
```

缓存齐全时才能增加 `--offline`。离线模式不会下载缺失的 Gradle 发行包，也不能同时使用 `--build-wheels` 或 `--bootstrap-rust`。`--skip-native-build` 只跳过 Go WAA 重建，并不生成缺失的 wheel；它还要求原生缓存清单与当前源码、构建脚本、ABI、构建配置和 worker 字节校验一致。缺失溯源清单、旧格式缓存或源码变化时必须重新构建 WAA，不能继续跳过。

`--package-only` 只检查并归档已经编译好的 APK，不会把新改动编译进去；归档同样校验原生来源及 APK 内 worker 字节。Go worker 已通过 `-s -w` 移除调试信息，Gradle 对 `libpgwaa.so` 使用 `keepDebugSymbols` 防止二次 strip 改写文件，确保 APK 内字节与原生构建清单一致。

默认 Debug 输出位于 Git 忽略的 `artifacts/`；正式签名构建见下一节：

```text
PG-Proxy-0.3.0-arm64-v8a.apk
PG-Proxy-0.3.0-x86_64.apk
PG-Proxy-0.3.0-x86_64-androidTest.apk   # 使用 --tests 时
pg-proxy-apks.json                   # 版本、ABI、源码状态、SHA-256 等
runtime/manifest.json
wheels/build-report.json
```

更换上游运行时、NDK、Python ABI 或依赖版本后，应重新构建对应组件，不能沿用旧文件后把成功归档当作新代码编译成功。

## 3. 安装与签名

```powershell
adb devices -l
adb -s emulator-5556 install -r artifacts\PG-Proxy-0.3.0-x86_64.apk
adb -s emulator-5556 shell am start -n dev.pgproxy.app/.MainActivity
```

设备序列号以 `adb devices -l` 的结果为准。ARM64 真机使用 ARM64 主 APK，不安装 `androidTest.apk` 来日常使用。

`tools/build_app.py` 支持 **Debug** 和 **Release** 两种构建。默认 `--build-type debug` 用于开发和 instrumentation；正式发布使用 `--build-type release`。Release 不回退到调试签名，不接受 `--tests` 或 `--test-abi`，缺少签名配置时直接失败。

### 正式签名准备

签名密钥库可以放在仓库外受控目录，也可以放在本项目的 **`signing/` 本地目录**。使用仓库内目录时，必须同时满足 Git 忽略和未受跟踪两个条件；密钥库、私有 `signing.json` 及整个目录均不能提交或上传。通过以下环境变量为构建脚本提供配置，密码不能写进源码、命令历史、日志或公开发布材料。

| 环境变量 | 内容 |
| --- | --- |
| `PG_SIGNING_STORE_FILE` | 仓库外受控目录，或已忽略且未受跟踪的 `signing/` 中的密钥库绝对路径 |
| `PG_SIGNING_STORE_PASSWORD` | 密钥库密码 |
| `PG_SIGNING_KEY_ALIAS` | 发布密钥别名 |
| `PG_SIGNING_KEY_PASSWORD` | 发布密钥密码 |

本项目本地签名路径约定为 `D:\PG-Proxy\signing\pg-proxy-release.p12`，私有配置为同目录 `signing.json`。后者仅用于本机受控加载，不是要随源码创建或分享的示例文件；`tools/build_app.py` 仍通过上表环境变量读取签名配置。

已有有效正式签名时继续使用原密钥，不要每次发布都新建。0.3.0 沿用 0.2.0 的正式签名；0.1.0 的签名材料已不可用，0.2.0 曾因此更换签名，这一历史变更仍影响旧 0.1.0 包升级，详见下文。首次自行生成密钥，可用 JDK 的 `keytool` 交互输入密码，例如：

```powershell
keytool -genkeypair -keystore 'D:\PG-Proxy\signing\pg-proxy-release.p12' `
  -storetype PKCS12 -alias pgproxy-release -keyalg RSA -keysize 3072 `
  -validity 10000 -dname 'CN=PG Proxy'
```

这是本机路径和身份的示例，不是维护者私钥的下载位置。先创建签名目录并设置合适的访问权限；若密钥库已存在，不要覆盖它。密钥库及密码应妥善保管并单独留存恢复副本，它们决定今后能否为同一应用提供签名一致的更新。Git 忽略规则不等于文件访问权限，也不能保护手工上传的归档。

使用仓库内 `signing/` 时，在构建或提交前检查：

```powershell
git check-ignore -- signing/pg-proxy-release.p12 signing/signing.json
git ls-files -- signing/
```

第一条应列出被忽略的路径，第二条应无输出。若任何签名材料已被跟踪，先停止发布并处理，不使用 `git add -f` 绕过忽略规则。不要在诊断输出中打印 `signing.json` 或密码。

密码由本机密码管理器或受保护的构建环境临时注入；也可在 PowerShell 中交互读取，避免将密码字面量写进历史：

```powershell
function Read-SigningPassword([string]$Prompt) {
  $secureValue = Read-Host $Prompt -AsSecureString
  $passwordPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
  try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPtr) }
  finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPtr)
    $secureValue.Dispose()
  }
}

$env:PG_SIGNING_STORE_FILE = 'D:\PG-Proxy\signing\pg-proxy-release.p12'
$env:PG_SIGNING_KEY_ALIAS = 'pgproxy-release'
try {
  $env:PG_SIGNING_STORE_PASSWORD = Read-SigningPassword '密钥库密码'
  $env:PG_SIGNING_KEY_PASSWORD = Read-SigningPassword '发布密钥密码'
  .venv\Scripts\python.exe tools\build_app.py --build-type release --abi arm64-v8a --skip-native-build
  if ($LASTEXITCODE -ne 0) { throw 'Release 构建未完成' }
}
finally {
  Remove-Item Env:PG_SIGNING_STORE_PASSWORD, Env:PG_SIGNING_KEY_PASSWORD -ErrorAction SilentlyContinue
  Remove-Item Env:PG_SIGNING_STORE_FILE, Env:PG_SIGNING_KEY_ALIAS -ErrorAction SilentlyContinue
}
```

环境变量会在本次构建进程中成为明文，应仅在可信本机运行，完成后清理。上述命令适用于已有 Go 原生组件与 Python wheel 的环境；首次完整构建将 `--skip-native-build` 换为 `--build-wheels --bootstrap-rust`，缓存齐全时才可增加 `--offline`。

ARM64 Release 输出为 `artifacts/PG-Proxy-0.3.0-arm64-v8a-release.apk` 和 `artifacts/pg-proxy-release-apks.json`。归档检查包括 APK 签名、证书与配置密钥库是否匹配，以及应用是否关闭调试标记；正式签名不等同于 Google、应用商店或第三方安全认证。使用 `--build-type release --package-only` 时仍需 Android SDK、JDK 和上述四个变量，仅归档已有包，不重新编译。

### 安装与后续更新

**0.3.0 继续使用 0.2.0 的正式签名，版本码递增至 30，可覆盖同签名的 0.2.0 正式包。** 常规覆盖更新保留应用私有配置、凭证与浏览器会话；不要为正常升级主动卸载或清除数据。Google 会话本身仍可能过期。

**历史 0.1.0 正式包不能直接覆盖到 0.2.0 或后续版本。** 0.1.0 签名材料已不可用，0.2.0 曾更换签名。从旧 0.1.0 升级时，先记录端口、访问范围等必要设置，再卸载旧版、安装当前版并重新登录添加凭证。卸载会删除应用私有配置、凭证和浏览器会话；清除数据同样会删除这些资料，且仅清数据不能解决签名冲突。

0.2.0 起的正式版本应持续保持包名和签名身份一致。不同电脑的 Debug 签名、自行生成的签名与维护者发布签名可能不同，覆盖安装前仍需核对。

项目没有内置自动更新。不要提交 `signing/`、`signing.json`、`*.jks`、`*.keystore`、`*.p12`、签名密码或包含这些材料的归档；公开材料仅提供签名证书指纹。

**0.2.0 起沿用、0.3.0 继续使用的正式签名**公有证书 SHA-256 为：

```text
e7c43a66f990d01ed2bdb9d967db5b1df905e3527c31fcabac37f46f6017b39d
```

**0.1.0 旧正式签名**的公有证书 SHA-256 为（仅供识别旧包，不是 0.2.0 及后续版本的签名）：

```text
1ffe41e58b792b58bb30c0d8349e0d6c06c3419e2379e149bb1c56ec75d972cf
```

可用 `apksigner verify --print-certs <APK路径>` 核对证书指纹。每次 Release 仍须公布该附件的实际指纹；自行构建使用自己的密钥，签名可以不同。指纹用于识别签名者，不能替代代码审阅或安全审计。私钥和密码不公开。

## 4. 测试分层

每次报告应注明源码版本、设备 ABI、Android 版本、执行命令、通过／失败数以及未验证范围。[验证记录](VALIDATION.md) 和 [后台运行记录](BACKGROUND_RUN.md) 是本地开发阶段的已有证据，不代表读者此次构建或当前 Google 服务状态。

### Python 回归

```powershell
.venv\Scripts\python.exe -m pytest -q --junitxml=artifacts\python-tests.xml
```

测试使用替身、ASGI 或本机临时监听，覆盖协议解析、认证、调度与生命周期，不证明 Google 接受 WAA proof。

### APK 与模拟器

先构建主 APK 和匹配架构的测试 APK，再按需执行指定测试类：

```powershell
adb -s emulator-5556 install -r artifacts\PG-Proxy-0.3.0-x86_64-androidTest.apk
adb -s emulator-5556 shell am instrument -w -e class 'dev.pgproxy.app.UIInstrumentedTest,dev.pgproxy.app.MotionInstrumentedTest,dev.pgproxy.app.RuntimeInstrumentedTest,dev.pgproxy.app.GeckoProfilesTest,dev.pgproxy.app.ProfileStorageIoInstrumentedTest,dev.pgproxy.app.ReviewRegressionInstrumentedTest,dev.pgproxy.app.DeletionRecoveryInstrumentedTest,dev.pgproxy.app.ProxyConnectorInstrumentedTest,dev.pgproxy.app.ForegroundStateInstrumentedTest,dev.pgproxy.app.WakeLockInstrumentedTest,dev.pgproxy.app.BackgroundSettingsInstrumentedTest,dev.pgproxy.app.BackgroundRuntimeInstrumentedTest' dev.pgproxy.app.test/androidx.test.runner.AndroidJUnitRunner
```

UI 用例使用合成状态。部分原生测试会打开应用、临时启停服务、短时熄屏或访问本机健康接口；`ReviewRegressionInstrumentedTest` 还检查已安装 APK 的版本、入口拒绝状态和原有凭证／配置保留，不向 Google 发起生成。打开主界面可能触发公网 IP 查询，因此不要笼统称整组设备用例为“完全离线”，也不要在用户正在生成时运行它们。

不要直接执行整个 instrumentation 包：`LiveAccountInstrumentedTest` 和 `BrowserImportedAccountInstrumentedTest` 涉及真实账户生成。构建参数 `--tests` 只生成测试 APK，并不执行设备测试。

### 真实账户与端到端接口

用户在应用内完成登录及 AI Studio 确认，检查凭证，并主动启动服务。测试前确认账户、网络地区和额度允许执行生成。使用配置中的真实端口；以下按默认 `8888` 举例：

```powershell
adb -s emulator-5556 forward tcp:18080 tcp:8888
.venv\Scripts\python.exe tools\e2e_app.py `
  --base-url http://127.0.0.1:18080 `
  --connection-file artifacts\private-connection.json `
  --live --output artifacts\http-e2e.json
adb -s emulator-5556 forward --remove tcp:18080
```

私有连接 JSON 仅填写 `apiKey` 和可选的 `model`，不要在命令行、截图、Issue 或公开日志中放真实密钥。`e2e_app.py` 只访问已经运行的 APK 服务，不启动桌面替代后端；即使省略 `--live`，仍会查询真实模型目录，不是纯离线测试。`--live` 会执行真实生成并消耗账户额度。

单账户成功不能证明多账户轮询；ELF 段对齐检查不能证明 16 KB 页真机兼容；x86_64 模拟器成功不能替代 ARM64 真机、不同厂商后台管理、局域网跨设备和长时间锁屏验证。这些场景需单独记录。

## 5. 目录与修改入口

| 目录／文件 | 职责与维护重点 |
| --- | --- |
| `app/src/main/java/` | Compose 页面、Gecko 登录、Keystore、前台服务、私有 Binder、WAA 子进程管理 |
| `app/src/main/assets/gecko-bridge/` | 登录页面就绪与 Cookie 会话桥接；不得扩大为无关站点的凭证读取 |
| `backend/pgproxy/` | Playground 编解码、认证、账户轮询、HTTP 流式输出与资源清理 |
| `backend/pgproxy/request_guard.py` | 正文读取前认证、64 MiB 正文上限与完整上传时限；拒绝后关闭未读完正文的 HTTP/1 连接 |
| `backend/pgproxy/openai_compat.py`、`gemini_compat.py` | 客户端可选参数清理、缺省值与按模型规范化；保留必要消息和历史校验，不做生成重试 |
| `backend/pgproxy/model_catalog.py` | 标准字段、能力与模态资料、不同凭证的保守合并、模型详情与分页 |
| `backend/pgproxy/token_protocol.py` | Playground `CountTokens` 请求和返回；不得改成本地估算或隐式生成 |
| `runtime/` | Goja WAA 宿主和 vendor；协议与浏览器画像变化需真实验证 |
| `tools/` | 构建、许可采集、APK 检查和端到端测试工具 |
| `tests/`、`app/src/androidTest/` | Python 回归与 Android instrumentation |
| `app/src/main/assets/third_party/` | 随源码和 APK 保存的第三方许可、来源信息 |

WAA 必须实际执行上游挑战程序，生成与请求绑定的 proof。遇到失效应定位初始化、宿主、资源加载或请求绑定差异，不使用固定／伪造 proof，不通过隐藏错误标记“已验证”。

### 请求调度与排队约定

`AccountPool` 使用一个全局 `asyncio.Lock`。生成请求先通过 `queued()` 等待获得锁，再由 `_select()` 从当前符合条件且支持目标模型的凭证中轮询选择；锁一直保持到生成流结束或取消清理完成。空闲账号不会绕过该锁并行生成。模型目录、凭证检查及导入等操作也使用同一锁。

`queueTimeout` 默认 30 秒，限制等待获得锁的时间；锁提前释放就立即继续，不等待凑满该时长。等待超时返回 `429 / account_busy` 和 `Retry-After: 5`，不会选择账号、推进轮询位置或自动重试。A、B 状态稳定且都支持模型时，A 结束后的下一次生成使用 B；选取时会重新考虑停用、登录错误、校验错误、删除及冷却状态。上游已经收到的生成不会自动跨账号重放。

当前策略保留每个账号的 HTTP／Cookie 状态，并在切换账号时释放其他账号的 WAA worker，最多保留一个常驻 WAA VM。若以后实现多账号并发，必须同时重新设计 worker 生命周期、在途删除、取消、停止清理及资源上限，不能只移除全局锁。

`tests/test_accounts.py` 覆盖 A/B 轮询、忙时等待、正常结束或中断后继续使用 B、排队超时不转发且不推进轮询，以及上游错误不自动重放。这些测试使用内存模拟账号和客户端；真实双账号轮询仍需独立验证，不能由单账号真机结果推断。

### 0.3.0 资源与生命周期约定

- 公开 API 在读取正文前认证，完整 JSON 最多 64 MiB，须在 60 秒内接收完成。超过大小返回 `413`、上传超时返回 `408`；不要把它们与上游每次读写超时混淆。Uvicorn 连接／任务并发上限为 16，达到上限时返回自身的纯文本 `503`；该上限不改变全局串行业务调度。
- 响应 Schema 与函数参数 Schema 在同次规范化中共享 4096 次节点访问、256 KiB 累计编码体积预算，字段名称、描述、枚举和示例都计入。超限整体放弃当前可选 Schema，不能输出半展开约束；已支持的正文、参数和单次生成语义应保留。
- Playground 页面初始化失败或被取消时清除部分状态；下一次独立请求从页面开始完整初始化。不在当前请求内自动重试生成。
- Android 登录资料扫描、元数据写入和文件清理经串行 IO 调度执行，避免阻塞主线程；已有导入标记避免重复持久化，页面取消不能导致文件操作交叉执行。
- WAA 定时器在同一锁内赋值并登记，回调在锁内删除记录，关闭时停止并清空定时器；需用短延迟与关闭竞态回归验证，不能只测试秒级延迟。
- 原生缓存清单记录源码与构建输入摘要、ABI、工具链和 worker 校验；复用和归档时不匹配即失败，源码发生变化应重建，不能手工修改清单使旧二进制通过。

用户侧限制和错误说明见[客户端兼容](CLIENT_COMPATIBILITY.md#请求入口与资源限制)。本轮回归入口包括 `test_request_guard.py`、`test_review_protocol_regressions.py`、`test_native_build_cache.py`、Go `runtime/waa/loop_test.go` 及 Android 资料 IO 测试；具体执行结果与未覆盖项按[验证记录](VALIDATION.md)分别保存。

### 模型目录与续写协议

`ListModels` 是在线目录，`model_ttl` 当前为 300 秒。新增模型先核对生成方法与能力字段，不通过模型名白名单补齐。能力 `91` 表示 `GenerateContent` 输出续写，不能与视频／Live／音乐路由一起排除；`row[78]` 的 Interactions 配置仍属于未实现通道，`row[77]` 的 Paid 标签不是过滤条件。

目录字段合同见[模型与接口说明](MODEL_API.md)。维护时分别核对 OpenAI 与 Gemini 的列表／详情、原生分页和 `countTokens`，不要将上游原始方法直接填入代理的 `supportedGenerationMethods`。`createCachedContent`／`batchGenerateContent` 目前只作为上游元数据保留：公开 API 的方法声明不能证明 Cookie Playground 的认证、任务生命周期和清理逻辑已实现；添加支持前须完成独立协议与端到端验收。

`pg_proxy` 扩展按 `schema_version` 维护，上游资料与代理能力分开。新增输入模态资料必须保留来源、核对日期及准确模型范围；统一上下文未知时保持 `null`，不要把输入和输出上限相加。多账号同 ID 的公开能力使用交集和较小限额，不能用某个账号的更大能力覆盖其他账号的约束。`supported_parameters` 仅列出适配器至少能接受一种有效取值的参数；思考能力不自动等于支持 `reasoning_effort`。

客户端兼容回归要检查实际消费者：本次参考的 RikkaHub 仍按模型 ID 和本地注册表提供能力标签，不自动读取全部扩展；OpenAI 路径还会丢失 Google 签名扩展，原生 Google 路径具有对应保留逻辑。不要把服务端字段测试写成客户端安装包实测。参考提交和 AGPL 边界见[来源说明](SOURCES.md)，消费者合同回归见 `tests/test_rikka_catalog_contract.py`。

`tests/test_model_catalog.py` 覆盖目录字段、分页、能力来源和保守合并；计数接口还需覆盖包装互斥、模型匹配、结构化输入、上游响应错误，以及“不推进生成轮询、不标记生成通过”的约定。`countTokens` 共享全局操作队列。当前概览统计包含模型列表、模型详情、计数和生成请求的成功／失败结果，排除健康检查、内部路径与 OPTIONS；统计回归见 `tests/test_request_statistics.py`。

新增功能的实际安装包、账号、接口与未验证项记录到[模型能力验收](MODEL_CAPABILITIES_TEST.md)。历史报告保留原包哈希和测试范围，后续构建另行记录。

续写模型的请求将 `generationConfig[45]` 置为 `1`，根字段 `[10]` 留空；每轮累计输出上限按 32768 增加，不超过用户总上限。候选 `[8]` 的续写 token 仅在内部保存，后续请求放入根字段 `[16]`，不得写入日志或公共响应。结束码 `22`、带 token 的分轮 `MAX_TOKENS`、缺失／重复 token、最后一轮预算和流关闭必须配合处理。每轮生成新的 WAA proof，跨轮保留同一账户锁，客户端断开后不继续发起下一轮。

`tests/test_continuation.py`、`tests/test_continuation_api.py` 和协议测试分别验证多轮网络生命周期、公开响应形状与字段编解码。需要覆盖中间结束标记隐藏、用量累计、第二轮失败不输出成功 `[DONE]`、取消清理和普通模型兼容。模拟 32768 分轮不代表真实生成过相应长度；短请求的上游验收与长文本续写应分别记录。

原生用量按上游字段保留工具 token（`toolUsePromptTokenCount`），续写累计输出、思考和工具计数，输入与缓存采用最新轮。OpenAI 兼容结果的 `total_tokens` 包含工具部分，但当前没有单独映射工具计数字段；工具参与时不能假定 `prompt_tokens + completion_tokens` 一定等于总量。输出计数仅在上游缺失时推导，不覆盖显式的 `0`。

## 6. 依赖与许可维护

升级前先核对根目录 [LICENSE](../LICENSE)、[NOTICE](../NOTICE)、第三方原文以及组件自身的再分发要求；项目许可证不会覆盖第三方原有授权。

1. Python 升级同步 `requirements.txt`、`requirements-lock.txt` 和 `app/build.gradle`。涉及 `pydantic-core` 或 Python ABI 时，还需更新 wheel 构建脚本的版本、源码 URL、SHA-256、Chaquopy target 哈希及文档。
2. GeckoView 升级同步 Gradle 坐标和 `tools/collect_licenses.py` 中的版本、修订号与 AAR 哈希，重新采集该版本的许可及对应源代码入口。不要沿用旧引擎的来源声明。
3. Go 模块升级同步 `go.mod`、`go.sum`、vendor 源码与许可；先审查变更，再离线构建，不手工拼接与模块版本不匹配的 vendor。
4. 许可采集工具仅访问本地源码和缓存；缺少工具链缓存时会保留已提交的声明。不要把“文件存在检查通过”当作新依赖已经完成许可审查。

```powershell
.venv\Scripts\python.exe tools\collect_licenses.py
.venv\Scripts\python.exe tools\collect_licenses.py --check
git diff -- app/src/main/assets/third_party
```

首次添加依赖时，核对其许可文本、版权声明、修改说明和源码提供要求。GitHub Release、群聊或其他渠道的二进制分发都应保留适用的第三方声明。应用内“开源许可”、随包资产和 Release 中的来源说明须与实际构建一致。

## 7. 常见构建与维护问题

| 现象 | 排查顺序 |
| --- | --- |
| 找不到 Java／SDK／NDK／Go | 使用 `--java`、`--sdk`、`--ndk`、`--go` 指向真实路径；检查 SDK 许可与指定 NDK 安装是否完整 |
| pydantic-core wheel 缺失 | 首次使用 `--build-wheels --bootstrap-rust`；检查 VS C++ 工作负载、固定 Rust 版本和 Android target |
| Android NDK 版本被拒绝 | wheel 脚本固定要求 `28.2.13676358`；不要用较新版本名称绕过校验 |
| 离线构建缺少 Gradle 或依赖 | 联网完整构建后再离线；不要删除 Wrapper 校验或改为跳过运行时检查 |
| 原生缓存来源／摘要不匹配 | 去掉 `--skip-native-build` 重建相应 ABI；不要沿用旧格式清单或手工改摘要，归档也会校验 APK 内 worker |
| 下载哈希不匹配 | 停止使用该文件，核对官方来源和锁定版本；不要把未知下载文件的哈希直接写回脚本 |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | 比较签名证书；不要在不知情时清除用户数据 |
| Release 提示缺少签名 | 检查四个 `PG_SIGNING_*` 环境变量、密钥库路径与别名；仓库内仅使用已忽略且未受跟踪的 `signing/`，不使用调试签名代替 |
| APK 可安装但服务启动失败 | 检查对应 ABI、pydantic-core 导入和 WAA worker 探针；保留脱敏错误码，勿回退到假服务 |
| 登录失败或生成 403 | 按 README 的网络地区、账户资格和会话排查步骤处理；不能仅凭一个状态码断定是 WAA 问题 |
| 后台服务停止 | 查看通知、电池优化与厂商后台管理；按 [后台运行说明](BACKGROUND_RUN.md) 区分短时检查和长期运行证据 |

提交问题前保留设备型号、Android 版本、应用版本、源码提交、复现步骤及脱敏错误码。不要上传 Cookie、Authorization、完整请求导出、Google 密码、验证码、WAA proof 或可识别个人的日志；公网 IP 截图也应按需遮挡。

### 通知图标与厂商缓存诊断

标准小图标通过 [`Notification.Builder.setSmallIcon`](https://developer.android.com/reference/android/app/Notification.Builder#setSmallIcon(int)) 提供，系统用它表示状态栏通知；`setLargeIcon` 提供通知内容区域的大图标。排查时同时核对桌面图标、通知小图标、实际安装 APK 与通知所引用的资源，不根据图标外观直接推断安装包版本。

本轮 vivo V2505A / Android 17 曾在安装包已包含烧瓶＋镂空星图案时继续显示旧 P。已观察到该系统使用主题应用图标缓存；仅重新发布通知或改变小图标的传递形式未消除旧显示。维护者随后通过 ADB 触发 SystemUI 内存缓存回收，再启动 PG Proxy 服务，状态栏和常驻通知均恢复新版图案。

以下是**这台设备上已执行成功的诊断步骤**，不是应用运行依赖，也不保证其他系统支持或有效：

1. 确认 APK 身份与图标资源正确，记录脱敏后的异常图标；从应用概览停止 API 服务。
2. 在用户授权的 USB 调试设备上，先检查 `adb -s DEVICE_SERIAL shell am help` 是否提供 `send-trim-memory`，将占位符换成目标设备序列号后执行一次：

   ```text
   adb -s DEVICE_SERIAL shell am send-trim-memory --user 0 com.android.systemui RUNNING_CRITICAL
   ```

3. 从应用重新启动服务，分别检查状态栏和展开通知；最后恢复测试前的服务状态。命令返回成功仍须以画面确认刷新结果。

该操作向整个 SystemUI 发送内存回收通知，并非只针对 PG Proxy 的缓存；本次没有使用 Root、`pm clear`、卸载应用或终止 SystemUI，Google 凭证保持不变。权限不允许时停止该诊断，不尝试提权；普通用户可先停止／重启服务，再在合适时尝试重启手机，后者未在本轮单独验证。

应用内不应加入清理其他系统进程缓存、伪造系统广播或未经验证的厂商通知 extras／Manifest 元信息。保持标准通知 API 和正确图标资源，系统缓存问题按设备记录，不把单一厂商的诊断命令包装为通用修复。不得将分析时取得的系统 APK、反编译代码或用户通知内容提交到源码仓库。

## 8. 正式版本与 GitHub Release

### 冻结源码与测试范围

1. 更新应用、后端和文档中的版本信息；后续更新递增 Android `versionCode`。依赖版本按兼容性单独维护。
2. 执行相关回归、许可采集与检查，完成源码和文档审阅。记录测试命令、设备、包哈希、结果与未验证范围。
3. 检查 `git status --short`、`git diff --check` 和拟提交文件。`data/`、`artifacts/`、生成的 `jniLibs/`、APK、凭证及签名材料不得误入 Git。
4. 提交最终源码，记录完整提交 SHA。正式包从该提交的干净工作区构建；构建后若产生新的受跟踪文件改动，先处理并提交，再重新构建。

已有的 ARM64 真机、x86_64 模拟器和离线测试报告分别保留。新 ARM64 Release 包完成构建与签名检查，仍应如实写明是否安装运行；不能借用旧包或另一 ABI 的结果宣称已通过真机验收。

### 从最终提交构建 ARM64 Release

按第 3 节加载受控本地目录中的正式签名配置，确认密钥和配置均未受 Git 跟踪，再执行完整构建：

```powershell
git status --short
git rev-parse HEAD
.venv\Scripts\python.exe tools\build_app.py --build-type release --abi arm64-v8a --build-wheels --bootstrap-rust
if ($LASTEXITCODE -ne 0) { throw '正式构建失败，停止发布' }
git status --short
Get-FileHash artifacts\PG-Proxy-0.3.0-arm64-v8a-release.apk -Algorithm SHA256
```

已有与该源码及锁定依赖匹配的 Go 组件、Python wheel 和完整缓存时，可使用 `--skip-native-build --offline` 替换依赖构建参数。依赖或原生源码发生变化时，应重新构建相应组件。

本版本输出 `PG-Proxy-0.3.0-arm64-v8a-release.apk` 与 `pg-proxy-release-apks.json`。核对清单中的 `build_invoked`、`workspace_revision_at_packaging`、`workspace_dirty_at_packaging`、`native_source_sha256`、ABI、版本、APK SHA-256、证书指纹与调试状态；确认 `waa_source_provenance_verified`，同时核对 APK 内的版本控制信息。`--package-only` 仅检查和归档已有包，不能代替从最终提交重新编译。

以上流程便于复现构建步骤和追溯源码，不承诺字节级可复现。工具链、构建元数据和签名可能影响 APK 哈希；不同签名的自行构建包也不应与维护者附件直接比较整包哈希。

### 准备公开附件与发布说明

先创建指向最终源码提交的版本标签与草稿 Release，逐项核对后再正式发布。标签、Release 与 APK 对应同一源码提交，不以同名版本号代替核对。

| 发布材料 | 应包含的信息 |
| --- | --- |
| 正式 ARM64 APK | 文件名、应用版本、版本码、`arm64-v8a`、最低 Android 版本、Release 签名状态 |
| SHA-256 清单 | 每个公开附件的文件名和实际 SHA-256 |
| 公开构建记录 | 完整源码提交 SHA、工具链版本、实际构建命令、APK 哈希、签名证书 SHA-256 |
| 发布说明 | 本次改动、安装或升级说明、已执行测试、未验证环境、已知限制 |
| 许可与来源 | MIT、NOTICE、第三方完整许可证与对应源码入口；链接必须对应本次构建的源码版本 |

APK 可作为 GitHub Release 附件，仍保留在 `.gitignore` 范围内。公开构建记录应单独审阅，移除本机敏感路径和私有连接信息；不要整体上传 `artifacts/`。Cookie、Authorization、WAA proof、思考签名、签名密钥和密码不得出现在公开附件中。

使用 `Get-FileHash <APK路径> -Algorithm SHA256` 与 `apksigner verify --print-certs <APK路径>` 复核文件和签名。完成上传后，重新核对 Release 的目标提交、附件名称、大小和公开校验信息；下载回验也应遵守下述限速约定。通过文件校验表示内容一致，不等于独立安全审计。

### GitHub 联网限速

所有 GitHub 联网操作由一个执行方串行安排。**每次相邻请求之间显式执行 `Start-Sleep -Seconds 10` 或等效等待，至少间隔 10 秒**；API、Git 下载／推送、分页、附件上传和重试均适用。

- 创建草稿、上传每个附件、读取校验信息、发布草稿分别安排请求和等待。
- 使用单次 API 请求接口。避免一个命令自动分页、批量上传或后台并发，导致内部请求无法按要求等待。
- 遇到 `Retry-After`、限流或风控响应时停止连续重试，遵循服务端要求并增加退避。
- 纯本地 `git status`、`diff`、`add`、`commit` 和标签创建不需要联网等待；推送标签属于联网操作。

此约定用于控制请求频率，不保证能够避免平台限流或风控。
