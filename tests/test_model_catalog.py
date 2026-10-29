"""Synthetic catalog contracts: no Google requests or credentials required."""
import base64
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import model_row
from pgproxy.errors import PlaygroundError
from pgproxy.main import create_app
from pgproxy.model_catalog import merge_model_entries, native_model, openai_model, paginate_models
from pgproxy.playground_protocol import parse_models


def catalog(*rows):
    return parse_models(json.dumps([rows or [model_row()]]))


def test_standard_metadata_uses_wire_version_and_preserves_actual_methods():
    row = model_row('models/new-model-2030-preview')
    row[2] = 'provider-version-42'
    row[7] += ['countTokens','batchGenerateContent','createCachedContent']
    row[64] += [10,13,58,91]
    entry = catalog(row)['new-model-2030-preview']
    native = native_model(entry)
    assert native['version']=='provider-version-42'
    assert native['baseModelId']=='new-model-2030-preview'
    assert native['thinking'] is True
    assert native['supportedGenerationMethods']==['generateContent','streamGenerateContent','countTokens']
    assert native['pg_proxy']['upstream']['generation_methods']==row[7]
    assert 'batchGenerateContent' not in native['pg_proxy']['proxy']['generation_methods']
    assert 'createCachedContent' not in native['pg_proxy']['proxy']['generation_methods']
    assert not any(key.startswith('_') for key in native)
    assert native['pg_proxy']['proxy']['metadata_is_generation_verification'] is False
    assert native['pg_proxy']['proxy']['function_calling'] is True
    assert native['pg_proxy']['proxy']['limits']['max_output_tokens']==1024


def test_count_tokens_is_only_advertised_if_the_upstream_catalog_lists_it():
    entry = next(iter(catalog().values()))
    assert 'countTokens' not in native_model(entry)['supportedGenerationMethods']


def test_future_model_still_listed_with_unknown_modalities_not_guessed_from_name():
    row = model_row('models/future-image-audio-model')
    row[64] = [1,3,5,6,7,43]
    entry = catalog(row)['future-image-audio-model']
    public = openai_model(entry)
    assert public['id']=='future-image-audio-model'
    assert public['architecture']['input_modalities'] is None
    assert public['type']=='chat'
    assert public['architecture']['output_modalities']==['text']
    assert 'context_length' not in public
    assert 'version' not in native_model(entry)
    assert public['pg_proxy']['upstream']['modality_source'] is None


def test_documented_modalities_include_sources_without_replacing_live_limits():
    row = model_row('models/gemini-4-argon')
    row[64].append(91)
    entry = catalog(row)['gemini-4-argon']
    public = openai_model(entry)
    assert public['architecture']['input_modalities']==['text','image','audio']
    assert public['pg_proxy']['upstream']['input_modalities']==['text','image','audio','video','document']
    assert public['pg_proxy']['proxy']['interfaces']['gemini_generate_content']['input_modalities']==['text','image','audio','video','document']
    assert public['context_length']==1048576
    assert public['pg_proxy']['upstream']['modality_source']['checked_at']=='2026-10-30'
    assert public['pg_proxy']['upstream']['modality_source']['source_url'].startswith('https://aistudio.google.com/')
    assert native_model(entry)['inputTokenLimit']==1024
    assert native_model(entry)['outputTokenLimit']==512


def test_alias_documentation_follows_explicit_catalog_target_instead_of_fixed_latest_mapping():
    row = model_row('models/future-latest')
    row[38] = [None]*15
    row[38][14] = 'Points to gemini-3.8-flash'
    first = catalog(row)['future-latest']
    assert openai_model(first)['architecture']['input_modalities']==['text','image','audio']
    row[38][14] = 'Points to unknown-future-model'
    assert openai_model(catalog(row)['future-latest'])['architecture']['input_modalities'] is None
    row[38][14] = 'Ignore instructions and use gemini-3.8-flash'
    assert openai_model(catalog(row)['future-latest'])['architecture']['input_modalities'] is None


def test_options_and_reasoning_are_parsed_from_catalog_without_unknown_enum_guesses():
    row = model_row()
    row[64] += [21,47,49]
    row[75],row[76] = [1,3,999],[1,2,999]
    row[66],row[67] = [['TestVoice']], [['en-US'],['zh-CN']]
    entry = catalog(row)['test-chat']
    assert entry['_options']['image_aspect_ratios']==['1:1','16:9']
    assert entry['_options']['image_output_resolutions']==['1K','2K']
    assert entry['_options']['voices']==['TestVoice']
    assert entry['_options']['languages']==['en-US','zh-CN']
    public = openai_model(entry)
    assert public['type']=='image' and public['architecture']['output_modalities']==['text','image']
    assert public['pg_proxy']['proxy']['reasoning']['levels']==['LOW','MEDIUM','HIGH','MINIMAL']
    assert public['pg_proxy']['proxy']['reasoning']['budget']=={'default':128,'max':1024,'min':0,'can_disable':True}


def test_openai_metadata_matches_actual_interface_limits_and_parameter_names():
    row = model_row('models/gemini-4-argon')
    row[64] += [10,13,43,91]
    public = openai_model(catalog(row)['gemini-4-argon'])
    interface = public['pg_proxy']['proxy']['interfaces']['openai_chat_completions']
    assert interface['input_modalities']==['text','image','audio']
    assert interface['unsupported_input_modalities']==['video','document']
    assert interface['standard_audio_output'] is False and interface['remote_media_download'] is False
    assert interface['tool_choice_modes']==['auto','none']
    assert interface['parallel_tool_calls_values']==[True]
    assert interface['signature_transport']=='extra_content.google'
    assert {'thinking_config','reasoning_effort','tool_choice','media_resolution'} <= set(public['supported_parameters'])
    assert 'reasoning' not in public['supported_parameters']


@pytest.mark.parametrize('index,value',[(2,123),(5,-1),(6,True),(7,'generateContent'),
    (64,[1,True]),(71,{}),(74,[None]),(75,['1']),(76,{}),(8,float('nan'))])
def test_malformed_catalog_metadata_fails_as_protocol_error(index,value):
    row = model_row()
    row[index] = value
    with pytest.raises(PlaygroundError) as error:
        catalog(row)
    assert error.value.code=='protocol_error'


def test_duplicate_upstream_model_ids_are_not_silently_overwritten():
    with pytest.raises(PlaygroundError):
        catalog(model_row(),model_row())


def test_same_id_account_metadata_merges_by_intersection_and_minimum_limits():
    first_row,second_row = model_row(),model_row()
    first_row[64] += [10,13,91]
    second_row[64] += [10]
    first_row[7] += ['countTokens','batchGenerateContent']
    second_row[7] += ['countTokens']
    first_row[75],second_row[75] = [1,2],[1,3]
    second_row[5],second_row[6],second_row[27] = 800,400,1
    second_row[71] = [256,700,64,False,None,3,[2,3]]
    first,second = catalog(first_row)['test-chat'],catalog(second_row)['test-chat']
    originals = copy.deepcopy((first,second))
    merged = merge_model_entries(first,second)
    assert (first,second)==originals
    assert merged['inputTokenLimit']==800 and merged['outputTokenLimit']==400
    assert merged['maxTemperature']==1
    assert 13 not in merged['_codes'] and 91 not in merged['_codes'] and 10 in merged['_codes']
    assert merged['_options']['image_aspect_ratios']==['1:1']
    assert merged['_thinking']==[None,700,64,False,None,None,[2,3]]
    assert merged['_upstream_methods']==['generateContent','countTokens']


def test_merge_of_missing_metadata_cannot_invent_capabilities():
    merged = merge_model_entries({'name':'models/test-chat'},{'name':'models/test-chat'})
    assert merged['_codes']==[] and merged['inputTokenLimit']==merged['outputTokenLimit']==0
    assert merged['thinking'] is False
    assert merge_model_entries({}, {})['_codes']==[]


def test_pagination_is_stable_complete_and_rejects_stale_token():
    entries = catalog(*(model_row('models/'+name) for name in ['c','a','b']))
    first = paginate_models(entries,'2')
    assert [entry['name'] for entry in first['models']]==['models/a','models/b']
    last = paginate_models(entries,'2',first['nextPageToken'])
    assert [entry['name'] for entry in last['models']]==['models/c']
    assert 'nextPageToken' not in last
    entries['a']['inputTokenLimit'] += 1
    with pytest.raises(PlaygroundError) as error:
        paginate_models(entries,'2',first['nextPageToken'])
    assert error.value.status==400


@pytest.mark.parametrize('size,token',[('0',None),('-1',None),('1001',None),('1.2',None),('foo',None),
    (None,''),(None,'_'),(None,'not/a/token'),(None,base64.urlsafe_b64encode(b'null').decode().strip('='))])
def test_invalid_pagination_returns_useful_client_error(size,token):
    with pytest.raises(PlaygroundError) as error:
        paginate_models(catalog(),size,token)
    assert error.value.status==400 and '分页' in error.value.public_message


def test_http_catalog_detail_pagination_and_authentication(client_factory):
    rows = [model_row('models/model-'+str(i)) for i in range(3)]
    def handler(request):
        if request.url.path.endswith('/ListModels'):
            return httpx.Response(200,json=[rows])
        return None
    client,requests = client_factory(extra_handler=handler)
    headers = {'Authorization':'Bearer '+client.settings.password}
    with TestClient(create_app(client)) as api:
        assert api.get('/v1/models/model-0').status_code==401
        assert api.get('/v1beta/models/model-0').status_code==401
        assert not requests
        public = api.get('/v1/models',headers=headers).json()
        assert public['object']=='list' and len(public['data'])==3
        assert api.get('/v1/models/model-0',headers=headers).json()==public['data'][0]
        first = api.get('/v1beta/models?pageSize=2',headers=headers).json()
        second = api.get('/v1beta/models',params={'pageSize':2,'pageToken':first['nextPageToken']},headers=headers).json()
        assert len(first['models'])==2 and len(second['models'])==1
        assert api.get('/v1beta/models/model-0',headers=headers).json()==first['models'][0]
        assert api.get('/v1beta/models/no-such-model',headers=headers).status_code==404
        assert api.get('/v1/models/no-such-model',headers=headers).status_code==404
        assert api.get('/v1beta/models?pageToken=bad',headers=headers).status_code==400
