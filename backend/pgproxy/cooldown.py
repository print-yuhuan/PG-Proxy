"""Bound upstream Retry-After and preserve the original recovery deadline."""

import math
from datetime import timezone
from email.utils import parsedate_to_datetime

from .errors import PlaygroundError


DEFAULT_RETRY_SECONDS = 60
MAX_RETRY_SECONDS = 24 * 60 * 60


def retry_deadline(value, now):
    delay = DEFAULT_RETRY_SECONDS
    if isinstance(value, str):
        value = value.strip()
        if value.isascii() and value.isdecimal():
            digits = value.lstrip('0') or '0'
            delay = int(digits) if len(digits) <= len(str(MAX_RETRY_SECONDS)) else MAX_RETRY_SECONDS
        else:
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                delay = date.timestamp() - now
            except (ValueError, TypeError, OverflowError, OSError):
                pass
    return now + min(MAX_RETRY_SECONDS, max(1, delay))


def retry_after_header(remaining):
    return str(math.ceil(min(MAX_RETRY_SECONDS, max(1, remaining))))


class CooldownError(PlaygroundError):
    def __init__(self, deadline, remaining, message='Upstream rate limit cooldown active'):
        super().__init__(message, 429, 'rate_limit', retry_after_header(remaining))
        self.cooldown_deadline = deadline
