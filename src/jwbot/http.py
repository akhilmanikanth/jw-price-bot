"""Shared HTTP session with retries and browser-like headers."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Chromium";v="126", "Not:A-Brand";v="24", "Google Chrome";v="126"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


def build_session(user_agent: str, total_retries: int = 3, backoff: float = 2.0) -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    session.headers["User-Agent"] = user_agent

    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "POST"}),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def polite_sleep(min_s: float = 0.4, max_s: float = 1.4) -> None:
    time.sleep(random.uniform(min_s, max_s))


def get_json(session: requests.Session, url: str, timeout: float, **kwargs: Any) -> Any:
    headers = dict(kwargs.pop("headers", {}))
    headers.setdefault("Accept", "application/json, text/plain, */*")
    headers.setdefault("Sec-Fetch-Dest", "empty")
    headers.setdefault("Sec-Fetch-Mode", "cors")
    headers.setdefault("Sec-Fetch-Site", "same-site")
    response = session.get(url, timeout=timeout, headers=headers, **kwargs)
    response.raise_for_status()
    return response.json()


def get_text(session: requests.Session, url: str, timeout: float, **kwargs: Any) -> str:
    response = session.get(url, timeout=timeout, **kwargs)
    response.raise_for_status()
    return response.text
