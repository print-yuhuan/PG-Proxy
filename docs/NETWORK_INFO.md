# 概览网络信息

概览卡片显示查询服务看到的公网出口 IP、地区及可获取的网络资料，帮助排查连接环境。**它不能证明 Google 使用相同出口，也不能代替凭证、WAA 或生成接口检查。** VPN、系统路由和站点分流都可能影响实际出口。

## 数据源

PG Proxy 按以下顺序查询固定 HTTPS 地址；取得有效公网 IP 后即停止。每次展示同一来源的完整结果，避免混合不同出口的数据。

| 顺序 | 地址 | 可返回的信息 |
| --- | --- | --- |
| 1 | `https://api.ipquery.io/?format=json` | IP、国家地区、组织 / ISP / ASN、机房与移动标记、风险分、VPN / 代理 / Tor 标记 |
| 2 | `https://ident.me/json` | IP、国家地区，以及响应实际包含的组织、ASN、网络类型 |
| 3 | `https://www.cloudflare.com/cdn-cgi/trace` | IP 与国家代码，其余字段保持未知 |

风险分采用 IPQuery 原始的 0–100 数值；分数越高，表示该服务商判断的恶意活动可能性越高。网络类型只依据明确返回的字段：缺少机房或移动标记时，不自动推断为住宅网络；缺少风险数据时，不生成评分。

服务商资料：[IPQuery](https://ipquery.io/)、[IPQuery 响应格式](https://ipquery.gitbook.io/ipquery-docs)、[ident.me](https://api.ident.me/)。

## 刷新、缓存与失败处理

| 场景 | 行为 |
| --- | --- |
| 打开或返回应用 | 查询网络信息，五分钟内优先使用内存缓存 |
| 手动刷新 | 点击网络卡片或概览顶部刷新按钮；两轮查询至少间隔十秒 |
| 常规状态刷新 | 每五秒更新本机状态，不触发外网查询 |
| 查询中或失败 | 保留上次结果；失败时显示重试入口和旧结果标记 |
| 超时 | 单个来源六秒，总体二十秒；响应体另有大小上限 |
| 限流 | 收到 `429` 时按 `Retry-After` 暂缓该来源；没有提示时默认冷却一分钟 |
| 停止服务 | 停止 API 监听不会清空缓存；后端实例退出时回收查询资源 |

卡片支持深浅色主题、IPv6 和组织长名称换行，以及 IP 复制。来源与查询时间直接显示；ASN 和风险分说明放在“详情”中。

## 隐私与统计

网络查询使用独立 HTTP 客户端，不携带 Google Cookie、账号资料或接口密钥。查询结果仅保存在内存，不写入磁盘或日志。

该功能通过 Android 私有进程桥接调用，没有公开管理 HTTP 路由，也不计入概览的请求成功、失败统计。

## 参考与验证

数据源选择参考 FlClash 提交 `7fd417fb9a9a277c6a9339a8ac87f907df7c25f7` 的以下文件：

- [公网 IP 查询列表](https://github.com/chen08209/FlClash/blob/7fd417fb9a9a277c6a9339a8ac87f907df7c25f7/lib/common/service_probe.dart)：包含 Cloudflare、ip.sb、ipinfo.io、ipwho.is、GeoJS、country.is、IPQuery、ident.me。
- [IP 类型与风险查询](https://github.com/chen08209/FlClash/blob/7fd417fb9a9a277c6a9339a8ac87f907df7c25f7/lib/common/ip_quality.dart)：包含 ident.me、ip-api.com、IPQuery、IPLocate、ipapi.is、ProxyCheck。
- [仪表盘展示](https://github.com/chen08209/FlClash/blob/7fd417fb9a9a277c6a9339a8ac87f907df7c25f7/lib/views/dashboard/widgets/current_outbound.dart)与 [IP 字段映射](https://github.com/chen08209/FlClash/blob/7fd417fb9a9a277c6a9339a8ac87f907df7c25f7/lib/models/common.dart)。

项目自行实现 Python 查询客户端和 Compose 卡片，未引入 FlClash 源码或资源。数据源回退、缓存、限流及异常路径使用离线 HTTP 替身验证，界面使用合成网络信息验证；命令与结果见[验证说明](VALIDATION.md)。真实服务商响应会随网络和时间变化。
