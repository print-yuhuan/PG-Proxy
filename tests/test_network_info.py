import asyncio
import json

import httpx
import pytest

from pgproxy import network_info as module


@pytest.fixture
def transport(monkeypatch):
    original=httpx.AsyncClient
    def install(handler):
        def factory(**kwargs):
            assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
            assert kwargs['headers']['User-Agent'].startswith('PG-Proxy/')
            return original(transport=httpx.MockTransport(handler),**kwargs)
        monkeypatch.setattr(module.httpx,'AsyncClient',factory)
    return install


@pytest.mark.asyncio
async def test_serial_fallback_omits_cookies_and_never_combines_addresses(transport):
    requests=[]
    def handler(request):
        requests.append(request)
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        if len(requests)==1:
            return httpx.Response(429,headers={'Retry-After':'120','Set-Cookie':'tracking=private; Domain=.me'})
        return httpx.Response(200,json={'ip':'8.8.8.8','cc':'us','type':'hosting','asn':'AS15169'})
    transport(handler)
    lookup=module.NetworkInfo()
    result=await lookup.get()
    assert [r.url.host for r in requests]==['api.ipquery.io','ident.me']
    assert result['error']=='' and result['data']['source']=='ident.me'
    assert result['data']['networkType']=='hosting' and result['data']['countryCode']=='US'
    assert lookup._cooldowns['IPQuery']>module.time.monotonic()+100
    assert await lookup.get()==result and len(requests)==2
    assert (await lookup.get(force=True))['error'] and len(requests)==2
    lookup._last_started-=11
    await lookup.get(force=True)
    assert len(requests)==3 and requests[-1].url.host=='ident.me'
    await lookup.close()


@pytest.mark.asyncio
async def test_ip_only_success_keeps_unknown_fields_unknown(transport):
    count=0
    def handler(request):
        nonlocal count
        count+=1
        return httpx.Response(200,json={'ip':'1.1.1.1','risk':{'is_proxy':False,'is_vpn':False}})
    transport(handler)
    lookup=module.NetworkInfo()
    result=await lookup.get()
    assert count==1 and result['data']['networkType']==''
    assert result['data']['riskScore'] is None and result['data']['riskFlags']==[]
    result['data']['country']='caller mutation'
    assert (await lookup.get())['data']['country']==''
    await lookup.close()


@pytest.mark.asyncio
async def test_failed_refresh_preserves_stale_data_and_rejects_redirects_and_large_bodies(transport):
    fail=False
    hosts=[]
    def handler(request):
        hosts.append(request.url.host)
        if not fail: return httpx.Response(200,json={'ip':'1.1.1.1'})
        if request.url.host=='api.ipquery.io':
            return httpx.Response(302,headers={'Location':'https://untrusted.test/'})
        if request.url.host=='ident.me':
            return httpx.Response(200,content=b'x'*(65536+1))
        return httpx.Response(200,text='ip=127.0.0.1\nloc=US')
    transport(handler)
    lookup=module.NetworkInfo()
    first=await lookup.get()
    fail=True
    lookup._last_started-=11
    result=await lookup.get(force=True)
    assert result['data']==first['data'] and result['error']
    assert hosts==['api.ipquery.io','api.ipquery.io','ident.me','www.cloudflare.com']
    await lookup.close()


@pytest.mark.asyncio
async def test_concurrent_lookup_is_shared_and_close_cancels_request(transport):
    started=asyncio.Event()
    released=asyncio.Event()
    count=0
    async def handler(request):
        nonlocal count
        count+=1
        started.set()
        try: await asyncio.Event().wait()
        finally: released.set()
    transport(handler)
    lookup=module.NetworkInfo()
    first=asyncio.create_task(lookup.get())
    await started.wait()
    second=asyncio.create_task(lookup.get(force=True))
    await asyncio.sleep(0)
    await lookup.close()
    results=await asyncio.gather(first,second)
    assert count==1 and released.is_set()
    assert all(r['data'] is None and r['error']=='网络信息查询已关闭' for r in results)
    assert lookup._task is None


@pytest.mark.parametrize('ip',['127.0.0.1','10.0.0.1','100.64.0.1','::1','fe80::1','ff02::1','2001:db8::1','8.8.8.8%eth0'])
def test_nonpublic_addresses_rejected(ip):
    with pytest.raises(ValueError): module._public_ip(ip)


def test_source_fields_are_bounded_and_explicit_risk_only():
    value={'ip':'2606:4700:4700::1111','location':{'country':'A\u0000B\nC','country_code':'us'},
           'isp':{'org':'x'*1000,'asn':True},'risk':{'is_mobile':True,'is_proxy':True,'is_vpn':'true','risk_score':101}}
    result=module._normalize('IPQuery',json.dumps(value).encode())
    assert result['country']=='ABC' and len(result['organization'])==128
    assert result['asn']=='' and result['riskScore'] is None
    assert result['networkType']=='mobile' and result['riskFlags']==['proxy']
    trace=module._normalize('Cloudflare',b'ip=1.1.1.1\nloc=HK\nprivate=ignored')
    assert trace['countryCode']=='HK' and 'private' not in trace
