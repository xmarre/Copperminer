import aiohttp
import asyncio
from proxy_manager import ProxyPool

async def head_with_proxy(url, proxy_pool: ProxyPool | None, headers=None, timeout=10):
    headers = headers or {}
    if proxy_pool is None:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.head(url, headers=headers, allow_redirects=True, timeout=timeout) as resp:
                return resp.status, dict(resp.headers)
    for attempt in range(5):
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

async def fetch_html(url, proxy_pool: ProxyPool | None, headers=None, timeout=15):
    headers = headers or {}
    if proxy_pool is None:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url, headers=headers, timeout=timeout, allow_redirects=True) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return text, dict(resp.headers)
        raise Exception(f"Failed to fetch {url}")
    for attempt in range(5):
        proxy = await proxy_pool.get_proxy()
        proxy_url = f"http://{proxy}"
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, headers=headers, proxy=proxy_url, timeout=timeout, allow_redirects=True) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        return text, dict(resp.headers)
        except Exception:
            await proxy_pool.remove_proxy(proxy)
            continue
    raise Exception(f"Failed to fetch {url}")

async def download_with_proxy(url, out_path, proxy_pool: ProxyPool | None, referer=None):
    headers = {'Referer': referer} if referer else {}
    if proxy_pool is None:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url, headers=headers, timeout=15) as resp:
                if resp.status == 200 and resp.headers.get("Content-Type", "").startswith("image"):
                    with open(out_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(16*1024):
                            f.write(chunk)
                    return True
        return False
    for attempt in range(5):
        proxy = await proxy_pool.get_proxy()
        proxy_url = f"http://{proxy}"
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, proxy=proxy_url, headers=headers, timeout=15) as resp:
                    if resp.status == 200 and resp.headers.get("Content-Type", "").startswith("image"):
                        with open(out_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(16*1024):
                                f.write(chunk)
                        return True
        except Exception:
            await proxy_pool.remove_proxy(proxy)
            continue
    return False

