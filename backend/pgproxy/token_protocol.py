"""Authoritative Playground CountTokens; never estimate with a local tokenizer."""
from .errors import PlaygroundError
from .playground_protocol import encode_generate, invalid, loads, at


def encode_count_tokens(model_id, body, model):
    if not isinstance(body, dict) or set(body) - {'contents', 'generateContentRequest'}:
        raise invalid('Unsupported countTokens field')
    nested = 'generateContentRequest' in body
    if nested:
        if 'contents' in body or not isinstance(body['generateContentRequest'], dict):
            raise invalid('Use contents or generateContentRequest, not both')
        request = dict(body['generateContentRequest'])
        requested_model = request.pop('model', model_id)
        if requested_model not in (model_id, 'models/' + model_id):
            raise invalid('CountTokens model does not match the request path')
    else:
        request = {'contents': body.get('contents')}
    system_only = not request.get('contents') and bool(request.get('systemInstruction'))
    if system_only:
        # Validate using the same schema, then omit the empty content from counting.
        request['contents'] = [{'role': 'user', 'parts': [{'text': ''}]}]
    wire, _, _ = encode_generate(model_id, request, model)
    contents = None if system_only else wire[1]
    has_structured_parts = any(
        any(key in part for key in ('inlineData', 'functionCall', 'functionResponse', 'fileData',
                                    'videoMetadata', 'executableCode', 'codeExecutionResult',
                                    'toolCall', 'toolResponse'))
        for content in request['contents'] for part in content['parts'])
    if nested or has_structured_parts:
        generation = [None] * (7 if 'tools' in request else 6 if 'systemInstruction' in request else 2)
        generation[0], generation[1] = 'models/' + model_id, contents
        if 'systemInstruction' in request:
            generation[5] = wire[5]
        if 'tools' in request:
            generation[6] = wire[6]
        return ['models/' + model_id, None, generation]
    return ['models/' + model_id, contents]


def decode_token_count(raw):
    total = at(loads(raw), 0)
    if type(total) is not int or total < 0:
        raise PlaygroundError('CountTokens response has no valid total', code='protocol_error')
    return {'totalTokens': total}
