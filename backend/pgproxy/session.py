"""Validate current Gecko sessions and persist through the Android encrypted bridge."""
import http.cookiejar
import json
import copy
import math
import re
import time
from urllib.parse import urlparse
from .config import ORIGIN
from .errors import PlaygroundError


def google_domain(domain):
    if not isinstance(domain, str):
        return False
    domain = domain.lstrip('.').lower()
    return domain == 'google.com' or domain.endswith('.google.com')


class GoogleSession:
    def __init__(self, data=None):
        self.state = copy.deepcopy(data) if data is not None else {'cookies': [], 'origins': []}
        self.revision = 1

    def load(self):
        try:
            data = self.read_data()
            if not isinstance(data, dict) or not isinstance(data.get('cookies'), list):
                raise ValueError()
            cookies = []
            for c in data['cookies']:
                if not isinstance(c, dict) or not isinstance(c.get('name'), str) or not isinstance(c.get('value'), str):
                    raise ValueError()
                if not google_domain(c.get('domain', '')):
                    continue
                c = dict(c)
                c.setdefault('path', '/')
                c.setdefault('expires', c.pop('expirationDate', -1))
                if (not isinstance(c['path'],str) or not c['path'].startswith('/')
                        or not c['name'] or re.search(r'[\x00-\x20\x7f;=]',c['name'])
                        or not c['name'].isascii() or not c['value'].isascii()
                        or re.search(r'[\x00-\x1f\x7f]',c['value'])
                        or type(c['expires']) not in (int,float) or not math.isfinite(c['expires'])
                        or any(key in c and type(c[key]) is not bool for key in ('secure','httpOnly'))):
                    raise ValueError()
                cookies.append(c)
            origins = data.get('origins', [])
            if not isinstance(origins,list):
                raise ValueError()
            for origin in origins:
                if (not isinstance(origin,dict) or not isinstance(origin.get('origin'),str)
                        or not isinstance(origin.get('localStorage',[]),list)):
                    raise ValueError()
                if any(not isinstance(item,dict) or not isinstance(item.get('name'),str)
                       or not isinstance(item.get('value'),str) for item in origin.get('localStorage',[])):
                    raise ValueError()
            metadata = data.get('browserMetadata')
            if metadata is None:
                metadata = {}
            if not isinstance(metadata,dict):
                raise ValueError()
            language = metadata.get('language','')
            if not isinstance(language,str) or len(language)>128 or any(ord(c)<32 or ord(c)>126 for c in language):
                raise ValueError()
            self.state = {'cookies':cookies,'origins':origins,
                          'browserMetadata':{'language':language} if language else {}}
            self.authorization()
        except FileNotFoundError:
            raise PlaygroundError('Google session missing. Sign in using the Accounts page.', 503, 'session_required') from None
        except (ValueError, TypeError, KeyError, OSError, OverflowError):
            raise PlaygroundError('Invalid Google session data', 503, 'session_invalid') from None

    def read_data(self):
        return copy.deepcopy(self.state)

    def matching(self, url=ORIGIN+'/', now=None):
        now = time.time() if now is None else now
        target = urlparse(url)
        matches = []
        for c in self.state['cookies']:
            domain = c['domain'].lstrip('.').lower()
            host = target.hostname or ''
            domain_ok = host == domain or (c['domain'].startswith('.') and host.endswith('.'+domain))
            path = c.get('path', '/')
            request_path = target.path or '/'
            path_ok = request_path == path or (request_path.startswith(path) and (path.endswith('/') or request_path[len(path):].startswith('/')))
            expires = float(c.get('expires', -1))
            if domain_ok and path_ok and (expires < 0 or expires > now) and (not c.get('secure') or target.scheme == 'https'):
                matches.append(c)
        return sorted(matches, key=lambda c: -len(c.get('path','/')))

    def authorization(self, now=None):
        import hashlib
        stamp = int(time.time() if now is None else now)
        cookies = {c['name']: c['value'] for c in reversed(self.matching(now=stamp))}
        parts = []
        for label, name in [('SAPISIDHASH','SAPISID'), ('SAPISID1PHASH','__Secure-1PAPISID'), ('SAPISID3PHASH','__Secure-3PAPISID')]:
            value = cookies.get(name)
            if not value:
                raise PlaygroundError('Google session is expired or missing required signing cookies. Re-import a fresh session.', 503, 'session_required')
            digest = hashlib.sha1(f'{stamp} {value} {ORIGIN}'.encode()).hexdigest()
            parts.append(f'{label} {stamp}_{digest}')
        return ' '.join(parts)

    def install(self, jar):
        for c in self.state['cookies']:
            expires = float(c.get('expires', -1))
            jar.jar.set_cookie(http.cookiejar.Cookie(
                0,c['name'],c['value'],None,False,c['domain'],c['domain'].startswith('.'),c['domain'].startswith('.'),
                c['path'],True,c.get('secure',False),None if expires < 0 else int(expires),expires < 0,
                None,None,{'HttpOnly':True} if c.get('httpOnly') else {},False))

    def capture(self, jar):
        cookies = []
        for c in jar.jar:
            if google_domain(c.domain):
                cookies.append(dict(name=c.name,value=c.value,domain=c.domain,path=c.path or '/',expires=c.expires if c.expires is not None else -1,
                                    secure=c.secure,httpOnly=c.has_nonstandard_attr('HttpOnly')))
        self.state['cookies'] = cookies
        self.save()

    def save(self):
        self.revision += 1

    def local_storage(self):
        return {i['name']: i['value'] for o in self.state.get('origins', []) if o.get('origin') == ORIGIN for i in o.get('localStorage', [])}



class BridgeSession(GoogleSession):
    """Persist exclusively through Android Keystore-backed private storage."""
    def __init__(self, account_id, bridge):
        super().__init__()
        self.account_id = account_id
        self.bridge = bridge
        self.revision = 1

    def read_data(self):
        try:
            raw = self.bridge.readAccount(self.account_id)
        except Exception:
            raise PlaygroundError('Cannot read encrypted session',503,'session_storage_error') from None
        if not raw:
            raise FileNotFoundError()
        return json.loads(str(raw))

    def save(self):
        try:
            self.bridge.writeAccount(self.account_id, json.dumps(self.state, separators=(',', ':')))
        except Exception:
            raise PlaygroundError('Cannot save encrypted session',503,'session_storage_error') from None
        self.revision += 1
