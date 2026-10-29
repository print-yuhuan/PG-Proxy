"""Independent public egress lookup for the private Android overview panel."""
import asyncio
import contextlib
import copy
from email.utils import parsedate_to_datetime
import ipaddress
import json
import re
import time
import unicodedata

import httpx

from .config import VERSION


_SOURCES = (
    ('IPQuery','https://api.ipquery.io/?format=json'),
    ('ident.me','https://ident.me/json'),
    ('Cloudflare','https://www.cloudflare.com/cdn-cgi/trace'),
)
_MAX_BODY = 64 * 1024
_CACHE_SECONDS = 300
_ROUND_INTERVAL = 10


def _text(value, limit=128):
    if not isinstance(value,str):
        return ''
    cleaned = ''.join(c for c in value[:limit*4] if not unicodedata.category(c).startswith('C'))
    return ' '.join(cleaned.split())[:limit]


def _public_ip(value):
    if not isinstance(value,str) or len(value)>64 or '%' in value:
        raise ValueError('Invalid public address')
    address = ipaddress.ip_address(value.strip())
    if (not address.is_global or address.is_multicast or address.is_unspecified
            or address.is_loopback or address.is_link_local or address.is_reserved):
        raise ValueError('Address is not public')
    return str(address)


def _country_code(value):
    value = _text(value,8).upper()
    return value if len(value)==2 and value.isascii() and value.isalpha() else ''


def _asn(value):
    if type(value) is int:
        number = value
    elif isinstance(value,str):
        match = re.fullmatch(r'(?:AS)?([0-9]{1,10})',value.strip(),re.IGNORECASE)
        if not match:
            return ''
        number = int(match[1])
    else:
        return ''
    return f'AS{number}' if 1 <= number <= 4294967295 else ''


def _base(ip, source):
    return {'ip':_public_ip(ip),'countryCode':'','country':'','region':'','city':'',
            'organization':'','isp':'','asn':'','networkType':'','riskScore':None,
            'riskFlags':[],'source':source,'updatedAt':time.time()}


def _normalize(source, body):
    if source == 'Cloudflare':
        fields = {}
        for line in body.decode('utf-8').splitlines():
            name,sep,value = line.partition('=')
            if sep and name in ('ip','loc'):
                fields[name] = value
        result = _base(fields.get('ip'),source)
        result['countryCode'] = _country_code(fields.get('loc'))
        return result

    value = json.loads(body)
    if not isinstance(value,dict):
        raise ValueError('Invalid lookup response')
    result = _base(value.get('ip'),source)
    if source == 'IPQuery':
        location = value.get('location')
        location = location if isinstance(location,dict) else {}
        isp = value.get('isp')
        isp = isp if isinstance(isp,dict) else {}
        risk = value.get('risk')
        risk = risk if isinstance(risk,dict) else {}
        result.update(countryCode=_country_code(location.get('country_code')),
                      country=_text(location.get('country')),region=_text(location.get('state')),
                      city=_text(location.get('city')),organization=_text(isp.get('org')),
                      isp=_text(isp.get('isp')),asn=_asn(isp.get('asn')))
        if risk.get('is_datacenter') is True:
            result['networkType'] = 'hosting'
        elif risk.get('is_mobile') is True:
            result['networkType'] = 'mobile'
        score = risk.get('risk_score')
        if type(score) is int and 0 <= score <= 100:
            result['riskScore'] = score
        elif type(score) is float and score.is_integer() and 0 <= score <= 100:
            result['riskScore'] = int(score)
        result['riskFlags'] = [flag for flag in ('proxy','vpn','tor','abuser')
                               if risk.get('is_'+flag) is True]
    elif source == 'ident.me':
        result.update(countryCode=_country_code(value.get('cc')),country=_text(value.get('country')),
                      region=_text(value.get('region')),city=_text(value.get('city')),
                      organization=_text(value.get('aso')),asn=_asn(value.get('asn')))
        result['networkType'] = {
            'isp':'residential','business':'business','education':'business',
            'government':'business','banking':'business','hosting':'hosting',
        }.get(_text(value.get('type'),32).lower(),'')
    return result


def _retry_after(value):
    if not isinstance(value,str) or len(value)>128:
        return 60.0
    value = value.strip()
    if re.fullmatch(r'[0-9]+',value):
        return float(int(value))
    try:
        moment = parsedate_to_datetime(value)
        if moment.tzinfo is None:
            return 60.0
        return max(0.0,moment.timestamp()-time.time())
    except (TypeError,ValueError,OverflowError):
        return 60.0


class NetworkInfo:
    """One in-flight task, a five-minute cache, and independent source cooldowns."""
    def __init__(self):
        self._data = None
        self._error = ''
        self._last_success = None
        self._last_started = None
        self._cooldowns = {}
        self._task = None
        self._closed = False

    def _result(self, error=None):
        return {'data':copy.deepcopy(self._data),'error':self._error if error is None else error}

    async def get(self, force=False):
        if type(force) is not bool:
            raise ValueError('Invalid refresh flag')
        if self._closed:
            return self._result('网络信息查询已关闭')
        if self._task is not None and not self._task.done():
            task = self._task
        else:
            now = time.monotonic()
            if not force and self._last_success is not None and now-self._last_success < _CACHE_SECONDS:
                return self._result()
            if self._last_started is not None and now-self._last_started < _ROUND_INTERVAL:
                return self._result(self._error or '刷新过于频繁，请稍后重试')
            self._last_started = now
            task = self._task = asyncio.create_task(self._refresh())
        try:
            # One caller navigating away does not cancel the shared lookup.
            return copy.deepcopy(await asyncio.shield(task))
        except asyncio.CancelledError:
            if self._closed:
                return self._result('网络信息查询已关闭')
            raise

    async def _fetch(self, client, source, url):
        # The independent client never receives account headers; discard any
        # public provider cookies before sending another fixed-origin request.
        client.cookies.clear()
        async with client.stream('GET',url) as response:
            if response.status_code == 429:
                self._cooldowns[source] = time.monotonic()+_retry_after(response.headers.get('retry-after'))
                return None
            if response.status_code != 200:
                return None
            length = response.headers.get('content-length','')
            if length.isdigit() and int(length)>_MAX_BODY:
                return None
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                if len(body)+len(chunk)>_MAX_BODY:
                    return None
                body.extend(chunk)
            return _normalize(source,bytes(body))

    async def _refresh(self):
        try:
            async with asyncio.timeout(20):
                async with httpx.AsyncClient(
                    trust_env=False,follow_redirects=False,timeout=httpx.Timeout(6),
                    headers={'User-Agent':f'PG-Proxy/{VERSION}','Accept':'application/json, text/plain',
                             'Accept-Encoding':'identity'},
                    limits=httpx.Limits(max_connections=1,max_keepalive_connections=0),
                ) as client:
                    for source,url in _SOURCES:
                        if time.monotonic()<self._cooldowns.get(source,0):
                            continue
                        try:
                            async with asyncio.timeout(6):
                                data = await self._fetch(client,source,url)
                        except (httpx.HTTPError,TimeoutError,ValueError,UnicodeError,RecursionError):
                            continue
                        if data is not None:
                            self._data = data
                            self._error = ''
                            self._last_success = time.monotonic()
                            return self._result()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never expose provider bodies, URLs, headers or exception strings.
            pass
        self._error = ('网络查询服务暂时受限，请稍后重试'
                       if all(time.monotonic()<self._cooldowns.get(source,0) for source,_ in _SOURCES)
                       else '暂时无法获取网络信息，请稍后重试')
        return self._result()

    async def close(self):
        self._closed = True
        task, self._task = self._task, None
        if task is not None:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
