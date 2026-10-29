# Android Python 运行时与原生依赖

PG Proxy 通过 Chaquopy 17.2 在 APK 服务进程中运行 CPython 3.14.8，FastAPI、Uvicorn 和 HTTPX 业务代码随应用打包。手机无需安装 pip、Rust、Go 编译器或 Termux。完整工程的构建与测试入口见[构建与维护](MAINTENANCE.md)。

## 固定版本

| 组件 | 构建版本 |
| --- | --- |
| Chaquopy CPython target | `3.14.8-0` |
| Python ABI | `cp314`，常规 GIL 构建 |
| Pydantic | `2.14.1` |
| pydantic-core | `2.50.1` |
| Rust | `1.99.1` |
| Android NDK | `28.2.13676358` / r28c |
| 原生库最低 API | 28 |
| 目标架构 | `arm64-v8a`、`x86_64` |

项目从 PyPI 源码包交叉编译 `pydantic-core`，显式链接 Chaquopy 对应架构的 `libpython3.14.so`。此流程不依赖包索引提供 Android wheel，也不使用 Termux 的 Python、wheel、头文件或动态库。

### 下载校验

`pydantic-core` 源码包 SHA-256 固定为：

```text
e50d7b94baac6c7d09927fa5ca5800a0c7ee5015c7fcff65beb3a1931b5a6e09
```

两个架构的 Chaquopy target ZIP 哈希固定在 [tools/build_python_wheels.py](../tools/build_python_wheels.py)。Cargo 使用源码包自带的 `Cargo.lock`、`--locked` 和 crates.io sparse 索引；发现 Git 源依赖时中止构建。

## Windows 构建

准备 Python 3.14、Visual Studio“使用 C++ 的桌面开发”组件和 Android NDK r28c。首次运行：

```powershell
cd D:\PG-Proxy
python tools\build_python_wheels.py --bootstrap-rust
```

脚本将 Rust 安装到项目的 `data/python-wheels/`，不修改系统 PATH。首次构建会从 Rust 官方 CDN、PyPI、crates.io 和 Maven Central / Chaquopy 下载工具链、依赖及目标 Python 库。

工具链就绪后，可只构建模拟器架构：

```powershell
python tools\build_python_wheels.py --abi x86_64
```

也可以指定已有工具链路径：

```powershell
python tools\build_python_wheels.py --abi all `
  --ndk C:\Android\Sdk\ndk\28.2.13676358 `
  --cargo-home C:\BuildTools\cargo `
  --rustup-home C:\BuildTools\rustup
```

脚本同时接受 `CARGO_HOME`、`RUSTUP_HOME` 环境变量。已有工具链须包含 Rust 1.99.1 以及 `aarch64-linux-android`、`x86_64-linux-android` 标准库；该脚本目前仅实现 Windows 主机构建。

### 输出与打包

```text
artifacts/wheels/pydantic_core-2.50.1-cp314-cp314-android_28_arm64_v8a.whl
artifacts/wheels/pydantic_core-2.50.1-cp314-cp314-android_28_x86_64.whl
artifacts/wheels/build-report.json
```

APK 构建通过 `--find-links artifacts/wheels` 使用上述文件，并固定 Pydantic 与 core 版本。wheel 保留所需 MIT 许可证及 RECORD 哈希；生成产物和本地工具链目录由 Git 忽略。

## 验证范围

构建脚本检查动态依赖包含 `libpython3.14.so`，且不存在 Termux 私有路径、RPATH 或 RUNPATH；全部 ELF LOAD 段须至少按 16 KB 对齐。报告记录 wheel 架构、Python ABI、版本、源码哈希和目标 Python 哈希。

两个 ABI 的 wheel 已在本地开发阶段成功交叉编译。运行验收仍须由普通 APK UID 完成 `import pydantic_core`、模型验证、FastAPI / Uvicorn 启停和实际网络请求。**编译与 ELF 检查不能证明 APK 已正常运行**，既往测试也不能代替新构建的验收；具体记录见[验证说明](VALIDATION.md)。

首次导入或 ABI 变更导致的 `ImportError` 应明确报告，不得静默切换到未经测试的后端。

## Python 与 Android 桥接

`pgproxy.android_host.Controller` 在专属 Python 事件循环线程中管理 Uvicorn、凭证池和 WAA。UI 经应用内部 Binder 请求管理操作，公开 HTTP 端口不提供 Cookie 管理功能。

| 职责 | 实现 |
| --- | --- |
| 凭证存储 | 通过 NativeBridge 调用 Android Keystore 支持的 AES-GCM 私有存储，不在磁盘写入明文 `session.json` |
| 会话隔离 | 每个账号独立维护 Cookie、HTTP 客户端、模型缓存与 WAA 状态；默认只驻留一个 WAA 工作进程 |
| 工作进程管理 | Python 预分配 UUID，Kotlin 创建已安装的原生程序；启动、管道写入、资源回调和响应读取共用超时截止时间 |
| 取消与超时 | Kotlin 按 ID 终止并回收进程，包括进程创建完成前已发生取消的情况 |
| 日志与快照 | 不输出凭证、Cookie、WAA proof 或解释器原始错误 |

“检查凭证”会调用真实模型，最多请求 1024 个输出 token；只有收到可见文本和结束帧，才记录为生成验证通过。
