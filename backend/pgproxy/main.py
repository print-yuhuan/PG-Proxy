"""Public compatibility API; management remains in the private Android bridge."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .config import NAME, VERSION
from .errors import PlaygroundError
from .openai_routes import router as openai_router
from .gemini_routes import router as gemini_router
from .request_statistics import RequestStatistics, RequestStatisticsMiddleware
from .request_guard import RequestGuardMiddleware


def create_app(client, close_on_shutdown=True, request_statistics=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.playground = client
        try:
            yield
        finally:
            if close_on_shutdown:
                await app.state.playground.close()

    app = FastAPI(title=NAME,version=VERSION,lifespan=lifespan)
    # Added before CORS/statistics so early rejections retain their headers/counts.
    app.add_middleware(RequestGuardMiddleware)
    app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['GET','POST','OPTIONS'],allow_headers=['*'])
    statistics = request_statistics if request_statistics is not None else RequestStatistics()
    app.state.request_statistics = statistics
    app.add_middleware(RequestStatisticsMiddleware,statistics=statistics)

    @app.exception_handler(PlaygroundError)
    async def upstream_error(request: Request,exc: PlaygroundError):
        return JSONResponse(exc.body(),status_code=exc.status,headers={'Retry-After':exc.retry_after} if exc.retry_after else None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request,exc: RequestValidationError):
        return JSONResponse({'error':{'message':'请求体须为有效 JSON 对象，请检查字段类型。','type':'invalid_request_error','code':400}},status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request,exc: HTTPException):
        message = {401:'接口密钥不正确，请检查客户端认证设置。',404:'接口路径不存在。',405:'此接口不支持该请求方法。'}.get(exc.status_code,'请求未能完成，请检查参数。')
        return JSONResponse({'error':{'message':message,'type':'authentication_error' if exc.status_code==401 else 'invalid_request_error','code':exc.status_code}},status_code=exc.status_code,headers=exc.headers)

    @app.exception_handler(Exception)
    async def internal_error(request: Request,exc: Exception):
        return JSONResponse({'error':{'message':'服务处理请求时发生异常，请稍后重试。','type':'service_error','code':500}},status_code=500)

    @app.get('/')
    async def root():
        return {'name':NAME,'upstream':'Google AI Studio Playground','version':VERSION,
                'endpoints':['/v1/models','/v1/models/{model}','/v1/chat/completions',
                    '/v1beta/models','/v1beta/models/{model}',
                    '/v1beta/models/{model}:countTokens','/v1beta/models/{model}:generateContent',
                    '/v1beta/models/{model}:streamGenerateContent',
                    '/v1/models/{model}:countTokens','/v1/models/{model}:generateContent',
                    '/v1/models/{model}:streamGenerateContent'],
                'waa_backend':'bundled local JavaScript VM (Goja + Firefox host)'}

    @app.get('/health')
    async def health():
        return {'status':'running','service':NAME,'version':VERSION}

    app.include_router(openai_router)
    app.include_router(gemini_router)
    return app
