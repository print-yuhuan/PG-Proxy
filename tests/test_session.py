import copy
import hashlib
import json

import httpx
import pytest

from pgproxy.session import BridgeSession, GoogleSession
from pgproxy.errors import PlaygroundError
from test_accounts import MemoryBridge, credentials


def test_cookie_scope_expiry_and_signer():
    session=GoogleSession(credentials('test'))
    names=['SAPISID','__Secure-1PAPISID','__Secure-3PAPISID','__Secure-1PSID']
    session.state['cookies'] += [dict(name='expired',value='bad',domain='.google.com',path='/',expires=1),
                                dict(name='hostonly',value='bad',domain='google.com',path='/',expires=-1),
                                dict(name='path',value='bad',domain='.google.com',path='/abc',expires=-1)]
    session.load()
    assert [c['name'] for c in session.matching('https://aistudio.google.com/',now=123)]==names
    assert not session.matching('https://evilgoogle.com/',now=123)
    assert not any(c['name']=='path' for c in session.matching('https://aistudio.google.com/abcd',now=123))
    digest=hashlib.sha1(b'123 test https://aistudio.google.com').hexdigest()
    assert session.authorization(123).split()[1]=='123_'+digest


def test_current_gecko_session_survives_encrypted_bridge_capture():
    bridge=MemoryBridge()
    state=credentials('test')
    for cookie in state['cookies']:
        cookie['expirationDate']=cookie.pop('expires')
    state.update(browserMetadata={'language':'zh-CN','userAgent':'unused'},
                 importMetadata={'source':'pgproxy-gecko-profile'},
                 origins=[{'origin':'https://aistudio.google.com','localStorage':[{'name':'x','value':'中文'}]}])
    bridge.writeAccount('a',json.dumps(state))
    session=BridgeSession('a',bridge)
    session.load()
    assert session.local_storage()=={'x':'中文'}
    assert session.state['browserMetadata']=={'language':'zh-CN'}
    assert 'importMetadata' not in session.state
    jar=httpx.Cookies()
    session.install(jar)
    session.capture(jar)
    fresh=BridgeSession('a',bridge)
    fresh.load()
    assert fresh.authorization() and len(fresh.state['cookies'])==4


@pytest.mark.parametrize('change',[
    {'path':None},{'path':[]},{'expires':float('nan')},{'expires':float('inf')},
    {'expires':True},{'expires':'tomorrow'},{'secure':'false'},
    {'name':'X\r\nCookie'},{'name':'X;Y'},{'value':'secret\r\nvalue'},
])
def test_invalid_cookie_fields_are_controlled_errors(change):
    state=credentials()
    state['cookies'][0].update(change)
    with pytest.raises(PlaygroundError) as error:
        GoogleSession(state).load()
    assert error.value.code=='session_invalid'
    assert 'secret' not in error.value.body()['error']['message']


@pytest.mark.parametrize('state',[[],{},'curl https://example.test',{'cookies':[],'origins':[]},
                                {'cookies':[],'browserMetadata':[]}])
def test_obsolete_or_empty_session_inputs_fail_safely(state):
    with pytest.raises(PlaygroundError):
        GoogleSession(copy.deepcopy(state)).load()


def test_bridge_errors_do_not_expose_sensitive_exception_text():
    bridge=MemoryBridge()
    def unavailable(*args): raise RuntimeError('cookie=private-session-token')
    bridge.readAccount=unavailable
    bridge.writeAccount=unavailable
    session=BridgeSession('a',bridge)
    for action in (session.load,session.save):
        with pytest.raises(PlaygroundError) as error:
            action()
        assert error.value.code=='session_storage_error'
        assert 'private-session-token' not in error.value.body()['error']['message']
