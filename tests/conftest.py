import json
from types import SimpleNamespace
import pytest
import httpx
from pgproxy.config import Settings
from pgproxy.session import GoogleSession
from pgproxy.playground_client import PlaygroundClient


def model_row(name='models/test-chat'):
    row = [None]*83
    for i,v in {0:name,3:'Test Chat',4:'Synthetic test catalog',5:1024,6:512,7:['generateContent'],8:1.0,9:0.95,10:64,27:2,64:[1,9,12,25,35,52],71:[128,1024,0,True,None,2,[1,2,3,4]]}.items():
        row[i]=v
    return row


def frame(text='OK',finish=1):
    candidate = [[[[None,text]],'model'],finish]
    return [[candidate],None,[5,1,6]]


class TestWAA:
    """Synthetic transport contract only. Never used for a Google request."""
    def __init__(self):
        self.process = SimpleNamespace(returncode=None)
        self.calls = []
    async def call(self,op,**kw):
        self.calls.append((op,kw))
        # Deliberately not a Google proof. httpx.MockTransport has no network.
        return {'proof':'!UNIT_TEST_ONLY'}
    async def close(self):
        self.process = None


@pytest.fixture
def client_factory():
    def create(generate_status=200,generate_body=None,extra_handler=None):
        settings = Settings()
        session = GoogleSession()
        session.state['cookies'] = [dict(name=n,value='synthetic-'+n,domain='.google.com',path='/',expires=-1,secure=True,httpOnly=False) for n in ('SAPISID','__Secure-1PAPISID','__Secure-3PAPISID')]
        session.save()
        requests=[]
        def handler(req):
            requests.append(req)
            if extra_handler:
                response = extra_handler(req)
                if response is not None:
                    return response
            method = req.url.path.rsplit('/',1)[-1]
            if req.method=='GET' and req.url.host=='aistudio.google.com':
                return httpx.Response(200,text='<html>"WIu0Nc":"test-page-key"</html>',headers={'content-type':'text/html'})
            if method=='GetLoggingContext':
                return httpx.Response(200,json=['visit',42,True])
            if method=='GetAiStudioBenefitTier':
                return httpx.Response(200,json=[1])
            if method=='ListModels':
                return httpx.Response(200,json=[[model_row()]])
            if method=='GenerateContent':
                return httpx.Response(generate_status,content=generate_body or json.dumps([[frame('O',None),frame('K',1)]]).encode(),headers={'content-type':'application/json+protobuf','retry-after':'25'})
            return httpx.Response(200,json=[])
        client = PlaygroundClient(settings,httpx.MockTransport(handler),session=session)
        client.waa = TestWAA()
        async def prepared():
            client.waa_at = 1
        client._prepare_waa = prepared
        return client,requests
    return create
