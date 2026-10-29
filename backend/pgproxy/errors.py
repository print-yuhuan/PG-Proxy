PUBLIC_MESSAGES = {
    'invalid_request':'操作参数不正确，请检查输入后重试。',
    'invalid_request_error':'请求参数格式不正确，请检查模型、消息和生成参数。',
    'invalid_port':'监听端口须为 1024 至 65535 之间的整数。',
    'invalid_api_key':'接口密钥须为 3 至 256 位可见 ASCII 字符，不能包含空格。',
    'invalid_timeout':'等待时间须为 1 至 600 秒之间的有限数值。',
    'invalid_session_import':'登录会话格式不正确，请从应用凭证页重新登录。',
    'session_invalid':'保存的登录会话无法读取，请重新登录该凭证。',
    'session_required':'登录会话缺失或已过期，请在凭证页重新登录。',
    'session_storage_error':'登录会话保存失败，请检查应用存储空间后重试。',
    'account_busy':'请求排队超时，请稍后重试。',
    'account_deleted':'该凭证已进入删除状态，请添加新凭证后继续。',
    'account_not_found':'凭证记录不存在，请刷新凭证列表。',
    'account_deletion_pending':'正在等待当前请求结束，后台会继续删除凭证。',
    'account_deletion_failed':'凭证清理失败，该凭证已停用，请重试删除。',
    'no_available_account':'暂无可用凭证，请检查登录状态、启用状态或冷却时间。',
    'model_not_found':'该模型不在可用目录中，请选择账号实际支持的模型。',
    'not_found':'接口路径不存在，请使用受支持的接口地址。',
    'rate_limit':'上游请求暂时受限，请等待冷却时间结束后重试。',
    'port_unavailable':'监听端口不可用，可能已被占用，请更换端口。',
    'service_start_failed':'本机 API 启动失败，请检查端口和应用运行状态。',
    'service_error':'服务操作未完成，请稍后重试。',
    'service_closed':'服务已关闭，请返回概览页查看运行状态。',
    'generation_unverified':'生成检查未通过：未收到完整的可见文本响应。',
    'upstream_timeout':'上游响应超时，请检查网络或调整等待时间。',
    'upstream_connection_error':'无法连接上游，请检查网络连接后重试。',
    'upstream_authentication_error':'上游未接受登录会话，请确认网站可用后重新登录。',
    'upstream_permission_denied':'上游拒绝访问，请检查账号权限及请求校验状态。',
    'upstream_error':'上游请求未能完成，请稍后重试。',
    'page_initialization_failed':'Playground 页面初始化失败，请确认登录和网站状态。',
    'protocol_error':'上游响应格式不符合预期，本次请求未能完整处理。',
    'stream_interrupted':'流式响应未完整结束，请检查网络与客户端请求状态。',
    'waa_unavailable':'请求校验运行环境暂不可用，请稍后重试。',
    'waa_protocol_error':'请求校验数据不符合预期，本次请求未能完成。',
    'waa_execution_error':'请求校验程序执行失败，请稍后重试。',
}


class PlaygroundError(Exception):
    def __init__(self, message, status=502, code="upstream_error", retry_after=None, *, public_message=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retry_after = retry_after
        self.public_message = public_message or PUBLIC_MESSAGES.get(code,'操作未完成，请稍后重试。')

    def body(self):
        return {"error": {"message": self.public_message, "type": self.code, "code": self.status}}
