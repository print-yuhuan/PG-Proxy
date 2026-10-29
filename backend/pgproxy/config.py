"""Application configuration supplied by the Android encrypted store."""
from dataclasses import dataclass
import secrets

NAME = 'PG Proxy'
VERSION = '0.1.0'
ORIGIN = 'https://aistudio.google.com'
RPC_BASE = 'https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/'
WAA_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0'
DEFAULT_PORT = 8888
DEFAULT_API_KEY = '123'

@dataclass
class Settings:
    password: str = DEFAULT_API_KEY
    user_agent: str = WAA_USER_AGENT
    language: str = 'en-US,en;q=0.9'
    timezone: str = 'Asia/Shanghai'
    timeout: float = 180
    waa_timeout: float = 45
    waa_ttl: float = 43200
    model_ttl: float = 300
    queue_timeout: float = 30

    def __post_init__(self):
        if not self.password:
            self.password = secrets.token_urlsafe(24)
