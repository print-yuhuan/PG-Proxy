from fastapi import APIRouter, Depends, Request, Body
from .auth import authenticate_user
from .errors import PlaygroundError
from .playground_protocol import public_model
from .responses import generation_response
from .model_catalog import find_model, paginate_models
from .request_logging import record_parameters, set_stage

router = APIRouter(dependencies=[Depends(authenticate_user)])


@router.get('/v1beta/models')
async def list_models(request: Request):
    set_stage('model_catalog')
    return paginate_models(await request.app.state.playground.models(),
        request.query_params.get('pageSize'),request.query_params.get('pageToken'))


@router.get('/v1beta/models/{model}')
async def model_detail(request: Request, model: str):
    record_parameters({'model': model})
    set_stage('model_catalog')
    return public_model(find_model(await request.app.state.playground.models(),model))


async def generate(request: Request, model: str, action: str, body: dict = Body(...)):
    set_stage('parameters')
    record_parameters(body)
    record_parameters({'model': model, 'stream': action == 'streamGenerateContent'})
    if action=='countTokens':
        return await request.app.state.playground.count_tokens(model,body)
    if action not in ('generateContent','streamGenerateContent'):
        raise PlaygroundError('Unsupported Gemini operation',404,'not_found')
    alt = request.query_params.get('alt')
    if alt not in (None,'sse','json'):
        raise PlaygroundError('alt must be sse or json',400,'invalid_request_error')
    return await generation_response(request.app.state.playground,model,body,streaming=action=='streamGenerateContent',
                                     sse=alt=='sse',request=request)


for prefix in ('/v1beta/models','/v1/models'):
    # Both supported Gemini API versions use the standard colon operation path.
    router.add_api_route(prefix+'/{model}:{action}',generate,methods=['POST'])
