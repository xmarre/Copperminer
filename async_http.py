import aiohttp
import asyncio
import logging
import time
from proxy_manager import ProxyPool

log = logging.getLogger("ripper.http")
log.setLevel(logging.DEBUG)

async def head_with_proxy(url, proxy_pool: ProxyPool | None, headers=None, timeout=5):
    headers = headers or {}
    if proxy_pool is None:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.head(url, headers=headers, allow_redirects=True, timeout=timeout) as resp:
                return resp.status, dict(resp.headers)
    for attempt in range(3):
        proxy = await proxy_pool.get_proxy()
        proxy_url = f"http://{proxy}"
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.head(url, headers=headers, proxy=proxy_url, allow_redirects=True, timeout=timeout) as resp:
                    return resp.status, dict(resp.headers)
        except Exception:
            await proxy_pool.remove_proxy(proxy)
            continue
    raise Exception(f"Failed to HEAD {url}")

async def fetch_html(url, proxy_pool: ProxyPool | None, headers=None,
                     timeout=15, *, label="GET"):
    headers = headers or {}
    attempts = 5 if proxy_pool else 1
    for attempt in range(1, attempts + 1):
        proxy = None
        if proxy_pool:
            proxy = await proxy_pool.get_proxy()
            proxy_url = f"http://{proxy}"
        t0 = time.time()
        try:
            log.debug("[%s %d/%d] %s via %s", label, attempt, attempts,
                      url, proxy or "DIRECT")
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as s:
                async with s.get(url, headers=headers,
                                 proxy=proxy_url if proxy else None,
                                 timeout=timeout,
                                 allow_redirects=True) as r:
                    log.debug("[HTTP] %s -> %s in %.1fs",
                              url, r.status, time.time() - t0)
                    if r.status == 200:
                        return await r.text(), dict(r.headers)
        except Exception as e:
            log.debug("[HTTP-ERR] %s via %s : %s", url, proxy or "DIRECT", e)
            if proxy_pool and proxy:
                await proxy_pool.remove_proxy(proxy)
    raise Exception(f"Failed to fetch {url}")

async def download_with_proxy(url, out_path, proxy_pool: ProxyPool | None, referer=None):
    headers = {'Referer': referer} if referer else {}
    attempts = 5 if proxy_pool else 1
    for attempt in range(1, attempts + 1):
        proxy = None
        if proxy_pool:
            proxy = await proxy_pool.get_proxy()
            proxy_url = f"http://{proxy}"
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, proxy=proxy_url if proxy else None,
                                       headers=headers, timeout=15) as resp:
                    if resp.status == 200 and resp.headers.get("Content-Type", "").startswith("image"):
                        log.debug("[IMG %d/%d] %s via %s", attempt, attempts, url, proxy or "DIRECT")
                        with open(out_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(16*1024):
                                f.write(chunk)
                        return True
        except Exception as e:
            log.debug("[HTTP-ERR] %s via %s : %s", url, proxy or "DIRECT", e)
            if proxy_pool and proxy:
                await proxy_pool.remove_proxy(proxy)
            continue
    return False

