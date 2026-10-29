# Argon 目录与生成验证 · 2026-10-29

本次解决网页可用、PG Proxy 目录却缺少 `gemini-4-argon` 的问题。测试使用手机已有的同一份 Google 凭证，服务与 WAA 均在手机中运行；没有重新登录或导出 Cookie。时间按北京时间记录。

**结果：目录恢复为 19 个模型，Argon 的 OpenAI / Gemini 流式与非流式四项真实短请求通过。真实跨轮长输出仍未验证。**本文只对应下列包哈希，后续 Release 的构建与运行结果单独记录。

## 问题与修复

`ListModels` 在线返回的能力码 `91` 表示可续接输出，仍使用 Playground 的 `GenerateContent`。旧解析器把它与视频、Live、音乐等未实现通道一起过滤，导致 Argon 不在对外目录中。`Paid` 标签不是这次遗漏的原因，项目没有按该标签排除模型。

修复按能力码处理，不添加模型名称白名单。除恢复目录显示外，还补齐请求中的续写配置、续写 token、每轮 WAA proof、分轮预算、用量累计、流关闭与取消清理。同一个生成任务跨轮固定使用同一凭证，不将失败请求自动重放到另一个账号。视频、Live、音乐和仅使用 Interactions 的模型仍不开放。

## 真实设备结果

| 项目 | 条件或结果 |
| --- | --- |
| 设备 | vivo V2505A，Android 17 / API 37，ARM64，4 KB 页 |
| 应用 | PG Proxy `0.1.0`，版本码 `10`，正式签名 Release |
| 测试包 SHA-256 | `e8884af5d9561828221a3f1b9dd11eeee21e66461b8931c958cb4862e11a6eab` |
| 包核对 | 覆盖安装后，设备上的 APK 哈希与本地测试包一致 |
| 连接 | USB 将电脑 `18888` 转发到手机 `8888`；服务和 WAA 均在手机中运行 |
| 模型目录 | 修复前 18 个；修复后 19 个，`gemini-4-argon` 在两个模型目录中均可见 |
| 请求 | `Reply exactly OK.`，最大输出 1024，思考级别 `LOW` |

| 实际调用 | HTTP | 结束标记 | 耗时 |
| --- | --- | --- | --- |
| OpenAI 非流式 | 200 | `stop` | 11.564 秒 |
| OpenAI SSE | 200 | `stop`，随后 `[DONE]` | 3.975 秒 |
| Gemini 非流式 | 200 | `STOP` | 5.311 秒 |
| Gemini SSE | 200 | `STOP` | 6.419 秒 |

四项均收到 2 个可见字符。原生 Gemini SSE 不使用 OpenAI 的 `[DONE]` 标记。非流式用量均为输入 167、输出 5、总计 172；这些数字仅描述本次上游返回，不作为固定计费或性能承诺。

表中测试包来自完成代码修改、尚未整理 Git 提交的工作区。构建工具会将提交信息嵌入 APK，后续整理提交也会改变包哈希。正式发布时应从最终提交重新构建，并在 Release 中提供源码提交、签名证书和 APK 校验信息；同一版本号下的不同包仍须分别核对。

## 模拟回归与验证边界

Python 全量回归 **251 项通过，2.95 秒**，保留 1 项已有的 Starlette HTTP 客户端弃用警告。

- 协议用例覆盖能力码筛选、普通模型兼容、分轮字段、预算和用量解析。
- 多轮用例覆盖结束码 `22`、带续写 token 的 `MAX_TOKENS`、提前收到 token、缺失／重复 token、第二轮失败、流截断、取消与资源释放。
- API 用例覆盖两个目录、OpenAI 和 Gemini 的非流式／流式形状、中间结束标记隐藏、内部 token 不外泄及最终用量累计。

模拟响应不访问 Google，不以固定 proof 替代真实校验。**本次真实短请求没有触发跨轮长输出，因此真实多轮续写、百万 token 输出及对应耗时／额度消耗尚未验证。**单个真实账号通过也不代表双账号轮询已实测。

本次没有重复局域网、锁屏、电池或通知图标验收。此前登录修复包的相关结果独立保存在[真机验收记录](PHYSICAL_DEVICE_TEST.md)，不得当作本测试包重新执行的项目。

## 复现

1. 记录准备验证的源码提交，按[维护文档](MAINTENANCE.md)构建并安装。在手机中自行登录、保存有效凭证并启动服务。升级后刷新调用端模型列表；停止并重新启动服务可清除最多 5 分钟的目录缓存。
2. 在 Git 忽略目录中创建私有连接文件，仅保存实际 `apiKey`，不要上传或贴到聊天、Issue。
3. 选择目标手机序列号并建立转发，执行已有的接口验收工具：

```powershell
adb -s <手机序列号> forward tcp:18888 tcp:8888
.venv\Scripts\python.exe tools\e2e_app.py `
  --base-url http://127.0.0.1:18888 `
  --connection-file artifacts\private-connection.json `
  --model gemini-4-argon --live `
  --output artifacts\argon-e2e.json
adb -s <手机序列号> forward --remove tcp:18888
```

该通用工具会执行目录、认证及更多短请求，使用上游默认思考配置，与上表固定 `LOW` 的四项测试范围不同。`--live` 会消耗真实生成额度；检查报告中的每项结果，不仅查看命令是否退出。需要验证局域网时，应直接使用手机 Wi-Fi 地址，不能以 USB 转发代替。

离线回归命令：

```powershell
.venv\Scripts\python.exe -m pytest -q
```

原始记录位于本机忽略目录 `artifacts/model-catalog-investigation/`：`phone-before.json`、`phone-argon-live.json`、`live-test-manifest.json`、`python-tests.log` / `.xml` 及 `arm64-build.log`。原始诊断资料保留在本机；正式 APK 与公开校验清单可通过 Release 提供。复现时使用自己的账号、设备和网络，并单独记录结果。
