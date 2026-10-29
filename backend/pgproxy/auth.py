"""Authentication for local API clients, independent of the Google session."""
import base64
import binascii
import secrets
from fastapi import Request, HTTPException


def authenticate_user(request: Request):
    password = request.app.state.playground.settings.password
    credentials = [request.query_params.get('key'), request.headers.get('x-goog-api-key')]
    auth = request.headers.get('authorization', '').split(' ', 1)
    if len(auth) == 2:
        if auth[0].lower() == 'bearer':
            credentials.append(auth[1])
        elif auth[0].lower() == 'basic':
            try:
                decoded = base64.b64decode(auth[1], validate=True).decode('utf-8')
                if ':' in decoded:
                    credentials.append(decoded.split(':', 1)[1])
            except (ValueError, UnicodeError, binascii.Error):
                pass
    if any(isinstance(v, str) and secrets.compare_digest(v.encode(), password.encode()) for v in credentials):
        return 'local-client'
    raise HTTPException(401, 'Invalid API credentials', headers={'WWW-Authenticate': 'Basic'})
