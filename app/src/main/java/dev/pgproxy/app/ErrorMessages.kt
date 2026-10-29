package dev.pgproxy.app

import org.json.JSONObject

/** Messages cross a private IPC channel. The backend supplies curated, non-sensitive text. */
internal object ErrorMessages {
    fun fromResponse(response: JSONObject): String {
        val error = response.optJSONObject("error") ?: return "操作未完成，请稍后重试。"
        val code = error.optString("code")
        return when (code) {
            "service_timeout" -> "服务响应超时，操作可能仍在处理中，请刷新状态后再试。"
            "service_disconnected", "service_bind_failed" -> "服务连接已中断，正在尝试重连，也可点击右上角刷新。"
            "service_invalid_response" -> "服务响应异常，请重新打开应用后重试。"
            else -> (error.opt("message") as? String)?.takeIf { it.isNotBlank() && it.length <= 300 }
                ?: "操作未完成，请稍后重试。"
        }
    }
}
