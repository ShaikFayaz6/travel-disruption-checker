"""Shared async HTTP client with a small in-memory TTL cache.

Caching keeps us well inside the free-tier / fair-use limits of the public APIs
when the same airport or point is queried repeatedly.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from . import perf
from .config import HTTP_TIMEOUT, USER_AGENT

_client: httpx.AsyncClient | None = None
_cache: dict[str, tuple[float, Any]] = {}
_locks: dict[str, asyncio.Lock] = {}


async def _first_byte(resp: httpx.Response) -> None:
    resp.request.extensions["first_byte"] = time.perf_counter()  # headers in, body not read yet


class _TimedClient(httpx.AsyncClient):
    """Records every request (each retry and Gemini call included) in the current perf trace."""

    async def send(self, request: httpx.Request, **kw) -> httpx.Response:
        t0 = time.perf_counter()
        try:
            resp = await super().send(request, **kw)
        except Exception as e:
            perf.call(str(request.url), t0, request.extensions.get("first_byte"), time.perf_counter(),
                      e.__class__.__name__)
            raise
        perf.call(str(request.url), t0, request.extensions.get("first_byte"), time.perf_counter(),
                  resp.status_code, None if kw.get("stream") else len(resp.content))
        return resp


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = _TimedClient(
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            follow_redirects=True,
            event_hooks={"response": [_first_byte]},
        )
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _key(url: str, params: dict | None) -> str:
    if not params:
        return url
    return url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))


RETRY_STATUS = {429, 500, 502, 503, 504}


async def get(url: str, *, params: dict | None = None, ttl: float = 300, as_json: bool = True,
              headers: dict | None = None, timeout: float | None = None, retries: int = 1,
              retry_wait: float = 2.0) -> Any:
    """GET with TTL caching and a retry on timeouts / 429 / 5xx."""
    key = _key(url, params) + ("#json" if as_json else "#text")
    shown = _key(url, params)
    now, t0 = time.time(), time.perf_counter()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        perf.call(shown, t0, None, time.perf_counter(), "-", cached="cache")
        return hit[1]

    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():  # the same call was in flight: waited for its answer
            perf.call(shown, t0, None, time.perf_counter(), "-", cached="waited for the same call")
            return hit[1]
        for attempt in range(retries + 1):
            try:
                resp = await client().get(url, params=params, headers=headers,
                                          timeout=timeout or HTTP_TIMEOUT)
                if resp.status_code in RETRY_STATUS and attempt < retries:
                    await asyncio.sleep(retry_wait * (attempt + 1))
                    continue
                resp.raise_for_status()
                break
            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError):
                if attempt >= retries:
                    raise
                await asyncio.sleep(retry_wait * (attempt + 1))
        data = resp.json() if as_json else resp.text
        _cache[key] = (time.time() + ttl, data)
        return data
