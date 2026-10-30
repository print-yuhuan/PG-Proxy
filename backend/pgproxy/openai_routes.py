from fastapi import APIRouter, Depends, Request, Body
from .auth import authenticate_user
from .openai_transformers import openai_request_to_gemini
from .responses import generation_response
from .model_catalog import find_model, openai_model
from .openai_compat import normalize_openai_request, select_default_model

router = APIRouter(dependencies=[Depends(authenticate_user)])


@router.get('/v1/models')
async def list_models(request: Request):
    models = await request.app.state.playground.models()
    return {'object':'list','data':[openai_model(model) for model in models.values()]}


@router.get('/v1/models/{model}')
async def model_detail(request: Request, model: str):
    return openai_model(find_model(await request.app.state.playground.models(),model))


@router.post('/v1/chat/completions')
async def completions(request: Request, body: dict = Body(...)):
    body = normalize_openai_request(body)
    if 'model' not in body:
        # Fail malformed conversational data before making a catalog request.
        openai_request_to_gemini({**body, 'model': 'validation-only'})
        body['model'] = select_default_model(await request.app.state.playground.models())
    model,payload = openai_request_to_gemini(body)
    return await generation_response(request.app.state.playground,model,payload,streaming=body.get('stream',False),openai=True,
                                     include_usage=body.get('stream_options',{}).get('include_usage',False),request=request)
