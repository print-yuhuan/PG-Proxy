# WAA JavaScript 运行时

本目录提供 PG Proxy 随包运行的 WAA 工作进程。它在 Goja 中执行 Google 实际下发的解释器与挑战程序，并提供参考实现中的 Firefox 浏览器环境。每次 proof 均由挑战程序计算，不使用固定或缓存的 proof 替代校验。

工作进程没有 HTTP 监听，也不承担账号、模型目录、接口路由或生成业务：

- **Python** 管理会话、签名、页面初始化、协议编解码、网络请求、流式响应与错误。
- **Android** 启停工作进程，通过标准输入／输出传输 JSON Lines。
- **WAA 运行时** 执行 JavaScript；需要外部资源时，向 Python 发出资源请求，再接收受允许的 Google 资源内容。

它是应用内部组件，不需要部署或连接 AIStudio2API 服务。

## 构建

在仓库根目录执行，准备 Go 1.27+ 与 Android NDK r28+：

```bash
python tools/build_runtime.py --go /path/to/go --ndk /path/to/android-ndk --windows
```

只构建 Android 时省略 `--windows`。构建脚本使用随仓库保存的 `vendor/` 依赖，不在线查找 Go 模块：

```text
GOPROXY=off
GOSUMDB=off
GOTOOLCHAIN=local
go build -mod=vendor -trimpath -buildvcs=false
```

Android 构建启用 CGO，通过 NDK 进行外部 PIE 链接；可选 Windows 测试程序使用 `CGO_ENABLED=0`。Android 产物写入 Git 忽略目录 `app/src/main/jniLibs/`，ELF 检查报告、哈希及 Windows 测试程序写入 `artifacts/runtime/`。

## 在 Android 中运行

`libpgwaa.so` 是 **PIE 可执行文件**，不是 JNI 库。由 Android 原生库打包机制安装后，应用通过 `ProcessBuilder` 从 `nativeLibraryDir` 启动它；不要使用 `System.loadLibrary`，也不要将其解压到可写应用数据目录再执行。

构建成功与 `ping` 成功只说明对应环节可用。普通 APK UID 下的运行、进程清理以及上游是否接受 proof，需要分别验证。详见[原生运行时说明](../docs/NATIVE_RUNTIME.md)与[验证记录](../docs/VALIDATION.md)。

## 来源与环境

`waa/` 改编自 AIStudio2API 提交 `7cb5cc04676751b588146052475deec147fee35e` 的 `internal/waa`，采用 MIT；`main.go` 为本项目的管道通信与生命周期适配。版权及许可见根目录 [NOTICE](../NOTICE) 和 [AIStudio2API 许可证](../THIRD_PARTY_LICENSE_AIStudio2API.txt)。引擎依赖由 `go.mod`、`go.sum` 与 `vendor/modules.txt` 记录，第三方原始许可保持不变。

WAA 宿主模拟的是 **Windows 上的 Firefox 152**，包括浏览器接口形状和 Intl 兼容数据。这是 JavaScript 执行环境配置，不代表手机操作系统，也与用于用户登录的 GeckoView 版本分别维护；这些兼容数据不是 proof。
