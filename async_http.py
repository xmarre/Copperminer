import aiohttp
import asyncio
import logging
import time
from proxy_manager import ProxyPool

log = logging.getLogger("ripper.http")
# Use INFO so important connection details show even when the root logger
# runs at its default INFO level. More verbose timing info is logged at DEBUG.
log.setLevel(logging.INFO)

async def head_with_proxy(url, proxy_pool: ProxyPool | None, headers=None, timeout=5):
    headers = headers or {}
    if proxy_pool is None:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.head(url, headers=headers, allow_redirects=True, timeout=timeout) as resp:
                return resp.status, dict(resp.headers)
    attempts = 3
    for attempt in range(1, attempts + 1):
        proxy = await proxy_pool.get_proxy()
        proxy_url = f"http://{proxy}"
        try:
            log.info("[HEAD %d/%d] %s via %s", attempt, attempts, url, proxy)
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.head(
                    url,
                    headers=headers,
                    proxy=proxy_url,
                    allow_redirects=True,
                    timeout=timeout,
                ) as resp:
                    log.info("[HEAD] %s -> %s", url, resp.status)
                    return resp.status, dict(resp.headers)
        except Exception as e:
            log.info("[HEAD-ERR] %s via %s : %s", url, proxy, e)
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
            log.info("[%s %d/%d] %s via %s", label, attempt, attempts,
                     url, proxy or "DIRECT")
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as s:
                async with s.get(
                    url,
                    headers=headers,
                    proxy=proxy_url if proxy else None,
                    timeout=timeout,
                    allow_redirects=True,
                ) as r:
                    log.info("[HTTP] %s -> %s in %.1fs",
                             url, r.status, time.time() - t0)
                    if r.status == 200:
                        return await r.text(), dict(r.headers)
        except Exception as e:
            log.info("[HTTP-ERR] %s via %s : %s", url, proxy or "DIRECT", e)
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
            log.info("[IMG %d/%d] %s via %s", attempt, attempts, url, proxy or "DIRECT")
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    url,
                    proxy=proxy_url if proxy else None,
                    headers=headers,
                    timeout=15,
                ) as resp:
                    if resp.status == 200 and resp.headers.get("Content-Type", "").startswith("image"):
                        log.info("[IMG] %s -> %s", url, resp.status)
                        with open(out_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(16*1024):
                                f.write(chunk)
                        return True
        except Exception as e:
            log.info("[HTTP-ERR] %s via %s : %s", url, proxy or "DIRECT", e)
            if proxy_pool and proxy:
                await proxy_pool.remove_proxy(proxy)
            continue
    return False

