# 后台运行

PG Proxy 使用前台服务和运行通知，并提供电池设置引导与可选的 CPU 唤醒锁。这些措施可以减少后台中断，但仍受 Android 和厂商电源策略限制；用户强制停止应用后，需要重新打开并启动服务。

## 推荐设置

打开 **设置 → 后台运行**，按需要完成以下操作：

| 设置项 | 操作与作用 |
| --- | --- |
| 运行通知 | 允许 PG Proxy 和“本机 API 服务”通知，以便查看运行状态、从通知停止服务 |
| 电池优化 | 按系统提示选择是否允许豁免；返回应用后会重新读取实际状态 |
| 后台耗电管理 | 进入应用详情，找到电池或后台耗电选项，选择“允许后台耗电”或“不受限制”；按需开启后台活动、自启动，或在最近任务中锁定应用 |
| 保持 CPU 唤醒 | 需要锁屏持续提供接口时开启并保存；默认关闭，会增加耗电，但不会保持屏幕常亮 |

完成后返回概览启动服务。CPU 唤醒的实际状态显示在设置和运行通知中；关闭选项或停止服务都会释放唤醒锁。单独更改此选项不会重启 HTTP 服务或主动中断正在生成的请求。

**标准电池优化豁免与厂商后台许可是两类设置。**“已忽略 Android 电池优化”不能证明厂商的后台耗电、自启动等开关也已开启。应用无法可靠读取的厂商开关，需要在系统页面自行确认。

通知关闭后，前台服务不一定立即停止，但通知栏控制可能不可见，应用会单独提示。系统不支持直接打开电池优化确认页时，会依次尝试电池优化列表、应用详情和系统设置；仍无法打开时，按提示手动查找。

### vivo 真机设置

在 **vivo V2505A / Android 17** 上验证的路径为：

**PG Proxy 设置 → 后台耗电管理（应用详情）→ 电量 → 后台耗电管理 → 允许后台耗电**

其他机型或系统版本的入口名称、层级可能不同。2026-10-29 在同一设备进行的对照如下，两轮均保持 USB 调试及供电连接：

| 系统与应用设置 | 锁屏结果 |
| --- | --- |
| 默认“智能控制后台耗电”，CPU 唤醒开启 | 第二次局域网轮询超时；前台服务仍存在，但唤醒锁被系统标为 `DISABLED`，进程为 `isFrozen`，USB 转发访问也超时 |
| “允许后台耗电”，系统显示电池优化已豁免，CPU 唤醒开启 | 373.52 秒内 13 次局域网健康检查全部成功；锁屏期间查询到 18 个模型并完成真实生成 |

这说明**通知存在或已申请唤醒锁，不能单独证明服务仍可访问**。更改厂商设置后，标准 Android 电池优化豁免也同时生效，因此不能把成功单独归因于某一个开关。

该轮还验证了从常驻通知停止服务：前台状态结束、监听端口关闭、WAA 子进程退出、唤醒锁释放。最终交付包补测后也确认了资源清理。测试结束时已关闭 CPU 唤醒选项，用户需要时应重新开启并保存。安装包哈希和完整证据见[真机验收记录](PHYSICAL_DEVICE_TEST.md)。

上述约 6 分钟结果未覆盖完整唤醒锁续期、长期 Doze、拔线运行、实际耗电或其他厂商设备。

## 实现与限制

- 前台服务配合低打扰通知运行，系统设置仅通过用户主动操作打开。
- `PARTIAL_WAKE_LOCK` 仅在后端最新状态确认服务运行、且用户已开启选项时申请。每次申请带 10 分钟系统超时；续期前由后台线程确认服务状态，查询卡住时不会无限续期。
- 停止服务、关闭选项、首次启动失败或服务销毁时释放唤醒锁。
- 未实现开机自启、定时闹钟复活、无障碍保活或进程互拉。Doze、网络策略、低内存及厂商清理仍可能中断服务。

相关平台说明：[Doze 与待机](https://developer.android.com/training/monitoring-device-state/doze-standby)、[Wake lock](https://developer.android.com/develop/background-work/background-tasks/awake/wakelock/set)、[系统设置 Intent](https://developer.android.com/reference/android/provider/Settings)、[通知权限](https://developer.android.com/develop/ui/compose/notifications/notification-permission)。

## 开发验证记录

以下为 **2026-10-29 的后台功能开发测试**，与上面的 ARM64 真机测试分开记录，不代表后续每个 APK 都已重复验收。Python 使用替身，UI 使用合成状态；设备运行用例仅访问本机 `/health`，不读取或修改真实账号，也不调用 Google 生成接口。打开主界面可能触发正常的公网 IP 查询。

| 验证项 | 结果与范围 |
| --- | --- |
| Python 回归 | 152 项通过，2.83 秒；1 项已有 Starlette 测试客户端弃用警告 |
| 构建 | ARM64、x86_64 主 APK 和 x86_64 测试 APK 通过 |
| Pixel Android 16 x86_64 模拟器 | 47 项通过，77.058 秒：UI 21、唤醒锁 8、设置回退 2、后台运行 1、前台通知状态 3、原生运行时 6、IPC 6 |
| 短时熄屏 | 实际申请部分唤醒锁，确认服务处于前台；约 10 秒内 5 次本机健康检查通过，关闭选项和停止服务均释放锁 |
| 设置与布局 | 人工检查通知设置、电池豁免、应用详情，以及正常字体、320 dp / 2 倍字体下的表单和失败提示；临时设置均已恢复 |

本地证据位于忽略目录 `artifacts/background/`：`python.log`、`python.xml`、`build-final.log`、`android.log`、`runtime.json`、`manual-settings.json` 及相关截图；APK 哈希清单为 `artifacts/pg-proxy-apks.json`。这些开发产物不随源码提交。其他测试阶段见[验证说明](VALIDATION.md)。

### 复现后台测试

先按[构建与维护](MAINTENANCE.md)生成主 APK 与测试 APK，再选择测试类运行。以下按当前 0.2.0 构建文件名举例，设备编号应替换为自己的模拟器编号；命令更新不表示已对 0.2.0 重新执行后台验收：

```powershell
cd D:\PG-Proxy
.venv\Scripts\python.exe -m pytest -q
adb -s emulator-5556 install -r artifacts\PG-Proxy-0.2.0-x86_64.apk
adb -s emulator-5556 install -r artifacts\PG-Proxy-0.2.0-x86_64-androidTest.apk
adb -s emulator-5556 shell am instrument -w -e class 'dev.pgproxy.app.UIInstrumentedTest,dev.pgproxy.app.WakeLockInstrumentedTest,dev.pgproxy.app.BackgroundSettingsInstrumentedTest,dev.pgproxy.app.BackgroundRuntimeInstrumentedTest,dev.pgproxy.app.ForegroundStateInstrumentedTest,dev.pgproxy.app.RuntimeInstrumentedTest,dev.pgproxy.app.ProxyConnectorInstrumentedTest' dev.pgproxy.app.test/androidx.test.runner.AndroidJUnitRunner
```

设备用例会临时启停服务、短时熄屏，随后恢复配置和运行状态。运行期间请暂停生成任务；长期锁屏、强制 Doze 和实际耗电需要另行测试。
