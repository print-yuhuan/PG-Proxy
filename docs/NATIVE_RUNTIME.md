# 内置 Go WAA 工作进程

PG Proxy 将 Goja 与 Firefox 152 WAA 宿主预编译后打包进 APK，手机不需要 Go 编译器、Termux、Node 或外部反代服务。Firefox 152 指工作进程提供的 JavaScript 宿主环境，不是对手机操作系统的描述。

实际挑战程序仍从 WAA 服务获取，程序执行、请求摘要绑定、proof 生成和刷新均在应用内完成。构建成功、进程可运行和 Google 接受生成请求是不同的验证阶段。

## 构建

组件脚本需要 Go 1.27+、Android NDK r28+ 和 Python 3.10+。完整应用的推荐版本为 Go 1.27.3、NDK 28.2.13676358、Python 3.14，详见[构建与维护](MAINTENANCE.md)。依赖源码与许可证已包含在 `runtime/vendor/` 中，构建时关闭 Go 模块网络访问。

在仓库根目录运行：

```powershell
python tools/build_runtime.py `
  --go C:\path\to\go\bin\go.exe `
  --ndk C:\path\to\Android\Sdk\ndk\28.2.13676358 `
  --windows
```

| 选项 | 说明 |
| --- | --- |
| `--windows` | 额外生成 Windows 测试工作进程；只需 Android 产物时省略 |
| `--abi arm64-v8a` | 仅构建 ARM64；也可指定 `x86_64`，该选项允许重复使用 |
| 未指定 ABI | 默认构建 ARM64 与 x86_64，Android API 默认为 28 |

组件脚本包含 Linux、macOS 的 Go 和 NDK 路径适配，但完整应用的 Python wheel 构建流程目前仅支持 Windows，尚不能据此认定完整项目已跨平台验证。

### 生成文件

| 路径 | 内容 |
| --- | --- |
| `app/src/main/jniLibs/arm64-v8a/libpgwaa.so` | ARM64 工作进程 |
| `app/src/main/jniLibs/x86_64/libpgwaa.so` | x86_64 工作进程 |
| `artifacts/runtime/manifest.json` | 编译器版本、ABI、SHA-256 和 ELF 校验结果 |
| `artifacts/runtime/*-elf.txt` | NDK `llvm-readelf` 原始报告 |
| `artifacts/runtime/pgwaa-windows-amd64.exe` | 可选的 Windows 测试工作进程 |

生成文件由 Git 忽略；APK 发布流程见[构建与维护](MAINTENANCE.md)。

## Android 启动与生命周期

`libpgwaa.so` 虽然使用 `.so` 后缀，实际是带入口地址和 `PT_INTERP` 的 **PIE 可执行文件**。Gradle 必须启用原生库安装解压（`jniLibs.useLegacyPackaging = true`），再由 `ProcessBuilder` 执行：

```text
context.applicationInfo.nativeLibraryDir + "/libpgwaa.so"
```

不要使用 `System.loadLibrary`，不要从 `assets` 解压到可写数据目录后执行，也不要通过 shell 拼接启动命令。

Kotlin 管理管道、超时、退出与强制回收；Python 负责 WAA 指令和资源请求。工作进程通过标准输入输出传递 JSON 行，不开放网络监听。凭证、挑战内容和 proof 不得写入日志。

## 管道协议

输入探针：

```json
{"id":1,"op":"ping"}
```

Android x86_64 的响应如下，字段顺序不限；ARM64 的 `goarch` 为 `arm64`：

```json
{"id":1,"result":{"runtime":"goja-firefox","goos":"android","goarch":"amd64"}}
```

`init`、`proof`、`refresh` 复用 `id/result` 封装。执行中发出的 `resource` 消息由 Python 加载允许的真实 Google 资源，再通过 `resource_result` 回传。输入流结束（EOF）会终止进程；初始化前请求 `proof` 返回错误，不产生占位结果。

## 验证范围

2026-10-29 的本地开发记录中，Windows 使用 Go 1.27.3 与 NDK 28.2.13676358 完成两个 ABI 的构建，并验证 Windows 工作进程 `ping` 后正常退出。构建脚本逐项检查：

- ELF64、小端、目标架构正确，类型为 `ET_DYN`，程序入口非零。
- 解释器为 `/system/bin/linker64`，动态标志包含 PIE。
- 全部 `PT_LOAD` 段至少按 16 KB 对齐，地址与文件偏移满足映射要求。
- 动态依赖仅允许 Android 系统的 `liblog.so`、`libdl.so`、`libc.so`、`libm.so`，实际依赖记入报告。

ELF 检查只证明工作进程的文件布局，不能替代整个 APK 的 16 KB 兼容测试；adb shell 下可执行也不能替代普通 APK UID 下的运行验证。

完整验收还应覆盖 APK 内的 `ping → init → proof → refresh`、真实生成、资源回调、超时终止、后台运行与停止后的清理。既往结果及未覆盖项见[验证说明](VALIDATION.md)，新构建应另行记录。

## 来源与许可

宿主移植来源、MIT 许可证及依赖信息见 [runtime/README.md](../runtime/README.md)、[NOTICE](../NOTICE) 和 [AIStudio2API 许可证](../THIRD_PARTY_LICENSE_AIStudio2API.txt)。各依赖保留其自身许可证。
